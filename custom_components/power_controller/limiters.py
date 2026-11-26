"""Three-layer grid & tariff limiters for 230V IT systems."""
from __future__ import annotations

from .overload import OAParams, OverloadAcceptance
from .const import (
    PhasePair,
    SAFE_MODE_MAX_KW,
    TARIFF_BUFFER_KWH,
    phase_pair_conductors,
    power_kw_to_it_phase_amps,
)


class GridAndTariffLimiters:
    """Enforces internal phase breakers, external shared cable limits, and hourly tariff tiers."""

    def __init__(
        self,
        internal_limit_a: float = 63.0,
        external_limit_a: float | None = None,
        oa_params: OAParams | None = None,
    ) -> None:
        self.internal_limit_a = float(internal_limit_a)
        self.external_limit_a = float(external_limit_a) if external_limit_a is not None else None
        self.oa = OverloadAcceptance(oa_params)

    def effective_phase_limits_a(self) -> dict[str, float]:
        """Return effective per-phase current limit (minimum of internal and external limits)."""
        cap = self.internal_limit_a
        if self.external_limit_a is not None and self.external_limit_a > 0:
            cap = min(cap, self.external_limit_a)
        return {"L1": cap, "L2": cap, "L3": cap}

    def apply_overload_acceptance(self, measured_a: dict[str, float], dt_seconds: float) -> dict[str, float]:
        """Effective per-phase limits after Overload Acceptance (see overload.py).

        Used by the real-time distributor and EV feed-forward. The planner keeps using
        the rated limits from :meth:`effective_phase_limits_a`.
        """
        rated = self.effective_phase_limits_a()
        return {
            ph: round(self.oa.update(ph, float(measured_a.get(ph, 0.0)), rated[ph], dt_seconds), 2)
            for ph in ("L1", "L2", "L3")
        }

    @staticmethod
    def compute_phase_currents(
        loads: list[tuple[PhasePair | str, float]],
    ) -> dict[str, float]:
        """Compute total line current [A] on L1, L2, L3 from 230V IT phase-pair loads [kW]."""
        totals = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
        for pair, power_kw in loads:
            amps = power_kw_to_it_phase_amps(power_kw)
            p_a, p_b = phase_pair_conductors(pair)
            totals[p_a] += amps
            totals[p_b] += amps
        return totals

    @staticmethod
    def calculate_tariff_limit_now_kw(
        tariff_tier_kw: float,
        energy_used_this_hour_kwh: float,
        minutes_elapsed: float,
        target_kwh: float | None = None,
    ) -> float:
        """Max average power [kW] allowed for the rest of the clock hour.

        ``target_kwh`` is the cost-neutral hourly allowance from the price engine
        (active step minus buffer, or higher if today's peak / the top-3 of other days
        already allow it). The raw value is capped at 2x the hourly target so that a
        lightly-used hour does not hand out unrealistic power in its final minutes
        (phase limits still apply on top).
        """
        if target_kwh is None:
            target_kwh = max(0.2, float(tariff_tier_kw) - TARIFF_BUFFER_KWH)
        target_kwh = max(0.2, float(target_kwh))
        remaining_kwh = max(0.0, target_kwh - max(0.0, float(energy_used_this_hour_kwh)))
        minutes = min(59.0, max(0.0, float(minutes_elapsed)))
        remaining_hours = max(1.0 / 60.0, (60.0 - minutes) / 60.0)
        raw_limit_kw = remaining_kwh / remaining_hours
        return round(max(0.0, min(raw_limit_kw, 2.0 * target_kwh)), 3)

    @staticmethod
    def evaluate_total_budget_kw(
        grid_power_valid: bool,
        tariff_limit_now_kw: float,
    ) -> tuple[float, bool]:
        """Return (effective_budget_kw, is_safe_mode)."""
        if not grid_power_valid:
            return (SAFE_MODE_MAX_KW, True)
        return (max(0.0, float(tariff_limit_now_kw)), False)
