"""Smoke test of the HA-side card contract (device_attributes) and the panel feed
(overview_payload) with minimal Home Assistant stubs and a simulated house.

Skipped when real Home Assistant is installed (use pytest-homeassistant-custom-component there).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import json
import sys
import types

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("homeassistant") is not None, reason="real Home Assistant installed"
)

NOW = datetime(2026, 11, 26, 11, 20, tzinfo=timezone.utc)   # Thursday


def _stub_ha() -> None:
    def mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _):
            return cls

        def __init__(self, hass, logger, name=None, update_interval=None):
            self.hass = hass
            self.data = None

        def async_set_updated_data(self, data):
            self.data = data

    class Store:
        def __init__(self, *a, **k):
            pass

    class Registry:
        def async_get_entity_id(self, domain, platform, unique_id):
            return f"{domain}.uid_{unique_id}"

    mod("homeassistant")
    mod("homeassistant.config_entries", ConfigEntry=object)
    mod("homeassistant.core", Event=object, HomeAssistant=object, callback=lambda f: f)
    mod("homeassistant.helpers")
    mod("homeassistant.helpers.event", async_track_state_change_event=None, async_track_time_change=None,
        async_track_time_interval=None)
    mod("homeassistant.helpers.entity_registry", async_get=lambda hass: Registry())
    sys.modules["homeassistant.helpers"].entity_registry = sys.modules["homeassistant.helpers.entity_registry"]
    mod("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=DataUpdateCoordinator)
    mod("homeassistant.helpers.storage", Store=Store)
    dt = mod("homeassistant.util.dt", now=lambda: NOW, utcnow=lambda: NOW,
             parse_datetime=lambda s: datetime.fromisoformat(s) if s else None, as_local=lambda d: d)
    mod("homeassistant.util", dt=dt)


class _State:
    def __init__(self, state, attrs=None):
        self.state = str(state)
        self.attributes = attrs or {}
        self.last_updated = NOW


class _Entry:
    def __init__(self, entry_id, title, data):
        self.entry_id, self.title, self.data, self.options, self.version = entry_id, title, data, {}, 1


@pytest.fixture
def coordinator():
    saved = dict(sys.modules)
    _stub_ha()
    for name in [m for m in sys.modules if m.startswith("custom_components.power_controller.")]:
        if name.split(".")[-1] in ("coordinator", "storage"):
            sys.modules.pop(name)
    cmod = importlib.import_module("custom_components.power_controller.coordinator")
    from custom_components.power_controller.const import DeviceType, PhasePair
    from custom_components.power_controller.planner import EnergyPlanner

    states = {
        "sensor.room": _State(20.6, {"unit_of_measurement": "°C"}),
        "input_number.comfort": _State(21.5), "input_number.eco": _State(18.0),
        "sensor.soc": _State(46, {"unit_of_measurement": "%"}), "binary_sensor.plugged": _State("on"),
    }

    class Hass:
        data = {}

        class states_cls:
            pass

    hass = Hass()
    hass.states = types.SimpleNamespace(get=lambda e: states.get(e))
    hub = _Entry("HUB", "Power Controller", {"entry_type": "hub", "zone_id": "z1"})
    c = cmod.PowerControllerCoordinator(hass, hub)
    from custom_components.power_controller.thermodynamics import ThermalZoneModel
    c.zones["z1"] = ThermalZoneModel(zone_id="z1", alpha=1.4, beta=0.08)
    c.register_subentry(_Entry("HP", "Heat Pump", {
        "entry_type": "device", "device_type": DeviceType.CLIMATE_HEATING, "phase_pair": PhasePair.L1_L2,
        "nominal_power_kw": 1.5, "zone_id": "z1", "target_entity": "climate.heat_pump",
        "room_temp_sensor": "sensor.room", "comfort_temp_entity": "input_number.comfort",
        "eco_temp_entity": "input_number.eco", "comfort_during": ["home_awake", "home_office"]}))
    c.register_subentry(_Entry("EV", "Elbillader", {
        "entry_type": "device", "device_type": DeviceType.EV_CHARGER, "phase_pair": PhasePair.L2_L3,
        "nominal_power_kw": 7.36, "ev_soc_sensor": "sensor.soc", "ev_connected_sensor": "binary_sensor.plugged",
        "ev_battery_kwh": 75}))
    c.committed.update(HP=True, EV=True)
    c.finish_by["EV"] = NOW.replace(hour=7) + timedelta(days=1)
    # plan made at 10:59 -> start 10:00
    start = NOW.replace(minute=0) - timedelta(hours=1)
    c.plan_start = start
    c.plans_72h = {"HP": [0.0, 0.0, 1.5, 1.5] + [0.3] * 68, "EV": [0.0] * 12 + [6.0] * 6 + [0.0] * 54}
    c.plan_result.setpoints = {"HP": [18.0, 18.0, 22.0, 22.5] + [21.5] * 68}
    c.baseload_72h = [0.5] * 72
    c.price_series = c.price_engine.build_72h_price_series(start, [1.0] * 72, [True] * 72, 0.0)
    c._update_occupancy(start)
    yield c
    sys.modules.clear()
    sys.modules.update(saved)


def test_device_attributes_contract(coordinator):
    a = coordinator.device_attributes("HP")
    assert a["name"] == "Heat Pump" and a["ui_status"] == "waiting"
    # index fix: at 11:20 a plan starting 10:00 is at index 1 -> window starts at plan[1]
    assert a["plan_24h"][:3] == [0.0, 1.5, 1.5]
    assert a["next_action"]["running_now"] is False
    assert a["next_action"]["start"].startswith("2026-11-26T12:00")
    assert a["next_action"]["setpoint"] == 22.5
    assert a["comfort_temp"] == 21.5 and a["eco_temp"] == 18.0 and a["current_temp"] == 20.6
    assert a["entities"]["committed"] == "switch.uid_HP_committed"
    assert a["entities"]["comfort_temp"] == "input_number.comfort"
    ev = coordinator.device_attributes("EV")
    assert ev["soc"] == 46 and ev["target_soc"] == 80.0 and ev["connected"] is True
    assert ev["entities"]["finish_by"] == "datetime.uid_EV_finish_by"
    json.dumps(a), json.dumps(ev)   # must be JSON serialisable (state attributes)


def test_planned_now_uses_index(coordinator):
    assert coordinator.plan_idx(NOW) == 1
    assert coordinator._planned_setpoint("HP") == 18.0      # plan[1], not plan[0] of the previous hour


def test_overview_payload_is_json_and_complete(coordinator):
    p = coordinator.overview_payload()
    s = json.dumps(p)
    assert len(s) > 1000
    assert p["plan"]["index_now"] == 1
    assert len(p["plan"]["price_72h"]) == 72 and p["plan"]["price_72h"][0] is not None
    assert {d["name"] for d in p["devices"]} == {"Heat Pump", "Elbillader"}
    assert len(p["occupancy_days"]) == 7 and len(p["occupancy_days"][0]["quarters"]) == 96
    assert p["occupancy_days"][1]["switch"] and p["occupancy_days"][2]["switch"] is None
    assert p["norgespris"]["volume_kwh"] == 5000 and "remaining_kwh" in p["norgespris"]
    assert {"grid_limiter", "tariff_limiter", "overload", "edges", "baseload_kw"} <= set(p["flow"])
    assert set(p["history_entities"]) >= {"grid_power", "currents", "voltages", "devices"}
    assert p["occupancy_days"][0]["home_office"] is False
    assert len(p["tariff"]["today"]) == 24 and p["tariff"]["capacity_steps"]
    assert set(p["phases"]) == {"L1", "L2", "L3"}
    assert p["kpi"]["price_now"]["total"] > 0
