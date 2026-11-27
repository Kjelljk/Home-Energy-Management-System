"""Loop 2 – 72h global planner (linear program, HiGHS via scipy.optimize.linprog).

Resolution: the planner works in *slots*. The slot length follows the price series it is
given: 288 prices = 15-minute slots (Nord Pool quarter prices), 72 prices = whole hours.
Hourly inputs (baseload, outdoor temperature, comfort targets, schedule masks, hour
allowance) are expanded to slots. The capacity charge is still enforced per clock hour
(sum of the four quarters ≤ hour allowance), because that is how the grid operator bills.

Variables are average power per slot [kW]; energy in a slot = power × slot length.
Temperatures are explicit state variables (sparse equality constraints), which keeps the
model small at 288 slots. Every "must" requirement has a penalised slack variable, so the
LP is always feasible.

Outputs per device (all per slot):
* ``plans[eid]``            – kW per slot (kWh per hour when the resolution is hourly)
* ``setpoints[eid]``        – climate setpoint trajectory (°C)
* ``predicted_temps[eid]``  – modelled zone / tank temperature at the end of each slot
* ``legionella_slots[eid]`` – slots reserved for the sanitation cycle
``legionella_hours`` keeps the hour indices for the dashboards.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math
import time

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from .const import (
    DeviceType,
    GridMode,
    HOTWATER_AMBIENT_C,
    HOTWATER_LEGIONELLA_C,
    HOTWATER_LEGIONELLA_HOURS,
    MAX_PREHEAT_C,
    NOMINAL_VOLTAGE_IT,
    PLANNER_TIME_LIMIT_S,
    PLANNING_HORIZON_HOURS,
    PhasePair,
    PriorityProfile,
    TARIFF_BUFFER_KWH,
    phase_pair_conductors,
    power_kw_to_it_phase_amps,
)

AMPS_PER_KW = 1000.0 / NOMINAL_VOLTAGE_IT
EV_MIN_SOC_WEIGHT_RANK = 0          # minimum state of charge is as important as Priority now
DEFAULT_EV_DELAY_PENALTY = 0.02     # NOK per kWh per hour of delay when the EV has no deadline


def priority_weight(rank: int) -> float:
    """NOK penalty per unit of unmet requirement (°C·h or kWh). Rank 0 = Priority Now."""
    return 5.0 * (2.0 ** max(0, 10 - int(rank)))


def slots_per_hour_for(n_prices: int) -> int:
    """288 prices -> 4 (15 min), 144 -> 2, otherwise hourly."""
    for spp in (4, 2):
        if n_prices >= spp * PLANNING_HORIZON_HOURS:
            return spp
    return 1


def aggregate_hourly(values: list | None, spp: int, how: str = "mean") -> list | None:
    """Slot series -> hourly series. ``mean`` of kW = kWh per hour; ``max``/``last`` for °C."""
    if values is None:
        return None
    if spp <= 1:
        return list(values)
    out = []
    for h in range(len(values) // spp):
        chunk = [v for v in values[h * spp:(h + 1) * spp] if v is not None]
        if not chunk:
            out.append(None)
        elif how == "max":
            out.append(round(max(chunk), 3))
        elif how == "last":
            out.append(chunk[-1])
        else:
            out.append(round(sum(chunk) / spp, 3))
    return out


@dataclass
class PlanResult:
    plans: dict[str, list[float | None]] = field(default_factory=dict)
    setpoints: dict[str, list[float]] = field(default_factory=dict)
    predicted_temps: dict[str, list[float]] = field(default_factory=dict)
    legionella_hours: dict[str, list[int]] = field(default_factory=dict)
    legionella_slots: dict[str, list[int]] = field(default_factory=dict)
    solver: str = ""
    solve_ms: float = 0.0
    unmet: dict[str, float] = field(default_factory=dict)
    # devices that are NOT committed: their expected consumption is forecast (flat profile,
    # never shifted) and counted against the hourly/phase capacity of the committed devices
    forecast_only: list[str] = field(default_factory=list)
    uncontrolled_72h: list[float] = field(default_factory=list)   # per slot (kW)
    slots_per_hour: int = 1
    ev_min_unmet: dict[str, float] = field(default_factory=dict)


class _Vars:
    """Tiny variable-index allocator."""

    def __init__(self) -> None:
        self.n = 0
        self.cost: list[float] = []
        self.lb: list[float] = []
        self.ub: list[float] = []

    def add(self, count: int, cost: float | list[float] = 0.0, ub: float | list[float] = np.inf,
            lb: float = 0.0) -> int:
        start = self.n
        self.n += count
        self.cost.extend(cost if isinstance(cost, list) else [cost] * count)
        self.ub.extend(ub if isinstance(ub, list) else [ub] * count)
        self.lb.extend([lb] * count)
        return start


class _Rows:
    """Sparse A x (<= or ==) b collector."""

    def __init__(self) -> None:
        self.r: list[int] = []
        self.c: list[int] = []
        self.v: list[float] = []
        self.b: list[float] = []

    def add(self, coeffs: dict[int, float], rhs: float) -> None:
        row = len(self.b)
        for col, val in coeffs.items():
            if val != 0.0:
                self.r.append(row)
                self.c.append(col)
                self.v.append(val)
        self.b.append(rhs)

    def matrix(self, n: int):
        return coo_matrix((self.v, (self.r, self.c)), shape=(len(self.b), n)).tocsr()


def thermal_coeffs(alpha: float, beta: float, dt: float) -> tuple[float, float, float]:
    """(decay, heat gain per kW, outdoor coefficient) for one slot of ``dt`` hours.

    Hourly model: T+ = (1-beta)·T + alpha·kWh + beta·Tout. The continuous equivalent is
    integrated exactly over the slot, so dt = 1 reproduces the hourly model.
    """
    beta = min(0.95, max(0.0, float(beta)))
    decay = (1.0 - beta) ** dt
    gain = alpha * (1.0 - decay) / beta if beta > 1e-6 else alpha * dt
    return decay, gain, 1.0 - decay


class EnergyPlanner:
    """Global 72-hour cost optimiser."""

    def __init__(self, time_limit_s: float = PLANNER_TIME_LIMIT_S) -> None:
        self.time_limit_s = time_limit_s
        self.last_result = PlanResult()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def optimize_72h(
        self,
        now: datetime,
        mode: GridMode | str,
        prices_72h: list[float],
        outdoor_temps_72h: list[float],
        baseload_72h: list[float],
        tariff_limit_kw: float,
        phase_limit_a: float,
        devices: list[dict],
        arrival_time: datetime | None = None,
        departure_time: datetime | None = None,
        hourly_cap_kwh_72h: list[float] | None = None,
        priority_profile: PriorityProfile | str = PriorityProfile.NORMAL,
        ev_delay_penalty: float = DEFAULT_EV_DELAY_PENALTY,
    ) -> dict[str, list[float | None]]:
        """Return {entry_id: plan per slot [kW]}. ``prices_72h`` may be hourly (72) or 15-minute
        (288). All other series are hourly. Details are kept in ``self.last_result``."""
        spp = slots_per_hour_for(len(prices_72h))
        dt = 1.0 / spp
        H = min(PLANNING_HORIZON_HOURS * spp, len(prices_72h))
        NH = max(1, H // spp)
        result = PlanResult(slots_per_hour=spp)

        t_out = self._expand(self._pad([float(x) for x in outdoor_temps_72h], NH, 5.0), spp)[:H]
        base_h = self._pad([float(x) for x in baseload_72h], NH, 0.5)
        away = self._away_mask(now, H, GridMode(mode), arrival_time, departure_time, dt)

        committed: list[dict] = []
        uncontrolled = [0.0] * H
        for dev in devices:
            if dev.get("committed", False):
                committed.append(dev)
            else:
                prof = self.uncontrolled_profile(dev, now, H, t_out, away, dt)
                result.plans[dev["entry_id"]] = prof
                result.forecast_only.append(dev["entry_id"])
                uncontrolled = [u + p for u, p in zip(uncontrolled, prof)]
        result.uncontrolled_72h = [round(u, 3) for u in uncontrolled]
        # baseload per slot (kW) incl. the uncommitted devices; hourly energy for the caps
        base_slot = [base_h[t // spp] + uncontrolled[t] for t in range(H)]

        if not committed:
            self.last_result = result
            return result.plans

        if hourly_cap_kwh_72h is None:
            hourly_cap_kwh_72h = [max(0.5, float(tariff_limit_kw) - TARIFF_BUFFER_KWH)] * NH

        ordered = self._apply_intra_tier_rotation(now, committed)
        ctx = dict(
            now=now,
            mode=GridMode(mode),
            prices=[float(p) for p in prices_72h[:H]],
            t_out=t_out,
            base=base_slot,
            caps=self._pad([float(x) for x in hourly_cap_kwh_72h], NH, 4.85),
            phase_limit_a=float(phase_limit_a),
            devices=ordered,
            away=away,
            spp=spp,
            profile=PriorityProfile(priority_profile),
            ev_delay_penalty=max(0.0, float(ev_delay_penalty)),
        )
        t0 = time.perf_counter()
        try:
            self._solve_lp(result, **ctx)
            result.solver = "lp"
        except Exception:  # noqa: BLE001 – never leave the house without a plan
            keep = {k: v for k, v in result.plans.items() if k in result.forecast_only}
            result = PlanResult(plans=keep, forecast_only=list(result.forecast_only),
                                uncontrolled_72h=list(result.uncontrolled_72h), slots_per_hour=spp)
            self._solve_greedy_fallback(result, **ctx)
            result.solver = "fallback"
        result.solve_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        for eid, slots in result.legionella_slots.items():
            result.legionella_hours[eid] = sorted({s // spp for s in slots})
        self.last_result = result
        return result.plans

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _pad(values: list[float], horizon: int, default: float) -> list[float]:
        if not values:
            return [default] * horizon
        return (values + [values[-1]] * horizon)[:horizon]

    @staticmethod
    def _expand(values: list, spp: int) -> list:
        return [v for v in values for _ in range(spp)] if spp > 1 else list(values)

    def _hourly_series(self, values: list | None, H: int, spp: int, default: float) -> list[float]:
        """Hourly device series (targets, masks) -> per slot, padded to the horizon."""
        if not values:
            return [default] * H
        return self._expand(self._pad(list(values), (H + spp - 1) // spp, default), spp)[:H]

    def uncontrolled_profile(self, dev: dict, now: datetime, H: int, t_out: list[float], away: list[bool],
                             dt: float = 1.0) -> list[float]:
        """Expected consumption (kW per slot) of a device Power Controller does NOT control.

        Never optimised or shifted – a flat, physically plausible estimate so the plan and the
        capacity limits reflect what the house will really use:
        * heating:   steady-state power to hold the device's own setpoint (outdoor forecast)
        * hot water: the daily energy quota spread evenly over the day
        * EV:        nominal power until the needed energy is delivered – ONLY when the car is
                     connected AND actually charging (plugging in alone does not start a session)
        * switch:    the daily runtime spread evenly
        """
        dtype = DeviceType(dev["device_type"])
        p_nom = max(0.0, float(dev.get("nominal_power_kw", 1.0)))
        spp = max(1, int(round(1.0 / dt)))
        out = [0.0] * H
        if dtype == DeviceType.CLIMATE_HEATING:
            alpha = max(1e-3, float(dev.get("alpha", 1.5)) * float(dev.get("heating_coefficient", 1.0)))
            beta = float(dev.get("beta", 0.05))
            hold = dev.get("hold_setpoint")
            tgt = self._hourly_series(dev.get("target_temps_72h"), H, spp, 20.0)
            for t in range(H):
                sp = float(dev.get("oot_temp", 10.0)) if away[t] else (float(hold) if hold is not None else tgt[t])
                out[t] = min(p_nom, max(0.0, beta * (sp - t_out[t]) / alpha))
        elif dtype == DeviceType.CLIMATE_HOTWATER:
            per_h = float(dev.get("daily_quota_hours", 3.0)) * p_nom / 24.0
            out = [per_h * (0.2 if away[t] else 1.0) for t in range(H)]
        elif dtype == DeviceType.EV_CHARGER:
            if dev.get("connected") and dev.get("charging"):
                need = max(0.0, float(dev.get("energy_needed_kwh", 0.0)))
                rate = max(0.0, float(dev.get("live_power_kw") or p_nom))
                for t in range(H):
                    if need <= 1e-3:
                        break
                    out[t] = min(rate, need / dt)
                    need -= out[t] * dt
        elif dtype == DeviceType.SWITCH:
            per_h = float(dev.get("runtime_hours", 0.0)) * p_nom / 24.0
            out = [0.0 if away[t] else per_h for t in range(H)]
        return [round(v, 3) for v in out]

    @staticmethod
    def _apply_intra_tier_rotation(now: datetime, devices: list[dict]) -> list[dict]:
        """Sort by plan priority; rotate equal priorities daily (rullering)."""
        day_num = now.timetuple().tm_yday
        grouped: dict[int, list[dict]] = {}
        for dev in devices:
            grouped.setdefault(EnergyPlanner._rank(dev), []).append(dev)
        ordered: list[dict] = []
        for pri in sorted(grouped):
            tier = sorted(grouped[pri], key=lambda d: d["entry_id"])
            if len(tier) > 1:
                shift = day_num % len(tier)
                tier = tier[shift:] + tier[:shift]
            for pos, dev in enumerate(tier):
                dev = dict(dev)
                dev["_tie_factor"] = 1.0 + 0.02 * (len(tier) - 1 - pos)
                ordered.append(dev)
        return ordered

    @staticmethod
    def _rank(dev: dict) -> int:
        return 0 if dev.get("priority_now") else max(1, int(dev.get("plan_priority", 5)))

    @staticmethod
    def _profile_factor(profile: PriorityProfile, dtype: DeviceType) -> float:
        if profile == PriorityProfile.COMFORT and dtype in (DeviceType.CLIMATE_HEATING, DeviceType.CLIMATE_HOTWATER):
            return 10.0
        if profile == PriorityProfile.EV_BOOST and dtype == DeviceType.EV_CHARGER:
            return 10.0
        if profile == PriorityProfile.ECONOMY:
            return 0.3
        return 1.0

    def _weight(self, dev: dict, profile: PriorityProfile) -> float:
        dtype = DeviceType(dev["device_type"])
        return priority_weight(self._rank(dev)) * self._profile_factor(profile, dtype) * dev.get("_tie_factor", 1.0)

    @staticmethod
    def effective_mode_at_hour(
        base_mode: GridMode,
        hour_ts: datetime,
        arrival_time: datetime | None,
        departure_time: datetime | None,
    ) -> GridMode:
        """Away/Arriving lasts until arrival; in Home, departure is used for *planning* only."""
        if base_mode in (GridMode.AWAY, GridMode.ARRIVING):
            if arrival_time is not None and hour_ts >= arrival_time:
                return GridMode.HOME
            return GridMode.AWAY
        if departure_time is not None and hour_ts >= departure_time:
            return GridMode.AWAY
        return GridMode.HOME

    def _away_mask(self, now, H, mode, arrival_time, departure_time, dt: float = 1.0) -> list[bool]:
        return [
            self.effective_mode_at_hour(mode, now + timedelta(hours=t * dt), arrival_time, departure_time) == GridMode.AWAY
            for t in range(H)
        ]

    @staticmethod
    def _deadline_index(now: datetime, finish_by: datetime | None, H: int, dt: float = 1.0) -> int:
        if finish_by is None:
            return H
        return max(1, min(H, int(math.ceil((finish_by - now).total_seconds() / 3600.0 / dt - 1e-9))))

    @staticmethod
    def _day_buckets(now: datetime, H: int, dt: float = 1.0) -> list[list[int]]:
        buckets: dict = {}
        for t in range(H):
            buckets.setdefault((now + timedelta(hours=t * dt)).date(), []).append(t)
        return list(buckets.values())

    @staticmethod
    def pick_legionella_window(prices: list[float], allowed: list[bool], hours: int = HOTWATER_LEGIONELLA_HOURS,
                               within: int = 24, spp: int = 1) -> list[int]:
        """Cheapest block of consecutive slots (``hours`` long) inside the next ``within`` hours."""
        n = hours * spp
        best: tuple[float, int] | None = None
        last_start = min(within * spp, len(prices)) - n
        for s in range(0, max(0, last_start) + 1):
            idx = list(range(s, s + n))
            if not all(allowed[i] for i in idx):
                continue
            cost = sum(prices[i] for i in idx)
            if best is None or cost < best[0] - 1e-9:
                best = (cost, s)
        return list(range(best[1], best[1] + n)) if best else []

    @staticmethod
    def needs_legionella(dev: dict, now: datetime, away_now: bool) -> bool:
        if DeviceType(dev["device_type"]) != DeviceType.CLIMATE_HOTWATER or away_now:
            return False
        last = dev.get("last_legionella_ts")
        return last is None or (now - last) >= timedelta(days=7)

    @staticmethod
    def _simulate(temp0: float, decay: float, gain: float, cout: float, heat_kw: list[float], t_out: list[float]) -> list[float]:
        temps, temp = [], float(temp0)
        for t in range(len(t_out)):
            temp = decay * temp + gain * float(heat_kw[t]) + cout * float(t_out[t])
            temps.append(temp)
        return temps

    # ------------------------------------------------------------------
    # LP model
    # ------------------------------------------------------------------
    def _solve_lp(self, result: PlanResult, now, mode, prices, t_out, base, caps, phase_limit_a, devices,
                  away, spp, profile, ev_delay_penalty) -> None:
        H = len(prices)
        dt = 1.0 / spp
        NH = (H + spp - 1) // spp
        V = _Vars()
        R = _Rows()   # A_ub x <= b
        E = _Rows()   # A_eq x == b

        # --- power variables p[d, t] (kW, average over the slot)
        p_idx: dict[str, int] = {}
        for dev in devices:
            p_nom = max(0.05, float(dev.get("nominal_power_kw", 1.0)))
            costs = [prices[t] * dt + 1e-5 * t * dt for t in range(H)]
            if DeviceType(dev["device_type"]) == DeviceType.EV_CHARGER and dev.get("finish_by") is None:
                # no deadline: charge as cheaply as possible, but sooner is better
                costs = [c + ev_delay_penalty * (t * dt) * dt for t, c in enumerate(costs)]
            p_idx[dev["entry_id"]] = V.add(H, cost=costs, ub=p_nom)

        def ub_zero(eid: str, t: int) -> None:
            V.ub[p_idx[eid] + t] = 0.0

        # --- capacity charge: energy per CLOCK HOUR; phase currents per slot
        for h in range(NH):
            slots = range(h * spp, min(H, (h + 1) * spp))
            row = {p_idx[d["entry_id"]] + t: dt for d in devices for t in slots}
            base_kwh = sum(base[t] * dt for t in slots)
            R.add(row, max(0.0, caps[h] - base_kwh))
        base_amps = [b * AMPS_PER_KW * 2.0 / 3.0 for b in base]  # baseload spread over 3 pairs
        for t in range(H):
            for ph in ("L1", "L2", "L3"):
                coeffs = {p_idx[d["entry_id"]] + t: AMPS_PER_KW for d in devices
                          if ph in phase_pair_conductors(d.get("phase_pair", PhasePair.L1_L2))}
                if coeffs:
                    R.add(coeffs, max(0.0, phase_limit_a - base_amps[t]))

        # --- thermal zones (heaters sharing a room share ONE temperature state)
        zones: dict[str, list[dict]] = {}
        for dev in devices:
            if DeviceType(dev["device_type"]) == DeviceType.CLIMATE_HEATING:
                zones.setdefault(str(dev.get("zone_id") or dev["entry_id"]), []).append(dev)

        zone_meta: dict[str, dict] = {}
        for zid, zdevs in zones.items():
            ref = zdevs[0]
            alpha = float(ref.get("alpha", 1.5))
            beta = float(ref.get("beta", 0.05))
            decay, gain, cout = thermal_coeffs(alpha, beta, dt)
            temp0 = float(ref.get("current_temp", 20.0))
            tgts = [self._hourly_series(d.get("target_temps_72h"), H, spp, 20.0) for d in zdevs]
            req = []
            for t in range(H):
                if away[t]:
                    req.append(max(float(d.get("oot_temp", 10.0)) for d in zdevs))
                else:
                    req.append(max(tg[t] for tg in tgts))
            # requirement at the end of slot t covers the target of slot t and t+1 (pre-heating)
            req_end = [max(req[t], req[t + 1] if t + 1 < H else req[t]) for t in range(H)]
            w = max(self._weight(d, profile) for d in zdevs)
            T = V.add(H, lb=-np.inf)
            s_lo = V.add(H, cost=w * dt)
            s_hi = V.add(H, cost=20.0 * dt)
            passive = self._simulate(temp0, decay, 0.0, cout, [0.0] * H, t_out)
            for t in range(H):
                eq = {T + t: 1.0}
                if t > 0:
                    eq[T + t - 1] = -decay
                for d in zdevs:
                    eq[p_idx[d["entry_id"]] + t] = -gain * float(d.get("heating_coefficient", 1.0))
                E.add(eq, cout * t_out[t] + (decay * temp0 if t == 0 else 0.0))
                R.add({T + t: -1.0, s_lo + t: -1.0}, -req_end[t])
                R.add({T + t: 1.0, s_hi + t: -1.0}, max(req[t], passive[t]) + MAX_PREHEAT_C)
            zone_meta[zid] = dict(decay=decay, gain=gain, cout=cout, temp0=temp0, req=req, s_lo=s_lo)

        # --- device specific constraints
        ev_info: dict[str, dict] = {}
        hw_info: dict[str, dict] = {}
        for dev in devices:
            eid = dev["entry_id"]
            dtype = DeviceType(dev["device_type"])
            p_nom = max(0.05, float(dev.get("nominal_power_kw", 1.0)))
            w = self._weight(dev, profile)
            base_i = p_idx[eid]

            if dtype == DeviceType.EV_CHARGER:
                need = max(0.0, float(dev.get("energy_needed_kwh", 0.0)))
                if not dev.get("connected", False) or need <= 0.05:
                    for t in range(H):
                        ub_zero(eid, t)
                    continue
                dl = self._deadline_index(now, dev.get("finish_by"), H, dt)
                for t in range(dl, H):
                    ub_zero(eid, t)
                s = V.add(1, cost=w)
                R.add({**{base_i + t: -dt for t in range(dl)}, s: -1.0}, -need)
                info = dict(deadline=dl, need=need, slack=s)
                # minimum state of charge before a fixed deadline (e.g. within 24 h of plugging in)
                min_need = min(need, max(0.0, float(dev.get("min_energy_kwh", 0.0))))
                if min_need > 0.05 and dev.get("min_deadline") is not None:
                    dl_min = min(dl, self._deadline_index(now, dev["min_deadline"], H, dt))
                    s_min = V.add(1, cost=priority_weight(EV_MIN_SOC_WEIGHT_RANK))
                    R.add({**{base_i + t: -dt for t in range(dl_min)}, s_min: -1.0}, -min_need)
                    info.update(min_need=min_need, min_deadline=dl_min, min_slack=s_min)
                ev_info[eid] = info

            elif dtype == DeviceType.SWITCH:
                mask = self._hourly_series(dev.get("schedule_mask_72h"), H, spp, True)
                eligible = {t for t in range(H) if mask[t] and not away[t]}
                for t in range(H):
                    if t not in eligible:
                        ub_zero(eid, t)
                req_h = max(0.0, float(dev.get("runtime_hours", 0.0)))
                done_today = max(0.0, float(dev.get("runtime_done_today_h", 0.0)))
                for i, bucket in enumerate(self._day_buckets(now, H, dt)):
                    elig = [t for t in bucket if t in eligible]
                    need_h = min(len(elig) * dt, (req_h - done_today) if i == 0 else req_h)
                    if need_h <= 0 or not elig:
                        continue
                    s = V.add(1, cost=w)
                    R.add({**{base_i + t: -dt for t in elig}, s: -1.0}, -need_h * p_nom)

            elif dtype == DeviceType.CLIMATE_HOTWATER:
                hw_info[eid] = self._add_hotwater(dev, now, H, spp, prices, away, V, R, E, base_i, p_nom, w)

        # --- solve
        res = linprog(
            c=np.array(V.cost),
            A_ub=R.matrix(V.n),
            b_ub=np.array(R.b),
            A_eq=E.matrix(V.n) if E.b else None,
            b_eq=np.array(E.b) if E.b else None,
            bounds=list(zip(V.lb, V.ub)),
            method="highs",
            options={"time_limit": self.time_limit_s, "presolve": True},
        )
        if res.x is None or res.status not in (0, 1):
            raise RuntimeError(f"LP failed: {res.message}")
        x = res.x

        # --- extract and post-process
        raw = {
            d["entry_id"]: [max(0.0, min(float(d.get("nominal_power_kw", 1.0)), float(x[p_idx[d["entry_id"]] + t])))
                            for t in range(H)]
            for d in devices
        }
        for dev in devices:  # relays first (whole slots), then EVs fill around them
            if DeviceType(dev["device_type"]) == DeviceType.SWITCH:
                raw[dev["entry_id"]] = self._postprocess_switch(raw[dev["entry_id"]], float(dev.get("nominal_power_kw", 1.0)),
                                                                prices, now, dt)
        for dev in devices:
            eid = dev["entry_id"]
            if DeviceType(dev["device_type"]) == DeviceType.EV_CHARGER and eid in ev_info:
                others_h = [0.0] * NH
                for o, plan in raw.items():
                    if o != eid:
                        for t in range(H):
                            others_h[t // spp] += plan[t] * dt
                room_h = [max(0.0, caps[h] - sum(base[t] * dt for t in range(h * spp, min(H, (h + 1) * spp))) - others_h[h])
                          for h in range(NH)]
                info = ev_info[eid]
                raw[eid] = self._postprocess_ev(raw[eid], float(dev.get("min_power_kw", 1.38)),
                                                float(dev.get("nominal_power_kw", 1.0)), prices,
                                                info["deadline"], room_h, spp)
                delivered = sum(raw[eid]) * dt
                result.unmet[eid] = round(max(0.0, info["need"] - delivered), 3)
                if "min_need" in info:
                    early = sum(raw[eid][:info["min_deadline"]]) * dt
                    result.ev_min_unmet[eid] = round(max(0.0, info["min_need"] - early), 3)
        for eid, plan in raw.items():
            result.plans[eid] = [round(v, 3) for v in plan]

        for zid, meta in zone_meta.items():
            zdevs = zones[zid]
            heat = [sum(result.plans[d["entry_id"]][t] * float(d.get("heating_coefficient", 1.0)) for d in zdevs)
                    for t in range(H)]
            temps = self._simulate(meta["temp0"], meta["decay"], meta["gain"], meta["cout"], heat, t_out)
            for d in zdevs:
                eid = d["entry_id"]
                result.predicted_temps[eid] = [round(v, 2) for v in temps]
                result.setpoints[eid] = self._heating_setpoints(result.plans[eid], meta["req"], temps)
            result.unmet[f"zone:{zid}"] = round(float(sum(x[meta["s_lo"] + t] for t in range(H))) * dt, 3)

        for eid, info in hw_info.items():
            dev = next(d for d in devices if d["entry_id"] == eid)
            result.legionella_slots[eid] = info["legionella"]
            result.setpoints[eid] = self._hotwater_setpoints(dev, result.plans[eid], info, H)
            if info.get("model"):
                m = info["model"]
                net = [result.plans[eid][t] - m["draw_kw"][t] for t in range(H)]
                temps = self._simulate(m["temp0"], m["decay"], m["gain"], m["cout"], net, [HOTWATER_AMBIENT_C] * H)
                result.predicted_temps[eid] = [round(v, 2) for v in temps]

    @staticmethod
    def _series(values: list[float] | None, t: int, default: float) -> float:
        if not values:
            return default
        return float(values[t] if t < len(values) else values[-1])

    def _add_hotwater(self, dev, now, H, spp, prices, away, V: _Vars, R: _Rows, E: _Rows, base_i, p_nom, w) -> dict:
        dt = 1.0 / spp
        info: dict = {"legionella": [], "model": None}
        volume = max(30.0, float(dev.get("tank_volume_l", 200.0)))
        quota_h = max(0.0, float(dev.get("daily_quota_hours", 3.0)))
        daily_use = float(dev.get("daily_use_kwh", quota_h * p_nom * 0.85))

        if self.needs_legionella(dev, now, away[0]):
            win = self.pick_legionella_window(prices, [not a for a in away], spp=spp)
            info["legionella"] = win
            for t in win:  # p[t] >= p_nom - s  (rank 0 weight)
                s = V.add(1, cost=priority_weight(0) * dt)
                R.add({base_i + t: -1.0, s: -1.0}, -p_nom)

        temp0 = dev.get("current_temp")
        if temp0 is None:
            # No tank sensor: daily energy quota placed in the cheapest slots of each day.
            done_today = max(0.0, float(dev.get("energy_done_today_kwh", 0.0)))
            for i, bucket in enumerate(self._day_buckets(now, H, dt)):
                frac_away = sum(1 for t in bucket if away[t]) / max(1, len(bucket))
                need = quota_h * p_nom * (1.0 - 0.8 * frac_away)
                if i == 0:
                    need = max(0.0, need - done_today)
                need = min(need, len(bucket) * p_nom * dt)
                if need <= 0:
                    continue
                s = V.add(1, cost=w)
                R.add({**{base_i + t: -dt for t in bucket}, s: -1.0}, -need)
            return info

        # Tank model: 1 kWh raises the tank by 1/(1.163e-3*V) °C, standby loss to the room.
        alpha = 1.0 / (0.001163 * volume)
        decay, gain, cout = thermal_coeffs(alpha, 0.008, dt)
        draw_kw = []
        for t in range(H):
            hr = (now + timedelta(hours=t * dt)).hour
            share = 0.4 / 3 if 6 <= hr < 9 else (0.6 / 5 if 17 <= hr < 22 else 0.0)
            draw_kw.append(0.0 if away[t] else daily_use * share)     # kWh per hour == kW
        mins = self._hourly_series(dev.get("min_temps_72h"), H, spp, 50.0)
        T = V.add(H, lb=-np.inf)
        s_lo = V.add(H, cost=w * dt)
        top = HOTWATER_LEGIONELLA_C + 5.0
        for t in range(H):
            eq = {T + t: 1.0, base_i + t: -gain}
            if t > 0:
                eq[T + t - 1] = -decay
            E.add(eq, -gain * draw_kw[t] + cout * HOTWATER_AMBIENT_C + (decay * float(temp0) if t == 0 else 0.0))
            req = float(dev.get("oot_temp", 15.0)) if away[t] else mins[t]
            R.add({T + t: -1.0, s_lo + t: -1.0}, -req)
            R.add({T + t: 1.0}, max(top, float(temp0)))
        info["model"] = dict(decay=decay, gain=gain, cout=cout, temp0=float(temp0), draw_kw=draw_kw)
        return info

    # ------------------------------------------------------------------
    # Post processing
    # ------------------------------------------------------------------
    @staticmethod
    def _postprocess_ev(plan, min_kw, p_nom, prices, deadline, room_h=None, spp: int = 1) -> list[float]:
        """Remove sub-minimum slots (OCPP cannot charge below ~6 A) without breaking the
        hourly capacity room: top up slots that already charge, else open the cheapest slots
        at minimum power. ``room_h`` is the energy [kWh] left per clock hour for the EV."""
        plan = list(plan)
        H = len(plan)
        dt = 1.0 / spp
        NH = (H + spp - 1) // spp
        room = list(room_h) if room_h is not None else [p_nom] * NH
        small = [t for t in range(H) if 1e-3 < plan[t] < min_kw]
        if not small:
            return plan
        energy = sum(plan[t] for t in small) * dt
        for t in small:
            plan[t] = 0.0
        used = [0.0] * NH
        for t in range(H):
            used[t // spp] += plan[t] * dt

        def free_kw(t: int) -> float:
            return max(0.0, (room[t // spp] - used[t // spp]) / dt)

        for t in sorted((t for t in range(min(deadline, H)) if plan[t] >= min_kw), key=lambda t: prices[t]):
            add = max(0.0, min(energy / dt, p_nom - plan[t], free_kw(t)))
            plan[t] += add
            used[t // spp] += add * dt
            energy -= add * dt
            if energy <= 1e-6:
                return plan
        for t in sorted((t for t in range(min(deadline, H)) if plan[t] == 0.0), key=lambda t: prices[t]):
            if energy <= 1e-6:
                break
            if free_kw(t) < min_kw:
                continue
            plan[t] = min(p_nom, free_kw(t), max(min_kw, energy / dt))
            used[t // spp] += plan[t] * dt
            energy -= plan[t] * dt
        return plan

    @staticmethod
    def _postprocess_switch(plan, p_nom, prices, now, dt: float = 1.0) -> list[float]:
        """Relays run whole slots: per day keep ceil(energy / slot energy) slots, highest LP share first."""
        H = len(plan)
        out = [0.0] * H
        for bucket in EnergyPlanner._day_buckets(now, H, dt):
            energy = sum(plan[t] for t in bucket) * dt
            n_slots = int(math.ceil(energy / (p_nom * dt) - 0.05)) if energy > 0.05 * p_nom * dt else 0
            for t in sorted(bucket, key=lambda t: (-round(plan[t], 3), prices[t]))[:n_slots]:
                out[t] = p_nom
        return out

    @staticmethod
    def _heating_setpoints(plan, req, temps) -> list[float]:
        """Planned heat -> setpoint = predicted temperature (pre-heat), else slot target."""
        out = []
        for t, p in enumerate(plan):
            target = req[t]
            if p and p > 0.05:
                sp = min(target + MAX_PREHEAT_C, max(target, temps[t]))
            else:
                sp = target
            out.append(round(sp * 2.0) / 2.0)
        # Smooth: a pre-heat block (setpoint above the target) becomes ONE step at the block's
        # highest value instead of a staircase of 0.5 °C changes – fewer commands to the device.
        t = 0
        while t < len(out):
            if out[t] > round(req[t] * 2.0) / 2.0 + 0.01:
                j = t
                while j < len(out) and out[j] > round(req[j] * 2.0) / 2.0 + 0.01:
                    j += 1
                top = max(out[t:j])
                for k in range(t, j):
                    out[k] = top
                t = j
            else:
                t += 1
        return out

    @staticmethod
    def _hotwater_setpoints(dev, plan, info, H) -> list[float]:
        nominal = float(dev.get("nominal_temp", 65.0))
        minimum = float(dev.get("min_temp", 50.0))
        leg = set(info["legionella"])
        out = []
        for t in range(H):
            if t in leg:
                out.append(HOTWATER_LEGIONELLA_C + 2.0)
            elif plan[t] > 0.05:
                out.append(nominal)
            else:
                out.append(minimum)
        return out

    # ------------------------------------------------------------------
    # Fallback (no solver / solver error)
    # ------------------------------------------------------------------
    def _solve_greedy_fallback(self, result: PlanResult, now, mode, prices, t_out, base, caps, phase_limit_a,
                               devices, away, spp, profile, ev_delay_penalty) -> None:
        """Deterministic cheapest-slots heuristic covering every device type."""
        H = len(prices)
        dt = 1.0 / spp
        NH = (H + spp - 1) // spp
        rem_h = [max(0.0, caps[h] - sum(base[t] * dt for t in range(h * spp, min(H, (h + 1) * spp)))) for h in range(NH)]
        rem_a = {ph: [phase_limit_a] * H for ph in ("L1", "L2", "L3")}
        order = sorted(range(H), key=lambda t: prices[t])

        def place(dev, plan, slots, energy, min_kw=0.0):
            p_nom = float(dev.get("nominal_power_kw", 1.0))
            p1, p2 = phase_pair_conductors(dev.get("phase_pair", PhasePair.L1_L2))
            for t in slots:
                if energy <= 1e-3:
                    break
                phase_kw = min(rem_a[p1][t], rem_a[p2][t]) / AMPS_PER_KW
                alloc = min(p_nom - plan[t], rem_h[t // spp] / dt, phase_kw, energy / dt)
                if alloc < max(min_kw, 0.01):
                    continue
                plan[t] += alloc
                rem_h[t // spp] -= alloc * dt
                rem_a[p1][t] -= alloc * AMPS_PER_KW
                rem_a[p2][t] -= alloc * AMPS_PER_KW
                energy -= alloc * dt
            return energy

        for dev in devices:
            eid = dev["entry_id"]
            dtype = DeviceType(dev["device_type"])
            p_nom = float(dev.get("nominal_power_kw", 1.0))
            plan = [0.0] * H
            if dtype == DeviceType.EV_CHARGER and dev.get("connected"):
                dl = self._deadline_index(now, dev.get("finish_by"), H, dt)
                left = place(dev, plan, [t for t in order if t < dl], float(dev.get("energy_needed_kwh", 0.0)),
                             float(dev.get("min_power_kw", 1.38)))
                result.unmet[eid] = round(left, 3)
            elif dtype == DeviceType.SWITCH:
                mask = self._hourly_series(dev.get("schedule_mask_72h"), H, spp, True)
                for bucket in self._day_buckets(now, H, dt):
                    elig = [t for t in order if t in bucket and mask[t] and not away[t]]
                    place(dev, plan, elig, float(dev.get("runtime_hours", 0.0)) * p_nom)
                plan = self._postprocess_switch(plan, p_nom, prices, now, dt)
            elif dtype == DeviceType.CLIMATE_HOTWATER:
                quota = float(dev.get("daily_quota_hours", 3.0)) * p_nom
                for bucket in self._day_buckets(now, H, dt):
                    place(dev, plan, [t for t in order if t in bucket], quota)
                result.legionella_slots[eid] = []
                result.setpoints[eid] = self._hotwater_setpoints(dev, plan, {"legionella": []}, H)
            elif dtype == DeviceType.CLIMATE_HEATING:
                alpha = float(dev.get("alpha", 1.5)) * float(dev.get("heating_coefficient", 1.0))
                beta = float(dev.get("beta", 0.05))
                tgt = self._hourly_series(dev.get("target_temps_72h"), H, spp, 20.0)
                req = []
                for t in range(H):
                    req.append(float(dev.get("oot_temp", 10.0)) if away[t] else tgt[t])
                    need = max(0.0, beta * (req[t] - t_out[t]) / max(alpha, 1e-3))
                    plan[t] = min(p_nom, need, rem_h[t // spp] / dt)
                    rem_h[t // spp] -= plan[t] * dt
                result.setpoints[eid] = [round(r * 2) / 2 for r in req]
            result.plans[eid] = [round(v, 3) for v in plan]
