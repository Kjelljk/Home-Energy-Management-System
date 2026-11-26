"""Shadow evaluation: "would I pay less without Norgespris?"

Runs next to the real controller and NEVER actuates anything. Every hour three
scenarios are priced with the same spot price and the same grid tariff:

* ``actual``     – measured consumption, priced with the real price model (e.g. Norgespris)
* ``passive``    – the same measured consumption, priced with the shadow model
                   (spot + electricity support, or plain spot)
* ``optimised``  – measured consumption where the controllable loads are replaced by what a
                   second planner, optimising for the shadow prices, would have used.
                   Priced with the shadow model and with its own capacity-charge peaks.

To keep the comparison honest the shadow planner carries its own state forward instead of
being re-seeded from the real house every hour:

* zone temperature offset   dT[t+1] = (1-beta)·dT[t] + alpha·hc·(shadow_kWh - actual_kWh)
* tank temperature offset   same idea with the tank's heat capacity
* EV energy balance         kWh the shadow has charged more (+) or less (-) than reality;
                            settled at the shadow price when the car is unplugged
* hot-water / switch daily energy and runtime counters

Model errors largely cancel because only the *difference* to reality is modelled.
"""
from __future__ import annotations

from copy import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .const import DeviceType, PriceModel
from .price_engine import EnergyPriceEngine

SHADOW_OFF = "off"
ZONE_OFFSET_LIMIT_C = 5.0
TANK_OFFSET_LIMIT_C = 20.0
TANK_STANDBY_DECAY = 0.992
HISTORY_MONTHS = 24


def hour_cost(
    engine: EnergyPriceEngine,
    ts: datetime,
    kwh: float,
    spot_nok: float,
    is_workday: bool,
    month_kwh_before: float,
) -> tuple[float, float]:
    """(energy cost, grid energy cost) in NOK incl. VAT for one hour.

    Energy = energy price after Norgespris/support + supplier mark-up.
    Grid   = time-of-use energy charge + consumption tax/Enova fee.
    The capacity charge is monthly and handled separately.
    """
    kwh = max(0.0, float(kwh))
    vat = 1.0 + engine.vat_percent / 100.0
    energy = engine.energy_price_after_support(float(spot_nok), float(month_kwh_before))
    grid, fee, _ = engine.grid_price_at(ts, is_workday)
    return (
        kwh * (energy + engine.supplier_markup_nok) * vat,
        kwh * (grid + fee) * vat,
    )


@dataclass
class Ledger:
    """Monthly running totals for one scenario."""

    energy_nok: float = 0.0
    grid_nok: float = 0.0
    kwh: float = 0.0
    ev_settlement_nok: float = 0.0
    daily_peaks: dict[str, float] = field(default_factory=dict)

    def add(self, ts: datetime, kwh: float, energy_nok: float, grid_nok: float) -> None:
        self.energy_nok += energy_nok
        self.grid_nok += grid_nok
        self.kwh += max(0.0, kwh)
        key = ts.strftime("%Y-%m-%d")
        self.daily_peaks[key] = max(self.daily_peaks.get(key, 0.0), max(0.0, kwh))

    @property
    def top3_mean_kw(self) -> float:
        top = sorted(self.daily_peaks.values(), reverse=True)[:3]
        return sum(top) / len(top) if top else 0.0

    def capacity_nok(self, engine: EnergyPriceEngine, day) -> float:
        if not self.daily_peaks:
            return 0.0
        version = engine.grid_tariff.version_for(day)
        ex = version.monthly_capacity_ex_vat(self.top3_mean_kw, engine.vat_percent)
        return ex * (1.0 + engine.vat_percent / 100.0)

    def summary(self, engine: EnergyPriceEngine, day, extra_fee_nok: float = 0.0) -> dict[str, float]:
        capacity = self.capacity_nok(engine, day)
        total = self.energy_nok + self.grid_nok + capacity + self.ev_settlement_nok + extra_fee_nok
        return {
            "energy_nok": round(self.energy_nok, 2),
            "grid_energy_nok": round(self.grid_nok, 2),
            "capacity_nok": round(capacity, 2),
            "ev_settlement_nok": round(self.ev_settlement_nok, 2),
            "extra_fee_nok": round(extra_fee_nok, 2),
            "total_nok": round(total, 2),
            "kwh": round(self.kwh, 2),
            "top3_mean_kw": round(self.top3_mean_kw, 3),
        }


class ShadowEvaluator:
    """Keeps the shadow state, prices the three scenarios and keeps monthly history."""

    def __init__(self, shadow_model: PriceModel | str = PriceModel.SPOT_SUPPORT, extra_monthly_fee_nok: float = 0.0) -> None:
        self.shadow_model: str = str(PriceModel(shadow_model).value if shadow_model != SHADOW_OFF else SHADOW_OFF)
        self.extra_monthly_fee_nok = float(extra_monthly_fee_nok)
        self.month: str = ""
        self.actual = Ledger()
        self.passive = Ledger()
        self.optimised = Ledger()
        self.hours = 0
        self.hours_estimated_spot = 0
        self.ev_shortfall_kwh = 0.0
        # carried shadow state
        self.zone_offset_c: dict[str, float] = {}
        self.tank_offset_c: dict[str, float] = {}
        self.ev_balance_kwh: dict[str, float] = {}
        self.ev_was_connected: dict[str, bool] = {}
        self.day: str = ""
        self.day_energy_kwh: dict[str, float] = {}
        self.day_runtime_h: dict[str, float] = {}
        # shadow capacity engine state (own peaks for the planner's hourly caps)
        self.shadow_daily_peaks: dict[str, float] = {}
        # for valuing open balances: last shadow unit price and kWh-per-°C per zone/tank
        self.last_unit_price: float = 0.0
        self.kwh_per_c: dict[str, float] = {}
        self.history: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ helpers
    @property
    def enabled(self) -> bool:
        return self.shadow_model != SHADOW_OFF

    def shadow_engine(self, real: EnergyPriceEngine) -> EnergyPriceEngine:
        """Shallow copy of the real engine with the shadow price model and the shadow's own
        monthly kWh/peaks (grid tariff, spot history, VAT etc. are shared)."""
        eng = copy(real)
        eng.price_model = PriceModel(self.shadow_model if self.enabled else PriceModel.SPOT_SUPPORT)
        eng.daily_peaks = dict(self.shadow_daily_peaks)
        eng.monthly_energy_kwh = self.optimised.kwh
        eng.active_tariff_kw = max(real.selected_tariff_kw, eng.locked_tier_kw())
        return eng

    def shadow_specs(self, specs: list[dict]) -> list[dict]:
        """Real device specs with the shadow's carried-forward state applied."""
        out: list[dict] = []
        for spec in specs:
            s = dict(spec)
            dtype = DeviceType(s["device_type"])
            eid = s["entry_id"]
            if dtype == DeviceType.CLIMATE_HEATING:
                zid = str(s.get("zone_id") or eid)
                s["current_temp"] = float(s.get("current_temp", 20.0)) + self.zone_offset_c.get(zid, 0.0)
            elif dtype == DeviceType.CLIMATE_HOTWATER:
                if s.get("current_temp") is not None:
                    s["current_temp"] = float(s["current_temp"]) + self.tank_offset_c.get(eid, 0.0)
                s["energy_done_today_kwh"] = self.day_energy_kwh.get(eid, 0.0)
            elif dtype == DeviceType.EV_CHARGER:
                s["energy_needed_kwh"] = max(0.0, float(s.get("energy_needed_kwh", 0.0)) - self.ev_balance_kwh.get(eid, 0.0))
            elif dtype == DeviceType.SWITCH:
                s["runtime_done_today_h"] = self.day_runtime_h.get(eid, 0.0)
            out.append(s)
        return out

    # ------------------------------------------------------------------ hourly close
    def close_hour(
        self,
        hour_ts: datetime,
        real_engine: EnergyPriceEngine,
        spot_nok: float,
        spot_is_forecast: bool,
        is_workday: bool,
        measured_kwh: float,
        actual_device_kwh: dict[str, float],
        shadow_device_kwh: dict[str, float | None],
        specs: list[dict],
    ) -> None:
        """Book one finished hour. ``shadow_device_kwh`` is the shadow plan for that hour
        (None for devices the shadow did not plan, e.g. uncommitted → actual is used)."""
        if not self.enabled:
            return
        month = hour_ts.strftime("%Y-%m")
        if self.month and month != self.month:
            self._archive_month(real_engine)
        self.month = month
        day = hour_ts.strftime("%Y-%m-%d")
        if day != self.day:
            self.day = day
            self.day_energy_kwh.clear()
            self.day_runtime_h.clear()

        measured_kwh = max(0.0, float(measured_kwh))
        shadow_eng = self.shadow_engine(real_engine)

        # -- actual and passive: identical consumption, different price model
        e, g = hour_cost(real_engine, hour_ts, measured_kwh, spot_nok, is_workday, self.actual.kwh)
        self.actual.add(hour_ts, measured_kwh, e, g)
        e, g = hour_cost(shadow_eng, hour_ts, measured_kwh, spot_nok, is_workday, self.passive.kwh)
        self.passive.add(hour_ts, measured_kwh, e, g)

        # -- optimised: swap controllable loads for the shadow plan
        delta = 0.0
        by_id = {s["entry_id"]: s for s in specs}
        swapped: dict[str, float] = {}
        for eid, planned in shadow_device_kwh.items():
            if planned is None or eid not in by_id or not by_id[eid].get("committed", False):
                continue
            act = max(0.0, float(actual_device_kwh.get(eid, 0.0)))
            shd = max(0.0, float(planned))
            swapped[eid] = shd - act
            delta += shd - act
        opt_kwh = max(0.0, measured_kwh + delta)
        e, g = hour_cost(shadow_eng, hour_ts, opt_kwh, spot_nok, is_workday, self.optimised.kwh)
        self.optimised.add(hour_ts, opt_kwh, e, g)
        self.shadow_daily_peaks[day] = max(self.shadow_daily_peaks.get(day, 0.0), opt_kwh)
        hour_total_price = self._unit_price(shadow_eng, hour_ts, spot_nok, is_workday, self.optimised.kwh)
        self.last_unit_price = hour_total_price

        # -- carry shadow state forward
        zone_heat: dict[str, float] = {}
        for eid, diff in swapped.items():
            s = by_id[eid]
            dtype = DeviceType(s["device_type"])
            shadow_kwh = diff + max(0.0, float(actual_device_kwh.get(eid, 0.0)))
            if dtype == DeviceType.CLIMATE_HEATING:
                zid = str(s.get("zone_id") or eid)
                self.kwh_per_c[f"zone:{zid}"] = 1.0 / max(1e-3, float(s.get("alpha", 1.5)))
                zone_heat[zid] = zone_heat.get(zid, 0.0) + float(s.get("alpha", 1.5)) * float(
                    s.get("heating_coefficient", 1.0)) * diff
                zone_heat.setdefault(f"__beta__{zid}", float(s.get("beta", 0.05)))
            elif dtype == DeviceType.CLIMATE_HOTWATER:
                self.day_energy_kwh[eid] = self.day_energy_kwh.get(eid, 0.0) + shadow_kwh
                if s.get("current_temp") is not None:
                    vol = max(30.0, float(s.get("tank_volume_l", 200.0)))
                    self.kwh_per_c[f"tank:{eid}"] = 0.001163 * vol
                    off = TANK_STANDBY_DECAY * self.tank_offset_c.get(eid, 0.0) + diff / (0.001163 * vol)
                    self.tank_offset_c[eid] = max(-TANK_OFFSET_LIMIT_C, min(TANK_OFFSET_LIMIT_C, off))
            elif dtype == DeviceType.SWITCH:
                if shadow_kwh > 0.05:
                    self.day_runtime_h[eid] = self.day_runtime_h.get(eid, 0.0) + 1.0
            elif dtype == DeviceType.EV_CHARGER:
                self.ev_balance_kwh[eid] = self.ev_balance_kwh.get(eid, 0.0) + diff
        for zid in [k for k in zone_heat if not k.startswith("__beta__")]:
            beta = zone_heat[f"__beta__{zid}"]
            off = (1.0 - beta) * self.zone_offset_c.get(zid, 0.0) + zone_heat[zid]
            self.zone_offset_c[zid] = max(-ZONE_OFFSET_LIMIT_C, min(ZONE_OFFSET_LIMIT_C, off))
        for zid in list(self.zone_offset_c):
            if zid not in zone_heat:  # no swap this hour: offset still decays
                beta = next((float(s.get("beta", 0.05)) for s in specs
                             if str(s.get("zone_id") or s["entry_id"]) == zid), 0.05)
                self.zone_offset_c[zid] *= (1.0 - beta)

        # -- EV settlement when unplugged: balance valued at this hour's shadow price
        for s in specs:
            if DeviceType(s["device_type"]) != DeviceType.EV_CHARGER:
                continue
            eid = s["entry_id"]
            connected = bool(s.get("connected", False))
            if self.ev_was_connected.get(eid) and not connected:
                bal = self.ev_balance_kwh.pop(eid, 0.0)
                self.optimised.ev_settlement_nok += -bal * hour_total_price
                if bal < 0:
                    self.ev_shortfall_kwh += -bal
            self.ev_was_connected[eid] = connected

        self.hours += 1
        if spot_is_forecast:
            self.hours_estimated_spot += 1

    @staticmethod
    def _unit_price(engine: EnergyPriceEngine, ts: datetime, spot: float, workday: bool, month_kwh: float) -> float:
        e, g = hour_cost(engine, ts, 1.0, spot, workday, month_kwh)
        return e + g

    def pending_balance(self) -> tuple[float, float]:
        """(kWh, NOK) the shadow still 'owes' (+) or has 'pre-paid' (-) versus reality:
        open EV sessions plus zone/tank temperature differences, valued at the last
        shadow price. Included in the optimised total so a comparison taken mid-session
        (e.g. car still plugged in, charging planned for later) is not biased."""
        kwh = -sum(self.ev_balance_kwh.values())
        for zid, off in self.zone_offset_c.items():
            kwh += -off * self.kwh_per_c.get(f"zone:{zid}", 0.7)
        for eid, off in self.tank_offset_c.items():
            kwh += -off * self.kwh_per_c.get(f"tank:{eid}", 0.23)
        return kwh, kwh * self.last_unit_price

    # ------------------------------------------------------------------ reporting
    def month_summary(self, real_engine: EnergyPriceEngine, day=None) -> dict[str, Any]:
        day = day or datetime.strptime((self.month or "2000-01") + "-01", "%Y-%m-%d").date()
        actual = self.actual.summary(real_engine, day)
        passive = self.passive.summary(real_engine, day, self.extra_monthly_fee_nok)
        optimised = self.optimised.summary(real_engine, day, self.extra_monthly_fee_nok)
        pend_kwh, pend_nok = self.pending_balance()
        optimised["pending_kwh"] = round(pend_kwh, 2)
        optimised["pending_nok"] = round(pend_nok, 2)
        optimised["total_nok"] = round(optimised["total_nok"] + pend_nok, 2)
        return {
            "month": self.month,
            "actual_model": real_engine.price_model.value,
            "shadow_model": self.shadow_model,
            "actual": actual,
            "passive_spot": passive,
            "optimised_spot": optimised,
            "benefit_nok": round(optimised["total_nok"] - actual["total_nok"], 2),
            "benefit_passive_nok": round(passive["total_nok"] - actual["total_nok"], 2),
            "optimiser_gain_nok": round(passive["total_nok"] - optimised["total_nok"], 2),
            "hours": self.hours,
            "hours_estimated_spot": self.hours_estimated_spot,
            "ev_shortfall_kwh": round(self.ev_shortfall_kwh, 2),
            "zone_offset_c": {k: round(v, 2) for k, v in self.zone_offset_c.items()},
            "ev_balance_kwh": {k: round(v, 2) for k, v in self.ev_balance_kwh.items()},
        }

    def rolling_12m(self, real_engine: EnergyPriceEngine) -> dict[str, Any]:
        months = sorted(self.history)[-11:]
        rows = [self.history[m] for m in months]
        if self.month:
            rows.append(self.month_summary(real_engine))
        return {
            "months": len(rows),
            "actual_nok": round(sum(r["actual"]["total_nok"] for r in rows), 2),
            "optimised_spot_nok": round(sum(r["optimised_spot"]["total_nok"] for r in rows), 2),
            "passive_spot_nok": round(sum(r["passive_spot"]["total_nok"] for r in rows), 2),
            "benefit_nok": round(sum(r["benefit_nok"] for r in rows), 2),
        }

    def _archive_month(self, real_engine: EnergyPriceEngine) -> None:
        summary = self.month_summary(real_engine)
        # open balances continue into the next month and are settled there -> archive without them
        opt = summary["optimised_spot"]
        opt["total_nok"] = round(opt["total_nok"] - opt["pending_nok"], 2)
        opt["pending_carried_kwh"], opt["pending_kwh"], opt["pending_nok"] = opt["pending_kwh"], 0.0, 0.0
        summary["benefit_nok"] = round(opt["total_nok"] - summary["actual"]["total_nok"], 2)
        self.history[self.month] = summary
        for key in sorted(self.history)[:-HISTORY_MONTHS]:
            self.history.pop(key, None)
        self.actual, self.passive, self.optimised = Ledger(), Ledger(), Ledger()
        self.hours = self.hours_estimated_spot = 0
        self.ev_shortfall_kwh = 0.0
        self.shadow_daily_peaks.clear()

    def reset(self) -> None:
        """Start the comparison again (keeps settings and archived history)."""
        keep = (self.shadow_model, self.extra_monthly_fee_nok, self.history)
        self.__init__(keep[0], keep[1])
        self.history = keep[2]

    # ------------------------------------------------------------------ persistence
    def to_dict(self) -> dict[str, Any]:
        return {
            "shadow_model": self.shadow_model,
            "extra_monthly_fee_nok": self.extra_monthly_fee_nok,
            "month": self.month,
            "actual": asdict(self.actual),
            "passive": asdict(self.passive),
            "optimised": asdict(self.optimised),
            "hours": self.hours,
            "hours_estimated_spot": self.hours_estimated_spot,
            "ev_shortfall_kwh": self.ev_shortfall_kwh,
            "zone_offset_c": self.zone_offset_c,
            "tank_offset_c": self.tank_offset_c,
            "ev_balance_kwh": self.ev_balance_kwh,
            "ev_was_connected": self.ev_was_connected,
            "day": self.day,
            "day_energy_kwh": self.day_energy_kwh,
            "day_runtime_h": self.day_runtime_h,
            "shadow_daily_peaks": self.shadow_daily_peaks,
            "last_unit_price": self.last_unit_price,
            "kwh_per_c": self.kwh_per_c,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ShadowEvaluator:
        ev = cls()
        if not data:
            return ev
        ev.shadow_model = data.get("shadow_model", ev.shadow_model)
        ev.extra_monthly_fee_nok = float(data.get("extra_monthly_fee_nok", 0.0))
        ev.month = data.get("month", "")
        for name in ("actual", "passive", "optimised"):
            setattr(ev, name, Ledger(**data.get(name, {})))
        for key in ("hours", "hours_estimated_spot"):
            setattr(ev, key, int(data.get(key, 0)))
        ev.ev_shortfall_kwh = float(data.get("ev_shortfall_kwh", 0.0))
        for key in ("zone_offset_c", "tank_offset_c", "ev_balance_kwh", "ev_was_connected",
                    "day_energy_kwh", "day_runtime_h", "shadow_daily_peaks", "kwh_per_c", "history"):
            setattr(ev, key, dict(data.get(key, {})))
        ev.day = data.get("day", "")
        ev.last_unit_price = float(data.get("last_unit_price", 0.0))
        return ev
