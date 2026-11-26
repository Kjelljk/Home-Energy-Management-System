from datetime import datetime, timezone
import pytest

from custom_components.power_controller.price_engine import EnergyPriceEngine


def test_15min_nordpool_averaging():
    engine = EnergyPriceEngine()
    hourly = engine.average_quarter_hourly_prices([0.40, 0.60, 0.80, 1.00])
    assert hourly == [pytest.approx(0.70)]


def test_norgespris_cap_and_midmonth_rollover(sample_price_config):
    engine = EnergyPriceEngine(**sample_price_config)
    result = engine.build_72h_price_series(
        start_time=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
        spot_prices_72h=[1.20] * 72,
        workday_mask_72h=[True] * 72,
        monthly_kwh_used=3995.0,
        expected_load_kwh_72h=[3.0] * 72,
    )
    assert result.spot_after_support_72h[0] == pytest.approx(0.50)
    assert result.total_price_72h[0] == pytest.approx(1.325)
    assert result.spot_after_support_72h[2] == pytest.approx(1.20)
    assert result.total_price_72h[2] == pytest.approx(2.20)


def test_peak_vs_offpeak_and_holiday_handling(sample_price_config):
    engine = EnergyPriceEngine(**sample_price_config)
    result = engine.build_72h_price_series(
        start_time=datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc),
        spot_prices_72h=[0.50] * 72,
        workday_mask_72h=[False] * 72,
        monthly_kwh_used=100.0,
        expected_load_kwh_72h=[1.0] * 72,
    )
    assert result.grid_price_72h[0] == pytest.approx(0.15)
    assert result.total_price_72h[0] == pytest.approx(1.0125)


def test_monthly_max_top3_distinct_days_and_tariff_step_up():
    engine = EnergyPriceEngine()
    engine.set_selected_tariff_kw(5.0)
    engine.record_hourly_consumption(datetime(2026, 11, 1, 17, 0, tzinfo=timezone.utc), 4.8)
    engine.record_hourly_consumption(datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc), 5.1)
    engine.record_hourly_consumption(datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc), 5.4)
    stepped_up = engine.record_hourly_consumption(
        datetime(2026, 11, 3, 19, 0, tzinfo=timezone.utc), 5.5
    )
    assert engine.monthly_max_kw == pytest.approx((5.5 + 5.4 + 4.8) / 3.0)
    assert stepped_up is True
    assert engine.active_tariff_kw == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# v2.2: price models, BKK tariff, holidays, cost-neutral capacity allowance
# ---------------------------------------------------------------------------
from datetime import date, timedelta  # noqa: E402

from custom_components.power_controller.const import PriceModel  # noqa: E402
from custom_components.power_controller.price_engine import (  # noqa: E402
    build_workday_mask,
    is_norwegian_workday,
    norwegian_public_holidays,
)


def test_norwegian_holidays_2026_2027():
    h26 = norwegian_public_holidays(2026)
    assert date(2026, 4, 2) in h26   # Maundy Thursday
    assert date(2026, 4, 3) in h26   # Good Friday
    assert date(2026, 5, 14) in h26  # Ascension Day
    assert date(2026, 5, 25) in h26  # Whit Monday
    assert date(2026, 12, 25) in h26
    assert date(2027, 3, 29) in norwegian_public_holidays(2027)  # Easter Monday 2027
    assert not is_norwegian_workday(date(2026, 5, 17))
    assert is_norwegian_workday(date(2026, 11, 26))


def test_workday_mask_uses_calendar_for_tomorrow():
    # Thu 24 Dec 2026 23:00 -> tomorrow is Christmas Day (holiday) even though it is a Friday
    mask = build_workday_mask(datetime(2026, 12, 24, 23, 0, tzinfo=timezone.utc), 30, today_override=True)
    assert mask[0] is True
    assert mask[1] is False


def test_bkk_default_tariff_night_cheaper_than_day():
    engine = EnergyPriceEngine(price_model=PriceModel.NORGESPRIS)
    start = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)  # Wednesday
    res = engine.build_72h_price_series(start, [1.0] * 72, build_workday_mask(start, 72), 100.0)
    night, day = res.total_price_72h[3], res.total_price_72h[10]
    # BKK incl. taxes & VAT: 0.2329 NOK night vs 0.4613 NOK day (+ Norgespris 0.50 NOK incl. VAT)
    assert night == pytest.approx(0.50 + 0.2329, abs=0.002)
    assert day == pytest.approx(0.50 + 0.4613, abs=0.002)
    # Saturday daytime uses the night/weekend price
    assert res.grid_price_72h[72 - 1] == pytest.approx(0.105)


def test_spot_with_stromstotte_and_plain_spot():
    start = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
    spots = [1.77] * 72
    supp = EnergyPriceEngine(price_model=PriceModel.SPOT_SUPPORT)
    r = supp.build_72h_price_series(start, spots, [True] * 72, 0.0)
    assert r.spot_after_support_72h[0] == pytest.approx(0.77 + 0.10 * 1.0)  # pay 10 % above 0.77 NOK
    spot = EnergyPriceEngine(price_model=PriceModel.SPOT)
    r2 = spot.build_72h_price_series(start, spots, [True] * 72, 0.0)
    assert r2.spot_after_support_72h[0] == pytest.approx(1.77)
    # above the 5000 kWh cap the support stops
    r3 = supp.build_72h_price_series(start, spots, [True] * 72, 6000.0)
    assert r3.spot_after_support_72h[0] == pytest.approx(1.77)


def test_spot_forecast_uses_same_weekday_last_week_scaled():
    engine = EnergyPriceEngine(price_model=PriceModel.SPOT)
    start = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
    profile = [0.5 + 0.02 * h for h in range(24)]
    for back in range(1, 8):
        engine.update_spot_history((start - timedelta(days=back)).date(), profile)
    known = [2 * x for x in profile] * 2  # today + tomorrow are twice as expensive
    prices, flags = engine.forecast_missing_spot_prices(start, known, 72)
    assert flags[:48] == [False] * 48 and all(flags[48:])
    assert prices[48 + 10] == pytest.approx(profile[10] * 2.0, rel=1e-3)


def test_cost_neutral_hourly_allowance():
    engine = EnergyPriceEngine()
    engine.set_selected_tariff_kw(10.0)
    for d, kwh in [(2, 7.0), (3, 6.5), (4, 6.0)]:
        engine.record_hourly_consumption(datetime(2026, 11, d, 18, tzinfo=timezone.utc), kwh)
    today = date(2026, 11, 26)
    assert engine.hourly_energy_allowance_kwh(today) == pytest.approx(9.85)
    engine.set_selected_tariff_kw(5.0)  # already locked into 5–10 kW this month
    assert engine.active_tariff_kw == pytest.approx(10.0)
    engine2 = EnergyPriceEngine()
    engine2.set_selected_tariff_kw(5.0)
    for d, kwh in [(2, 4.6), (3, 4.5), (4, 4.4)]:
        engine2.record_hourly_consumption(datetime(2026, 11, d, 18, tzinfo=timezone.utc), kwh)
    engine2.record_hourly_consumption(datetime(2026, 11, 26, 7, tzinfo=timezone.utc), 4.9)
    # today's 4.9 kWh is already "paid": later hours today may use it
    assert engine2.hourly_energy_allowance_kwh(today) == pytest.approx(4.9)


def test_no_step_up_before_three_distinct_days():
    engine = EnergyPriceEngine()
    engine.set_selected_tariff_kw(5.0)
    assert engine.record_hourly_consumption(datetime(2026, 11, 1, 17, tzinfo=timezone.utc), 6.0) is False
    assert engine.record_hourly_consumption(datetime(2026, 11, 2, 17, tzinfo=timezone.utc), 6.0) is False
    assert engine.active_tariff_kw == pytest.approx(5.0)
