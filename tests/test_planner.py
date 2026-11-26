from datetime import timedelta
import pytest

from custom_components.power_controller.const import DeviceType, GridMode, PhasePair
from custom_components.power_controller.planner import EnergyPlanner


def test_global_milp_shifts_preheat_to_give_ev_cheapest_hour(base_timestamp):
    planner = EnergyPlanner()
    prices = [0.45, 0.20, 1.50] + [1.20] * 69
    devices = [
        {
            "entry_id": "heater_1",
            "device_type": DeviceType.CLIMATE_HEATING,
            "phase_pair": PhasePair.L1_L2,
            "nominal_power_kw": 1.5,
            "plan_priority": 1,
            "committed": True,
            "alpha": 2.0,
            "beta": 0.02,
            "current_temp": 20.5,
            "target_temps_72h": [20.0, 20.0, 22.0] + [20.0] * 69,
        },
        {
            "entry_id": "ev_1",
            "device_type": DeviceType.EV_CHARGER,
            "phase_pair": PhasePair.L2_L3,
            "nominal_power_kw": 3.68,
            "min_power_kw": 1.38,
            "plan_priority": 2,
            "committed": True,
            "connected": True,
            "energy_needed_kwh": 3.68,
            "finish_by": base_timestamp + timedelta(hours=2),
        },
    ]
    plan = planner.optimize_72h(
        now=base_timestamp,
        mode=GridMode.HOME,
        prices_72h=prices,
        outdoor_temps_72h=[5.0] * 72,
        baseload_72h=[0.3] * 72,
        tariff_limit_kw=5.0,
        phase_limit_a=63.0,
        devices=devices,
    )
    res = planner.last_result
    assert res.solver == "lp"
    # comfort (22 °C) is required from the start of hour 2 -> reached by end of hour 1
    assert res.predicted_temps["heater_1"][1] >= 22.0 - 0.05
    # EV gets its energy before the deadline, never below minimum power
    assert plan["ev_1"][0] + plan["ev_1"][1] >= 3.68 - 1e-6
    assert all(p == 0.0 or p >= 1.38 for p in plan["ev_1"][:2])
    # hourly tariff respected every hour
    for t in range(3):
        assert plan["heater_1"][t] + plan["ev_1"][t] + 0.3 <= 4.85 + 1e-6
    assert plan["ev_1"][2] == pytest.approx(0.0)
    # the expensive hour 2 is avoided by the heater (pre-heating instead)
    assert plan["heater_1"][2] < 1.5


def test_legionella_7day_override_takes_top_priority(base_timestamp):
    planner = EnergyPlanner()
    prices = [1.00] * 72
    prices[3] = 0.25
    prices[4] = 0.25
    devices = [
        {
            "entry_id": "hw_tank",
            "device_type": DeviceType.CLIMATE_HOTWATER,
            "phase_pair": PhasePair.L1_L2,
            "nominal_power_kw": 2.0,
            "plan_priority": 5,
            "committed": True,
            "current_temp": None,  # no tank sensor -> quota model
            "daily_quota_hours": 0.0,
            "last_legionella_ts": base_timestamp - timedelta(days=8),
        }
    ]
    plan = planner.optimize_72h(
        now=base_timestamp,
        mode=GridMode.HOME,
        prices_72h=prices,
        outdoor_temps_72h=[5.0] * 72,
        baseload_72h=[0.3] * 72,
        tariff_limit_kw=10.0,
        phase_limit_a=63.0,
        devices=devices,
    )
    assert plan["hw_tank"][3] == pytest.approx(2.0)
    assert plan["hw_tank"][4] == pytest.approx(2.0)


def test_infeasible_constraints_trigger_deterministic_fallback(base_timestamp):
    planner = EnergyPlanner()
    devices = [
        {
            "entry_id": "ev_impossible",
            "device_type": DeviceType.EV_CHARGER,
            "phase_pair": PhasePair.L1_L2,
            "nominal_power_kw": 7.36,
            "min_power_kw": 1.38,
            "plan_priority": 1,
            "committed": True,
            "connected": True,
            "energy_needed_kwh": 50.0,
            "finish_by": base_timestamp + timedelta(hours=1),
        }
    ]
    plan = planner.optimize_72h(
        now=base_timestamp,
        mode=GridMode.HOME,
        prices_72h=[0.80] * 72,
        outdoor_temps_72h=[5.0] * 72,
        baseload_72h=[0.2] * 72,
        tariff_limit_kw=5.0,
        phase_limit_a=32.0,
        devices=devices,
    )
    assert 0.0 < plan["ev_impossible"][0] <= 4.65


def _heater(eid, zone, prio, temp=20.5):
    return {
        "entry_id": eid,
        "device_type": DeviceType.CLIMATE_HEATING,
        "zone_id": zone,
        "phase_pair": PhasePair.L1_L2,
        "nominal_power_kw": 1.0,
        "plan_priority": prio,
        "committed": True,
        "alpha": 1.4,
        "beta": 0.08,
        "current_temp": temp,
        "target_temps_72h": [21.0] * 72,
    }


def test_heaters_in_same_zone_share_one_temperature(base_timestamp):
    """v2.1 bug: two heaters in one room each had to heat 'their own' room."""
    planner = EnergyPlanner()
    devices = [_heater("a", "living", 1), _heater("b", "living", 2)]
    plan = planner.optimize_72h(
        now=base_timestamp, mode=GridMode.HOME, prices_72h=[1.0] * 72,
        outdoor_temps_72h=[0.0] * 72, baseload_72h=[0.3] * 72,
        tariff_limit_kw=10.0, phase_limit_a=63.0, devices=devices,
    )
    # steady state: 0.08*(21-0)/1.4 = 1.2 kWh/h for the ZONE, not per heater
    steady = sum(plan["a"][24:48]) + sum(plan["b"][24:48])
    assert steady == pytest.approx(24 * 1.2, rel=0.05)
    assert planner.last_result.predicted_temps["a"] == planner.last_result.predicted_temps["b"]


def test_preheat_setpoint_before_expensive_hours(base_timestamp):
    planner = EnergyPlanner()
    prices = ([0.3] * 4 + [3.0] * 4 + [1.0] * 64)
    dev = _heater("a", "z", 1)
    dev["nominal_power_kw"] = 2.0
    planner.optimize_72h(
        now=base_timestamp, mode=GridMode.HOME, prices_72h=prices,
        outdoor_temps_72h=[0.0] * 72, baseload_72h=[0.3] * 72,
        tariff_limit_kw=10.0, phase_limit_a=63.0, devices=[dev],
    )
    sp = planner.last_result.setpoints["a"]
    assert max(sp[:4]) > 21.0          # pre-heats in cheap hours
    assert max(sp[:4]) <= 21.0 + 2.0   # but never more than MAX_PREHEAT


def test_away_until_arrival_preheats_before_arrival(base_timestamp):
    planner = EnergyPlanner()
    dev = _heater("a", "z", 1, temp=12.0)
    dev["oot_temp"] = 12.0
    dev["nominal_power_kw"] = 3.0
    plan = planner.optimize_72h(
        now=base_timestamp, mode=GridMode.AWAY, prices_72h=[1.0] * 72,
        outdoor_temps_72h=[0.0] * 72, baseload_72h=[0.3] * 72,
        tariff_limit_kw=10.0, phase_limit_a=63.0, devices=[dev],
        arrival_time=base_timestamp + timedelta(hours=10),
    )
    temps = planner.last_result.predicted_temps["a"]
    assert temps[9] >= 21.0 - 0.05     # warm when the arrival hour (index 10) starts
    assert max(plan["a"][:5]) < 0.8     # only holds the away temperature early on


def test_switch_runtime_per_day_whole_hours(base_timestamp):
    planner = EnergyPlanner()
    prices = [1.0 + (t % 24) / 10 for t in range(72)]
    dev = {
        "entry_id": "sw", "device_type": DeviceType.SWITCH, "phase_pair": PhasePair.L1_L3,
        "nominal_power_kw": 1.0, "plan_priority": 3, "committed": True, "runtime_hours": 2,
        "schedule_mask_72h": [True] * 72,
    }
    plan = planner.optimize_72h(
        now=base_timestamp, mode=GridMode.HOME, prices_72h=prices,
        outdoor_temps_72h=[0.0] * 72, baseload_72h=[0.3] * 72,
        tariff_limit_kw=10.0, phase_limit_a=63.0, devices=[dev],
    )
    assert set(plan["sw"]) <= {0.0, 1.0}
    assert sum(plan["sw"][16:40]) == pytest.approx(2.0)  # full next day


def test_planner_is_fast_with_many_devices(base_timestamp):
    import time
    planner = EnergyPlanner()
    devices = [_heater(f"h{i}", f"z{i // 2}", i + 1) for i in range(10)]
    devices.append({
        "entry_id": "ev", "device_type": DeviceType.EV_CHARGER, "phase_pair": PhasePair.L2_L3,
        "nominal_power_kw": 7.36, "min_power_kw": 1.38, "plan_priority": 2, "committed": True,
        "connected": True, "energy_needed_kwh": 40.0, "finish_by": base_timestamp + timedelta(hours=24),
    })
    t0 = time.perf_counter()
    planner.optimize_72h(
        now=base_timestamp, mode=GridMode.HOME, prices_72h=[1.0 + (t % 24) / 20 for t in range(72)],
        outdoor_temps_72h=[-5.0] * 72, baseload_72h=[0.5] * 72,
        tariff_limit_kw=15.0, phase_limit_a=40.0, devices=devices,
    )
    assert time.perf_counter() - t0 < 5.0
    assert planner.last_result.solver == "lp"
