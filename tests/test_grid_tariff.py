"""User-editable grid tariff: periods, days, hours, months, holidays, versions, steps."""
from datetime import date, datetime, timezone

import pytest

from custom_components.power_controller.grid_tariff import (
    CapacityStep,
    GridTariffSchedule,
    TariffPeriod,
    TariffVersion,
    preset,
    steps_from_text,
    steps_to_text,
)
from custom_components.power_controller.price_engine import EnergyPriceEngine, build_workday_mask


def ts(y, m, d, h):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def test_bkk_preset_matches_published_prices_incl_vat_and_fees():
    v = preset("bkk_2026")
    day, _ = v.energy_price(ts(2026, 11, 25, 10), False, 25.0)   # Wednesday 10:00
    night, _ = v.energy_price(ts(2026, 11, 25, 23), False, 25.0)
    sat, _ = v.energy_price(ts(2026, 11, 28, 10), False, 25.0)   # Saturday
    fee = v.energy_fee_ex_vat(25.0)
    assert (day + fee) * 1.25 == pytest.approx(0.4613, abs=1e-4)
    assert (night + fee) * 1.25 == pytest.approx(0.2329, abs=1e-4)
    assert sat == pytest.approx(night)
    assert v.monthly_capacity_ex_vat(4.2, 25.0) * 1.25 == pytest.approx(250.0)


def test_first_matching_period_wins_and_wrapping_hours():
    v = TariffVersion(
        name="x", valid_from="2026-01-01", default_price=0.10,
        periods=[
            TariffPeriod(name="Peak", price=0.50, days=["mon", "tue", "wed", "thu", "fri"], start_hour=17, end_hour=20),
            TariffPeriod(name="Day", price=0.30, days=["mon", "tue", "wed", "thu", "fri"], start_hour=6, end_hour=22),
            TariffPeriod(name="Night", price=0.05, days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"], start_hour=22, end_hour=6),
        ],
    )
    assert v.energy_price(ts(2026, 11, 25, 18), False, 25)[1] == "Peak"
    assert v.energy_price(ts(2026, 11, 25, 12), False, 25)[1] == "Day"
    assert v.energy_price(ts(2026, 11, 25, 23), False, 25)[1] == "Night"
    assert v.energy_price(ts(2026, 11, 25, 3), False, 25)[1] == "Night"
    assert v.energy_price(ts(2026, 11, 28, 12), False, 25)[1] == "Default"  # Saturday daytime


def test_seasonal_months_and_holiday_rules():
    winter = TariffPeriod(name="Winter day", price=0.40, start_hour=6, end_hour=22, months=[11, 12, 1, 2, 3])
    v = TariffVersion(name="x", valid_from="2026-01-01", default_price=0.10, periods=[winter])
    assert v.energy_price(ts(2026, 11, 25, 10), False, 25)[0] == pytest.approx(0.40)
    assert v.energy_price(ts(2026, 6, 10, 10), False, 25)[0] == pytest.approx(0.10)
    # Ascension Day 14 May 2026 is a Thursday; holidays -> Sunday prices
    v.months = []
    winter.months = []
    assert v.energy_price(ts(2026, 5, 14, 10), True, 25)[0] == pytest.approx(0.10)
    v.holidays_as = "none"
    assert v.energy_price(ts(2026, 5, 14, 10), True, 25)[0] == pytest.approx(0.40)


def test_prices_entered_incl_vat_are_normalised():
    v = TariffVersion(name="x", valid_from="2026-01-01", default_price=0.25, energy_fee=0.125,
                      prices_include_vat=True, capacity_steps=[CapacityStep(0, 500)])
    assert v.energy_price(ts(2026, 3, 3, 3), False, 25)[0] == pytest.approx(0.20)
    assert v.energy_fee_ex_vat(25) == pytest.approx(0.10)
    assert v.monthly_capacity_ex_vat(3.0, 25) == pytest.approx(400.0)


def test_new_year_version_switch_inside_72h_horizon():
    old = preset("bkk_2026")
    new = old.copy_as("BKK 2027", "2027-01-01")
    new.periods[0].price = 0.40
    new.default_price = 0.20
    engine = EnergyPriceEngine(grid_tariff=GridTariffSchedule([new, old]))  # unsorted on purpose
    start = ts(2026, 12, 31, 12)
    res = engine.build_72h_price_series(start, [1.0] * 72, build_workday_mask(start, 72), 0.0)
    assert res.grid_price_72h[0] == pytest.approx(0.2877)  # Thu 31 Dec 12:00, old day price
    assert res.grid_price_72h[11] == pytest.approx(0.105)  # Thu 31 Dec 23:00, old night price
    assert res.grid_price_72h[13] == pytest.approx(0.20)   # Fri 1 Jan 01:00, new version
    assert res.grid_price_72h[22] == pytest.approx(0.20)   # Fri 1 Jan 10:00 = holiday -> Sunday price
    assert res.grid_period_72h[22] == "Default"


def test_capacity_threshold_rule_and_text_roundtrip():
    v = preset("bkk_2026")
    assert v.step_for(5.0).upper_kw == 10          # threshold counted in next step
    v.threshold_in_next_step = False
    assert v.step_for(5.0).upper_kw == 5
    assert v.step_for(250.0).upper_kw == 0         # "over 100 kW"
    text = steps_to_text(v.capacity_steps)
    assert "over = 5520" in text
    assert steps_from_text(text) == sorted(v.capacity_steps, key=lambda s: (s.upper_kw <= 0, s.upper_kw))
    assert steps_from_text("2 kW: 155 kr; 5=250\nover 415")[2].upper_kw == 0
    with pytest.raises(ValueError):
        steps_from_text("2 = 3 = 4")


def test_schedule_serialisation_roundtrip_and_validation():
    sched = GridTariffSchedule([preset("bkk_2026"), preset("flat", "2027-01-01")])
    again = GridTariffSchedule.from_list(sched.to_list())
    assert again.to_list() == sched.to_list()
    assert again.version_for(date(2027, 6, 1)).name == "Flat"
    bad = preset("flat")
    bad.periods = [TariffPeriod(name="x", price=-1, days=[])]
    assert set(bad.validate()) == {"negative_price", "no_days"}


def test_engine_uses_tariff_steps_for_step_up():
    v = TariffVersion(name="x", valid_from="2026-01-01", default_price=0.1,
                      capacity_steps=[CapacityStep(3, 100), CapacityStep(6, 200), CapacityStep(0, 400)])
    engine = EnergyPriceEngine(grid_tariff=GridTariffSchedule([v]))
    engine.set_selected_tariff_kw(3.0)
    for d in (2, 3, 4):
        engine.record_hourly_consumption(ts(2026, 11, d, 18), 3.5)
    assert engine.active_tariff_kw == pytest.approx(6.0)   # custom steps, not 5/10
