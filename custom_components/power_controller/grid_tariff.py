"""User-editable grid tariff (nettleie) model.

Nothing about a specific grid operator is hard-coded in the engine. A tariff is a list of
*versions* (one per price list, each with a ``valid_from`` date) so next year's prices can
be entered in advance and the 72h horizon switches correctly at midnight on 1 January.

Each version contains:
* ``default_price``  – energiledd for all hours not covered by a period 
* ``periods``        – ordered list of exceptions; the FIRST matching period wins.
                       A period matches on weekday(s), hour range and (optional) months.
* ``energy_fee``     – forbruksavgift + Enova-avgift per kWh
* ``capacity_steps`` – kapasitetsledd: [{"upper_kw": 2, "monthly_nok": 124}, ...]
                       the last step may use upper_kw = 0 meaning "and above"
* ``holidays_as``    – "sun": public holidays use Sunday's prices, "none": ignore holidays
* ``prices_include_vat`` – True if all numbers in this version were entered incl. VAT
* ``threshold_in_next_step`` – True if exactly 5.0 kW lands in the 5–10 kW step

All values are normalised to NOK/kWh (or NOK/month) excluding VAT before use.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import uuid

DAY_KEYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAYS: list[str] = list(DAY_KEYS[:5])
WEEKEND: list[str] = list(DAY_KEYS[5:])


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class TariffPeriod:
    """One energiledd exception, e.g. 'Day' = Mon–Fri 06–22 at 0.2877 NOK/kWh."""

    name: str
    price: float
    days: list[str] = field(default_factory=lambda: list(WEEKDAYS))
    start_hour: int = 6        # inclusive, 0–23
    end_hour: int = 22         # exclusive, 1–24; end <= start wraps past midnight
    months: list[int] = field(default_factory=list)  # empty = all months
    id: str = field(default_factory=_new_id)

    def covers_hour(self, hour: int) -> bool:
        start = int(self.start_hour) % 24
        end = 24 if int(self.end_hour) in (0, 24) else int(self.end_hour)
        if start == end:          # e.g. 06–06 = whole day
            return True
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end  # wraps past midnight, e.g. 22–06

    def matches(self, day_key: str, hour: int, month: int) -> bool:
        if self.months and month not in self.months:
            return False
        if day_key not in self.days:
            return False
        return self.covers_hour(hour)

    def describe(self) -> str:
        days = ",".join(self.days) if self.days else "-"
        months = f" months {','.join(str(m) for m in self.months)}" if self.months else ""
        return f"{self.name}: {days} {self.start_hour:02d}–{self.end_hour:02d}{months} = {self.price:.4f}"


@dataclass
class CapacityStep:
    upper_kw: float       # 0 = no upper bound (last step)
    monthly_nok: float


@dataclass
class TariffVersion:
    name: str
    valid_from: str                     # ISO date "2026-01-01"
    default_price: float
    periods: list[TariffPeriod] = field(default_factory=list)
    energy_fee: float = 0.0             # forbruksavgift + Enova per kWh
    capacity_steps: list[CapacityStep] = field(default_factory=list)
    holidays_as: str = "sun"            # "sun" | "none"
    prices_include_vat: bool = False
    threshold_in_next_step: bool = True
    id: str = field(default_factory=_new_id)

    # -------------------------------------------------------------- helpers
    @property
    def valid_from_date(self) -> date:
        return date.fromisoformat(self.valid_from)

    def _ex_vat(self, value: float, vat_percent: float) -> float:
        return value / (1.0 + vat_percent / 100.0) if self.prices_include_vat else value

    def day_key(self, ts: datetime, is_holiday: bool) -> str:
        if is_holiday and self.holidays_as == "sun":
            return "sun"
        return DAY_KEYS[ts.weekday()]

    def matching_period(self, ts: datetime, is_holiday: bool) -> TariffPeriod | None:
        key = self.day_key(ts, is_holiday)
        for period in self.periods:
            if period.matches(key, ts.hour, ts.month):
                return period
        return None

    def energy_price(self, ts: datetime, is_holiday: bool, vat_percent: float) -> tuple[float, str]:
        """(energiledd ex. VAT, period name) for the hour starting at ``ts``."""
        period = self.matching_period(ts, is_holiday)
        raw = period.price if period else self.default_price
        return self._ex_vat(raw, vat_percent), (period.name if period else "Default")

    def energy_fee_ex_vat(self, vat_percent: float) -> float:
        return self._ex_vat(self.energy_fee, vat_percent)

    def step_uppers(self) -> list[float]:
        """Sorted finite upper bounds of the capacity steps (kW)."""
        return sorted(s.upper_kw for s in self.capacity_steps if s.upper_kw > 0)

    def step_for(self, mean_top3_kw: float) -> CapacityStep | None:
        """The capacity step a monthly top-3 average falls into."""
        steps = sorted(self.capacity_steps, key=lambda s: (s.upper_kw <= 0, s.upper_kw))
        for step in steps:
            if step.upper_kw <= 0:
                return step
            inside = mean_top3_kw < step.upper_kw if self.threshold_in_next_step else mean_top3_kw <= step.upper_kw
            if inside:
                return step
        return steps[-1] if steps else None

    def monthly_capacity_ex_vat(self, mean_top3_kw: float, vat_percent: float) -> float:
        step = self.step_for(mean_top3_kw)
        return self._ex_vat(step.monthly_nok, vat_percent) if step else 0.0

    def validate(self) -> list[str]:
        errors: list[str] = []
        try:
            self.valid_from_date
        except ValueError:
            errors.append("invalid_date")
        if self.default_price < 0 or self.energy_fee < 0:
            errors.append("negative_price")
        for p in self.periods:
            if p.price < 0:
                errors.append("negative_price")
            if not p.days:
                errors.append("no_days")
            if not (0 <= int(p.start_hour) <= 23 and 0 <= int(p.end_hour) <= 24):
                errors.append("invalid_hours")
            if any(d not in DAY_KEYS for d in p.days):
                errors.append("invalid_days")
        uppers = [s.upper_kw for s in self.capacity_steps if s.upper_kw > 0]
        if len(uppers) != len(set(uppers)):
            errors.append("duplicate_steps")
        return sorted(set(errors))

    # -------------------------------------------------------------- (de)serialise
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TariffVersion:
        d = dict(data)
        d["periods"] = [TariffPeriod(**p) for p in d.get("periods", [])]
        d["capacity_steps"] = [CapacityStep(**s) for s in d.get("capacity_steps", [])]
        return cls(**d)

    def copy_as(self, name: str, valid_from: str) -> TariffVersion:
        new = deepcopy(self)
        new.id = _new_id()
        new.name = name
        new.valid_from = valid_from
        for p in new.periods:
            p.id = _new_id()
        return new


class GridTariffSchedule:
    """All tariff versions; picks the one valid for any given hour."""

    def __init__(self, versions: list[TariffVersion] | None = None) -> None:
        self.versions: list[TariffVersion] = sorted(versions or [], key=lambda v: v.valid_from)

    def sort(self) -> None:
        self.versions.sort(key=lambda v: v.valid_from)

    def version_for(self, day: date) -> TariffVersion:
        if not self.versions:
            raise ValueError("No grid tariff configured")
        chosen = self.versions[0]
        for v in self.versions:
            if v.valid_from_date <= day:
                chosen = v
        return chosen

    def get(self, version_id: str) -> TariffVersion | None:
        return next((v for v in self.versions if v.id == version_id), None)

    def remove(self, version_id: str) -> None:
        if len(self.versions) > 1:
            self.versions = [v for v in self.versions if v.id != version_id]

    def to_list(self) -> list[dict]:
        return [v.to_dict() for v in self.versions]

    @classmethod
    def from_list(cls, data: list[dict] | None) -> GridTariffSchedule:
        if not data:
            return cls([preset("bkk_2026")])
        return cls([TariffVersion.from_dict(v) for v in data])


# ---------------------------------------------------------------------------
# Presets – only starting points; every value is editable in the UI.
# ---------------------------------------------------------------------------
PRESETS: dict[str, str] = {
    "bkk_2026": "BKK 2026 (example – verify against the price list)",
    "flat": "Flat price all day",
    "day_night": "Day/night (weekdays 06–22)",
}


def preset(key: str, valid_from: str = "2026-01-01") -> TariffVersion:
    """Return a new tariff version from a preset (values excl. VAT)."""
    if key == "bkk_2026":
        # BKK price list valid from 2026-01-01, private households, excl. VAT and fees.
        return TariffVersion(
            name="BKK 2026",
            valid_from=valid_from,
            default_price=0.1050,
            periods=[TariffPeriod(name="Day", price=0.2877, days=list(WEEKDAYS), start_hour=6, end_hour=22)],
            energy_fee=0.0713 + 0.0100,
            capacity_steps=[
                CapacityStep(2, 124), CapacityStep(5, 200), CapacityStep(10, 332),
                CapacityStep(15, 480), CapacityStep(20, 616), CapacityStep(25, 752),
                CapacityStep(50, 1440), CapacityStep(75, 2120), CapacityStep(100, 2800),
                CapacityStep(0, 5520),
            ],
            holidays_as="sun",
            prices_include_vat=False,
            threshold_in_next_step=True,
        )
    if key == "day_night":
        return TariffVersion(
            name="Day/night", valid_from=valid_from, default_price=0.15,
            periods=[TariffPeriod(name="Day", price=0.35)],
            energy_fee=0.0813,
            capacity_steps=[CapacityStep(2, 150), CapacityStep(5, 250), CapacityStep(10, 400), CapacityStep(0, 600)],
        )
    return TariffVersion(
        name="Flat", valid_from=valid_from, default_price=0.25, periods=[], energy_fee=0.0813,
        capacity_steps=[CapacityStep(2, 150), CapacityStep(5, 250), CapacityStep(10, 400), CapacityStep(0, 600)],
    )


# ---------------------------------------------------------------------------
# Capacity steps as editable text ("2 = 124" per line)
# ---------------------------------------------------------------------------
def steps_to_text(steps: list[CapacityStep]) -> str:
    lines = []
    for s in sorted(steps, key=lambda s: (s.upper_kw <= 0, s.upper_kw)):
        upper = "over" if s.upper_kw <= 0 else f"{s.upper_kw:g}"
        lines.append(f"{upper} = {s.monthly_nok:g}")
    return "\n".join(lines)


def steps_from_text(text: str) -> list[CapacityStep]:
    """Parse lines like '5 = 200', '5: 200', '5 200' or 'over = 5520'. Raises ValueError."""
    steps: list[CapacityStep] = []
    for raw in str(text).replace(";", "\n").splitlines():
        line = raw.strip().lower().replace("kw", "").replace("kr", "").replace(",", ".")
        if not line or line.startswith("#"):
            continue
        for sep in ("=", ":"):
            line = line.replace(sep, " ")
        parts = line.split()
        if len(parts) != 2:
            raise ValueError(raw)
        upper = 0.0 if parts[0] in ("over", "max", "inf", "0") else float(parts[0])
        steps.append(CapacityStep(upper, float(parts[1])))
    if not steps:
        raise ValueError("empty")
    return steps


# ---------------------------------------------------------------------------
# Editing one period from the panel's Settings page (2.13.1)
# ---------------------------------------------------------------------------
PERIOD_EDIT_ERRORS: dict[str, str] = {
    "unknown_version": "The price list version no longer exists – reload the page.",
    "unknown_period": "The period no longer exists – reload the page.",
    "no_days": "Choose at least one day.",
    "invalid_hours": "From hour must be 0–23 and to hour 0–24.",
    "invalid_months": "Months must be 1–12.",
    "invalid_price": "The price must be between 0 and 10 NOK/kWh.",
    "empty_name": "The period needs a name.",
}


def edit_period(schedule: GridTariffSchedule, version_id: str, period_id: str, data: dict) -> list[str]:
    """Validate and apply an edit of one period: name, price, days, start/end hour and months.
    Keys that are missing in ``data`` are left unchanged. Returns error codes (nothing is
    changed when there is an error)."""
    version = schedule.get(version_id)
    if version is None:
        return ["unknown_version"]
    period = next((p for p in version.periods if p.id == period_id), None)
    if period is None:
        return ["unknown_period"]
    errors: list[str] = []
    name = str(data.get("name", period.name)).strip()
    if not name:
        errors.append("empty_name")
    try:
        price = float(data.get("price", period.price))
        if not 0.0 <= price <= 10.0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("invalid_price")
        price = period.price
    days = [d for d in DAY_KEYS if d in (data.get("days", period.days) or [])]
    if not days or any(d not in DAY_KEYS for d in (data.get("days", period.days) or [])):
        errors.append("no_days")
    try:
        start, end = int(data.get("start_hour", period.start_hour)), int(data.get("end_hour", period.end_hour))
        if not (0 <= start <= 23 and 0 <= end <= 24):
            raise ValueError
    except (TypeError, ValueError):
        errors.append("invalid_hours")
        start, end = period.start_hour, period.end_hour
    try:
        months = sorted({int(m) for m in (data.get("months", period.months) or [])})
        if any(not 1 <= m <= 12 for m in months):
            raise ValueError
    except (TypeError, ValueError):
        errors.append("invalid_months")
        months = period.months
    if errors:
        return sorted(set(errors))
    period.name, period.price, period.days = name[:40], round(price, 5), days
    period.start_hour, period.end_hour = start, end
    period.months = [] if len(months) == 12 else months   # all twelve = all year
    return []
