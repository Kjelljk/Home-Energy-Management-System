"""Overload Acceptance (OA) – inverse-time tolerance for phase/main-fuse overloads.

Decides HOW MUCH current a phase may carry right now; the distributor then allocates by
priority/plan within that limit. Small overloads are tolerated longer than large ones,
roughly like a fuse/MCB characteristic.

Behaviour per phase (ratio r = measured / rated limit):
* r <= 1.0  -> RECOVERING: the OA budget (0–100 %) refills. Full refill takes
               ``recover_fast_min`` at r <= 0.8 and ``recover_slow_min`` at r = 1.0.
* 1.0 < r   -> ACCEPTING: the budget drains. Empty after ``delay_5_min`` at r = 1.05 and
               ``delay_20_min`` at r >= 1.20 (linear in between). While accepting, the
               effective limit is the measured current: nothing is cut, but nothing new
               is granted either.
* budget 0  -> LIMITING: the effective limit ramps from the overload level down to the
               rated limit over ``ramp_5_min`` / ``ramp_20_min``.
* r >= hard_ratio -> immediate LIMITING at the rated limit (no tolerance).

Defaults are conservative compared with MCB/fuse curves (an IEC 60898 breaker does not
trip at 1.13 × In within one hour), but they are user-configurable.
"""
from __future__ import annotations

from dataclasses import dataclass

PHASES = ("L1", "L2", "L3")

STATE_OK = "ok"
STATE_ACCEPTING = "accepting"
STATE_LIMITING = "limiting"
RELEASE_BUDGET_PCT = 10.0  # budget needed before LIMITING is released after the ramp


def _interp(x: float, x1: float, y1: float, x2: float, y2: float) -> float:
    if x <= x1:
        return y1
    if x >= x2:
        return y2
    return y1 + (x - x1) * (y2 - y1) / (x2 - x1)


@dataclass
class OAParams:
    delay_5_min: float = 10.0
    delay_20_min: float = 2.0
    ramp_5_min: float = 10.0
    ramp_20_min: float = 5.0
    recover_fast_min: float = 20.0
    recover_slow_min: float = 60.0
    hard_ratio: float = 1.5

    def delay_min(self, ratio: float) -> float:
        return max(0.05, _interp(ratio, 1.05, self.delay_5_min, 1.20, self.delay_20_min))

    def ramp_min(self, ratio: float) -> float:
        return max(0.05, _interp(ratio, 1.05, self.ramp_5_min, 1.20, self.ramp_20_min))

    def recover_min(self, ratio: float) -> float:
        return max(0.05, _interp(ratio, 0.80, self.recover_fast_min, 1.00, self.recover_slow_min))


@dataclass
class _PhaseState:
    budget: float = 100.0          # %
    state: str = STATE_OK
    ramp_from: float = 1.0         # ratio when limiting started
    ramp_total_min: float = 1.0
    ramp_elapsed_min: float = 0.0


class OverloadAcceptance:
    """Per-phase OA state machine. Call :meth:`update` once per control cycle."""

    def __init__(self, params: OAParams | None = None) -> None:
        self.params = params or OAParams()
        self._ph: dict[str, _PhaseState] = {p: _PhaseState() for p in PHASES}

    def reset(self) -> None:
        self._ph = {p: _PhaseState() for p in PHASES}

    def update(self, phase: str, measured_a: float, rated_a: float, dt_s: float) -> float:
        """Advance the state machine and return the effective current limit [A] for ``phase``."""
        st = self._ph[phase]
        p = self.params
        dt_min = max(0.0, float(dt_s)) / 60.0
        if rated_a <= 0:
            return rated_a
        ratio = max(0.0, float(measured_a)) / rated_a

        if ratio >= p.hard_ratio:
            st.budget = 0.0
            st.state = STATE_LIMITING
            st.ramp_from, st.ramp_total_min, st.ramp_elapsed_min = 1.0, 1.0, 1.0
            return rated_a

        if st.state == STATE_LIMITING:
            if ratio <= 1.0:
                st.budget = min(100.0, st.budget + 100.0 / p.recover_min(ratio) * dt_min)
            st.ramp_elapsed_min += dt_min
            progress = min(1.0, st.ramp_elapsed_min / st.ramp_total_min)
            if progress >= 1.0 and st.budget >= RELEASE_BUDGET_PCT:
                st.state = STATE_OK
                return rated_a
            factor = st.ramp_from - (st.ramp_from - 1.0) * progress
            return rated_a * max(1.0, factor)

        if ratio <= 1.0:
            st.budget = min(100.0, st.budget + 100.0 / p.recover_min(ratio) * dt_min)
            st.state = STATE_OK
            return rated_a

        # ratio > 1.0: consume budget
        st.budget = max(0.0, st.budget - 100.0 / p.delay_min(ratio) * dt_min)
        if st.budget > 0.0:
            st.state = STATE_ACCEPTING
            return float(measured_a)  # tolerate, but grant nothing beyond today's draw
        st.state = STATE_LIMITING
        st.ramp_from = ratio
        st.ramp_total_min = p.ramp_min(ratio)
        st.ramp_elapsed_min = 0.0
        return rated_a * ratio

    def state(self, phase: str) -> str:
        return self._ph[phase].state

    def budget(self, phase: str) -> float:
        return round(self._ph[phase].budget, 1)

    def any_limiting(self) -> bool:
        return any(s.state == STATE_LIMITING for s in self._ph.values())

    def snapshot(self) -> dict[str, dict]:
        return {p: {"state": s.state, "budget_pct": round(s.budget, 1)} for p, s in self._ph.items()}
