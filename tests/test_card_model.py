"""Card view-model helpers: status mapping, plan index and next action."""
from datetime import datetime, timedelta, timezone

from custom_components.power_controller.card_model import (
    UI_BLOCKED,
    UI_LENT,
    UI_MANUAL,
    UI_PRIORITY_NOW,
    UI_RUNNING,
    UI_SHEDDING,
    UI_WAITING,
    next_action,
    plan_index,
    plan_window,
    ui_status,
)

T0 = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)


def test_plan_index_after_xx59_plan():
    # plan solved 10:59 has hour 0 = 10:00; at 11:05 we must use index 1 (bug in <= 2.5.0)
    assert plan_index(T0, T0 + timedelta(minutes=59)) == 0
    assert plan_index(T0, T0 + timedelta(hours=1, minutes=5)) == 1
    assert plan_index(None, T0) == 0
    assert plan_index(T0, T0 - timedelta(hours=2)) == 0


def test_ui_status_mapping():
    assert ui_status(False, True, "Prioritized", None, 1.0, False) == UI_MANUAL
    assert ui_status(True, True, "Hold", "blocked", 0.0, False) == UI_PRIORITY_NOW
    assert ui_status(True, False, "Prioritized", None, 1.0, False) == UI_RUNNING
    assert ui_status(True, False, "Redistributing", None, 0.0, False) == UI_LENT
    assert ui_status(True, False, "Hold", "not planned", 0.0, False) == UI_WAITING
    assert ui_status(True, False, "Hold", "blocked", 0.0, False) == UI_BLOCKED
    assert ui_status(True, False, "Hold", "blocked", 1.2, True) == UI_SHEDDING
    assert ui_status(True, False, None, None, 0.0, False) == UI_WAITING


def test_next_action_running_and_future_blocks():
    plan = [0.0, 1.5, 1.5, 0.0, 0.0, 2.0, 2.0, 2.0] + [0.0] * 64
    sp = [18.0, 21.0, 22.0, 18.0, 18.0, 21.0, 21.5, 21.0] + [18.0] * 64
    nxt = next_action(plan, sp, T0, T0 + timedelta(minutes=20))
    assert nxt["start"] == (T0 + timedelta(hours=1)).isoformat()
    assert nxt["end"] == (T0 + timedelta(hours=3)).isoformat()
    assert nxt["kwh"] == 3.0 and nxt["setpoint"] == 22.0 and nxt["running_now"] is False
    nxt = next_action(plan, sp, T0, T0 + timedelta(hours=1, minutes=10))
    assert nxt["running_now"] is True
    nxt = next_action(plan, sp, T0, T0 + timedelta(hours=3))
    assert nxt["start"] == (T0 + timedelta(hours=5)).isoformat() and nxt["setpoint"] == 21.5
    assert next_action([0.0] * 72, None, T0, T0) is None
    assert next_action(plan, None, None, T0) is None


def test_plan_window_is_shifted_and_padded():
    series = list(range(72))
    assert plan_window(series, T0, T0 + timedelta(hours=2), 4) == [2, 3, 4, 5]
    assert plan_window(series, T0, T0 + timedelta(hours=70), 4) == [70, 71, None, None]
