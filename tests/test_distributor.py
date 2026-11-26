import pytest

from custom_components.power_controller.const import DeviceType, DistStatus, PhasePair
from custom_components.power_controller.distributor import PowerDistributor


def test_unused_power_lending_and_immediate_withdrawal():
    dist = PowerDistributor()
    devices = [
        {
            "entry_id": "hw_tank",
            "device_type": DeviceType.CLIMATE_HOTWATER,
            "phase_pair": PhasePair.L1_L2,
            "nominal_power_kw": 2.0,
            "live_power_kw": 0.0,
            "dist_priority": 1,
            "committed": True,
            "planned_kw": 2.0,
        },
        {
            "entry_id": "floor_heat",
            "device_type": DeviceType.CLIMATE_HEATING,
            "phase_pair": PhasePair.L1_L2,
            "nominal_power_kw": 1.8,
            "live_power_kw": 0.0,
            "dist_priority": 2,
            "committed": True,
            "planned_kw": 1.8,
        },
    ]
    alloc1 = dist.distribute(
        devices=devices,
        tariff_limit_now_kw=2.2,
        phase_limits_a={"L1": 32.0, "L2": 32.0, "L3": 32.0},
        grid_phase_currents_a={"L1": 2.0, "L2": 2.0, "L3": 2.0},
    )
    assert alloc1["hw_tank"].status == DistStatus.REDISTRIBUTING
    assert alloc1["floor_heat"].allowed_kw == pytest.approx(1.8)
    assert alloc1["floor_heat"].status == DistStatus.PRIORITIZED

    devices[0]["live_power_kw"] = 2.0
    alloc2 = dist.distribute(
        devices=devices,
        tariff_limit_now_kw=2.2,
        phase_limits_a={"L1": 32.0, "L2": 32.0, "L3": 32.0},
        grid_phase_currents_a={"L1": 10.7, "L2": 10.7, "L3": 2.0},
    )
    assert alloc2["hw_tank"].status == DistStatus.PRIORITIZED
    assert alloc2["floor_heat"].allowed_kw == pytest.approx(0.0)
    assert alloc2["floor_heat"].status == DistStatus.HOLD


def test_feedforward_ev_headroom_instant_drop_and_slewed_ramp():
    dist = PowerDistributor(ev_ramp_up_step_a=2.0)
    target_a = dist.compute_ev_feedforward_amps(
        prev_target_a=25,
        ev_live_power_kw=3.68,
        ev_phase_pair=PhasePair.L1_L2,
        grid_phase_currents_a={"L1": 24.0, "L2": 26.0, "L3": 5.0},
        phase_limits_a={"L1": 32.0, "L2": 32.0, "L3": 32.0},
        tariff_limit_now_kw=10.0,
        house_non_ev_kw=2.3,
        min_ev_a=6,
        max_ev_a=32,
    )
    assert target_a == 21

    ramped_a = dist.compute_ev_feedforward_amps(
        prev_target_a=21,
        ev_live_power_kw=4.83,
        ev_phase_pair=PhasePair.L1_L2,
        grid_phase_currents_a={"L1": 23.0, "L2": 24.0, "L3": 5.0},
        phase_limits_a={"L1": 32.0, "L2": 32.0, "L3": 32.0},
        tariff_limit_now_kw=10.0,
        house_non_ev_kw=0.7,
        min_ev_a=6,
        max_ev_a=32,
    )
    assert ramped_a == 23


def test_clamped_pi_heatpump_setpoint_governor():
    dist = PowerDistributor()
    cmd_temp = dist.compute_heatpump_governor_setpoint(
        zone_id="living_room",
        target_temp=22.0,
        external_room_temp=19.0,
        dt_seconds=60.0,
    )
    assert 22.0 < cmd_temp <= 26.0
    assert (cmd_temp * 2) % 1 == 0


def test_unplanned_load_does_not_get_free_power():
    """v2.1 bug: every committed load got power whenever there was headroom."""
    dist = PowerDistributor()
    devices = [
        {
            "entry_id": "floor_heat",
            "device_type": DeviceType.CLIMATE_HEATING,
            "phase_pair": PhasePair.L1_L2,
            "nominal_power_kw": 1.8,
            "live_power_kw": 0.0,
            "dist_priority": 1,
            "committed": True,
            "planned_kw": 0.0,
        },
        {
            "entry_id": "ev",
            "device_type": DeviceType.EV_CHARGER,
            "phase_pair": PhasePair.L2_L3,
            "nominal_power_kw": 7.36,
            "min_current_a": 6,
            "live_power_kw": 0.0,
            "dist_priority": 2,
            "committed": True,
            "planned_kw": 0.0,
            "opportunistic_need": True,
        },
    ]
    alloc = dist.distribute(
        devices=devices,
        tariff_limit_now_kw=8.0,
        phase_limits_a={"L1": 32.0, "L2": 32.0, "L3": 32.0},
        grid_phase_currents_a={"L1": 2.0, "L2": 2.0, "L3": 2.0},
    )
    assert alloc["floor_heat"].status == DistStatus.HOLD
    assert alloc["floor_heat"].allowed_kw == 0.0
    assert alloc["ev"].allowed_kw > 1.38
