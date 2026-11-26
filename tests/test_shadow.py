"""Shadow evaluation: Norgespris vs. spot (+ support) with an optimiser planning for spot."""
from datetime import datetime, timedelta, timezone
import math

import pytest

from custom_components.power_controller.const import DeviceType, GridMode, PhasePair, PriceModel
from custom_components.power_controller.planner import EnergyPlanner
from custom_components.power_controller.price_engine import EnergyPriceEngine, build_workday_mask
from custom_components.power_controller.shadow import SHADOW_OFF, ShadowEvaluator, hour_cost

START = datetime(2026, 11, 23, 0, tzinfo=timezone.utc)  # Monday


def spot_at(t: int) -> float:
    """Volatile spot price: cheap nights, expensive morning/evening peaks."""
    h = t % 24
    return 0.6 + 0.9 * math.exp(-((h - 8) ** 2) / 4) + 1.4 * math.exp(-((h - 18) ** 2) / 4) - (0.3 if h < 5 else 0.0)


def test_hour_cost_models():
    real = EnergyPriceEngine(price_model=PriceModel.NORGESPRIS)
    ts = START.replace(hour=10)
    e, g = hour_cost(real, ts, 2.0, 2.0, True, 0.0)
    assert e == pytest.approx(2.0 * 0.40 * 1.25)          # Norgespris, flat
    assert g == pytest.approx(2.0 * (0.2877 + 0.0813) * 1.25)
    sup = EnergyPriceEngine(price_model=PriceModel.SPOT_SUPPORT)
    e2, g2 = hour_cost(sup, ts, 2.0, 2.0, True, 0.0)
    assert e2 == pytest.approx(2.0 * (0.77 + 0.10 * (2.0 - 0.77)) * 1.25)
    assert g2 == pytest.approx(g)                          # same grid tariff in both


def _house_specs(temp, ev_connected, ev_need):
    return [
        {"entry_id": "heat", "device_type": DeviceType.CLIMATE_HEATING, "zone_id": "z",
         "phase_pair": PhasePair.L1_L2, "nominal_power_kw": 3.0, "plan_priority": 1, "committed": True,
         "alpha": 1.4, "beta": 0.08, "heating_coefficient": 1.0, "current_temp": temp,
         "target_temps_72h": [21.0] * 72, "oot_temp": 12.0},
        {"entry_id": "ev", "device_type": DeviceType.EV_CHARGER, "phase_pair": PhasePair.L2_L3,
         "nominal_power_kw": 7.36, "min_power_kw": 1.38, "plan_priority": 2, "committed": True,
         "connected": ev_connected, "energy_needed_kwh": ev_need, "finish_by": None},
    ]


def simulate(hours=72):
    """Real house runs the Norgespris plan; the shadow plans for spot + support."""
    real = EnergyPriceEngine(price_model=PriceModel.NORGESPRIS)
    real.set_selected_tariff_kw(10.0)
    shadow = ShadowEvaluator(PriceModel.SPOT_SUPPORT)
    p_real, p_shadow = EnergyPlanner(), EnergyPlanner()
    temp, ev_need = 20.5, 30.0
    for t in range(hours):
        now = START + timedelta(hours=t)
        connected = (t % 24) >= 17 or (t % 24) < 7              # car home 17–07
        if (t % 24) == 17:
            ev_need = 30.0                                       # arrives needing 30 kWh
        deadline = now.replace(hour=7) + timedelta(days=1 if now.hour >= 7 else 0)
        specs = _house_specs(temp, connected, ev_need if connected else 0.0)
        for s in specs:
            if s["entry_id"] == "ev":
                s["finish_by"] = deadline
        spots = [spot_at(t + k) for k in range(72)]
        wd = build_workday_mask(now, 72)
        base = [0.6] * 72
        out = [0.0] * 72
        ps_real = real.build_72h_price_series(now, spots, wd, real.monthly_energy_kwh)
        plan = p_real.optimize_72h(now, GridMode.HOME, ps_real.total_price_72h, out, base, 10.0, 40.0, specs,
                                   hourly_cap_kwh_72h=real.hourly_cap_series_72h(now))
        s_eng = shadow.shadow_engine(real)
        s_specs = shadow.shadow_specs(specs)
        ps_sh = s_eng.build_72h_price_series(now, spots, wd, s_eng.monthly_energy_kwh)
        s_plan = p_shadow.optimize_72h(now, GridMode.HOME, ps_sh.total_price_72h, out, base, 10.0, 40.0, s_specs,
                                       hourly_cap_kwh_72h=s_eng.hourly_cap_series_72h(now))
        act = {k: float(v[0] or 0.0) for k, v in plan.items()}
        measured = 0.6 + sum(act.values())
        shadow.close_hour(now, real, spots[0], False, wd[0], measured, act,
                          {k: v[0] for k, v in s_plan.items()}, specs)
        real.record_hourly_consumption(now, measured)
        temp = (1 - 0.08) * temp + 1.4 * act["heat"]
        if connected:
            ev_need = max(0.0, ev_need - act["ev"])
    return shadow, real


def test_end_to_end_three_days():
    shadow, real = simulate(72)
    s = shadow.month_summary(real)
    assert s["hours"] == 72
    assert s["passive_spot"]["kwh"] == pytest.approx(s["actual"]["kwh"], rel=1e-9)
    assert s["optimiser_gain_nok"] > 0
    assert s["optimised_spot"]["energy_nok"] < s["passive_spot"]["energy_nok"]
    assert abs(s["zone_offset_c"].get("z", 0.0)) < 1.5
    assert s["ev_shortfall_kwh"] < 1.0
    assert set(s) >= {"benefit_nok", "benefit_passive_nok", "actual", "optimised_spot"}
    # open EV session at the end (car still plugged in) is valued, not ignored
    assert s["optimised_spot"]["pending_kwh"] == pytest.approx(-sum(shadow.ev_balance_kwh.values()), abs=0.5)
    assert s["optimised_spot"]["pending_nok"] >= 0


def test_month_rollover_history_and_persistence():
    real = EnergyPriceEngine()
    sh = ShadowEvaluator(PriceModel.SPOT)
    for d, h in [(30, 22), (30, 23)]:
        sh.close_hour(datetime(2026, 11, d, h, tzinfo=timezone.utc), real, 1.0, False, True, 2.0, {}, {}, [])
    sh.close_hour(datetime(2026, 12, 1, 0, tzinfo=timezone.utc), real, 1.0, True, False, 1.0, {}, {}, [])
    assert "2026-11" in sh.history
    assert sh.history["2026-11"]["hours"] == 2
    assert sh.hours == 1 and sh.hours_estimated_spot == 1
    again = ShadowEvaluator.from_dict(sh.to_dict())
    assert again.to_dict() == sh.to_dict()
    assert again.rolling_12m(real)["months"] == 2


def test_disabled_does_nothing():
    sh = ShadowEvaluator(SHADOW_OFF)
    sh.close_hour(START, EnergyPriceEngine(), 1.0, False, True, 3.0, {}, {}, [])
    assert sh.hours == 0 and sh.actual.kwh == 0.0


def test_ev_shortfall_is_settled_at_shadow_price():
    real = EnergyPriceEngine()
    sh = ShadowEvaluator(PriceModel.SPOT)
    spec = {"entry_id": "ev", "device_type": DeviceType.EV_CHARGER, "committed": True, "connected": True}
    sh.close_hour(START, real, 1.0, False, True, 7.5, {"ev": 7.0}, {"ev": 0.0}, [spec])
    assert sh.ev_balance_kwh["ev"] == pytest.approx(-7.0)
    sh.close_hour(START + timedelta(hours=1), real, 1.0, False, True, 0.5, {"ev": 0.0}, {"ev": 0.0},
                  [{**spec, "connected": False}])
    assert sh.ev_shortfall_kwh == pytest.approx(7.0)
    assert sh.optimised.ev_settlement_nok > 0
    assert "ev" not in sh.ev_balance_kwh


def test_zone_offset_carried_into_shadow_specs():
    real = EnergyPriceEngine()
    sh = ShadowEvaluator()
    spec = _house_specs(20.0, False, 0.0)[0]
    sh.close_hour(START, real, 1.0, False, True, 2.0, {"heat": 1.0}, {"heat": 0.0}, [spec])
    assert sh.zone_offset_c["z"] == pytest.approx(-1.4)
    assert sh.shadow_specs([spec])[0]["current_temp"] == pytest.approx(18.6)
