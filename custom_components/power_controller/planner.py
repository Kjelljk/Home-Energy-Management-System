"""Loop 2 – 72h global planner (linear program, HiGHS via scipy.optimize.linprog).

Why an LP and not a MILP: per-hour on/off integers made the v2.1 model explode
(minutes for 3 heaters). Power per hour is continuous here (= energy in kWh for that
hour). Discrete behaviour (EV minimum current, relay whole hours, anti-cycling) is
handled by post-processing and by the real-time loops. The LP always has a feasible
solution because every "must" requirement has a penalised slack variable.

Outputs per committed device:
* ``plan[eid]``            – kW (= kWh) per hour for 72 h
* ``setpoints[eid]``       – climate setpoint trajectory (°C) per hour (heating / hot water)
* ``predicted_temps[eid]`` – modelled zone / tank temperature at end of each hour
* ``legionella_hours[eid]``– hour indices reserved for the sanitation cycle
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


def priority_weight(rank: int) -> float:
    """NOK penalty per unit of unmet requirement (°C·h or kWh). Rank 0 = Priority Now."""
    return 5.0 * (2.0 ** max(0, 10 - int(rank)))


@dataclass
class PlanResult:
    plans: dict[str, list[float | None]] = field(default_factory=dict)
    setpoints: dict[str, list[float]] = field(default_factory=dict)
    predicted_temps: dict[str, list[float]] = field(default_factory=dict)
    legionella_hours: dict[str, list[int]] = field(default_factory=dict)
    solver: str = ""
    solve_ms: float = 0.0
    unmet: dict[str, float] = field(default_factory=dict)
    # devices that are NOT committed: their expected consumption is forecast (flat profile,
    # never shifted) and counted against the hourly/phase capacity of the committed devices
    forecast_only: list[str] = field(default_factory=list)
    uncontrolled_72h: list[float] = field(default_factory=list)


class _Vars:
    """Tiny variable-index allocator."""

    def __init__(self) -> None:
        self.n = 0
        self.cost: list[float] = []
        self.ub: list[float] = []

    def add(self, count: int, cost: float | list[float] = 0.0, ub: float | list[float] = np.inf) -> int:
        start = self.n
        self.n += count
        self.cost.extend(cost if isinstance(cost, list) else [cost] * count)
        self.ub.extend(ub if isinstance(ub, list) else [ub] * count)
        return start


class _Rows:
    """Sparse A_ub x <= b_ub collector."""

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
    ) -> dict[str, list[float | None]]:
        """Return {entry_id: 72h kW plan}. Details are kept in ``self.last_result``."""
        horizon = min(PLANNING_HORIZON_HOURS, len(prices_72h))
        result = PlanResult()
        t_out_p = self._pad([float(x) for x in outdoor_temps_72h], horizon, 5.0)
        away_m = self._away_mask(now, horizon, GridMode(mode), arrival_time, departure_time)
        committed: list[dict] = []
        uncontrolled = [0.0] * horizon
        for dev in devices:
            if dev.get("committed", False):
                committed.append(dev)
            else:
                prof = self.uncontrolled_profile(dev, now, horizon, t_out_p, away_m)
                result.plans[dev["entry_id"]] = prof
                result.forecast_only.append(dev["entry_id"])
                uncontrolled = [u + p for u, p in zip(uncontrolled, prof)]
        result.uncontrolled_72h = [round(u, 3) for u in uncontrolled]
        # uncommitted devices behave like extra baseload for the optimisation
        baseload_72h = [float(b) + u for b, u in zip(self._pad([float(x) for x in baseload_72h], horizon, 0.5), uncontrolled)]

        if not committed:
            self.last_result = result
            return result.plans

        if hourly_cap_kwh_72h is None:
            hourly_cap_kwh_72h = [max(0.5, float(tariff_limit_kw) - TARIFF_BUFFER_KWH)] * horizon

        ordered = self._apply_intra_tier_rotation(now, committed)
        ctx = dict(
            now=now,
            mode=GridMode(mode),
            prices=[float(p) for p in prices_72h[:horizon]],
            t_out=self._pad([float(x) for x in outdoor_temps_72h], horizon, 5.0),
            baseload=self._pad([float(x) for x in baseload_72h], horizon, 0.5),
            caps=self._pad([float(x) for x in hourly_cap_kwh_72h], horizon, 4.85),
            phase_limit_a=float(phase_limit_a),
            devices=ordered,
            arrival_time=arrival_time,
            departure_time=departure_time,
            profile=PriorityProfile(priority_profile),
        )
        t0 = time.perf_counter()
        try:
            self._solve_lp(result, **ctx)
            result.solver = "lp"
        except Exception:  # noqa: BLE001 – never leave the house without a plan
            result = PlanResult(plans={k: v for k, v in result.plans.items() if k in result.forecast_only},
                                forecast_only=list(result.forecast_only), uncontrolled_72h=list(result.uncontrolled_72h))
            self._solve_greedy_fallback(result, **ctx)
            result.solver = "fallback"
        result.solve_ms = round((time.perf_counter() - t0) * 1000.0, 1)
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

    def uncontrolled_profile(self, dev: dict, now: datetime, H: int, t_out: list[float], away: list[bool]) -> list[float]:
        """Expected consumption (kWh per hour) of a device Power Controller does NOT control.

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
        out = [0.0] * H
        if dtype == DeviceType.CLIMATE_HEATING:
            alpha = max(1e-3, float(dev.get("alpha", 1.5)) * float(dev.get("heating_coefficient", 1.0)))
            beta = float(dev.get("beta", 0.05))
            hold = dev.get("hold_setpoint")
            for t in range(H):
                sp = float(dev.get("oot_temp", 10.0)) if away[t] else (
                    float(hold) if hold is not None else self._series(dev.get("target_temps_72h"), t, 20.0))
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
                    out[t] = min(rate, need)
                    need -= out[t]
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

    def _away_mask(self, now, H, mode, arrival_time, departure_time) -> list[bool]:
        return [
            self.effective_mode_at_hour(mode, now + timedelta(hours=t), arrival_time, departure_time) == GridMode.AWAY
            for t in range(H)
        ]

    @staticmethod
    def _deadline_index(now: datetime, finish_by: datetime | None, H: int) -> int:
        if finish_by is None:
            return H
        return max(1, min(H, int(math.ceil((finish_by - now).total_seconds() / 3600.0))))

    @staticmethod
    def _day_buckets(now: datetime, H: int) -> list[list[int]]:
        buckets: dict = {}
        for t in range(H):
            buckets.setdefault((now + timedelta(hours=t)).date(), []).append(t)
        return list(buckets.values())

    @staticmethod
    def pick_legionella_window(prices: list[float], allowed: list[bool], hours: int = HOTWATER_LEGIONELLA_HOURS, within: int = 24) -> list[int]:
        """Cheapest block of consecutive hours inside the next ``within`` hours."""
        best: tuple[float, int] | None = None
        last_start = min(within, len(prices)) - hours
        for s in range(0, max(0, last_start) + 1):
            idx = list(range(s, s + hours))
            if not all(allowed[i] for i in idx):
                continue
            cost = sum(prices[i] for i in idx)
            if best is None or cost < best[0] - 1e-9:
                best = (cost, s)
        return list(range(best[1], best[1] + hours)) if best else []

    @staticmethod
    def needs_legionella(dev: dict, now: datetime, away_now: bool) -> bool:
        if DeviceType(dev["device_type"]) != DeviceType.CLIMATE_HOTWATER or away_now:
            return False
        last = dev.get("last_legionella_ts")
        return last is None or (now - last) >= timedelta(days=7)

    # ------------------------------------------------------------------
    # LP model
    # ------------------------------------------------------------------
    def _solve_lp(
        self,
        result: PlanResult,
        now: datetime,
        mode: GridMode,
        prices: list[float],
        t_out: list[float],
        baseload: list[float],
        caps: list[float],
        phase_limit_a: float,
        devices: list[dict],
        arrival_time: datetime | None,
        departure_time: datetime | None,
        profile: PriorityProfile,
    ) -> None:
        H = len(prices)
        V = _Vars()
        R = _Rows()
        away = self._away_mask(now, H, mode, arrival_time, departure_time)

        # --- power variables p[d, t] (kW == kWh per hour)
        p_idx: dict[str, int] = {}
        for dev in devices:
            p_nom = max(0.05, float(dev.get("nominal_power_kw", 1.0)))
            # tiny time-preference epsilon makes ties deterministic (earlier is preferred)
            costs = [prices[t] + 1e-5 * t for t in range(H)]
            p_idx[dev["entry_id"]] = V.add(H, cost=costs, ub=p_nom)

        def ub_zero(eid: str, t: int) -> None:
            V.ub[p_idx[eid] + t] = 0.0

        # --- shared limits: tariff energy per hour and 230V IT conductor currents
        base_amps = [b * AMPS_PER_KW * 2.0 / 3.0 for b in baseload]  # baseload spread over 3 pairs
        for t in range(H):
            R.add({p_idx[d["entry_id"]] + t: 1.0 for d in devices}, max(0.0, caps[t] - baseload[t]))
            for ph in ("L1", "L2", "L3"):
                coeffs = {}
                for d in devices:
                    if ph in phase_pair_conductors(d.get("phase_pair", PhasePair.L1_L2)):
                        coeffs[p_idx[d["entry_id"]] + t] = AMPS_PER_KW
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
            decay = max(0.0, 1.0 - beta)
            temp0 = float(ref.get("current_temp", 20.0))
            req = []
            for t in range(H):
                if away[t]:
                    req.append(max(float(d.get("oot_temp", 10.0)) for d in zdevs))
                else:
                    tgt = [self._series(d.get("target_temps_72h"), t, 20.0) for d in zdevs]
                    req.append(max(tgt))
            # requirement at end of hour t covers the target of hour t and t+1 (pre-heating)
            req_end = [max(req[t], req[t + 1] if t + 1 < H else req[t]) for t in range(H)]
            w = max(self._weight(d, profile) for d in zdevs)
            s_lo = V.add(H, cost=w)
            s_hi = V.add(H, cost=20.0)
            zone_meta[zid] = dict(alpha=alpha, decay=decay, beta=beta, temp0=temp0, req=req, s_lo=s_lo)
            passive = self._passive_trajectory(temp0, decay, beta, t_out, [0.0] * H, 0.0)
            for t in range(H):
                coeffs: dict[int, float] = {}
                for k in range(t + 1):
                    g = (decay ** (t - k)) * alpha
                    for d in zdevs:
                        coeffs[p_idx[d["entry_id"]] + k] = g * float(d.get("heating_coefficient", 1.0))
                # T_end(t) >= req_end[t] - s_lo   <=>   -gain·p - s_lo <= passive - req
                lo = {c: -v for c, v in coeffs.items()}
                lo[s_lo + t] = -1.0
                R.add(lo, passive[t] - req_end[t])
                # T_end(t) <= max(req, T0-ish) + MAX_PREHEAT + s_hi
                hi = dict(coeffs)
                hi[s_hi + t] = -1.0
                R.add(hi, max(req[t], passive[t]) + MAX_PREHEAT_C - passive[t])

        # --- device specific constraints
        ev_info: dict[str, dict] = {}
        sw_info: dict[str, dict] = {}
        hw_info: dict[str, dict] = {}
        for dev in devices:
            eid = dev["entry_id"]
            dtype = DeviceType(dev["device_type"])
            p_nom = max(0.05, float(dev.get("nominal_power_kw", 1.0)))
            w = self._weight(dev, profile)
            base = p_idx[eid]

            if dtype == DeviceType.EV_CHARGER:
                need = max(0.0, float(dev.get("energy_needed_kwh", 0.0)))
                if not dev.get("connected", False) or need <= 0.05:
                    for t in range(H):
                        ub_zero(eid, t)
                    continue
                dl = self._deadline_index(now, dev.get("finish_by"), H)
                for t in range(dl, H):
                    ub_zero(eid, t)
                s = V.add(1, cost=w)
                R.add({**{base + t: -1.0 for t in range(dl)}, s: -1.0}, -need)
                ev_info[eid] = dict(deadline=dl, need=need, slack=s)

            elif dtype == DeviceType.SWITCH:
                mask = dev.get("schedule_mask_72h") or [True] * H
                eligible = [t for t in range(H) if (mask[t] if t < len(mask) else False) and not away[t]]
                for t in range(H):
                    if t not in eligible:
                        ub_zero(eid, t)
                req_h = max(0.0, float(dev.get("runtime_hours", 0.0)))
                done_today = max(0.0, float(dev.get("runtime_done_today_h", 0.0)))
                for i, bucket in enumerate(self._day_buckets(now, H)):
                    elig = [t for t in bucket if t in eligible]
                    need_h = min(len(elig), (req_h - done_today) if i == 0 else req_h)
                    if need_h <= 0 or not elig:
                        continue
                    s = V.add(1, cost=w)
                    R.add({**{base + t: -1.0 for t in elig}, s: -1.0}, -need_h * p_nom)
                sw_info[eid] = dict(eligible=eligible)

            elif dtype == DeviceType.CLIMATE_HOTWATER:
                info = self._add_hotwater(
                    dev, now, H, prices, away, V, R, base, p_nom, w
                )
                hw_info[eid] = info

        # --- solve
        A = coo_matrix((R.v, (R.r, R.c)), shape=(len(R.b), V.n)).tocsr()
        res = linprog(
            c=np.array(V.cost),
            A_ub=A,
            b_ub=np.array(R.b),
            bounds=list(zip([0.0] * V.n, V.ub)),
            method="highs",
            options={"time_limit": self.time_limit_s, "presolve": True},
        )
        if res.x is None or res.status not in (0, 1):
            raise RuntimeError(f"LP failed: {res.message}")
        x = res.x

        # --- extract and post-process
        raw = {
            d["entry_id"]: [max(0.0, min(float(d.get("nominal_power_kw", 1.0)), float(x[p_idx[d["entry_id"]] + t]))) for t in range(H)]
            for d in devices
        }
        for dev in devices:  # relays first (whole hours), then EVs fill around them
            if DeviceType(dev["device_type"]) == DeviceType.SWITCH:
                raw[dev["entry_id"]] = self._postprocess_switch(raw[dev["entry_id"]], float(dev.get("nominal_power_kw", 1.0)), prices, now)
        for dev in devices:
            eid = dev["entry_id"]
            if DeviceType(dev["device_type"]) == DeviceType.EV_CHARGER and eid in ev_info:
                others = [sum(raw[o][t] for o in raw if o != eid) for t in range(H)]
                headroom = [max(0.0, caps[t] - baseload[t] - others[t]) for t in range(H)]
                raw[eid] = self._postprocess_ev(
                    raw[eid], float(dev.get("min_power_kw", 1.38)), float(dev.get("nominal_power_kw", 1.0)),
                    prices, ev_info[eid]["deadline"], headroom,
                )
                result.unmet[eid] = round(max(0.0, ev_info[eid]["need"] - sum(raw[eid])), 3)
        for eid, plan in raw.items():
            result.plans[eid] = [round(v, 3) for v in plan]

        for zid, meta in zone_meta.items():
            zdevs = zones[zid]
            heat = [
                sum(result.plans[d["entry_id"]][t] * float(d.get("heating_coefficient", 1.0)) for d in zdevs)
                for t in range(H)
            ]
            temps = self._passive_trajectory(meta["temp0"], meta["decay"], meta["beta"], t_out, heat, meta["alpha"])
            for d in zdevs:
                eid = d["entry_id"]
                result.predicted_temps[eid] = [round(v, 2) for v in temps]
                result.setpoints[eid] = self._heating_setpoints(result.plans[eid], meta["req"], temps)
            result.unmet[f"zone:{zid}"] = round(float(sum(x[meta["s_lo"] + t] for t in range(H))), 3)

        for eid, info in hw_info.items():
            dev = next(d for d in devices if d["entry_id"] == eid)
            result.legionella_hours[eid] = info["legionella"]
            result.setpoints[eid] = self._hotwater_setpoints(dev, result.plans[eid], info, H)
            if info.get("model"):
                m = info["model"]
                temps = self._passive_trajectory(
                    m["temp0"], m["decay"], m["beta"], [HOTWATER_AMBIENT_C] * H,
                    [result.plans[eid][t] - m["draw"][t] for t in range(H)], m["alpha"],
                )
                result.predicted_temps[eid] = [round(v, 2) for v in temps]

    @staticmethod
    def _series(values: list[float] | None, t: int, default: float) -> float:
        if not values:
            return default
        return float(values[t] if t < len(values) else values[-1])

    @staticmethod
    def _passive_trajectory(temp0, decay, beta, t_out, heat_kwh, alpha) -> list[float]:
        """T_end(t) for t=0..H-1 with T[t+1] = decay*T[t] + alpha*heat[t] + beta*Tout[t]."""
        temps, temp = [], float(temp0)
        for t in range(len(t_out)):
            temp = decay * temp + alpha * float(heat_kwh[t]) + beta * float(t_out[t])
            temps.append(temp)
        return temps

    def _add_hotwater(self, dev, now, H, prices, away, V: _Vars, R: _Rows, base, p_nom, w) -> dict:
        eid = dev["entry_id"]
        info: dict = {"legionella": [], "model": None}
        volume = max(30.0, float(dev.get("tank_volume_l", 200.0)))
        quota_h = max(0.0, float(dev.get("daily_quota_hours", 3.0)))
        daily_use = float(dev.get("daily_use_kwh", quota_h * p_nom * 0.85))

        if self.needs_legionella(dev, now, away[0]):
            win = self.pick_legionella_window(prices, [not a for a in away])
            info["legionella"] = win
            for t in win:  # p[t] >= p_nom - s  (rank 0 weight)
                s = V.add(1, cost=priority_weight(0))
                R.add({base + t: -1.0, s: -1.0}, -p_nom)

        temp0 = dev.get("current_temp")
        if temp0 is None:
            # No tank sensor: daily energy quota placed in the cheapest hours of each day.
            done_today = max(0.0, float(dev.get("energy_done_today_kwh", 0.0)))
            for i, bucket in enumerate(self._day_buckets(now, H)):
                frac_away = sum(1 for t in bucket if away[t]) / max(1, len(bucket))
                need = quota_h * p_nom * (1.0 - 0.8 * frac_away)
                if i == 0:
                    need = max(0.0, need - done_today)
                need = min(need, len(bucket) * p_nom)
                if need <= 0:
                    continue
                s = V.add(1, cost=w)
                R.add({**{base + t: -1.0 for t in bucket}, s: -1.0}, -need)
            return info

        # Tank model: 1 kWh raises the tank by 1/(1.163e-3*V) °C, standby loss to the room.
        alpha = 1.0 / (0.001163 * volume)
        beta = 0.008
        decay = 1.0 - beta
        draw = []
        for t in range(H):
            hr = (now + timedelta(hours=t)).hour
            share = 0.4 / 3 if 6 <= hr < 9 else (0.6 / 5 if 17 <= hr < 22 else 0.0)
            draw.append(0.0 if away[t] else daily_use * share)
        passive = self._passive_trajectory(float(temp0), decay, beta, [HOTWATER_AMBIENT_C] * H, [-x for x in draw], alpha)
        s_lo = V.add(H, cost=w)
        for t in range(H):
            req = float(dev.get("oot_temp", 15.0)) if away[t] else self._series(dev.get("min_temps_72h"), t, 50.0)
            coeffs = {base + k: -(decay ** (t - k)) * alpha for k in range(t + 1)}
            coeffs[s_lo + t] = -1.0
            R.add(coeffs, passive[t] - req)
            # physical max: the thermostat cuts out at ~nominal + few degrees (legionella excepted)
            hi = {base + k: (decay ** (t - k)) * alpha for k in range(t + 1)}
            top = HOTWATER_LEGIONELLA_C + 5.0
            R.add(hi, max(top, passive[t]) - passive[t])
        info["model"] = dict(alpha=alpha, beta=beta, decay=decay, temp0=float(temp0), draw=draw)
        return info

    # ------------------------------------------------------------------
    # Post processing
    # ------------------------------------------------------------------
    @staticmethod
    def _postprocess_ev(plan, min_kw, p_nom, prices, deadline, headroom=None) -> list[float]:
        """Remove sub-minimum hours (OCPP cannot charge below ~6 A) without breaking the
        hourly tariff headroom: top up hours that already charge, else open the cheapest
        hours at minimum power."""
        plan = list(plan)
        H = len(plan)
        room = list(headroom) if headroom is not None else [p_nom] * H
        small = [t for t in range(H) if 1e-3 < plan[t] < min_kw]
        if not small:
            return plan
        energy = sum(plan[t] for t in small)
        for t in small:
            plan[t] = 0.0
        for t in sorted((t for t in range(min(deadline, H)) if plan[t] >= min_kw), key=lambda t: prices[t]):
            add = max(0.0, min(energy, p_nom - plan[t], room[t] - plan[t]))
            plan[t] += add
            energy -= add
            if energy <= 1e-6:
                return plan
        for t in sorted((t for t in range(min(deadline, H)) if plan[t] == 0.0), key=lambda t: prices[t]):
            if energy <= 1e-6:
                break
            if room[t] < min_kw:
                continue
            plan[t] = min(p_nom, room[t], max(min_kw, energy))
            energy -= plan[t]
        return plan

    @staticmethod
    def _postprocess_switch(plan, p_nom, prices, now) -> list[float]:
        """Relays run whole hours: per day keep ceil(energy/p_nom) hours, highest LP share first."""
        H = len(plan)
        out = [0.0] * H
        for bucket in EnergyPlanner._day_buckets(now, H):
            energy = sum(plan[t] for t in bucket)
            n_hours = int(math.ceil(energy / p_nom - 0.05)) if energy > 0.05 * p_nom else 0
            for t in sorted(bucket, key=lambda t: (-round(plan[t], 3), prices[t]))[:n_hours]:
                out[t] = p_nom
        return out

    @staticmethod
    def _heating_setpoints(plan, req, temps) -> list[float]:
        """Planned heat -> setpoint = predicted temperature (pre-heat), else hour target."""
        out = []
        for t, p in enumerate(plan):
            target = req[t]
            if p and p > 0.05:
                sp = min(target + MAX_PREHEAT_C, max(target, temps[t]))
            else:
                sp = target
            out.append(round(sp * 2.0) / 2.0)
        return out

    @staticmethod
    def _hotwater_setpoints(dev, plan, info, H) -> list[float]:
        nominal = float(dev.get("nominal_temp", 65.0))
        minimum = float(dev.get("min_temp", 50.0))
        out = []
        for t in range(H):
            if t in info["legionella"]:
                out.append(HOTWATER_LEGIONELLA_C + 2.0)
            elif plan[t] > 0.05:
                out.append(nominal)
            else:
                out.append(minimum)
        return out

    # ------------------------------------------------------------------
    # Fallback (no solver / solver error)
    # ------------------------------------------------------------------
    def _solve_greedy_fallback(
        self,
        result: PlanResult,
        now: datetime,
        mode: GridMode,
        prices: list[float],
        t_out: list[float],
        baseload: list[float],
        caps: list[float],
        phase_limit_a: float,
        devices: list[dict],
        arrival_time: datetime | None,
        departure_time: datetime | None,
        profile: PriorityProfile,
    ) -> None:
        """Deterministic cheapest-hours heuristic covering every device type."""
        H = len(prices)
        away = self._away_mask(now, H, mode, arrival_time, departure_time)
        rem_kw = [max(0.0, caps[t] - baseload[t]) for t in range(H)]
        rem_a = {ph: [phase_limit_a] * H for ph in ("L1", "L2", "L3")}
        order = sorted(range(H), key=lambda t: prices[t])

        def place(dev, plan, hours, energy, min_kw=0.0):
            p_nom = float(dev.get("nominal_power_kw", 1.0))
            p1, p2 = phase_pair_conductors(dev.get("phase_pair", PhasePair.L1_L2))
            for t in hours:
                if energy <= 1e-3:
                    break
                phase_kw = min(rem_a[p1][t], rem_a[p2][t]) / AMPS_PER_KW
                alloc = min(p_nom - plan[t], rem_kw[t], phase_kw, energy)
                if alloc < max(min_kw, 0.01):
                    continue
                plan[t] += alloc
                rem_kw[t] -= alloc
                rem_a[p1][t] -= alloc * AMPS_PER_KW
                rem_a[p2][t] -= alloc * AMPS_PER_KW
                energy -= alloc
            return energy

        for dev in devices:
            eid = dev["entry_id"]
            dtype = DeviceType(dev["device_type"])
            p_nom = float(dev.get("nominal_power_kw", 1.0))
            plan = [0.0] * H
            if dtype == DeviceType.EV_CHARGER and dev.get("connected"):
                dl = self._deadline_index(now, dev.get("finish_by"), H)
                left = place(dev, plan, [t for t in order if t < dl], float(dev.get("energy_needed_kwh", 0.0)), float(dev.get("min_power_kw", 1.38)))
                result.unmet[eid] = round(left, 3)
            elif dtype == DeviceType.SWITCH:
                mask = dev.get("schedule_mask_72h") or [True] * H
                for bucket in self._day_buckets(now, H):
                    elig = [t for t in order if t in bucket and mask[t] and not away[t]]
                    place(dev, plan, elig, float(dev.get("runtime_hours", 0.0)) * p_nom)
                plan = self._postprocess_switch(plan, p_nom, prices, now)
            elif dtype == DeviceType.CLIMATE_HOTWATER:
                quota = float(dev.get("daily_quota_hours", 3.0)) * p_nom
                for bucket in self._day_buckets(now, H):
                    place(dev, plan, [t for t in order if t in bucket], quota)
                result.legionella_hours[eid] = []
                result.setpoints[eid] = self._hotwater_setpoints(dev, plan, {"legionella": []}, H)
            elif dtype == DeviceType.CLIMATE_HEATING:
                alpha = float(dev.get("alpha", 1.5)) * float(dev.get("heating_coefficient", 1.0))
                beta = float(dev.get("beta", 0.05))
                req = []
                for t in range(H):
                    req.append(float(dev.get("oot_temp", 10.0)) if away[t] else self._series(dev.get("target_temps_72h"), t, 20.0))
                    need = max(0.0, beta * (req[t] - t_out[t]) / max(alpha, 1e-3))
                    plan[t] = min(p_nom, need, rem_kw[t])
                    rem_kw[t] -= plan[t]
                result.setpoints[eid] = [round(r * 2) / 2 for r in req]
            result.plans[eid] = [round(v, 3) for v in plan]
