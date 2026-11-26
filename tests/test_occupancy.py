"""Occupancy profile: home & awake, away at work, sleep, and home-office days."""
from datetime import date, datetime, timedelta, timezone

import pytest

from custom_components.power_controller.const import DeviceType, GridMode, PhasePair
from custom_components.power_controller.occupancy import (
    STATE_AWAY_WORK,
    STATE_HOME,
    STATE_HOME_OFFICE,
    STATE_SLEEP,
    DayTimes,
    OccupancyProfile,
    parse_hhmm,
)
from custom_components.power_controller.planner import EnergyPlanner
from custom_components.power_controller.price_engine import EnergyPriceEngine, is_norwegian_workday

WED = date(2026, 11, 25)
THU = date(2026, 11, 26)
SAT = date(2026, 11, 28)


def at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone.utc)


def test_workday_free_day_and_sleep():
    p = OccupancyProfile()
    wd = is_norwegian_workday
    assert p.state_at(at(WED, 5), wd) == STATE_SLEEP
    assert p.state_at(at(WED, 7), wd) == STATE_HOME
    assert p.state_at(at(WED, 10), wd) == STATE_AWAY_WORK
    assert p.state_at(at(WED, 17), wd) == STATE_HOME
    assert p.state_at(at(WED, 23, 30), wd) == STATE_SLEEP
    assert p.state_at(at(SAT, 7), wd) == STATE_SLEEP
    assert p.state_at(at(SAT, 12), wd) == STATE_HOME
    assert p.state_at(at(date(2026, 12, 25), 12), wd) == STATE_HOME


def test_sleep_after_midnight_spills_into_next_day():
    p = OccupancyProfile(free_day=DayTimes(wake="09:00", sleep="01:00"))
    wd = is_norwegian_workday
    assert p.state_at(at(date(2026, 11, 29), 0, 30), wd) == STATE_HOME   # Sun 00:30
    assert p.state_at(at(date(2026, 11, 29), 1, 30), wd) == STATE_SLEEP


def test_home_office_one_off_and_recurring():
    p = OccupancyProfile(home_office_weekdays=["wed"])
    wd = is_norwegian_workday
    assert p.state_at(at(WED, 10), wd) == STATE_HOME_OFFICE
    assert p.state_at(at(THU, 10), wd) == STATE_AWAY_WORK
    p.set_home_office(THU, True)
    assert p.state_at(at(THU, 10), wd) == STATE_HOME_OFFICE
    p.set_home_office(WED, False)
    assert p.state_at(at(WED, 10), wd) == STATE_AWAY_WORK
    assert p.home_office_dates == {WED.isoformat(): False, THU.isoformat(): True}
    p.set_home_office(WED, True)
    assert WED.isoformat() not in p.home_office_dates
    assert p.state_at(at(SAT, 10), wd) == STATE_HOME
    assert p.upcoming_home_office(WED, wd, 8) == [WED.isoformat(), THU.isoformat(), "2026-12-02"]
    p.prune(date(2026, 12, 10))
    assert p.home_office_dates == {}


def test_comfort_masks_per_room():
    p = OccupancyProfile()
    p.set_home_office(THU, True)
    hs = p.hour_states(at(WED, 0), is_norwegian_workday, 48)
    living = p.comfort_mask(hs, ["home_awake", "home_office"])
    office = p.comfort_mask(hs, ["home_office"])
    bedroom = p.comfort_mask(hs, ["sleep"])
    assert [h for h in range(24) if living[h]] == [6, 7, 16, 17, 18, 19, 20, 21, 22]
    assert not any(office[:24])
    assert all(living[24 + h] for h in range(6, 23))
    assert [h for h in range(24) if office[24 + h]] == list(range(7, 17))
    assert bedroom[3] and not bedroom[12]
    occ = p.occupied_mask(hs)
    assert occ[24 + 12] and not occ[12]


def test_parse_and_persistence():
    assert parse_hhmm("6:05") == 365
    with pytest.raises(ValueError):
        parse_hhmm("25:00")
    p = OccupancyProfile(home_office_weekdays=["mon", "fri"])
    p.set_home_office(THU, True)
    assert OccupancyProfile.from_dict(p.to_dict()) == p
    assert OccupancyProfile.from_dict(None) == OccupancyProfile()


def test_baseload_follows_occupancy():
    eng = EnergyPriceEngine(baseload_24h_kw=0.4, baseload_daytime_kw=0.6)
    p = OccupancyProfile()
    p.set_home_office(THU, True)
    start = at(THU, 0)
    occ = p.occupied_mask(p.hour_states(start, is_norwegian_workday, 72))
    loads = eng.build_baseload_series_72h(start, [True] * 72, occupied_mask_72h=occ)
    assert loads[12] == pytest.approx(1.0)
    assert loads[3] == pytest.approx(0.4)


def test_planner_heats_office_only_on_home_office_day():
    p = OccupancyProfile()
    p.set_home_office(THU, True)
    start = at(WED, 18)
    hs = p.hour_states(start, is_norwegian_workday, 72)
    mask = p.comfort_mask(hs, ["home_office"])
    dev = {
        "entry_id": "office", "device_type": DeviceType.CLIMATE_HEATING, "zone_id": "office",
        "phase_pair": PhasePair.L1_L2, "nominal_power_kw": 1.5, "plan_priority": 1, "committed": True,
        "alpha": 1.4, "beta": 0.08, "current_temp": 17.0,
        "target_temps_72h": [21.0 if m else 17.0 for m in mask],
    }
    planner = EnergyPlanner()
    planner.optimize_72h(start, GridMode.HOME, [1.0] * 72, [0.0] * 72, [0.4] * 72, 10.0, 40.0, [dev])
    temps = planner.last_result.predicted_temps["office"]
    assert temps[16 - 1] >= 21.0 - 0.05
    assert temps[40 - 1] < 19.0
