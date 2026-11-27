"""2.10.0: partial lending, calmer heat-pump setpoints, thermal model view/reset/manual."""
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.power_controller.const import DeviceType, DistStatus, GridMode, PhasePair
from custom_components.power_controller.distributor import PowerDistributor, lendable_kw
from custom_components.power_controller.planner import EnergyPlanner
from custom_components.power_controller.thermodynamics import (
    ThermalZoneModel,
    derived_parameters,
    params_from_physical,
)

NOW = datetime(2026, 11, 28, 6, tzinfo=timezone.utc)
LIM = {"L1": 32.0, "L2": 32.0, "L3": 32.0}
CUR = {"L1": 2.0, "L2": 2.0, "L3": 2.0}


# ---------------------------------------------------------------- partial lending
def test_lendable_amounts():
    assert lendable_kw(1.5, 0.6, modulating=True) == pytest.approx(1.5 - 0.6 - 0.225)   # margin 15 %
    assert lendable_kw(1.0, 0.75, modulating=True) == 0.0                              # within the margin
    assert lendable_kw(1.0, 0.0, modulating=False) == 1.0                              # on/off idle: all
    assert lendable_kw(1.0, 0.9, modulating=False) == 0.0                              # on/off running: none


def test_stepless_heat_pump_lends_unused_part_to_blocked_ev():
    d = PowerDistributor()
    devs = [
        {"entry_id": "hp", "device_type": DeviceType.CLIMATE_HEATING, "phase_pair": PhasePair.L1_L2,
         "nominal_power_kw": 1.5, "live_power_kw": 0.5, "dist_priority": 1, "committed": True,
         "planned_kw": 1.5, "heater_kind": "stepless"},
        {"entry_id": "ev", "device_type": DeviceType.EV_CHARGER, "phase_pair": PhasePair.L2_L3,
         "nominal_power_kw": 7.36, "min_current_a": 6, "live_power_kw": 0.0, "dist_priority": 2,
         "committed": True, "planned_kw": 2.0},
    ]
    # budget 2.2 kW: heat pump reserves 1.5, only 0.7 free -> EV (min 1.38) blocked without lending
    a = d.distribute(devs, 2.2, LIM, CUR)
    assert a["hp"].status == DistStatus.PRIORITIZED and a["hp"].allowed_kw == pytest.approx(1.5)
    assert a["ev"].allowed_kw >= 1.38                              # got 0.7 free + ~0.78 lent
    assert a["hp"].lent_kw > 0.5
    assert any(e["from"] == "hp" and e["to"] == "ev" for e in d.last_edges)


def test_partial_use_was_not_lent_before_but_is_now():
    d = PowerDistributor()
    hp = {"entry_id": "hp", "device_type": DeviceType.CLIMATE_HEATING, "phase_pair": PhasePair.L1_L2,
          "nominal_power_kw": 2.0, "live_power_kw": 1.0, "dist_priority": 1, "committed": True,
          "planned_kw": 2.0, "heater_kind": "stepless"}
    a = d.distribute([hp], 2.0, LIM, CUR)
    assert a["hp"].lent_kw == 0.0                                  # nobody borrowed -> nothing lent
    assert d.last_summary["lent_kw"] == 0.0


# ---------------------------------------------------------------- calmer heat pump
def test_pi_offset_hysteresis_stops_flipping():
    d = PowerDistributor(pi_kp=1.2, pi_ki=0.0)
    outs = set()
    for room in [20.78, 20.80, 20.76, 20.79, 20.81, 20.77] * 10:      # error ~0.2 °C -> offset ~0.25
        outs.add(d.compute_heatpump_governor_setpoint("hp", 21.0, room, 10))
    assert len(outs) == 1                                          # no 21.0 <-> 21.5 chatter
    held = outs.pop() - 21.0
    # a clearly larger error moves the held offset (≥ 0.75 °C away)
    out = d.compute_heatpump_governor_setpoint("hp", 21.0, 19.9, 10)   # offset 1.32
    assert out == 22.5 and out - 21.0 != held
    # a target change passes straight through with the held offset
    assert d.compute_heatpump_governor_setpoint("hp", 22.0, 20.9, 10) == 23.5


def test_preheat_is_one_step_not_a_staircase():
    out = EnergyPlanner._heating_setpoints(
        [0, 1.5, 1.5, 1.5, 1.5, 0, 0], [20, 21, 21, 21, 21, 21, 21], [20, 21.3, 21.7, 22.2, 22.6, 22.2, 21.8])
    assert out[1:5] == [22.5, 22.5, 22.5, 22.5]
    assert out[5] == 21.0


def test_quarter_plan_changes_setpoint_at_most_once_per_hour():
    dev = {"entry_id": "h", "device_type": DeviceType.CLIMATE_HEATING, "zone_id": "z", "phase_pair": PhasePair.L1_L2,
           "nominal_power_kw": 3.0, "plan_priority": 1, "committed": True, "alpha": 1.4, "beta": 0.08,
           "current_temp": 19.0, "target_temps_72h": [19.0] * 3 + [21.0] * 69}
    prices = [0.3] * 8 + [2.0] * 280
    p = EnergyPlanner()
    p.optimize_72h(NOW, GridMode.HOME, prices, [0.0] * 72, [0.3] * 72, 10.0, 63.0, [dev])
    sp = p.last_result.setpoints["h"]
    changes = sum(1 for i in range(1, 48) if sp[i] != sp[i - 1])
    assert changes <= 4


# ---------------------------------------------------------------- thermal model
def test_derived_and_inverse_parameters():
    d = derived_parameters(1.0 / 2.0, 0.05)
    assert d["heat_capacity_kwh_per_c"] == pytest.approx(2.0)
    assert d["time_constant_h"] == pytest.approx(20.0)
    assert d["heat_loss_w_per_c"] == pytest.approx(100.0)
    a, b = params_from_physical(2.0, 100.0)
    assert a == pytest.approx(0.5) and b == pytest.approx(0.05)


def test_learning_bookkeeping_reset_manual_and_persistence():
    z = ThermalZoneModel(zone_id="z", alpha=1.0, beta=0.05)
    assert z.status == "learning" and z.seed_alpha == 1.0
    t = NOW
    temp = 20.0
    for i in range(40):
        t += timedelta(minutes=15)
        heat = 1.0 if i % 2 else 0.0
        nxt = temp + (0.6 * heat - 0.04 * (temp - 0.0)) * 0.25
        z.update_rls(temp, nxt, 0.0, heat, 0.25, now=t)
        temp = nxt
    z.update_rls(20, 20.0, 19.9, 0.0, 0.25, now=t)                  # uninformative -> skipped
    assert z.samples == 40 and z.skipped == 1 and z.history and z.err_ema_c is not None
    assert z.status in ("settling", "stable")
    again = ThermalZoneModel.from_dict(z.to_dict())
    assert again.to_dict() == z.to_dict()
    z.set_manual(*params_from_physical(3.0, 80.0))
    assert z.status == "manual" and z.frozen
    a0 = z.alpha
    z.update_rls(20.0, 20.5, 0.0, 2.0, 0.25, now=t)
    assert z.alpha == a0                                           # frozen: no learning
    z.reset()
    assert z.alpha == 1.0 and z.samples == 0 and not z.frozen and z.history == []
    s = z.summary()
    assert {"heat_loss_w_per_c", "time_constant_h", "seed", "history", "status"} <= set(s)


def test_old_storage_without_bookkeeping_loads():
    z = ThermalZoneModel.from_dict({"zone_id": "z", "alpha": 0.8, "beta": 0.06})
    assert z.samples == 0 and z.seed_alpha == 0.8 and z.status == "learning"
