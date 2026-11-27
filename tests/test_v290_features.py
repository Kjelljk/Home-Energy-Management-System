"""2.9.0: 15-minute planning, EV without deadline + minimum SoC, command patience,
stepless/on-off heaters."""
from datetime import datetime, timedelta, timezone
import math
import time

import pytest

from custom_components.power_controller.actuation import (
    ControlSettings,
    ev_min_soc_deadline,
    should_send_command,
)
from custom_components.power_controller.const import DeviceType, DistStatus, GridMode, PhasePair
from custom_components.power_controller.distributor import PowerDistributor
from custom_components.power_controller.planner import EnergyPlanner, aggregate_hourly, slots_per_hour_for
from custom_components.power_controller.price_engine import EnergyPriceEngine, build_workday_mask

NOW = datetime(2026, 11, 25, 8, tzinfo=timezone.utc)


def _ev(**kw):
    d = {"entry_id": "ev", "device_type": DeviceType.EV_CHARGER, "phase_pair": PhasePair.L2_L3,
         "nominal_power_kw": 7.36, "min_power_kw": 1.38, "plan_priority": 2, "committed": True,
         "connected": True, "energy_needed_kwh": 30.0, "finish_by": None}
    d.update(kw)
    return d


# ---------------------------------------------------------------- 15-minute resolution
def test_quarter_prices_are_used_and_hour_cap_holds():
    hourly = [1.0] * 72
    quarters = [1.0 if q % 4 else 0.3 for q in range(288)]       # first quarter of each hour is cheap
    dev = _ev(energy_needed_kwh=3.0, finish_by=NOW + timedelta(hours=6))
    p = EnergyPlanner()
    plan = p.optimize_72h(NOW, GridMode.HOME, quarters, [0.0] * 72, [0.5] * 72, 10.0, 63.0, [dev],
                          hourly_cap_kwh_72h=[4.0] * 72)
    assert p.last_result.slots_per_hour == 4 and len(plan["ev"]) == 288
    charged = [i for i, v in enumerate(plan["ev"]) if v > 0.01]
    assert charged and all(i % 4 == 0 for i in charged)            # only the cheap quarters
    assert sum(plan["ev"]) * 0.25 == pytest.approx(3.0, abs=0.05)
    hour_kwh = aggregate_hourly(plan["ev"], 4)
    assert max(h + 0.5 for h in hour_kwh) <= 4.0 + 1e-6              # capacity per clock hour
    # the hourly model cannot see this and spreads/places by hour only
    hp = EnergyPlanner().optimize_72h(NOW, GridMode.HOME, hourly, [0.0] * 72, [0.5] * 72, 10.0, 63.0, [dev])
    assert len(hp["ev"]) == 72


def test_price_engine_builds_288_quarter_prices():
    e = EnergyPriceEngine()
    spots_q = [0.5 + 0.1 * (i % 4) for i in range(96 * 2)]
    hourly = [sum(spots_q[h * 4:(h + 1) * 4]) / 4 for h in range(48)]
    ps = e.build_72h_price_series(NOW, hourly, build_workday_mask(NOW, 72), 100.0, spot_quarters=spots_q)
    assert len(ps.total_price_15m) == 288 and len(ps.raw_spot_15m) == 288
    assert ps.raw_spot_15m[1] == pytest.approx(0.6) and ps.raw_spot_15m[200] == pytest.approx(ps.raw_spot_72h[50])
    assert slots_per_hour_for(288) == 4 and slots_per_hour_for(72) == 1


def test_fifteen_minute_plan_is_fast_with_many_devices():
    prices = [0.8 + 0.5 * math.sin(t / 96 * 2 * math.pi) for t in range(288)]
    devs = [{"entry_id": f"h{i}", "device_type": DeviceType.CLIMATE_HEATING, "zone_id": f"z{i // 2}",
             "phase_pair": PhasePair.L1_L2, "nominal_power_kw": 1.5, "plan_priority": i + 1, "committed": True,
             "alpha": 1.4, "beta": 0.08, "current_temp": 20.5, "target_temps_72h": [21.0] * 72} for i in range(10)]
    devs.append(_ev())
    t0 = time.perf_counter()
    p = EnergyPlanner()
    p.optimize_72h(NOW, GridMode.HOME, prices, [-5.0] * 72, [0.5] * 72, 15.0, 40.0, devs)
    assert p.last_result.solver == "lp"
    assert time.perf_counter() - t0 < 10.0


def test_heating_steady_state_same_in_hourly_and_quarter_resolution():
    dev = {"entry_id": "h", "device_type": DeviceType.CLIMATE_HEATING, "zone_id": "z", "phase_pair": PhasePair.L1_L2,
           "nominal_power_kw": 3.0, "plan_priority": 1, "committed": True, "alpha": 1.4, "beta": 0.08,
           "current_temp": 21.0, "target_temps_72h": [21.0] * 72}
    h = EnergyPlanner().optimize_72h(NOW, GridMode.HOME, [1.0] * 72, [0.0] * 72, [0.3] * 72, 10.0, 63.0, [dev])
    q = EnergyPlanner().optimize_72h(NOW, GridMode.HOME, [1.0] * 288, [0.0] * 72, [0.3] * 72, 10.0, 63.0, [dev])
    assert sum(h["h"][24:48]) == pytest.approx(sum(q["h"][96:192]) * 0.25, rel=0.03)


# ---------------------------------------------------------------- EV deadline & minimum SoC
def test_ev_without_deadline_charges_cheap_but_soon():
    """2.11.0: waiting is free within 24 h; only waiting beyond 24 h costs (2 øre/kWh/h)."""
    p = EnergyPlanner()
    run = lambda prices: p.optimize_72h(NOW, GridMode.HOME, prices, [0.0] * 72, [0.5] * 72, 10.0, 63.0,  # noqa: E731
                                        [_ev(energy_needed_kwh=7.0)], ev_delay_penalty=0.02)
    prices = [1.0] * 72
    prices[14] = 0.98                      # 2 øre cheaper 14 h away (e.g. tonight) -> wait for it
    assert run(prices)["ev"][14] > 6.0
    prices = [1.0] * 72
    prices[40] = 0.95                      # 5 øre cheaper 40 h away: waiting 16 h beyond 24 h costs 32 øre
    assert sum(run(prices)["ev"][:3]) == pytest.approx(7.0, abs=0.05)
    prices[40] = 0.20                      # 80 øre cheaper -> worth waiting
    assert run(prices)["ev"][40] > 6.0


def test_ev_minimum_soc_reached_before_fixed_deadline():
    prices = [2.0] * 30 + [0.2] * 42      # cheap only after 30 h
    dev = _ev(energy_needed_kwh=30.0, min_energy_kwh=12.0, min_deadline=NOW + timedelta(hours=24))
    p = EnergyPlanner()
    plan = p.optimize_72h(NOW, GridMode.HOME, prices, [0.0] * 72, [0.5] * 72, 10.0, 63.0, [dev])
    assert sum(plan["ev"][:24]) == pytest.approx(12.0, abs=0.1)    # the minimum, not more, in the expensive day
    assert sum(plan["ev"]) == pytest.approx(30.0, abs=0.1)
    assert p.last_result.ev_min_unmet["ev"] == pytest.approx(0.0)


def test_min_soc_deadline_is_fixed_per_session():
    plug = datetime(2026, 11, 25, 18, tzinfo=timezone.utc)
    assert ev_min_soc_deadline(plug, None) == plug + timedelta(hours=24)
    later = plug + timedelta(hours=5)                              # switched on later in the session
    assert ev_min_soc_deadline(plug, later) == later + timedelta(hours=24)
    assert ev_min_soc_deadline(None, None) is None


# ---------------------------------------------------------------- command patience
def test_command_patience_rules():
    t0 = NOW
    cs = ControlSettings()
    send = lambda prev, tgt, last, now, urgent=False: should_send_command(  # noqa: E731
        prev, tgt, last, now, cs.ev_min_delta_a, cs.min_interval_s, urgent)
    assert send(None, 16, None, t0)                                     # first command
    assert not send(16, 17, t0 - timedelta(minutes=10), t0)             # change too small
    assert not send(16, 20, t0 - timedelta(minutes=2), t0)              # too soon
    assert send(16, 20, t0 - timedelta(minutes=6), t0)                  # big enough and patient
    assert send(16, 15, t0 - timedelta(seconds=20), t0, urgent=True)    # urgent reduction: at once
    assert not send(16, 25, t0 - timedelta(seconds=20), t0, urgent=True)  # never urgent increases
    assert send(6, 0, t0 - timedelta(minutes=6), t0)                    # stop is always "big enough"
    assert ControlSettings.from_dict(cs.to_dict()) == cs


# ---------------------------------------------------------------- heater type
def _heater(kind, planned, live=0.0):
    return {"entry_id": "hp", "device_type": DeviceType.CLIMATE_HEATING, "phase_pair": PhasePair.L1_L2,
            "nominal_power_kw": 2.0, "live_power_kw": live, "dist_priority": 1, "committed": True,
            "planned_kw": planned, "heater_kind": kind}


def test_stepless_heater_gets_partial_power_on_off_needs_full():
    d = PowerDistributor()
    lim = {"L1": 32.0, "L2": 32.0, "L3": 32.0}
    cur = {"L1": 2.0, "L2": 2.0, "L3": 2.0}
    a = d.distribute([_heater("stepless", 0.8, live=0.8)], 1.2, lim, cur)
    assert a["hp"].status == DistStatus.PRIORITIZED and a["hp"].allowed_kw == pytest.approx(0.8)
    b = d.distribute([_heater("on_off", 0.8, live=0.0)], 1.2, lim, cur)
    assert b["hp"].status == DistStatus.HOLD                            # needs its full 2 kW
