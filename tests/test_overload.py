"""Overload Acceptance: inverse-time tolerance of phase overloads."""
import pytest

from custom_components.power_controller.limiters import GridAndTariffLimiters
from custom_components.power_controller.overload import (
    STATE_ACCEPTING,
    STATE_LIMITING,
    STATE_OK,
    OAParams,
    OverloadAcceptance,
)


def run(oa, amps, rated, seconds, step=10.0):
    limit = rated
    for _ in range(int(seconds / step)):
        limit = oa.update("L1", amps, rated, step)
    return limit


def test_small_overload_is_tolerated_for_delay_5():
    oa = OverloadAcceptance()                       # 5 % -> 10 min
    limit = run(oa, 33.6, 32.0, 9 * 60)             # 1.05 x for 9 min
    assert oa.state("L1") == STATE_ACCEPTING
    assert limit == pytest.approx(33.6)             # measured current is accepted, not more
    run(oa, 33.6, 32.0, 70)
    assert oa.state("L1") == STATE_LIMITING


def test_large_overload_only_delay_20_then_ramps_to_rated():
    oa = OverloadAcceptance()                       # 20 % -> 2 min, ramp 5 min
    run(oa, 38.4, 32.0, 110)
    assert oa.state("L1") == STATE_ACCEPTING
    first = run(oa, 38.4, 32.0, 20)
    assert oa.state("L1") == STATE_LIMITING
    assert first == pytest.approx(38.4, abs=0.5)    # ramp starts at the overload level
    mid = run(oa, 38.4, 32.0, 150)
    assert 32.0 < mid < 38.4                        # half-way down
    end = run(oa, 38.4, 32.0, 200)
    assert end == pytest.approx(32.0)


def test_hard_ratio_limits_immediately():
    oa = OverloadAcceptance(OAParams(hard_ratio=1.4))
    assert oa.update("L1", 46.0, 32.0, 10) == pytest.approx(32.0)
    assert oa.state("L1") == STATE_LIMITING


def test_recovery_faster_when_well_below_limit():
    slow, fast = OverloadAcceptance(), OverloadAcceptance()
    for oa in (slow, fast):
        run(oa, 38.4, 32.0, 60)                     # drain half the budget
    run(slow, 32.0, 32.0, 600)                      # at rated: 60 min for 0->100 %
    run(fast, 20.0, 32.0, 600)                      # <= 80 %: 20 min for 0->100 %
    assert fast.budget("L1") > slow.budget("L1")
    assert fast.budget("L1") == pytest.approx(100.0)


def test_limiting_is_released_after_ramp_and_recovery():
    oa = OverloadAcceptance()
    run(oa, 38.4, 32.0, 130)                        # budget empty -> limiting
    assert oa.state("L1") == STATE_LIMITING
    run(oa, 20.0, 32.0, 6 * 60)                     # load shed, ramp done, budget refilling
    assert oa.state("L1") == STATE_OK
    assert oa.update("L1", 20.0, 32.0, 10) == pytest.approx(32.0)


def test_limiter_wrapper_uses_rated_and_external_minimum():
    lim = GridAndTariffLimiters(internal_limit_a=40.0, external_limit_a=32.0)
    limits = lim.apply_overload_acceptance({"L1": 33.0, "L2": 10.0, "L3": 0.0}, 10.0)
    assert limits["L1"] == pytest.approx(33.0)      # accepting on L1
    assert limits["L2"] == pytest.approx(32.0)      # rated = min(internal, external)
    assert not lim.oa.any_limiting()
