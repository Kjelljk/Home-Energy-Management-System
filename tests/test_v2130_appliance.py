"""2.13.0: household appliances (read only) – state, delayed start, program register, plan."""
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.power_controller.appliance_model import (
    ApplianceModel,
    classify,
    detect_roles,
    parse_duration_h,
)
from custom_components.power_controller.const import DeviceType, GridMode, PhasePair
from custom_components.power_controller.planner import EnergyPlanner

T0 = datetime(2026, 11, 28, 8, 0, tzinfo=timezone.utc)


def test_parsing_and_states():
    assert parse_duration_h("17:42") == pytest.approx(17.7)          # Home Connect "Start time" = in 17 h 42 min
    assert parse_duration_h("0:00") is None
    assert parse_duration_h("5400", "s") == pytest.approx(1.5)        # oven "Start In Relative"
    assert parse_duration_h("90", "min") == pytest.approx(1.5)
    assert classify("Delayed Start") == "delayed" and classify("Run") == "running"
    assert classify("BSH.Common.EnumType.OperationState.Run") == "running"
    assert classify("Ready") == "idle" and classify("Inactive") == "idle" and classify("Finished") == "finished"
    assert classify("Spin") == "running" and classify("wash_cycle_x", extra_running=("wash_cycle_x",)) == "running"


def test_roles_detected_from_home_connect_names():
    ents = [("sensor.siemens_dishwasher_operation_state", ""), ("sensor.siemens_dishwasher_start_time", ""),
            ("sensor.finish_at", "finish_at"), ("sensor.siemens_dishwasher_active_program", ""),
            ("select.siemens_dishwasher_programs", ""), ("sensor.siemens_dishwasher_selected_program", ""),
            ("sensor.siemens_dishwasher_current_programme_progress", ""), ("binary_sensor.siemens_dishwasher_door", ""),
            ("sensor.siemens_oven_micro_start_in_relative", ""), ("sensor.siemens_oven_micro_remaining_program_time", "")]
    r = detect_roles(ents)
    assert r["operation_state"] == "sensor.siemens_dishwasher_operation_state"
    assert r["start_in"] == "sensor.siemens_oven_micro_start_in_relative"      # most specific hint first
    assert r["finish_at"] == "sensor.finish_at" and r["remaining"].endswith("remaining_program_time")
    assert r["progress"].endswith("current_programme_progress") and r["door"].startswith("binary_sensor.")


def _run(m, start, minutes, program, kw_of):
    t = start
    for i in range(int(minutes * 6)):
        t = start + timedelta(seconds=10 * i)
        m.update(t, "running", program, kw_of(i / 6))
    return m.update(t + timedelta(seconds=10), "idle", None, 0.0)


def test_program_register_learns_energy_duration_and_profile():
    m = ApplianceModel(device_id="dw", default_kwh=1.0, default_duration_h=2.0)
    # Eco 50: 2 kW heating the first 20 min, 0.1 kW for 2 h 10 min -> ≈ 0.88 kWh in 2.5 h
    kw = lambda mins: 2.0 if mins < 20 else 0.1  # noqa: E731
    done = _run(m, T0, 150, "Eco 50°C", kw)
    assert done["program"] == "Eco 50°C" and done["kwh"] == pytest.approx(0.88, abs=0.03)
    st = m.stats("Eco 50°C")
    assert st.runs == 1 and st.duration_h == pytest.approx(2.5, abs=0.02)
    assert sum(st.profile) == pytest.approx(1.0) and st.profile[0] > st.profile[-1] * 5   # heat first
    _run(m, T0 + timedelta(days=1), 150, "Eco 50°C", lambda mins: 2.2 if mins < 20 else 0.1)
    assert m.stats("Eco 50°C").runs == 2 and 0.88 < m.stats("Eco 50°C").kwh < 0.96
    assert m.stats("Super 60°C").kwh == 1.0                               # unknown -> defaults
    m.set_manual("Super 60°C", 1.4, 1.2)
    assert m.stats("Super 60°C").kwh == 1.4 and m.stats("Super 60°C").manual
    again = ApplianceModel.from_dict(m.to_dict())
    assert again.summary() == m.summary()


def test_expected_load_for_delayed_start_and_running():
    m = ApplianceModel(device_id="dw")
    m.set_manual("Eco 50°C", 1.0, 2.0)
    plan_start = T0
    # delayed: starts in 17 h 42 min -> load between 01:42 and 03:42 next day, total 1 kWh
    q = m.expected_load(T0 + timedelta(minutes=5), plan_start, 288, 0.25, "delayed", "Eco 50°C",
                        T0 + timedelta(minutes=5, hours=17.7), None, None)
    assert sum(q) * 0.25 == pytest.approx(1.0, abs=0.01)
    first = next(i for i, v in enumerate(q) if v > 0)
    assert first == int((5 / 60 + 17.7) * 4)
    # running, half done: half the energy remains, starting now
    q2 = m.expected_load(T0, plan_start, 288, 0.25, "running", "Eco 50°C", None, 1.0, 50.0)
    assert sum(q2) * 0.25 == pytest.approx(0.5, abs=0.02) and q2[0] > 0
    assert sum(m.expected_load(T0, plan_start, 288, 0.25, "idle", "Eco 50°C", None, None, None)) == 0


def test_planner_plans_around_a_delayed_dishwasher():
    q = [0.0] * 288
    for i in range(8, 12):                    # 10:00–11:00: 4 kW appliance
        q[i] = 4.0
    appl = {"entry_id": "dw", "device_type": DeviceType.APPLIANCE, "phase_pair": PhasePair.L1_L2,
            "nominal_power_kw": 2.0, "committed": True, "fixed_load_15m": q}
    ev = {"entry_id": "ev", "device_type": DeviceType.EV_CHARGER, "phase_pair": PhasePair.L2_L3,
          "nominal_power_kw": 7.36, "min_power_kw": 1.38, "plan_priority": 1, "committed": True,
          "connected": True, "energy_needed_kwh": 8.0, "finish_by": T0 + timedelta(hours=4)}
    prices = [1.0] * 288
    for i in range(8, 12):
        prices[i] = 0.1                       # the cheapest hour is the dishwasher's hour
    p = EnergyPlanner()
    plan = p.optimize_72h(T0, GridMode.HOME, prices, [0.0] * 72, [0.5] * 72, 10.0, 63.0, [appl, ev],
                          hourly_cap_kwh_72h=[6.0] * 72)
    assert "dw" in p.last_result.forecast_only                              # never shifted or controlled
    assert sum(plan["dw"][8:12]) * 0.25 == pytest.approx(4.0)
    hour2 = (sum(plan["ev"][8:12]) + sum(plan["dw"][8:12])) * 0.25 + 0.5
    assert hour2 <= 6.0 + 1e-6                                             # EV fits around it
