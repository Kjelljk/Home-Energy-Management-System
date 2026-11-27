"""2.8.0: uncommitted devices in the plan, occupancy selector, Norgespris remaining,
lending attribution and PI governor diagnostics."""
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.power_controller.const import DeviceType, DistStatus, GridMode, PhasePair, PriceModel
from custom_components.power_controller.distributor import PowerDistributor
from custom_components.power_controller.occupancy import (
    STATE_AWAY_WORK,
    STATE_HOME,
    STATE_HOME_OFFICE,
    STATE_SLEEP,
    apply_selector,
    effective_state,
)
from custom_components.power_controller.planner import EnergyPlanner
from custom_components.power_controller.price_engine import EnergyPriceEngine

NOW = datetime(2026, 11, 25, 8, tzinfo=timezone.utc)


def _heater(committed, hold=None):
    return {"entry_id": "heat", "device_type": DeviceType.CLIMATE_HEATING, "zone_id": "z",
            "phase_pair": PhasePair.L1_L2, "nominal_power_kw": 3.0, "plan_priority": 1,
            "committed": committed, "alpha": 1.4, "beta": 0.08, "current_temp": 21.0,
            "target_temps_72h": [21.0] * 72, "oot_temp": 12.0, "hold_setpoint": hold}


def _ev(committed, connected=True, charging=True, need=20.0):
    return {"entry_id": "ev", "device_type": DeviceType.EV_CHARGER, "phase_pair": PhasePair.L2_L3,
            "nominal_power_kw": 7.36, "min_power_kw": 1.38, "plan_priority": 2, "committed": committed,
            "connected": connected, "charging": charging, "live_power_kw": 7.0 if charging else 0.0,
            "energy_needed_kwh": need, "finish_by": NOW + timedelta(hours=12)}


def test_uncommitted_heater_gets_flat_steady_state_forecast():
    p = EnergyPlanner()
    plan = p.optimize_72h(NOW, GridMode.HOME, [1.0] * 72, [0.0] * 72, [0.5] * 72, 10.0, 63.0, [_heater(False, hold=21.0)])
    # 0.08 * (21 - 0) / 1.4 = 1.2 kWh every hour, never shifted
    assert plan["heat"][:5] == [pytest.approx(1.2, abs=0.01)] * 5
    assert p.last_result.forecast_only == ["heat"]
    assert p.last_result.uncontrolled_72h[0] == pytest.approx(1.2, abs=0.01)


def test_uncommitted_ev_only_counts_when_charging():
    p = EnergyPlanner()
    plugged_only = p.optimize_72h(NOW, GridMode.HOME, [1.0] * 72, [0.0] * 72, [0.5] * 72, 10.0, 63.0,
                                  [_ev(False, connected=True, charging=False)])
    assert sum(plugged_only["ev"]) == 0.0
    charging = p.optimize_72h(NOW, GridMode.HOME, [1.0] * 72, [0.0] * 72, [0.5] * 72, 10.0, 63.0,
                              [_ev(False, connected=True, charging=True, need=20.0)])
    assert charging["ev"][:3] == [7.0, 7.0, 6.0]
    assert sum(charging["ev"]) == pytest.approx(20.0)


def test_uncommitted_load_reduces_room_for_committed_devices():
    p = EnergyPlanner()
    # uncommitted EV charges 7 kWh/h -> committed heater must fit under the 9.85 kWh cap
    plan = p.optimize_72h(NOW, GridMode.HOME, [1.0] * 72, [-15.0] * 72, [0.5] * 72, 10.0, 63.0,
                          [_heater(True), _ev(False, charging=True, need=40.0)])
    for t in range(5):
        assert plan["heat"][t] + plan["ev"][t] + 0.5 <= 9.85 + 1e-6


def test_occupancy_selector_rules():
    assert effective_state(None, STATE_SLEEP) == STATE_SLEEP
    assert effective_state(STATE_HOME, STATE_SLEEP) == STATE_HOME           # still up after bedtime
    assert effective_state(STATE_SLEEP, STATE_HOME) == STATE_SLEEP          # went to bed early
    assert effective_state(STATE_AWAY_WORK, STATE_HOME) == STATE_HOME       # late home: keep warm
    assert effective_state(STATE_AWAY_WORK, STATE_SLEEP) == STATE_AWAY_WORK
    assert effective_state(STATE_HOME_OFFICE, STATE_AWAY_WORK) == STATE_HOME_OFFICE
    hs = [{STATE_SLEEP}, {STATE_SLEEP}, {STATE_SLEEP}]
    assert apply_selector(hs, STATE_HOME) == [{STATE_HOME}, {STATE_HOME}, {STATE_SLEEP}]
    assert apply_selector(hs, None) == hs


def test_norgespris_remaining_and_projection():
    e = EnergyPriceEngine(price_model=PriceModel.NORGESPRIS)
    e.monthly_energy_kwh = 2000.0
    st = e.norgespris_status(datetime(2026, 11, 11, 0, tzinfo=timezone.utc))   # 10 days elapsed
    assert st["remaining_kwh"] == 3000.0 and st["avg_per_day_kwh"] == 200.0
    assert st["projected_reach_date"] == "2026-11-26"                          # 3000 / 200 = 15 days
    e.monthly_energy_kwh = 500.0
    assert e.norgespris_status(datetime(2026, 11, 11, 0, tzinfo=timezone.utc))["projected_reach_date"] is None


def test_lending_edges_and_governor_diagnostics():
    d = PowerDistributor()
    devices = [
        {"entry_id": "tank", "device_type": DeviceType.CLIMATE_HOTWATER, "phase_pair": PhasePair.L1_L2,
         "nominal_power_kw": 2.0, "live_power_kw": 0.0, "dist_priority": 1, "committed": True, "planned_kw": 2.0},
        {"entry_id": "floor", "device_type": DeviceType.CLIMATE_HEATING, "phase_pair": PhasePair.L1_L2,
         "nominal_power_kw": 1.8, "live_power_kw": 0.0, "dist_priority": 2, "committed": True, "planned_kw": 1.8},
    ]
    alloc = d.distribute(devices, 2.2, {"L1": 32.0, "L2": 32.0, "L3": 32.0}, {"L1": 2.0, "L2": 2.0, "L3": 2.0})
    assert alloc["tank"].status == DistStatus.REDISTRIBUTING
    assert {"from": None, "to": "floor", "kw": 0.2} in d.last_edges            # free headroom first
    assert {"from": "tank", "to": "floor", "kw": 1.6} in d.last_edges          # then the idle reservation
    assert d.last_summary["lent_kw"] == pytest.approx(1.6)
    out = d.compute_heatpump_governor_setpoint("heat", 21.0, 19.0, 60.0)
    g = d.pi_diag["heat"]
    assert g["error"] == pytest.approx(2.0) and g["setpoint_out"] == out and g["p_term"] == pytest.approx(2.4)
