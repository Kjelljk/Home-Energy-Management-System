"""Real-time Power Distributor, Lending Engine, Feed-Forward EV & PI Heat Pump Governor."""
from __future__ import annotations

from dataclasses import dataclass
from .const import (
    DeviceAllocation,
    DeviceType,
    DistStatus,
    PhasePair,
    it_phase_amps_to_power_kw,
    phase_pair_conductors,
    power_kw_to_it_phase_amps,
)


@dataclass
class _PIZoneState:
    integral: float = 0.0


class PowerDistributor:
    """Distributes power by priority, lends idle capacity, and regulates EV & heat pumps."""

    def __init__(
        self,
        ev_ramp_up_step_a: float = 2.0,
        ev_safety_margin_a: float = 1.0,
        pi_kp: float = 1.2,
        pi_ki: float = 0.002,
    ) -> None:
        self.ev_ramp_up_step_a = float(ev_ramp_up_step_a)
        self.ev_safety_margin_a = float(ev_safety_margin_a)
        self.pi_kp = float(pi_kp)
        self.pi_ki = float(pi_ki)
        self._pi_states: dict[str, _PIZoneState] = {}
        # diagnostics for the dashboards
        self.pi_diag: dict[str, dict] = {}
        self.last_edges: list[dict] = []        # lending: {"from", "to", "kw"} ("from": None = free headroom)
        self.last_summary: dict = {}

    def distribute(
        self,
        devices: list[dict],
        tariff_limit_now_kw: float,
        phase_limits_a: dict[str, float],
        grid_phase_currents_a: dict[str, float],
    ) -> dict[str, DeviceAllocation]:
        """Allocate instantaneous power budget across committed devices with unused-power lending."""
        allocations: dict[str, DeviceAllocation] = {}
        committed: list[dict] = []

        for dev in devices:
            eid = dev["entry_id"]
            if not dev.get("committed", False):
                allocations[eid] = DeviceAllocation(
                    entry_id=eid,
                    allowed_kw=0.0,
                    allowed_amps=0.0,
                    status=DistStatus.UNCOMMITTED,
                )
            else:
                committed.append(dev)

        # Sort by priority_now first, then distribution priority ascending
        ordered = sorted(
            committed,
            key=lambda d: (0 if d.get("priority_now") else 1, int(d.get("dist_priority", 5))),
        )

        # Estimate uncontrolled non-committed house load on each phase
        committed_phase_a = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
        committed_live_kw = 0.0
        for dev in ordered:
            live_kw = max(0.0, float(dev.get("live_power_kw", 0.0)))
            committed_live_kw += live_kw
            p1, p2 = phase_pair_conductors(dev.get("phase_pair", PhasePair.L1_L2))
            a = power_kw_to_it_phase_amps(live_kw)
            committed_phase_a[p1] += a
            committed_phase_a[p2] += a

        uncontrolled_phase_a = {
            ph: max(0.0, float(grid_phase_currents_a.get(ph, 0.0)) - committed_phase_a[ph])
            for ph in ("L1", "L2", "L3")
        }

        avail_phase_a = {
            ph: max(0.0, float(phase_limits_a.get(ph, 63.0)) - uncontrolled_phase_a[ph] - self.ev_safety_margin_a)
            for ph in ("L1", "L2", "L3")
        }
        avail_tariff_kw = max(0.0, float(tariff_limit_now_kw))

        budget_in = avail_tariff_kw
        lenders: list[list] = []   # [entry_id, remaining_kw] in priority order
        edges: list[dict] = []
        # Pass 1: reservations for devices the plan wants running now (or Priority Now)
        lent_pool_kw = 0.0
        lent_pool_a = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
        waiting: list[dict] = []

        for dev in ordered:
            eid = dev["entry_id"]
            dtype = DeviceType(dev["device_type"])
            p_nom = float(dev.get("nominal_power_kw", 1.0))
            planned_kw = float(dev.get("planned_kw", 0.0))
            live_kw = max(0.0, float(dev.get("live_power_kw", 0.0)))
            p1, p2 = phase_pair_conductors(dev.get("phase_pair", PhasePair.L1_L2))

            wants_power = dev.get("priority_now", False) or planned_kw > 0.05
            if not wants_power:
                waiting.append(dev)
                continue

            min_kw = self._min_kw(dev, dtype, p_nom)
            modulating = self._modulating(dev, dtype)
            want_kw = p_nom if (not modulating or dev.get("priority_now")) else max(min_kw, min(p_nom, planned_kw))
            phase_headroom_kw = it_phase_amps_to_power_kw(min(avail_phase_a[p1], avail_phase_a[p2]))
            max_grant_kw = min(want_kw, avail_tariff_kw, phase_headroom_kw)

            if max_grant_kw >= min_kw:
                grant_kw = max_grant_kw if modulating else p_nom
                grant_a = power_kw_to_it_phase_amps(grant_kw)
                avail_tariff_kw -= grant_kw
                avail_phase_a[p1] -= grant_a
                avail_phase_a[p2] -= grant_a

                # Lending: reservation held but the device is idle (own thermostat satisfied)
                if dtype != DeviceType.EV_CHARGER and live_kw < 0.15 * grant_kw:
                    lenders.append([eid, grant_kw])
                    lent_pool_kw += grant_kw
                    lent_pool_a[p1] += grant_a
                    lent_pool_a[p2] += grant_a
                    status = DistStatus.REDISTRIBUTING
                else:
                    status = DistStatus.PRIORITIZED
                allocations[eid] = DeviceAllocation(
                    entry_id=eid,
                    allowed_kw=round(grant_kw, 3),
                    allowed_amps=round(grant_a, 2),
                    status=status,
                    is_lent_capacity=status == DistStatus.REDISTRIBUTING,
                )
            else:
                # planned but no room right now -> first in line for lent capacity
                waiting.insert(0, {**dev, "_was_planned": True})

        # Pass 2: lend free headroom + idle reservations ONLY to loads that need it:
        # planned-but-blocked loads first, then loads flagged with an explicit opportunistic need
        # (e.g. EV behind its deadline). Unplanned heaters do NOT get power just because
        # there is room – that would undo the cost optimisation.
        free_after_pass1 = avail_tariff_kw
        total_kw = avail_tariff_kw + lent_pool_kw
        total_a = {ph: avail_phase_a[ph] + lent_pool_a[ph] for ph in ("L1", "L2", "L3")}
        waiting.sort(key=lambda d: (0 if d.get("_was_planned") else 1,
                                    0 if d.get("priority_now") else 1, int(d.get("dist_priority", 5))))
        for dev in waiting:
            eid = dev["entry_id"]
            dtype = DeviceType(dev["device_type"])
            p_nom = float(dev.get("nominal_power_kw", 1.0))
            p1, p2 = phase_pair_conductors(dev.get("phase_pair", PhasePair.L1_L2))
            min_kw = self._min_kw(dev, dtype, p_nom)
            eligible = dev.get("_was_planned") or bool(dev.get("opportunistic_need", False))
            headroom_kw = min(p_nom, total_kw, it_phase_amps_to_power_kw(min(total_a[p1], total_a[p2])))
            if eligible and headroom_kw >= min_kw:
                grant_kw = headroom_kw if self._modulating(dev, dtype) else p_nom
                grant_a = power_kw_to_it_phase_amps(grant_kw)
                # attribute the grant: free headroom first, then idle reservations (lending)
                need = grant_kw
                take = min(need, max(0.0, free_after_pass1))
                if take > 1e-3:
                    edges.append({"from": None, "to": eid, "kw": round(take, 3)})
                    free_after_pass1 -= take
                    need -= take
                for lender in lenders:
                    if need <= 1e-3:
                        break
                    take = min(need, lender[1])
                    if take > 1e-3:
                        edges.append({"from": lender[0], "to": eid, "kw": round(take, 3)})
                        lender[1] -= take
                        need -= take
                total_kw -= grant_kw
                total_a[p1] -= grant_a
                total_a[p2] -= grant_a
                allocations[eid] = DeviceAllocation(
                    entry_id=eid,
                    allowed_kw=round(grant_kw, 3),
                    allowed_amps=round(grant_a, 2),
                    status=DistStatus.PRIORITIZED,
                    is_lent_capacity=True,
                )
            else:
                allocations[eid] = DeviceAllocation(
                    entry_id=eid,
                    allowed_kw=0.0,
                    allowed_amps=0.0,
                    status=DistStatus.HOLD,
                    reason="blocked" if dev.get("_was_planned") else "not planned",
                )

        self.last_edges = edges
        self.last_summary = {
            "budget_kw": round(budget_in, 3),
            "reserved_kw": round(sum(a.allowed_kw for a in allocations.values()), 3),
            "lent_kw": round(sum(e["kw"] for e in edges if e["from"]), 3),
            "unused_kw": round(max(0.0, total_kw), 3),
            "uncontrolled_phase_a": {k: round(v, 2) for k, v in uncontrolled_phase_a.items()},
            "committed_live_kw": round(committed_live_kw, 3),
        }
        return allocations

    @staticmethod
    def _modulating(dev: dict, dtype: DeviceType) -> bool:
        """EV chargers and stepless heaters can run at part load; on/off devices cannot."""
        return dtype == DeviceType.EV_CHARGER or (
            dtype == DeviceType.CLIMATE_HEATING and dev.get("heater_kind") == "stepless")

    @staticmethod
    def _min_kw(dev: dict, dtype: DeviceType, p_nom: float) -> float:
        if dtype == DeviceType.EV_CHARGER:
            return it_phase_amps_to_power_kw(float(dev.get("min_current_a", 6.0)))
        if dtype == DeviceType.CLIMATE_HEATING and dev.get("heater_kind") == "stepless":
            return 0.25 * p_nom          # a modulating heat pump can run at part load
        return p_nom

    def compute_ev_feedforward_amps(
        self,
        prev_target_a: int,
        ev_live_power_kw: float,
        ev_phase_pair: PhasePair | str,
        grid_phase_currents_a: dict[str, float],
        phase_limits_a: dict[str, float],
        tariff_limit_now_kw: float,
        house_non_ev_kw: float,
        min_ev_a: int = 6,
        max_ev_a: int = 32,
    ) -> int:
        """Direct feed-forward EV current calculation with instant drop and slewed upward ramp."""
        ev_amps_now = power_kw_to_it_phase_amps(ev_live_power_kw)
        p1, p2 = phase_pair_conductors(ev_phase_pair)

        house_p1 = max(0.0, float(grid_phase_currents_a.get(p1, 0.0)) - ev_amps_now)
        house_p2 = max(0.0, float(grid_phase_currents_a.get(p2, 0.0)) - ev_amps_now)

        safe_a_p1 = float(phase_limits_a.get(p1, 32.0)) - house_p1 - self.ev_safety_margin_a
        safe_a_p2 = float(phase_limits_a.get(p2, 32.0)) - house_p2 - self.ev_safety_margin_a
        phase_safe_a = min(safe_a_p1, safe_a_p2)

        tariff_rem_kw = max(0.0, float(tariff_limit_now_kw) - max(0.0, float(house_non_ev_kw)))
        tariff_safe_a = power_kw_to_it_phase_amps(tariff_rem_kw)

        raw_target_a = min(float(max_ev_a), phase_safe_a, tariff_safe_a)
        if raw_target_a < float(min_ev_a):
            return 0

        target_int = int(raw_target_a)
        if target_int < prev_target_a:
            # Immediate drop on overload / appliance startup
            return target_int
        # Asymmetric upward slew rate
        max_step = max(1, int(round(self.ev_ramp_up_step_a)))
        return min(target_int, int(prev_target_a) + max_step)

    def compute_heatpump_governor_setpoint(
        self,
        zone_id: str,
        target_temp: float,
        external_room_temp: float,
        dt_seconds: float,
    ) -> float:
        """Clamped PI setpoint offset controller quantized to 0.5 C steps."""
        state = self._pi_states.setdefault(zone_id, _PIZoneState())
        error = float(target_temp) - float(external_room_temp)

        p_term = self.pi_kp * error
        candidate_i = state.integral + self.pi_ki * error * max(0.0, float(dt_seconds))
        raw_offset = p_term + candidate_i

        # Anti-windup clamping within [-2.0 C, +4.0 C]
        clamped_offset = max(-2.0, min(4.0, raw_offset))
        if -2.0 < raw_offset < 4.0:
            state.integral = candidate_i

        cmd_temp = float(target_temp) + clamped_offset
        out = round(cmd_temp * 2.0) / 2.0
        self.pi_diag[zone_id] = {
            "room_temp": round(float(external_room_temp), 2), "target_temp": round(float(target_temp), 2),
            "error": round(error, 3), "p_term": round(p_term, 3), "i_term": round(state.integral, 3),
            "offset_raw": round(raw_offset, 3), "offset": round(clamped_offset, 3),
            "saturated": not (-2.0 < raw_offset < 4.0), "setpoint_out": out,
            "kp": self.pi_kp, "ki": self.pi_ki,
        }
        return out
