"""Self-learning 1R1C thermal zone model using Recursive Least Squares (RLS)."""
from __future__ import annotations

from dataclasses import dataclass, field
from .const import InsulationClass


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
    ) -> None:
        """Online Recursive Least Squares update for [alpha, beta]."""
        if window_open or dt_hours <= 0.0:
            return

        y = (t_curr - t_prev) / dt_hours
        x0 = max(0.0, float(power_kw))
        x1 = -(float(t_prev) - float(t_out))

        # Skip uninformative steps where both heating and delta-T are near zero
        if abs(x0) < 0.05 and abs(x1) < 1.0:
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

    def to_dict(self) -> dict:
        """Serialize parameters for persistent .storage."""
        return {
            "zone_id": self.zone_id,
            "alpha": self.alpha,
            "beta": self.beta,
            "cov_p": self.cov_p,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ThermalZoneModel:
        """Restore parameters from .storage."""
        return cls(
            zone_id=data["zone_id"],
            alpha=float(data.get("alpha", 1.5)),
            beta=float(data.get("beta", 0.05)),
            cov_p=data.get("cov_p", [[0.5, 0.0], [0.0, 0.05]]),
        )
