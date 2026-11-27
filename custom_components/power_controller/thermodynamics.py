"""Self-learning 1R1C thermal zone model using Recursive Least Squares (RLS)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .const import InsulationClass

HISTORY_POINTS = 7 * 24          # one point per hour, 7 days
ERR_EMA_WEIGHT = 0.05            # smoothing of the prediction error


def derived_parameters(alpha: float, beta: float) -> dict:
    """Human-readable view of the 1R1C parameters.

    * heat capacity  C = 1/alpha [kWh per °C]  – how much heat it takes to raise the room 1 °C
    * time constant  tau = 1/beta [h]          – how fast the room cools towards outdoor temperature
    * heat loss      UA = beta/alpha [kW per °C indoor–outdoor difference]
    """
    alpha = max(1e-6, float(alpha))
    beta = max(1e-6, float(beta))
    ua_kw = beta / alpha
    return {
        "heat_capacity_kwh_per_c": round(1.0 / alpha, 3),
        "time_constant_h": round(1.0 / beta, 1),
        "heat_loss_w_per_c": round(ua_kw * 1000.0, 1),
        "power_21_at_0_kw": round(ua_kw * 21.0, 2),
        "power_21_at_minus10_kw": round(ua_kw * 31.0, 2),
        "cooling_2h_c": round((21.0 - 0.0) * (1.0 - (1.0 - min(0.95, beta)) ** 2), 2),
    }


def params_from_physical(heat_capacity_kwh_per_c: float, heat_loss_w_per_c: float) -> tuple[float, float]:
    """Inverse of :func:`derived_parameters` for manual adjustment."""
    c = max(0.05, float(heat_capacity_kwh_per_c))
    ua_kw = max(0.0005, float(heat_loss_w_per_c) / 1000.0)
    alpha = 1.0 / c
    beta = ua_kw * alpha
    return round(max(0.05, min(10.0, alpha)), 5), round(max(0.002, min(0.50, beta)), 5)


def seed_thermal_parameters(
    area_m2: float,
    insulation: InsulationClass | str = InsulationClass.STANDARD,
) -> tuple[float, float]:
    """Return initial (alpha [C/kWh], beta [1/h]) seeded from floor area and insulation class."""
    area = max(4.0, float(area_m2))
    ins = InsulationClass(insulation)
    # Thermal capacitance scales with floor area (~0.035 kWh/C per m2)
    capacitance_kwh_per_c = 0.035 * area
    alpha = 1.0 / capacitance_kwh_per_c

    ua_per_m2 = {
        InsulationClass.OLD: 0.0045,
        InsulationClass.STANDARD: 0.0028,
        InsulationClass.TEK17: 0.0016,
    }[ins]
    beta = (ua_per_m2 * area) / capacitance_kwh_per_c
    return (round(alpha, 4), round(beta, 4))


@dataclass
class ThermalZoneModel:
    """1R1C zone thermal dynamics: dT = (alpha * P_heat - beta * (T_room - T_out)) * dt."""

    zone_id: str
    alpha: float = 1.5
    beta: float = 0.05
    forgetting_factor: float = 0.995
    cov_p: list[list[float]] = field(
        default_factory=lambda: [[0.5, 0.0], [0.0, 0.05]]
    )
    # learning bookkeeping (shown on the Settings page)
    seed_alpha: float | None = None
    seed_beta: float | None = None
    samples: int = 0                    # RLS updates applied
    skipped: int = 0                    # uninformative steps / window open
    last_update: str | None = None
    err_ema_c: float | None = None      # smoothed |prediction error| per step [°C]
    frozen: bool = False                # manual parameters: learning paused
    manual: bool = False
    history: list[list] = field(default_factory=list)   # [iso, alpha, beta, err]

    def __post_init__(self) -> None:
        if self.seed_alpha is None:
            self.seed_alpha = self.alpha
        if self.seed_beta is None:
            self.seed_beta = self.beta

    @property
    def status(self) -> str:
        if self.frozen:
            return "manual"
        if self.samples < 30:
            return "learning"
        if self.samples < 200 or (self.err_ema_c is not None and self.err_ema_c > 0.3):
            return "settling"
        return "stable"

    def reset(self, alpha: float | None = None, beta: float | None = None) -> None:
        """Back to the start values (from area/insulation) and start learning again."""
        self.alpha = float(alpha if alpha is not None else self.seed_alpha)
        self.beta = float(beta if beta is not None else self.seed_beta)
        if alpha is not None:
            self.seed_alpha, self.seed_beta = self.alpha, self.beta
        self.cov_p = [[0.5, 0.0], [0.0, 0.05]]
        self.samples = self.skipped = 0
        self.err_ema_c = None
        self.frozen = self.manual = False
        self.history = []
        self.last_update = None

    def set_manual(self, alpha: float, beta: float, freeze: bool = True) -> None:
        self.alpha = max(0.05, min(10.0, float(alpha)))
        self.beta = max(0.002, min(0.50, float(beta)))
        self.cov_p = [[0.05, 0.0], [0.0, 0.005]]   # trust the manual values (slow learning if unfrozen)
        self.frozen = bool(freeze)
        self.manual = True

    def _record(self, now: datetime | None, err_c: float | None) -> None:
        ts = (now or datetime.now()).replace(microsecond=0)
        self.last_update = ts.isoformat()
        hour = ts.replace(minute=0, second=0).isoformat()
        point = [hour, round(self.alpha, 5), round(self.beta, 5), None if err_c is None else round(err_c, 3)]
        if self.history and self.history[-1][0] == hour:
            self.history[-1] = point
        else:
            self.history.append(point)
            del self.history[:-HISTORY_POINTS]

    def predict_next_temp(
        self,
        t_room: float,
        t_out: float,
        power_kw: float,
        dt_hours: float = 1.0,
    ) -> float:
        """Predict indoor temperature after dt_hours."""
        dt_c = (self.alpha * max(0.0, power_kw) - self.beta * (t_room - t_out)) * dt_hours
        return t_room + dt_c

    def update_rls(
        self,
        t_prev: float,
        t_curr: float,
        t_out: float,
        power_kw: float,
        dt_hours: float = 0.25,
        window_open: bool = False,
        now: datetime | None = None,
    ) -> None:
        """Online Recursive Least Squares update for [alpha, beta]."""
        if window_open or dt_hours <= 0.0:
            self.skipped += 1
            return

        y = (t_curr - t_prev) / dt_hours
        x0 = max(0.0, float(power_kw))
        x1 = -(float(t_prev) - float(t_out))

        # Skip uninformative steps where both heating and delta-T are near zero
        if abs(x0) < 0.05 and abs(x1) < 1.0:
            self.skipped += 1
            return
        err_c = abs(y - (self.alpha * x0 + self.beta * x1)) * dt_hours
        self.err_ema_c = err_c if self.err_ema_c is None else (1 - ERR_EMA_WEIGHT) * self.err_ema_c + ERR_EMA_WEIGHT * err_c
        if self.frozen:
            self._record(now, err_c)
            return

        p00, p01 = self.cov_p[0][0], self.cov_p[0][1]
        p10, p11 = self.cov_p[1][0], self.cov_p[1][1]
        lam = self.forgetting_factor

        px0 = p00 * x0 + p01 * x1
        px1 = p10 * x0 + p11 * x1
        denom = lam + x0 * px0 + x1 * px1
        if denom <= 1e-9:
            return

        k0 = px0 / denom
        k1 = px1 / denom

        y_hat = self.alpha * x0 + self.beta * x1
        err = y - y_hat

        new_alpha = self.alpha + k0 * err
        new_beta = self.beta + k1 * err

        # Clamp parameters to physically plausible bounds
        self.alpha = max(0.05, min(10.0, new_alpha))
        self.beta = max(0.002, min(0.50, new_beta))

        # Covariance matrix update: P = (P - K * x^T * P) / lam
        xt_p0 = x0 * p00 + x1 * p10
        xt_p1 = x0 * p01 + x1 * p11
        self.cov_p = [
            [(p00 - k0 * xt_p0) / lam, (p01 - k0 * xt_p1) / lam],
            [(p10 - k1 * xt_p0) / lam, (p11 - k1 * xt_p1) / lam],
        ]
        self.samples += 1
        self._record(now, err_c)

    def to_dict(self) -> dict:
        """Serialize parameters for persistent .storage."""
        return {
            "zone_id": self.zone_id,
            "alpha": self.alpha,
            "beta": self.beta,
            "cov_p": self.cov_p,
            "seed_alpha": self.seed_alpha,
            "seed_beta": self.seed_beta,
            "samples": self.samples,
            "skipped": self.skipped,
            "last_update": self.last_update,
            "err_ema_c": self.err_ema_c,
            "frozen": self.frozen,
            "manual": self.manual,
            "history": self.history,
        }

    def summary(self) -> dict:
        """Everything the Settings page shows for this zone."""
        return {
            "alpha": round(self.alpha, 5), "beta": round(self.beta, 5),
            "seed_alpha": self.seed_alpha, "seed_beta": self.seed_beta,
            **derived_parameters(self.alpha, self.beta),
            "seed": derived_parameters(self.seed_alpha, self.seed_beta),
            "samples": self.samples, "skipped": self.skipped, "last_update": self.last_update,
            "err_ema_c": None if self.err_ema_c is None else round(self.err_ema_c, 3),
            "status": self.status, "frozen": self.frozen, "manual": self.manual,
            "history": [[h[0], derived_parameters(h[1], h[2])["heat_loss_w_per_c"],
                         derived_parameters(h[1], h[2])["time_constant_h"], h[3]] for h in self.history],
        }

    @classmethod
    def from_dict(cls, data: dict) -> ThermalZoneModel:
        """Restore parameters from .storage."""
        return cls(
            zone_id=data["zone_id"],
            alpha=float(data.get("alpha", 1.5)),
            beta=float(data.get("beta", 0.05)),
            cov_p=data.get("cov_p", [[0.5, 0.0], [0.0, 0.05]]),
            seed_alpha=data.get("seed_alpha"),
            seed_beta=data.get("seed_beta"),
            samples=int(data.get("samples", 0)),
            skipped=int(data.get("skipped", 0)),
            last_update=data.get("last_update"),
            err_ema_c=data.get("err_ema_c"),
            frozen=bool(data.get("frozen", False)),
            manual=bool(data.get("manual", False)),
            history=list(data.get("history", [])),
        )
