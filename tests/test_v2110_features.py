"""2.11.0: hourly energy accounting, month rebuild from statistics, notifications,
15-minute next action, EV delay only beyond 24 h."""
from datetime import datetime, time, timedelta, timezone

import pytest

from custom_components.power_controller.card_model import next_action
from custom_components.power_controller.energy_meter import HourEnergyTracker, rebuild_month
from custom_components.power_controller.notifier import (
    ACTION_IGNORE,
    ACTION_PRIORITY_NOW,
    NotifySettings,
    Notifier,
    in_quiet,
)
from custom_components.power_controller.price_engine import EnergyPriceEngine

T0 = datetime(2026, 11, 27, 21, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- hourly energy
def _run(tr, start, minutes, kw, step_s=10, sensor=None):
    closed = []
    for i in range(int(minutes * 60 / step_s)):
        now = start + timedelta(seconds=i * step_s)
        c = tr.update(now, kw(now) if callable(kw) else kw, *(sensor(now) if sensor else (None, None)))
        if c:
            closed.append(c)
    return closed


def test_integrated_hour_matches_power_and_closes_with_its_own_energy():
    tr = HourEnergyTracker()
    closed = _run(tr, T0, 60 + 10, 3.0)            # 3 kW for 70 minutes
    assert len(closed) == 1
    assert closed[0].hour_start == T0 and closed[0].kwh == pytest.approx(3.0, abs=0.01)
    assert tr.energy_this_hour_kwh == pytest.approx(3.0 * (10 * 60 - 10) / 3600, abs=0.01)
    assert tr.avg_60min_kw(T0 + timedelta(minutes=70)) == pytest.approx(3.0, abs=0.01)


def test_the_bug_scenario_meter_updates_after_the_hour():
    """Total meter published once per hour, after the hour change: the tracker ignores it."""
    tr = HourEnergyTracker()
    closed = _run(tr, T0 + timedelta(minutes=50), 20, 2.4)
    assert closed[0].kwh == pytest.approx(2.4 * 10 / 60 + 2.4 * 50 / 60, abs=0.02)   # start-up estimate + 10 min
    assert tr.energy_this_hour_kwh == pytest.approx(2.4 * 10 / 60, abs=0.01)          # 0.4, not 3.6


def test_hour_sensor_used_only_after_it_has_reset():
    tr = HourEnergyTracker()
    # utility meter: still shows the previous hour's 3.6 kWh for 30 s after the hour, then resets
    def sensor(now):
        if now < T0 + timedelta(seconds=30):
            return 3.6, T0 - timedelta(seconds=5)
        return 0.02 + (now - T0).total_seconds() / 3600 * 2.0, now
    _run(tr, T0, 0.4, 2.0, sensor=sensor)
    assert tr.source == "integrated"                                     # stale 3.6 not trusted
    _run(tr, T0 + timedelta(seconds=30), 5, 2.0, sensor=sensor)
    assert tr.source == "hour_sensor" and tr.energy_this_hour_kwh == pytest.approx(0.02 + 2.0 * 5.4 / 60, abs=0.01)


def test_restart_continues_the_same_hour():
    tr = HourEnergyTracker()
    _run(tr, T0, 30, 2.0)
    saved = tr.to_dict()
    again = HourEnergyTracker()
    again.restore(saved, T0 + timedelta(minutes=32))                    # 2 minutes down
    assert again.energy_this_hour_kwh == pytest.approx(2.0 * 32 / 60, abs=0.02)
    stale = HourEnergyTracker()
    stale.restore(saved, T0 + timedelta(hours=2))                       # other hour -> ignored
    assert stale.hour_start is None


def test_month_rebuilt_from_statistics():
    e = EnergyPriceEngine()
    e.set_selected_tariff_kw(5.0)
    now = datetime(2026, 11, 27, 21, 20, tzinfo=timezone.utc)
    hours = [(datetime(2026, 11, d, h, tzinfo=timezone.utc), 6.0 if (d, h) == (3, 18) else 1.0)
             for d in range(1, 27) for h in range(24)]
    hours += [(datetime(2026, 11, 27, 21, tzinfo=timezone.utc), 9.0)]   # running hour
    s = rebuild_month(e, hours, lambda ts, before: 1.0, now)
    assert s["hours"] == 26 * 24                      # the running hour is not counted
    assert e.monthly_energy_kwh == pytest.approx(26 * 24 + 5.0)
    assert e.monthly_cost_nok == pytest.approx(26 * 24 + 5.0)
    assert e.daily_peaks["2026-11-03"] == pytest.approx(6.0)
    assert e.monthly_max_kw == pytest.approx((6.0 + 1.0 + 1.0) / 3)
    assert s["missing_hours"] == 21                   # 27 Nov 00–20 not in the statistics


# ---------------------------------------------------------------- notifications
def test_notifier_dedupe_quiet_hours_snooze_and_rearm():
    n = Notifier(settings=NotifySettings(service="notify.mobile_app_x"))
    day = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    cond = {"ev_unmet:EV": {"message": "short", "actions": [{"action": f"{ACTION_PRIORITY_NOW}|EV", "title": "Priority now"}]}}
    out = n.evaluate(cond, day)
    assert len(out) == 1 and out[0]["level"] == "warning"
    assert [a["title"] for a in out[0]["actions"]] == ["Priority now", "Ignore today"]
    assert n.evaluate(cond, day + timedelta(minutes=30)) == []          # max once per hour
    assert len(n.evaluate(cond, day + timedelta(minutes=61))) == 1
    n.evaluate({}, day + timedelta(minutes=62))                         # cleared -> re-armed
    assert len(n.evaluate(cond, day + timedelta(minutes=63))) == 1
    night = datetime(2026, 11, 27, 23, 30, tzinfo=timezone.utc)
    assert n.evaluate({"phase_limiting": {"message": "x"}}, night) == []            # warning: quiet
    assert len(n.evaluate({"phase_limiting": {"message": "x"}, "safe_mode": {"message": "y"}}, night)) == 1  # error passes
    n.snooze_today("phase_limiting", day)
    assert n.evaluate({"phase_limiting": {"message": "x"}}, day + timedelta(hours=2)) == []
    dom, svc, data = n.service_call(out[0])
    assert (dom, svc) == ("notify", "mobile_app_x") and data["data"]["actions"][-1]["action"] == f"{ACTION_IGNORE}|ev_unmet:EV"
    assert in_quiet(datetime(2026, 1, 1, 6, 59), time(23), time(7)) and not in_quiet(datetime(2026, 1, 1, 7, 0), time(23), time(7))
    again = Notifier()
    again.restore(n.to_dict())
    assert again.snoozed_until == n.snoozed_until and again.log == n.log


def test_daily_events_are_not_repeated_hourly():
    n = Notifier(settings=NotifySettings(service="notify.x"))
    t = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
    c = {"norgespris_volume:2026-11": {"message": "m"}}
    assert len(n.evaluate(c, t)) == 1
    assert n.evaluate(c, t + timedelta(hours=5)) == []


# ---------------------------------------------------------------- next action on the 15-minute plan
def test_next_action_uses_quarters():
    start = datetime(2026, 11, 27, 21, 0, tzinfo=timezone.utc)
    plan = [0.0, 0.0, 6.0, 6.0] + [0.0] * 284                           # charging 21:30–22:00
    n = next_action(plan, None, start, start + timedelta(minutes=35), 0.25)
    assert n["running_now"] is True and n["start"].startswith("2026-11-27T21:30") and n["kwh"] == 3.0
    n = next_action(plan, None, start, start + timedelta(minutes=5), 0.25)
    assert n["running_now"] is False and n["start"].startswith("2026-11-27T21:30")
