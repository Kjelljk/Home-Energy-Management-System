"""Click through the grid-tariff options flow with minimal HA/voluptuous stubs.

Runs only when Home Assistant is NOT installed (pure sandbox). In a real HA test
environment use pytest-homeassistant-custom-component instead.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("homeassistant") is not None, reason="real Home Assistant installed"
)


def _install_stubs() -> None:
    vol = types.ModuleType("voluptuous")

    class _Key(str):
        def __new__(cls, key, default=None, **_):
            obj = str.__new__(cls, key)
            obj.default = default
            return obj

    vol.Required = type("Required", (_Key,), {})
    vol.Optional = type("Optional", (_Key,), {})
    vol.Schema = lambda schema, *a, **k: schema
    vol.In = lambda x: x
    vol.All = lambda *a: a
    sys.modules["voluptuous"] = vol

    ha = types.ModuleType("homeassistant")
    ce = types.ModuleType("homeassistant.config_entries")

    class _Flow:
        def __init_subclass__(cls, **kw):
            super().__init_subclass__()

        def async_show_form(self, step_id, data_schema=None, errors=None, description_placeholders=None):
            return {"type": "form", "step_id": step_id, "schema": data_schema, "errors": errors or {},
                    "placeholders": description_placeholders or {}}

        def async_show_menu(self, step_id, menu_options, description_placeholders=None):
            return {"type": "menu", "step_id": step_id, "menu": list(menu_options),
                    "placeholders": description_placeholders or {}}

        def async_create_entry(self, title, data):
            return {"type": "create_entry", "data": data}

        def async_abort(self, reason):
            return {"type": "abort", "reason": reason}

    ce.ConfigFlow = _Flow
    ce.OptionsFlow = _Flow
    ce.ConfigEntry = object
    ce.ConfigFlowResult = dict
    core = types.ModuleType("homeassistant.core")
    core.callback = lambda f: f
    helpers = types.ModuleType("homeassistant.helpers")
    sel = types.ModuleType("homeassistant.helpers.selector")
    for name in ("EntitySelector", "NumberSelector", "SelectSelector", "TextSelector", "DateSelector"):
        setattr(sel, name, lambda *a, **k: ("selector", a, k))
    for name in ("EntitySelectorConfig", "NumberSelectorConfig", "SelectSelectorConfig", "TextSelectorConfig"):
        setattr(sel, name, lambda *a, **k: k)
    sel.SelectOptionDict = dict
    helpers.selector = sel
    sys.modules.update({
        "homeassistant": ha, "homeassistant.config_entries": ce, "homeassistant.core": core,
        "homeassistant.helpers": helpers, "homeassistant.helpers.selector": sel,
    })


@pytest.fixture
def flow():
    saved = dict(sys.modules)
    _install_stubs()
    sys.modules.pop("custom_components.power_controller.config_flow", None)
    cf = importlib.import_module("custom_components.power_controller.config_flow")
    from custom_components.power_controller.price_engine import EnergyPriceEngine

    applied = {}

    class FakeCoordinator:
        price_engine = EnergyPriceEngine()

        async def async_apply_grid_tariff(self, schedule):
            applied["schedule"] = schedule

    class Entry:
        data = {"entry_type": "hub"}
        options = {"x": 1}
        title = "Power Controller"

    f = cf.PowerControllerOptionsFlow(Entry())
    f._coordinator = lambda: FakeCoordinator()
    f.applied = applied
    yield f
    sys.modules.clear()
    sys.modules.update(saved)


def default(result, name):
    return next(k.default for k in result["schema"] if k == name)


def run(coro):
    return asyncio.run(coro)


def test_edit_period_add_season_and_save(flow):
    r = run(flow.async_step_init())
    assert r["type"] == "menu" and "grid_tariff" in r["menu"]
    r = run(flow.async_step_grid_tariff())
    assert "BKK 2026" in r["placeholders"]["versions"]
    r = run(flow.async_step_tariff_pick_version())
    vid = default(r, "version")
    r = run(flow.async_step_tariff_pick_version({"version": vid}))
    assert r["step_id"] == "tariff_version" and "Day" in r["placeholders"]["summary"]

    # change the day period: new price and 07–21
    r = run(flow.async_step_tariff_pick_period())
    pid = default(r, "period")
    run(flow.async_step_tariff_pick_period({"period": pid}))
    r = run(flow.async_step_tariff_period({
        "name": "Day", "price": 0.31, "days": ["mon", "tue", "wed", "thu", "fri"],
        "start_hour": 7, "end_hour": 21, "months": [], "position": 1, "delete": False,
    }))
    assert r["step_id"] == "tariff_version"

    # add a winter peak checked FIRST
    run(flow.async_step_tariff_add_period())
    r = run(flow.async_step_tariff_period({
        "name": "Winter peak", "price": 0.55, "days": ["mon", "tue", "wed", "thu", "fri"],
        "start_hour": 16, "end_hour": 20, "months": ["1", "2", "12"], "position": 1, "delete": False,
    }))
    # new capacity steps
    r = run(flow.async_step_tariff_capacity({"steps": "3 = 150\n7 = 300\nover = 900", "threshold_in_next_step": True}))
    assert r["step_id"] == "tariff_version"
    r = run(flow.async_step_tariff_save())
    assert r["type"] == "create_entry" and r["data"] == {"x": 1}   # options untouched -> no double reload

    v = flow.applied["schedule"].versions[0]
    assert [p.name for p in v.periods] == ["Winter peak", "Day"]
    assert v.periods[0].months == [1, 2, 12]
    assert v.periods[1].start_hour == 7 and v.periods[1].price == 0.31
    assert v.step_uppers() == [3.0, 7.0]


def test_validation_errors(flow):
    run(flow.async_step_tariff_add_period())
    r = run(flow.async_step_tariff_period({
        "name": "x", "price": 0.1, "days": [], "start_hour": 1, "end_hour": 2, "months": [], "position": 1,
    }))
    assert r["errors"] == {"days": "no_days"}
    r = run(flow.async_step_tariff_capacity({"steps": "hello", "threshold_in_next_step": True}))
    assert r["errors"] == {"steps": "invalid_steps"}
    r = run(flow.async_step_tariff_delete_version({"confirm": True}))
    assert r["errors"] == {"base": "last_version"}


def test_new_version_for_next_year(flow):
    r = run(flow.async_step_tariff_new_version())
    assert default(r, "valid_from") == "2027-01-01"
    r = run(flow.async_step_tariff_new_version({"name": "BKK 2027", "valid_from": "2027-01-01",
                                                 "based_on": flow._schedule().versions[0].id}))
    assert r["step_id"] == "tariff_version"
    r = run(flow.async_step_tariff_general({
        "name": "BKK 2027", "valid_from": "2027-01-01", "prices_include_vat": True,
        "default_price": 0.25, "energy_fee": 0.11, "holidays_as": "sun",
    }))
    r = run(flow.async_step_tariff_new_version({"name": "dup", "valid_from": "2027-01-01", "based_on": "flat"}))
    assert r["errors"] == {"valid_from": "duplicate_date"}
    run(flow.async_step_tariff_save())
    sched = flow.applied["schedule"]
    assert [v.name for v in sched.versions] == ["BKK 2026", "BKK 2027"]
    assert sched.versions[1].prices_include_vat is True
    assert sched.versions[0].prices_include_vat is False   # copy did not alter the original


def test_occupancy_weekdays_step(flow):
    calls = {}

    class Coord:
        from custom_components.power_controller.occupancy import OccupancyProfile
        occupancy = OccupancyProfile(home_office_weekdays=["wed"])

        async def async_set_home_office_weekdays(self, days):
            calls["days"] = days

    flow._coordinator = lambda: Coord()
    r = run(flow.async_step_init())
    assert "occupancy" in r["menu"]
    r = run(flow.async_step_occupancy())
    assert default(r, "home_office_weekdays") == ["wed"]
    r = run(flow.async_step_occupancy({"home_office_weekdays": ["fri", "mon"]}))
    assert r["type"] == "create_entry"
    assert calls["days"] == ["mon", "fri"]
