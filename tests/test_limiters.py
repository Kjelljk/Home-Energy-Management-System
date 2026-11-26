import pytest

from custom_components.power_controller.const import PhasePair, power_kw_to_it_phase_amps
from custom_components.power_controller.limiters import GridAndTariffLimiters


def test_230v_it_delta_phase_current_allocation():
    assert power_kw_to_it_phase_amps(3.68) == pytest.approx(16.0)
    limiters = GridAndTariffLimiters(internal_limit_a=32.0, external_limit_a=32.0)
    phase_currents = limiters.compute_phase_currents(
        [(PhasePair.L1_L2, 3.68), (PhasePair.L2_L3, 3.68)]
    )
    assert phase_currents["L1"] == pytest.approx(16.0)
    assert phase_currents["L2"] == pytest.approx(32.0)
    assert phase_currents["L3"] == pytest.approx(16.0)


def test_tariff_limiter_headroom_with_safety_margin():
    limiters = GridAndTariffLimiters(internal_limit_a=63.0, external_limit_a=63.0)
    limit_kw = limiters.calculate_tariff_limit_now_kw(
        tariff_tier_kw=5.0,
        energy_used_this_hour_kwh=3.0,
        minutes_elapsed=30.0,
    )
    assert limit_kw == pytest.approx(3.70)


def test_safe_mode_3kw_cap_on_invalid_grid_sensor():
    limiters = GridAndTariffLimiters(internal_limit_a=63.0, external_limit_a=63.0)
    effective_kw, safe_mode = limiters.evaluate_total_budget_kw(
        grid_power_valid=False,
        tariff_limit_now_kw=9.5,
    )
    assert safe_mode is True
    assert effective_kw == pytest.approx(3.0)
