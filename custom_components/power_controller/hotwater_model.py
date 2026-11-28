"""Self-learning hot-water tank model (pure Python, unit-tested).

Needs a tank temperature sensor and a power sensor for the element. Every ~15 minutes:

    energy in        E_in  = P · dt                         [kWh]
    stored change    ΔE    = C · (T_now − T_prev)           [kWh]   C = 1.163 Wh/(l·°C) · volume
    lost or tapped   R     = E_in − ΔE  = standby loss + hot water drawn

* **Standby loss** (UA, kW per °C above room temperature) is learned only from *quiet* steps:
  element off and only a slow temperature fall (no tapping). EMA of R / ((T − T_room) · dt).
* **Draw profile**: R minus the standby loss is the hot water used in that step. It is added to
  an hourly profile (24 values) for workdays and days off separately, smoothed day by day (EMA).
* **Heat-up rate**: °C per hour at full element power, from steps with the element on.

The planner uses the learned loss and the draw profile instead of the fixed defaults (0.8 %/h
loss, 40 % of the daily use in the morning and 60 % in the evening).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

KWH_PER_L_C = 0.001163
DEFAULT_LOSS_FRACTION = 0.008      # fraction of (T − ambient) lost per hour (old fixed model)
QUIET_MAX_DROP_C_PER_H = 0.8       # faster falls are treated as tapping
MIN_STEP_H = 0.2
PROFILE_ALPHA = 0.15               # weight of a new day in the hourly profile
LOSS_ALPHA = 0.05
HISTORY_DAYS = 14


def default_profile(daily_kwh: float) -> list[float]:
    """The fixed profile used before learning: 40 % 06–09, 60 % 17–22 (kWh per hour)."""
    out = [0.0] * 24
    for h in range(6, 9):
        out[h] = daily_kwh * 0.4 / 3
    for h in range(17, 22):
        out[h] = daily_kwh * 0.6 / 5
    return out


@dataclass
class HotWaterModel:
    device_id: str
    volume_l: float = 200.0
    ambient_c: float = 20.0
    seed_daily_kwh: float = 5.0
    ua_kw_per_c: float | None = None          # learned standby loss
    heatup_c_per_h: float | None = None
    profile_workday: list[float] = field(default_factory=list)    # kWh per hour
    profile_dayoff: list[float] = field(default_factory=list)
    days_workday: int = 0
    days_dayoff: int = 0
    samples: int = 0
    quiet_samples: int = 0
    skipped: int = 0
    frozen: bool = False
    manual: bool = False
    last_update: str | None = None
    history: list[list] = field(default_factory=list)   # [date, used_kwh, loss_w_per_c]
    # running day
    _day: str | None = None
    _day_workday: bool = True
    _day_profile: list[float] = field(default_factory=lambda: [0.0] * 24)
    _prev: tuple | None = None                          # (iso ts, temp)

    def __post_init__(self) -> None:
        if not self.profile_workday:
            self.profile_workday = default_profile(self.seed_daily_kwh)
        if not self.profile_dayoff:
            self.profile_dayoff = default_profile(self.seed_daily_kwh)

    # ------------------------------------------------------------------ derived
    @property
    def capacity_kwh_per_c(self) -> float:
        return KWH_PER_L_C * max(30.0, float(self.volume_l))

    @property
    def loss_fraction_per_h(self) -> float:
        """Fraction of (T − ambient) lost per hour – the planner's 'beta' for the tank."""
        if self.ua_kw_per_c is None:
            return DEFAULT_LOSS_FRACTION
        return max(0.001, min(0.05, self.ua_kw_per_c / self.capacity_kwh_per_c))

    @property
    def status(self) -> str:
        if self.frozen:
            return "manual"
        days = self.days_workday + self.days_dayoff
        if days < 3 or self.quiet_samples < 20:
            return "learning"
        if days < 10:
            return "settling"
        return "stable"

    def daily_use_kwh(self, workday: bool) -> float:
        return round(sum(self.profile_workday if workday else self.profile_dayoff), 3)

    def draw_kw_series(self, start: datetime, hours: int, workday_of) -> list[float]:
        """Expected draw per hour (kWh/h == kW) for the planning horizon."""
        out = []
        for t in range(hours):
            ts = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=t)
            prof = self.profile_workday if workday_of(ts.date()) else self.profile_dayoff
            out.append(float(prof[ts.hour]))
        return out

    # ------------------------------------------------------------------ learning
    def update(self, now: datetime, temp_c: float | None, power_kw: float | None, workday: bool,
               ambient_c: float | None = None) -> None:
        """Call regularly (e.g. every minute); a step is evaluated every ~15 minutes."""
        if temp_c is None or power_kw is None:
            self.skipped += 1
            return
        if ambient_c is not None:
            self.ambient_c = float(ambient_c)
        day = now.date().isoformat()
        if self._day != day:
            self._close_day()
            self._day, self._day_workday = day, bool(workday)
            self._day_profile = [0.0] * 24
        if self._prev is None:
            self._prev = (now.isoformat(), float(temp_c), 0.0, now.isoformat())
            return
        prev_ts = datetime.fromisoformat(self._prev[0])
        energy_in = float(self._prev[2]) + max(0.0, float(power_kw)) * max(0.0, (now - datetime.fromisoformat(self._prev[3])).total_seconds()) / 3600.0
        dt_h = (now - prev_ts).total_seconds() / 3600.0
        if dt_h < MIN_STEP_H:
            self._prev = (self._prev[0], self._prev[1], energy_in, now.isoformat())
            return
        t_prev = float(self._prev[1])
        self._prev = (now.isoformat(), float(temp_c), 0.0, now.isoformat())
        if self.frozen:
            return
        dT = float(temp_c) - t_prev
        stored = self.capacity_kwh_per_c * dT
        residual = energy_in - stored                       # loss + draw [kWh]
        over_amb = max(1.0, (t_prev + float(temp_c)) / 2 - self.ambient_c)
        self.samples += 1
        self.last_update = now.replace(microsecond=0).isoformat()
        if energy_in < 0.02 and -QUIET_MAX_DROP_C_PER_H * dt_h <= dT <= 0.05:
            loss_kw_per_c = max(0.0, residual) / dt_h / over_amb
            self.ua_kw_per_c = loss_kw_per_c if self.ua_kw_per_c is None else (
                (1 - LOSS_ALPHA) * self.ua_kw_per_c + LOSS_ALPHA * loss_kw_per_c)
            self.quiet_samples += 1
        if energy_in > 0.8 * float(power_kw) * dt_h and energy_in > 0.1 and dT > 0:
            rate = dT / dt_h                                 # element on for (most of) the step
            self.heatup_c_per_h = rate if self.heatup_c_per_h is None else 0.8 * self.heatup_c_per_h + 0.2 * rate
        ua = self.ua_kw_per_c if self.ua_kw_per_c is not None else DEFAULT_LOSS_FRACTION * self.capacity_kwh_per_c
        draw = max(0.0, residual - ua * over_amb * dt_h)
        mid = prev_ts + (now - prev_ts) / 2
        if mid.date().isoformat() == self._day:
            self._day_profile[mid.hour] += draw

    def _close_day(self) -> None:
        if self._day is None or self.frozen:
            return
        used = sum(self._day_profile)
        if used <= 0.05:          # no data that day (sensor missing) – keep the profile
            return
        prof = self.profile_workday if self._day_workday else self.profile_dayoff
        n = self.days_workday if self._day_workday else self.days_dayoff
        a = 1.0 / (n + 1) if n < 1 / PROFILE_ALPHA else PROFILE_ALPHA
        for h in range(24):
            prof[h] = round((1 - a) * prof[h] + a * self._day_profile[h], 4)
        if self._day_workday:
            self.days_workday += 1
        else:
            self.days_dayoff += 1
        loss_w = None if self.ua_kw_per_c is None else round(self.ua_kw_per_c * 1000, 2)
        self.history = (self.history + [[self._day, round(used, 3), loss_w]])[-HISTORY_DAYS:]

    # ------------------------------------------------------------------ manual / reset
    def set_manual(self, loss_w_per_c: float | None = None, daily_kwh: float | None = None,
                   freeze: bool = True) -> None:
        if loss_w_per_c is not None:
            self.ua_kw_per_c = max(0.0001, float(loss_w_per_c) / 1000.0)
        if daily_kwh is not None:
            for prof in (self.profile_workday, self.profile_dayoff):
                cur = sum(prof)
                if cur > 0.01:
                    f = float(daily_kwh) / cur
                    prof[:] = [round(v * f, 4) for v in prof]
                else:
                    prof[:] = default_profile(float(daily_kwh))
        self.frozen = bool(freeze)
        self.manual = True

    def reset(self) -> None:
        keep = (self.device_id, self.volume_l, self.ambient_c, self.seed_daily_kwh)
        self.__init__(*keep)

    # ------------------------------------------------------------------ view / persistence
    def summary(self) -> dict:
        loss_w = None if self.ua_kw_per_c is None else round(self.ua_kw_per_c * 1000, 2)
        return {
            "status": self.status, "frozen": self.frozen, "manual": self.manual,
            "volume_l": self.volume_l, "capacity_kwh_per_c": round(self.capacity_kwh_per_c, 3),
            "loss_w_per_c": loss_w,
            "loss_w_per_c_default": round(DEFAULT_LOSS_FRACTION * self.capacity_kwh_per_c * 1000, 2),
            "loss_kwh_per_day_at_65": round((self.ua_kw_per_c if self.ua_kw_per_c is not None
                                             else DEFAULT_LOSS_FRACTION * self.capacity_kwh_per_c) * (65 - self.ambient_c) * 24, 2),
            "cooling_c_per_h_at_65": round(self.loss_fraction_per_h * (65 - self.ambient_c), 2),
            "heatup_c_per_h": None if self.heatup_c_per_h is None else round(self.heatup_c_per_h, 1),
            "daily_use_workday_kwh": self.daily_use_kwh(True), "daily_use_dayoff_kwh": self.daily_use_kwh(False),
            "seed_daily_kwh": self.seed_daily_kwh,
            "profile_workday": [round(v, 3) for v in self.profile_workday],
            "profile_dayoff": [round(v, 3) for v in self.profile_dayoff],
            "today_so_far": [round(v, 3) for v in self._day_profile],
            "days_workday": self.days_workday, "days_dayoff": self.days_dayoff,
            "samples": self.samples, "quiet_samples": self.quiet_samples, "skipped": self.skipped,
            "last_update": self.last_update, "history": self.history,
        }

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "device_id", "volume_l", "ambient_c", "seed_daily_kwh", "ua_kw_per_c", "heatup_c_per_h",
            "profile_workday", "profile_dayoff", "days_workday", "days_dayoff", "samples", "quiet_samples",
            "skipped", "frozen", "manual", "last_update", "history", "_day", "_day_workday", "_day_profile", "_prev")}

    @classmethod
    def from_dict(cls, data: dict) -> HotWaterModel:
        m = cls(device_id=data["device_id"])
        for k, v in data.items():
            if hasattr(m, k):
                setattr(m, k, tuple(v) if k == "_prev" and v is not None else v)
        return m
