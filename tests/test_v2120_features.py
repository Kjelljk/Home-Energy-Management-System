"""2.12.0: hot-water learning model."""
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.power_controller.const import DeviceType, GridMode, PhasePair
from custom_components.power_controller.hotwater_model import DEFAULT_LOSS_FRACTION, HotWaterModel, default_profile
from custom_components.power_controller.planner import EnergyPlanner

T0 = datetime(2026, 11, 23, 0, 0, tzinfo=timezone.utc)      # Monday


def simulate(model, days, ua_kw=0.002, use=None, p_el=2.0, ambient=20.0, step_min=15):
    """Simple tank: element keeps 55–65 °C; draws per hour from ``use`` (kWh)."""
    use = use or {7: 1.5, 8: 1.0, 19: 2.0, 20: 1.5}
    C = 0.001163 * model.volume_l
    temp, heating = 62.0, False
    t = T0
    for _ in range(int(days * 24 * 60 / step_min)):
        dt = step_min / 60
        if temp < 55:
            heating = True
        elif temp > 65:
            heating = False
        e_in = p_el * dt if heating else 0.0
        draw = use.get(t.hour, 0.0) * dt
        loss = ua_kw * (temp - ambient) * dt
        temp += (e_in - draw - loss) / C
        t += timedelta(minutes=step_min)
        model.update(t, temp, p_el if heating else 0.0, t.weekday() < 5)
    return t


def test_learns_loss_and_draw_profile():
    m = HotWaterModel(device_id="vvb", volume_l=200, seed_daily_kwh=4.0)
    simulate(m, 12)
    s = m.summary()
    assert s["loss_w_per_c"] == pytest.approx(2.0, rel=0.25)          # true UA = 2 W/°C
    assert s["daily_use_workday_kwh"] == pytest.approx(6.0, rel=0.25)   # 1.5+1+2+1.5
    prof = s["profile_workday"]
    assert max(range(24), key=lambda h: prof[h]) in (19, 20, 7)
    assert prof[3] < 0.2
    assert m.status in ("settling", "stable")
    assert m.heatup_c_per_h is not None and m.heatup_c_per_h > 5


def test_manual_reset_persistence_and_defaults():
    m = HotWaterModel(device_id="vvb", volume_l=200, seed_daily_kwh=5.1)
    assert m.loss_fraction_per_h == DEFAULT_LOSS_FRACTION
    assert sum(m.profile_workday) == pytest.approx(5.1) and m.profile_workday == default_profile(5.1)
    simulate(m, 3)
    again = HotWaterModel.from_dict(m.to_dict())
    assert again.summary() == m.summary()
    m.set_manual(loss_w_per_c=3.0, daily_kwh=8.0)
    assert m.frozen and m.status == "manual" and m.summary()["loss_w_per_c"] == pytest.approx(3.0)
    assert m.daily_use_kwh(True) == pytest.approx(8.0, abs=0.01)
    n = m.samples
    simulate(m, 1)
    assert m.daily_use_kwh(True) == pytest.approx(8.0, abs=0.01)      # frozen
    m.reset()
    assert m.samples == 0 and not m.frozen and m.device_id == "vvb"
    assert n > 0


def test_planner_uses_learned_draw_profile():
    dev = {"entry_id": "vvb", "device_type": DeviceType.CLIMATE_HOTWATER, "phase_pair": PhasePair.L2_L3,
           "nominal_power_kw": 2.0, "plan_priority": 1, "committed": True, "current_temp": 60.0,
           "tank_volume_l": 200, "min_temps_72h": [50.0] * 72, "daily_quota_hours": 3.0,
           "last_legionella_ts": T0}
    base = EnergyPlanner()
    base.optimize_72h(T0, GridMode.HOME, [1.0] * 72, [0.0] * 72, [0.3] * 72, 10.0, 63.0, [dict(dev)])
    learned = dict(dev, tank_loss_fraction=0.004, draw_kw_72h=[0.0] * 72)   # nobody uses hot water
    p = EnergyPlanner()
    plan = p.optimize_72h(T0, GridMode.HOME, [1.0] * 72, [0.0] * 72, [0.3] * 72, 10.0, 63.0, [learned])
    assert sum(plan["vvb"]) < sum(base.last_result.plans["vvb"])           # less heating needed
