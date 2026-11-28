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
    # hourly fixture: the real-time slot plan equals the hourly plan
    c.plan_slots, c.plan_spp, c.plans_15m = c.plan_result, 1, c.plans_72h
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


def test_thermal_zones_and_settings_payload(coordinator):
    import asyncio
    p = coordinator.overview_payload()
    tz = {z["zone_id"]: z for z in p["thermal_zones"]}
    assert "z1" in tz
    z = tz["z1"]
    assert z["devices"][0]["name"] == "Heat Pump" and z["room_temp"] == 20.6
    assert {"heat_loss_w_per_c", "heat_capacity_kwh_per_c", "time_constant_h", "status", "seed"} <= set(z)
    # settings carry keys for the help texts
    first = p["settings"][0]["entities"][0]
    assert set(first) >= {"id", "key"} and first["key"] == "price_model"
    assert any(e["key"] == "regulated_interval" for g in p["settings"] for e in g["entities"])

    # manual values and reset through the coordinator (planner/refresh stubbed)
    async def _noop(*a, **k):
        return None
    coordinator.async_run_planner = _noop
    coordinator.async_request_refresh = _noop
    coordinator.schedule_save = lambda: None
    asyncio.run(coordinator.async_thermal_model("z1", "set", 3.0, 90.0))
    s = coordinator.zones["z1"].summary()
    assert s["heat_capacity_kwh_per_c"] == pytest.approx(3.0, rel=0.01)
    assert s["heat_loss_w_per_c"] == pytest.approx(90.0, rel=0.01) and s["frozen"]
    asyncio.run(coordinator.async_thermal_model("z1", "reset"))
    assert not coordinator.zones["z1"].frozen and coordinator.zones["z1"].samples == 0
    with pytest.raises(ValueError):
        asyncio.run(coordinator.async_thermal_model("nope", "reset"))


def test_regulated_heater_gets_long_command_interval(coordinator):
    coordinator.device_entries["HP"].data["enable_pi_governor"] = True
    assert coordinator.command_interval_s("HP") == 1800.0
    assert coordinator.command_interval_s("EV") == 300.0


def test_ev_start_sequence_priority_now(coordinator):
    """Priority now: limit first (limit_amps, no transaction yet), then the charge switch on;
    never a current below the minimum; live power 0 while the charge switch is off."""
    import asyncio
    from custom_components.power_controller.const import DeviceAllocation, DistStatus

    calls = []

    class Services:
        def has_service(self, d, s):
            return True

        async def async_call(self, d, s, data, blocking=False, return_response=False):
            calls.append((d, s, data))

    st = {}
    old_get = coordinator.hass.states.get
    coordinator.hass.states = type("S", (), {"get": staticmethod(lambda e: st.get(e) or old_get(e))})()
    coordinator.hass.services = Services()
    coordinator.hass.async_create_task = lambda c: c.close()
    coordinator.schedule_save = lambda: None

    class _St:
        def __init__(self, s): self.state, self.attributes = s, {}
    st["switch.charge"] = _St("off")
    st["sensor.tx"] = _St("unknown")
    st["input_number.car_target"] = _St("70")
    entry = coordinator.device_entries["EV"]
    entry.data.update(ev_charge_switch="switch.charge", ev_transaction_sensor="sensor.tx",
                      ev_min_current_a=6, nominal_current_a=32, ev_target_soc_entity="input_number.car_target")
    assert coordinator.get_device_live_power_kw("EV") == 0.0          # off -> no phantom 5 kW
    assert coordinator.ev_target_soc("EV") == 70.0                     # car's own target
    coordinator.priority_now["EV"] = True
    coordinator.allocations["EV"] = DeviceAllocation("EV", 4.6, 20.0, DistStatus.PRIORITIZED)
    limits = {"L1": 32.0, "L2": 32.0, "L3": 32.0}
    coordinator.phase_currents = {"L1": 3.0, "L2": 3.0, "L3": 3.0}
    coordinator._last_ev_target_a["EV"] = 0
    asyncio.run(coordinator._actuate_ev_charger(NOW, "EV", coordinator.device_cfg("EV"), limits, 10.0))
    assert calls[0][1] == "set_charge_rate" and calls[0][2]["limit_amps"] >= 6
    assert calls[1] == ("switch", "turn_on", {"entity_id": "switch.charge"})
    # charging started, transaction appears -> TxProfile bound to it at once
    calls.clear()
    st["switch.charge"] = _St("on")
    st["sensor.tx"] = _St("812")
    asyncio.run(coordinator._actuate_ev_charger(NOW, "EV", coordinator.device_cfg("EV"), limits, 10.0))
    prof = __import__("json").loads(calls[0][2]["custom_profile"])
    assert prof["transactionId"] == 812 and prof["chargingSchedule"]["chargingSchedulePeriod"][0]["limit"] >= 6
    assert coordinator.device_attributes("EV")["target_soc_source"] == "car"


def test_fast_loop_closes_hour_with_its_own_energy(coordinator):
    """2.11.0 regression: the hour is booked with its own energy in the step that sees the
    new hour (previously ~0 kWh, so month/top-3/Norgespris never counted)."""
    import asyncio
    from custom_components.power_controller import coordinator as cmod

    class _St:
        def __init__(self, s, unit="kW"):
            self.state, self.attributes, self.last_updated = str(s), {"unit_of_measurement": unit}, NOW
    st = {"sensor.grid": _St(3.0)}
    old_get = coordinator.hass.states.get
    coordinator.hass.states = type("S", (), {"get": staticmethod(lambda e: st.get(e) or old_get(e))})()
    coordinator.hub_entry.data["active_power_sensor"] = "sensor.grid"
    tasks = []
    coordinator.hass.async_create_task = lambda c: tasks.append(c)
    coordinator.hass.services = type("Sv", (), {"has_service": lambda *a: False})()
    coordinator.schedule_save = lambda: None
    coordinator.committed.update(HP=False, EV=False)
    clock = {"t": NOW.replace(minute=59, second=0)}
    real_now = cmod.dt_util.now
    cmod.dt_util.now = lambda: clock["t"]
    cmod.dt_util.utcnow = lambda: clock["t"]
    try:
        for i in range(13):                               # 11:59:00 … 12:01:00 every 10 s
            clock["t"] = NOW.replace(minute=59, second=0) + timedelta(seconds=10 * i)
            st["sensor.grid"].last_updated = clock["t"]
            asyncio.run(coordinator._async_update_data())
    finally:
        cmod.dt_util.now = real_now
    rollovers = [t for t in tasks if getattr(t, "__name__", "") == "_async_process_hourly_rollover"
                 or "hourly_rollover" in repr(t)]
    assert rollovers, "the hour was not closed"
    closed = rollovers[0].cr_frame.f_locals["closed"]
    assert closed.hour_start.hour == 11 and closed.kwh == pytest.approx(3.0, abs=0.01)   # start-up estimate 59 min + 1 min
    async def _noop(*a, **k):
        return None
    coordinator._async_persist_state = _noop
    coordinator.async_run_price_update = _noop
    asyncio.run(rollovers[0])
    assert coordinator.price_engine.monthly_energy_kwh == pytest.approx(3.0, abs=0.01)
    assert coordinator.energy_this_hour_kwh == pytest.approx(3.0 * 60 / 3600, abs=0.005)
    for t in tasks[1:]:
        t.close()


def test_safe_mode_is_held_to_end_of_hour_and_tariff_selector_fixed(coordinator):
    import asyncio
    from custom_components.power_controller import coordinator as cmod

    class _St:
        def __init__(self, s):
            self.state, self.attributes, self.last_updated = str(s), {"unit_of_measurement": "kW"}, NOW
    st = {}
    old_get = coordinator.hass.states.get
    coordinator.hass.states = type("S", (), {"get": staticmethod(lambda e: st.get(e) or old_get(e))})()
    coordinator.hub_entry.data["active_power_sensor"] = "sensor.grid"
    coordinator.hass.async_create_task = lambda c: c.close()
    coordinator.hass.services = type("Sv", (), {"has_service": lambda *a: False})()
    coordinator.schedule_save = lambda: None
    coordinator.committed.update(HP=False, EV=False)
    clock = {"t": NOW.replace(minute=20)}
    real_now, real_utc = cmod.dt_util.now, cmod.dt_util.utcnow
    cmod.dt_util.now = lambda: clock["t"]
    cmod.dt_util.utcnow = lambda: clock["t"]
    try:
        asyncio.run(coordinator._async_update_data())                 # no grid sensor -> safe mode
        assert coordinator.safe_mode_active and coordinator.limit_now_kw <= 3.0
        clock["t"] = NOW.replace(minute=30)
        st["sensor.grid"] = _St(2.0); st["sensor.grid"].last_updated = clock["t"]
        asyncio.run(coordinator._async_update_data())                 # sensor back -> still safe mode
        assert coordinator.safe_mode_active and coordinator.safe_mode_sensor_ok
        clock["t"] = NOW.replace(minute=0) + timedelta(hours=1, seconds=5)
        st["sensor.grid"].last_updated = clock["t"]
        asyncio.run(coordinator._async_update_data())                 # new hour -> released
        assert not coordinator.safe_mode_active
    finally:
        cmod.dt_util.now, cmod.dt_util.utcnow = real_now, real_utc
    pe = coordinator.price_engine
    pe.set_selected_tariff_kw(5.0)
    pe.active_tariff_kw = 10.0
    p = coordinator.overview_payload()
    assert p["kpi"]["step_locked"] and p["kpi"]["selected_step_kw"] == 5.0


def test_ev_status_sensor_and_car_limit(coordinator):
    class _St:
        def __init__(self, s, unit=""):
            self.state, self.attributes, self.last_updated = str(s), {"unit_of_measurement": unit}, NOW
    st = {"sensor.status": _St("SuspendedEV"), "number.car_limit": _St(10, "A"), "sensor.offered": _St(16, "A"),
          "sensor.session": _St(4.2, "kWh")}
    old_get = coordinator.hass.states.get
    coordinator.hass.states = type("S", (), {"get": staticmethod(lambda e: st.get(e) or old_get(e))})()
    entry = coordinator.device_entries["EV"]
    entry.data.update(ev_status_sensor="sensor.status", ev_car_current_limit_entity="number.car_limit",
                      ev_offered_current_sensor="sensor.offered", ev_session_energy_sensor="sensor.session",
                      nominal_current_a=32)
    d = coordinator.device_cfg("EV")
    assert coordinator.ev_plugged_in(d) and not coordinator.ev_is_charging("EV", d)
    st["sensor.status"] = _St("Charging")
    assert coordinator.ev_is_charging("EV", d)
    st["sensor.status"] = _St("Available")
    assert not coordinator.ev_plugged_in(d)
    assert coordinator.ev_max_current_a(d) == 10
    assert coordinator.device_nominal_kw(d) == pytest.approx(2.3)
    a = coordinator.device_attributes("EV")
    assert a["connector_status_text"] == "Not connected" and a["offered_current_a"] == 16
    assert a["session_energy_kwh"] == 4.2 and a["entities"]["car_current_limit"] == "number.car_limit"


def test_stable_power_reading_is_used_and_labelled(coordinator):
    """2.12.1: a sensor that reports the same value (last_updated old, last_reported fresh) or that
    only reports on change must still be MEASURED – not replaced by current x 230 V."""
    class _St:
        def __init__(self, s, unit="", age_s=0, reported_age_s=None):
            self.state, self.attributes = str(s), {"unit_of_measurement": unit}
            self.last_updated = NOW - timedelta(seconds=age_s)
            if reported_age_s is not None:
                self.last_reported = NOW - timedelta(seconds=reported_age_s)
    st = {"sensor.ev_power": _St(1730, "W", age_s=900, reported_age_s=5),
          "switch.charge": _St("on"), "sensor.status": _St("Charging"),
          "sensor.ev_current": _St(7.6, "A"), "sensor.offered": _St(8, "A")}
    old_get = coordinator.hass.states.get
    coordinator.hass.states = type("S", (), {"get": staticmethod(lambda e: st.get(e) or old_get(e))})()
    coordinator._last_ev_target_a["EV"] = 25
    entry = coordinator.device_entries["EV"]
    entry.data.update(power_sensor="sensor.ev_power", ev_charge_switch="switch.charge", ev_status_sensor="sensor.status",
                      ev_current_sensor="sensor.ev_current", ev_offered_current_sensor="sensor.offered")
    assert coordinator.device_power("EV") == (pytest.approx(1.73), "measured")       # not 25 A x 230 V = 5.75
    st["sensor.ev_power"] = _St(1730, "W", age_s=3600)                                # only reports on change
    assert coordinator.device_power("EV") == (pytest.approx(1.73), "measured")
    st["sensor.ev_power"] = _St("unavailable")
    assert coordinator.device_power("EV") == (pytest.approx(7.6 * 0.23), "measured_current")
    st.pop("sensor.ev_current")
    assert coordinator.device_power("EV") == (pytest.approx(8 * 0.23), "offered_current")
    st.pop("sensor.offered")
    assert coordinator.device_power("EV") == (pytest.approx(25 * 0.23), "commanded")
    st["sensor.status"] = _St("SuspendedEV")
    assert coordinator.device_power("EV") == (0.0, "off")
    a = coordinator.device_attributes("EV")
    assert a["power_source"] == "off" and a["entities"]["power"] == "sensor.ev_power"
    assert a["entities"]["charging_current"] is None or "charging_current" in a["entities"]
