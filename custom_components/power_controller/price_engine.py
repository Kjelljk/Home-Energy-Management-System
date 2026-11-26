"""Energy price engine (Loop 1).

Builds a 72-hour all-in price series that works for all three Norwegian household
price regimes:

* ``norgespris``      – fixed state price up to a monthly volume cap, spot above the cap
* ``spot_support``    – Nord Pool spot with *strømstøtte* (state electricity support) (90 % above a threshold, up to cap)
* ``spot``            – plain Nord Pool spot (no support scheme)

On top of the energy price comes the supplier mark-up, the grid operator's
time-of-use *energiledd* (e.g. BKK: weekday day vs. night/weekend/holiday),
*forbruksavgift* + Enova fee, and VAT.  All component inputs are **excluding VAT**.

The engine also tracks the *kapasitetsledd* (mean of the three highest hourly
energies on three distinct days) and exposes a *cost-neutral hourly energy
allowance* used by the planner and the real-time limiter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
import statistics

from .const import PLANNING_HORIZON_HOURS, TARIFF_BUFFER_KWH, PriceModel
from .grid_tariff import GridTariffSchedule, TariffVersion


# ---------------------------------------------------------------------------
# Norwegian public holidays (pure python, no external dependency)
# ---------------------------------------------------------------------------
def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


@lru_cache(maxsize=16)
def norwegian_public_holidays(year: int) -> frozenset[date]:
    """Return the Norwegian public holidays  for ``year``."""
    easter = _easter_sunday(year)
    fixed = {
        date(year, 1, 1),    # New Year's Day
        date(year, 5, 1),    # Labour Day
        date(year, 5, 17),   # Constitution Day
        date(year, 12, 25),  # Christmas Day
        date(year, 12, 26),  # Boxing Day
    }
    moving = {
        easter - timedelta(days=3),   # Maundy Thursday
        easter - timedelta(days=2),   # Good Friday
        easter,                       # Easter Sunday
        easter + timedelta(days=1),   # Easter Monday
        easter + timedelta(days=39),  # Ascension Day
        easter + timedelta(days=49),  # Whit Sunday
        easter + timedelta(days=50),  # Whit Monday
    }
    return frozenset(fixed | moving)


def is_norwegian_workday(day: date) -> bool:
    """Mon–Fri and not a Norwegian public holiday."""
    return day.weekday() < 5 and day not in norwegian_public_holidays(day.year)


def build_workday_mask(
    start_time: datetime,
    horizon: int = PLANNING_HORIZON_HOURS,
    today_override: bool | None = None,
) -> list[bool]:
    """Per-hour workday flags for the horizon.

    ``today_override`` (e.g. from ``binary_sensor.workday_sensor``) wins for the current
    calendar day; all other days use the built-in Norwegian holiday calendar.
    """
    mask: list[bool] = []
    today = start_time.date()
    for idx in range(horizon):
        d = (start_time + timedelta(hours=idx)).date()
        if d == today and today_override is not None:
            mask.append(bool(today_override))
        else:
            mask.append(is_norwegian_workday(d))
    return mask


# ---------------------------------------------------------------------------
# Price series
# ---------------------------------------------------------------------------
@dataclass
class PriceSeries72h:
    """72-hour hourly price breakdown (NOK/kWh). Component lists are excl. VAT except totals."""

    timestamps: list[datetime]
    raw_spot_72h: list[float]
    spot_after_support_72h: list[float]
    grid_price_72h: list[float]
    fee_and_vat_72h: list[float]
    total_price_72h: list[float]
    spot_is_forecast_72h: list[bool]
    price_model: str = PriceModel.NORGESPRIS.value
    grid_period_72h: list[str] = field(default_factory=list)


def next_tier_above(value_kw: float, uppers: list[float], threshold_in_next: bool = True) -> float:
    """Upper bound of the capacity step that ``value_kw`` falls into (from the tariff's steps).
    With ``threshold_in_next`` (usual in Norway) exactly 5.0 kW lands in the 5–10 kW step."""
    for upper in sorted(uppers):
        if (value_kw < upper) if threshold_in_next else (value_kw <= upper):
            return upper
    return max(uppers) * 2 if uppers else value_kw


class EnergyPriceEngine:
    """Calculates 72h electricity cost series and tracks monthly kapasitetsledd peaks."""

    def __init__(
        self,
        price_model: PriceModel | str = PriceModel.NORGESPRIS,
        norgespris_nok: float = 0.40,
        norgespris_volume_kwh: float = 5000.0,
        support_threshold_nok: float = 0.77,
        support_rate: float = 0.90,
        support_volume_kwh: float = 5000.0,
        supplier_markup_nok: float = 0.0,
        vat_percent: float = 25.0,
        baseload_24h_kw: float = 0.4,
        baseload_daytime_kw: float = 0.6,
        grid_tariff: GridTariffSchedule | None = None,
    ) -> None:
        # Energy price regime (Norgespris / spot + strømstøtte / spot). Values excl. VAT.
        self.price_model = PriceModel(price_model)
        self.norgespris_nok = norgespris_nok
        self.norgespris_volume_kwh = norgespris_volume_kwh
        self.support_threshold_nok = support_threshold_nok
        self.support_rate = support_rate
        self.support_volume_kwh = support_volume_kwh
        self.supplier_markup_nok = supplier_markup_nok
        self.vat_percent = vat_percent
        self.baseload_24h_kw = baseload_24h_kw
        self.baseload_daytime_kw = baseload_daytime_kw
        # Grid tariff (energiledd periods, fees, capacity steps) – fully user editable
        self.grid_tariff: GridTariffSchedule = grid_tariff or GridTariffSchedule.from_list(None)

        self.selected_tariff_kw: float = 5.0
        self.active_tariff_kw: float = 5.0
        self.daily_peaks: dict[str, float] = {}
        self.monthly_energy_kwh: float = 0.0
        self.monthly_cost_nok: float = 0.0
        # Hourly spot history keyed by ISO date -> 24 hourly prices (NOK/kWh ex. VAT)
        self.spot_history: dict[str, list[float]] = {}

    # ------------------------------------------------------------------
    # Kapasitetsledd
    # ------------------------------------------------------------------
    def tariff_version(self, day: date | None = None) -> TariffVersion:
        return self.grid_tariff.version_for(day or date.today())

    def capacity_uppers(self, day: date | None = None) -> list[float]:
        return self.tariff_version(day).step_uppers()

    def capacity_charge_nok(self, day: date | None = None, incl_vat: bool = True) -> float:
        """Monthly kapasitetsledd for the current top-3 average (what the month costs so far)."""
        ex = self.tariff_version(day).monthly_capacity_ex_vat(self.monthly_max_kw, self.vat_percent)
        return round(ex * (1 + self.vat_percent / 100.0) if incl_vat else ex, 2)

    def set_selected_tariff_kw(self, tariff_kw: float) -> None:
        """Set the user-selected target step; the active step never drops below what the
        month's peaks have already locked in."""
        self.selected_tariff_kw = float(tariff_kw)
        locked = self.locked_tier_kw()
        self.active_tariff_kw = max(self.selected_tariff_kw, locked)

    @property
    def monthly_max_kw(self) -> float:
        """Average of the 3 highest daily peaks (3 distinct days). With fewer than three days
        recorded, the average of the available days is shown."""
        if not self.daily_peaks:
            return 0.0
        top = sorted(self.daily_peaks.values(), reverse=True)[:3]
        return sum(top) / len(top)

    def locked_tier_kw(self) -> float:
        """The step that is already unavoidable this month (requires 3 distinct days).

        Note: the selected tariff's number is the *upper bound* of the step the user wants
        to stay in (e.g. ``5 kW`` = stay inside 2–5 kW)."""
        if len(self.daily_peaks) < 3:
            return 0.0
        mean3 = self.monthly_max_kw
        version = self.tariff_version()
        inside = mean3 < self.selected_tariff_kw if version.threshold_in_next_step else mean3 <= self.selected_tariff_kw
        if inside:
            return 0.0
        return next_tier_above(mean3, version.step_uppers(), version.threshold_in_next_step)

    def reset_monthly_counters(self) -> None:
        self.daily_peaks.clear()
        self.monthly_energy_kwh = 0.0
        self.monthly_cost_nok = 0.0
        self.active_tariff_kw = self.selected_tariff_kw

    def record_hourly_consumption(
        self,
        hour_ts: datetime,
        energy_kwh: float,
        total_price_nok_per_kwh: float = 0.0,
    ) -> bool:
        """Record a completed hour. Returns True if the active tariff stepped up."""
        energy_kwh = max(0.0, float(energy_kwh))
        self.monthly_energy_kwh += energy_kwh
        self.monthly_cost_nok += energy_kwh * max(0.0, float(total_price_nok_per_kwh))

        day_key = hour_ts.strftime("%Y-%m-%d")
        if energy_kwh > self.daily_peaks.get(day_key, 0.0):
            self.daily_peaks[day_key] = energy_kwh

        locked = self.locked_tier_kw()
        if locked > self.active_tariff_kw:
            self.active_tariff_kw = locked
            return True
        return False

    def hourly_energy_allowance_kwh(self, day: date, buffer_kwh: float = TARIFF_BUFFER_KWH) -> float:
        """Max kWh an hour on ``day`` may use without increasing the capacity charge.

        Cost-neutral rules (no speculation about future days):
        1. The active step itself (minus buffer) is always allowed.
        2. Today's peak so far is already 'paid' – later hours today may reach it.
        3. If today's peak would stay at or below the 3rd-highest peak of *other* days,
           today never enters the top-3, so that level is free as well.
        """
        base = max(0.2, self.active_tariff_kw - buffer_kwh)
        day_key = day.strftime("%Y-%m-%d")
        today_peak = self.daily_peaks.get(day_key, 0.0)
        others = sorted((v for k, v in self.daily_peaks.items() if k != day_key), reverse=True)
        third_other = others[2] if len(others) >= 3 else 0.0
        return round(max(base, today_peak, third_other), 3)

    def hourly_cap_series_72h(self, start_time: datetime, horizon: int = PLANNING_HORIZON_HOURS) -> list[float]:
        """Per-hour kWh allowance for the planner (today uses today's peak, other days the step)."""
        caps: list[float] = []
        today = start_time.date()
        base = max(0.2, self.active_tariff_kw - TARIFF_BUFFER_KWH)
        today_cap = self.hourly_energy_allowance_kwh(today)
        for idx in range(horizon):
            d = (start_time + timedelta(hours=idx)).date()
            caps.append(today_cap if d == today else base)
        return caps

    # ------------------------------------------------------------------
    # Spot prices
    # ------------------------------------------------------------------
    @staticmethod
    def average_quarter_hourly_prices(prices: list[float]) -> list[float]:
        """Average 15-minute Nord Pool prices (96/day) into hourly prices."""
        if not prices:
            return []
        if len(prices) < 4:
            return [float(statistics.mean(prices))]
        return [float(statistics.mean(prices[i : i + 4])) for i in range(0, len(prices), 4) if prices[i : i + 4]]

    def update_spot_history(self, day_start: datetime | date, hourly_prices_24h: list[float]) -> None:
        """Cache a full day of hourly prices (keeps ~15 days)."""
        if len(hourly_prices_24h) < 24:
            return
        d = day_start.date() if isinstance(day_start, datetime) else day_start
        self.spot_history[d.isoformat()] = [float(x) for x in hourly_prices_24h[:24]]
        if len(self.spot_history) > 15:
            for key in sorted(self.spot_history)[:-15]:
                self.spot_history.pop(key, None)

    def _history_price(self, ts: datetime) -> float | None:
        """Price at the same hour on the same weekday one (or two) week(s) earlier."""
        for weeks in (1, 2):
            arr = self.spot_history.get((ts.date() - timedelta(days=7 * weeks)).isoformat())
            if arr and len(arr) == 24:
                return arr[ts.hour]
        return None

    def forecast_missing_spot_prices(
        self,
        start_time: datetime,
        known_hourly_spots: list[float],
        horizon: int = PLANNING_HORIZON_HOURS,
    ) -> tuple[list[float], list[bool]]:
        """Extend known prices to the horizon.

        Rule (v1 spec): use the same weekday last week, scaled by how today's known prices
        compare with the *same hours* last week. Returns (prices, is_forecast flags).
        """
        known = [float(x) for x in known_hourly_spots[:horizon]]
        if len(known) >= horizon:
            return known, [False] * horizon

        pairs: list[tuple[float, float]] = []
        for idx, val in enumerate(known):
            hist = self._history_price(start_time + timedelta(hours=idx))
            if hist is not None:
                pairs.append((val, hist))
        scale = 1.0
        if len(pairs) >= 6:
            hist_mean = statistics.mean(h for _, h in pairs)
            curr_mean = statistics.mean(v for v, _ in pairs)
            if hist_mean > 0.01:
                scale = max(0.3, min(3.0, curr_mean / hist_mean))

        fallback = statistics.mean(known[-24:]) if known else 1.0
        result = list(known)
        flags = [False] * len(known)
        for idx in range(len(known), horizon):
            ts = start_time + timedelta(hours=idx)
            hist = self._history_price(ts)
            if hist is not None:
                result.append(round(hist * scale, 5))
            elif len(result) >= 24:
                result.append(result[idx - 24])  # repeat yesterday's profile
            else:
                result.append(fallback)
            flags.append(True)
        return result, flags

    # ------------------------------------------------------------------
    # Baseload & price series
    # ------------------------------------------------------------------
    def build_baseload_series_72h(
        self,
        start_time: datetime,
        workday_mask_72h: list[bool],
        is_away: bool = False,
        horizon: int = PLANNING_HORIZON_HOURS,
        away_mask_72h: list[bool] | None = None,
        occupied_mask_72h: list[bool] | None = None,
    ) -> list[float]:
        """Uncontrolled house baseload (kW) per hour.

        ``occupied_mask_72h`` (from the occupancy profile) decides when the daytime baseload
        applies; without it a fixed default (07–09 & 15–23 on workdays, 07–23 otherwise) is used."""
        loads: list[float] = []
        for idx in range(horizon):
            ts = start_time + timedelta(hours=idx)
            kw = self.baseload_24h_kw
            away = away_mask_72h[idx] if away_mask_72h and idx < len(away_mask_72h) else is_away
            if not away:
                workday = workday_mask_72h[idx] if idx < len(workday_mask_72h) else ts.weekday() < 5
                hr = ts.hour
                if occupied_mask_72h is not None and idx < len(occupied_mask_72h):
                    occupied = occupied_mask_72h[idx]
                else:
                    occupied = ((7 <= hr < 9) or (15 <= hr < 23)) if workday else (7 <= hr < 23)
                if occupied:
                    kw += self.baseload_daytime_kw
            loads.append(round(kw, 3))
        return loads

    def grid_price_at(self, ts: datetime, is_workday: bool) -> tuple[float, float, str]:
        """(energiledd ex. VAT, energy fee ex. VAT, period name) for the hour starting at ``ts``.
        A non-working Mon–Fri (public holiday / workday sensor off) counts as a holiday."""
        version = self.grid_tariff.version_for(ts.date())
        is_holiday = ts.weekday() < 5 and not is_workday
        price, name = version.energy_price(ts, is_holiday, self.vat_percent)
        return price, version.energy_fee_ex_vat(self.vat_percent), name

    def energy_price_after_support(self, raw_spot: float, kwh_used_this_month: float) -> float:
        """Energy price (ex. VAT, excl. mark-up) after Norgespris / strømstøtte."""
        model = self.price_model
        if model == PriceModel.NORGESPRIS and kwh_used_this_month < self.norgespris_volume_kwh:
            return self.norgespris_nok
        if model == PriceModel.SPOT_SUPPORT and kwh_used_this_month < self.support_volume_kwh:
            support = self.support_rate * max(0.0, raw_spot - self.support_threshold_nok)
            return raw_spot - support
        return raw_spot

    def build_72h_price_series(
        self,
        start_time: datetime,
        spot_prices_72h: list[float],
        workday_mask_72h: list[bool],
        monthly_kwh_used: float,
        expected_load_kwh_72h: list[float] | None = None,
    ) -> PriceSeries72h:
        """All-in price per hour: (energy + mark-up + grid + fees) × (1 + VAT)."""
        horizon = PLANNING_HORIZON_HOURS
        spots, forecast_flags = self.forecast_missing_spot_prices(start_time, spot_prices_72h, horizon)
        loads = (
            expected_load_kwh_72h
            if expected_load_kwh_72h and len(expected_load_kwh_72h) >= horizon
            else [self.baseload_24h_kw + 0.5] * horizon
        )
        vat_mult = self.vat_percent / 100.0
        running_kwh = float(monthly_kwh_used)

        timestamps, after_support, grid_prices, fees_and_vat, totals, periods = [], [], [], [], [], []
        for idx in range(horizon):
            ts = start_time + timedelta(hours=idx)
            if idx > 0 and ts.month != timestamps[-1].month:
                running_kwh = 0.0
            timestamps.append(ts)

            energy = self.energy_price_after_support(float(spots[idx]), running_kwh)
            running_kwh += max(0.0, float(loads[idx]))
            workday = workday_mask_72h[idx] if idx < len(workday_mask_72h) else is_norwegian_workday(ts.date())
            grid, fee, period_name = self.grid_price_at(ts, workday)
            periods.append(period_name)

            basis = energy + self.supplier_markup_nok + grid + fee
            fee_vat = fee + basis * vat_mult
            total = energy + self.supplier_markup_nok + grid + fee_vat

            after_support.append(round(energy, 5))
            grid_prices.append(round(grid, 5))
            fees_and_vat.append(round(fee_vat, 5))
            totals.append(round(total, 5))

        return PriceSeries72h(
            timestamps=timestamps,
            raw_spot_72h=[round(x, 5) for x in spots],
            spot_after_support_72h=after_support,
            grid_price_72h=grid_prices,
            fee_and_vat_72h=fees_and_vat,
            total_price_72h=totals,
            spot_is_forecast_72h=forecast_flags,
            price_model=self.price_model.value,
            grid_period_72h=periods,
        )
