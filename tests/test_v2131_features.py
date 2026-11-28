"""2.13.1: program names, grid-tariff period editing, EV status vs. measurement checks."""
from datetime import datetime, timezone

import pytest

from custom_components.power_controller.appliance_model import ApplianceModel, pretty_program
from custom_components.power_controller.card_model import ev_checks
from custom_components.power_controller.grid_tariff import GridTariffSchedule, TariffPeriod, edit_period, preset


# ---------------------------------------------------------------- program names
def test_readable_program_names_and_own_names():
    assert pretty_program("Dishcare.Dishwasher.Program.Eco50") == "Eco 50"
    assert pretty_program("LaundryCare.Washer.Program.Cotton") == "Cotton"
    assert pretty_program("BSH.Common.Program.Favorite.001") == "Favorite 001"
    assert pretty_program("quick_wash") == "Quick wash"
    assert pretty_program("Eco 50°C") == "Eco 50°C" and pretty_program("22") == "22"
    m = ApplianceModel(device_id="wm")
    m.set_manual("22", 0.9, 2.5)
    assert m.label("22") == "22"
    m.set_name("22", "  Bomull 40 ")
    assert m.label("22") == "Bomull 40" and m.label("Dishcare.Dishwasher.Program.Eco50") == "Eco 50"
    row = m.summary()["programs"][0]
    assert row["program"] == "22" and row["name"] == "Bomull 40" and row["named"] is True
    again = ApplianceModel.from_dict(m.to_dict())
    assert again.label("22") == "Bomull 40"
    m.set_name("22", "")
    assert m.label("22") == "22" and "22" not in m.names
    with pytest.raises(ValueError):
        m.set_name("", "x")
    assert ApplianceModel.from_dict({"device_id": "old"}).names == {}        # stored by 2.13.0


# ---------------------------------------------------------------- grid tariff period edit
def _sched():
    return GridTariffSchedule([preset("bkk_2026")])


def test_edit_period_hours_days_months_and_price():
    sched = _sched()
    v = sched.versions[0]
    pid = v.periods[0].id
    assert edit_period(sched, v.id, pid, {"price": 0.31, "days": ["sun", "mon", "tue", "wed", "thu", "fri"],
                                          "start_hour": 7, "end_hour": 21, "months": [12, 1, 2]}) == []
    p = v.periods[0]
    assert (p.price, p.start_hour, p.end_hour, p.months) == (0.31, 7, 21, [1, 2, 12])
    assert p.days == ["mon", "tue", "wed", "thu", "fri", "sun"]             # normalised order
    ts = datetime(2026, 12, 6, 10, tzinfo=timezone.utc)                       # Sunday in December
    assert v.energy_price(ts, False, 25)[1] == "Day"
    assert v.energy_price(ts.replace(month=6, day=7), False, 25)[1] == "Default"   # June: not in months
    assert v.energy_price(ts.replace(hour=6), False, 25)[1] == "Default"           # 06 < from 07
    assert edit_period(sched, v.id, pid, {"months": list(range(1, 13))}) == [] and p.months == []


def test_edit_period_rejects_bad_input_without_changes():
    sched = _sched()
    v = sched.versions[0]
    p = v.periods[0]
    before = TariffPeriod(**{**p.__dict__})
    assert edit_period(sched, v.id, p.id, {"days": []}) == ["no_days"]
    assert edit_period(sched, v.id, p.id, {"start_hour": 24, "price": -1}) == ["invalid_hours", "invalid_price"]
    assert edit_period(sched, v.id, p.id, {"months": [13], "name": " "}) == ["empty_name", "invalid_months"]
    assert edit_period(sched, v.id, "nope", {}) == ["unknown_period"]
    assert edit_period(sched, "nope", p.id, {}) == ["unknown_version"]
    assert p.__dict__ == before.__dict__


# ---------------------------------------------------------------- EV checks
def test_ev_checks_status_versus_measurement():
    ok = ev_checks("Charging", "Charging", True, 7.2, 31.2, 32, 32, True)
    assert ok == {}
    w = ev_checks("Charging", "Charging", True, 0.0, None, None, None, True)
    assert set(w) == {"charging_no_power"} and "power sensor shows 0.00 kW" in w["charging_no_power"]
    w = ev_checks("SuspendedEV", "Paused by the car", False, None, 7.6, None, None, True)
    assert set(w) == {"power_not_charging"} and "charging-current sensor" in w["power_not_charging"]
    w = ev_checks("Charging", "Charging", True, 5.75, 7.6, 8, 8, True)     # wrong kW sensor (another device)
    assert set(w) == {"power_current_mismatch"}
    w = ev_checks("Charging", "Charging", True, 3.7, 16.0, 32, 16, True)    # command not followed
    assert set(w) == {"offered_not_commanded"}
    assert ev_checks("Charging", "Charging", True, 3.7, 16.0, 32, 16, False) == {}   # uncommitted: not checked
    assert ev_checks(None, None, None, 3.0, None, None, None, True) == {}            # no status sensor
