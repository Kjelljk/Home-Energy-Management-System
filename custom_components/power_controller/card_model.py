"""Pure helpers that turn controller state into what the Lovelace cards show.

Kept free of Home Assistant imports so they are unit-tested.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# Card status keys (stable contract with power-controller-object-card.js)
UI_RUNNING = "running"        # has its planned power and is using it
UI_LENT = "lent"              # power reserved but idle (thermostat satisfied), lent to others
UI_WAITING = "waiting"        # not planned now; next start is shown
UI_BLOCKED = "blocked"        # planned, but no capacity left (tariff or phase limit)
UI_SHEDDING = "shedding"      # being cut back to avoid exceeding the hour or a phase
UI_PRIORITY_NOW = "priority_now"
UI_MANUAL = "manual"          # uncommitted – Power Controller ignores it

ACTIVE_KW = 0.05


def plan_index(plan_start: datetime | None, now: datetime) -> int:
    """Index into a 72-hour plan for ``now``.

    Plans are made at xx:59 with hour 0 = the hour in which they were solved, so the
    hour that follows is index 1. Never negative; caller must bounds-check the end.
    """
    if plan_start is None:
        return 0
    return max(0, int((now - plan_start).total_seconds() // 3600))


def value_at(series: list[Any] | None, idx: int) -> Any:
    if not series or idx >= len(series):
        return None
    return series[idx]


def ui_status(
    committed: bool,
    priority_now: bool,
    dist_status: str | None,
    reason: str | None,
    live_kw: float,
    warning_active: bool,
) -> str:
    """Map distributor output to the status shown on the object card."""
    if not committed:
        return UI_MANUAL
    if priority_now:
        return UI_PRIORITY_NOW
    if dist_status == "Redistributing":
        return UI_LENT
    if dist_status == "Prioritized":
        return UI_RUNNING
    if dist_status == "Hold":
        if reason == "blocked":
            return UI_SHEDDING if (warning_active and live_kw > ACTIVE_KW) else UI_BLOCKED
        return UI_WAITING
    return UI_WAITING


def next_action(
    plan: list[float | None] | None,
    setpoints: list[float | None] | None,
    plan_start: datetime | None,
    now: datetime,
    slot_hours: float = 1.0,
) -> dict[str, Any] | None:
    """The current (if running now) or next planned block of energy use.

    ``slot_hours`` = length of one plan slot (0.25 for the 15-minute plan). ``kwh`` is energy.

    Returns ``{"start", "end", "kwh", "setpoint", "running_now"}`` with ISO timestamps
    (block end is exclusive), or None if nothing is planned in the rest of the horizon.
    """
    if not plan or plan_start is None:
        return None
    sh = float(slot_hours) if slot_hours and slot_hours > 0 else 1.0
    idx = max(0, int((now - plan_start).total_seconds() // (3600.0 * sh)))
    n = len(plan)
    i = idx
    while i < n and not ((plan[i] or 0.0) > ACTIVE_KW):
        i += 1
    if i >= n:
        return None
    j = i
    kwh = 0.0
    sp_max: float | None = None
    while j < n and (plan[j] or 0.0) > ACTIVE_KW:
        kwh += float(plan[j] or 0.0) * sh
        sp = value_at(setpoints, j)
        if sp is not None:
            sp_max = float(sp) if sp_max is None else max(sp_max, float(sp))
        j += 1
    return {
        "start": (plan_start + timedelta(hours=i * sh)).isoformat(),
        "end": (plan_start + timedelta(hours=j * sh)).isoformat(),
        "kwh": round(kwh, 2),
        "setpoint": sp_max,
        "running_now": i == idx,
    }


def plan_window(series: list[Any] | None, plan_start: datetime | None, now: datetime, hours: int = 24) -> list[Any]:
    """Slice of a 72-hour series starting at the current hour (padded with None)."""
    idx = plan_index(plan_start, now)
    out = list((series or [])[idx: idx + hours])
    return out + [None] * (hours - len(out))


# ---------------------------------------------------------------------------
# EV: does the connector status agree with what is measured? (2.13.1)
# ---------------------------------------------------------------------------
EV_CHECK_DELAY_S = 120          # a disagreement must last this long before it is shown
EV_IDLE_KW = 0.2                # below this the car is not drawing power
EV_DRAWING_KW = 0.5             # above this the car is clearly drawing power


def ev_checks(status_raw: str | None, status_text: str | None, status_charging: bool | None,
              power_kw: float | None, current_a: float | None, offered_a: float | None,
              commanded_a: float | None, committed: bool, voltage: float = 230.0) -> dict[str, str]:
    """Disagreements between the connector status and the measurements, {code: message}.

    * status Charging, but the power (or current) sensor shows ~0
    * status not Charging, but the power (or current) sensor shows the car drawing power
    * power and current sensor disagree (P ≠ I × 230 V) – typically a sensor from another device
    * the charger offers a different current than Power Controller set (command not accepted)
    The caller shows a message only when it has lasted ``EV_CHECK_DELAY_S``.
    """
    out: dict[str, str] = {}
    meas_kw = power_kw if power_kw is not None else (current_a * voltage / 1000.0 if current_a is not None else None)
    src = "power sensor" if power_kw is not None else "charging-current sensor"
    status = status_text or status_raw
    if status_charging is True and meas_kw is not None and meas_kw < EV_IDLE_KW:
        out["charging_no_power"] = (f"Status says “{status}”, but the {src} shows {meas_kw:.2f} kW. "
                                    "Check that the sensor belongs to this charger.")
    if status_charging is False and meas_kw is not None and meas_kw > EV_DRAWING_KW:
        out["power_not_charging"] = (f"Status says “{status}”, but the {src} shows {meas_kw:.2f} kW. "
                                     "Check the status sensor or the power sensor.")
    if power_kw is not None and current_a is not None and max(power_kw, current_a * voltage / 1000.0) > EV_DRAWING_KW:
        from_i = current_a * voltage / 1000.0
        if abs(power_kw - from_i) > max(0.4, 0.35 * max(power_kw, from_i)):
            out["power_current_mismatch"] = (f"Power {power_kw:.2f} kW does not match the charging current "
                                             f"{current_a:.1f} A (≈ {from_i:.2f} kW at {voltage:.0f} V). "
                                             "One of the sensors may belong to another device.")
    if (committed and status_charging is True and offered_a is not None and commanded_a is not None
            and abs(offered_a - commanded_a) > 1.5):
        out["offered_not_commanded"] = (f"The charger offers {offered_a:.0f} A, but Power Controller set "
                                        f"{commanded_a:.0f} A. The command may not have been accepted.")
    return out
