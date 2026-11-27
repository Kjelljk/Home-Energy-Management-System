"""Command patience and EV session rules (pure Python, unit-tested).

Power Controller should not chatter: a new setpoint or charging current is only sent when the
change is meaningful and some minutes have passed since the previous command. Two exceptions:

* **Urgent relief** – when the hour allowance is about to be exceeded or a phase is beyond
  overload acceptance, a *reduction* is sent immediately (no minimum interval, no minimum step).
* **Never urgent increases** – more power is always sent patiently.

EV minimum state of charge: the deadline is fixed when the car is plugged in (or when the
minimum is switched on while it is already plugged in) and does not move while it stays
connected, so the planner cannot keep postponing it.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

EV_MIN_SOC_WINDOW_H = 24.0


@dataclass
class ControlSettings:
    """Common command-patience settings (persisted, editable as number entities)."""

    min_interval_s: float = 300.0       # minimum time between two commands to the same device
    regulated_min_interval_s: float = 1800.0   # heaters regulated by an external room sensor (heat pumps)
    ev_min_delta_a: float = 2.0         # minimum change of EV charging current
    temp_min_delta_c: float = 0.5       # minimum change of a climate setpoint
    ev_delay_penalty_nok: float = 0.02  # NOK per kWh per hour of delay (EV without deadline)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> ControlSettings:
        base = cls()
        for key, val in (data or {}).items():
            if hasattr(base, key) and val is not None:
                setattr(base, key, float(val))
        return base


def should_send_command(
    prev: float | None,
    target: float,
    last_ts: datetime | None,
    now: datetime,
    min_delta: float,
    min_interval_s: float,
    urgent: bool = False,
    lower_is_relief: bool = True,
) -> bool:
    """Decide whether a new command (setpoint or current) should be sent now."""
    if prev is None:
        return True                       # nothing commanded yet (start-up)
    diff = float(target) - float(prev)
    if abs(diff) < 1e-9:
        return False
    relief = diff < 0 if lower_is_relief else diff > 0
    if urgent and relief:
        return True                       # protect the tariff step / main fuse at once
    stop_or_start = (float(target) == 0.0) != (float(prev) == 0.0)
    if abs(diff) < float(min_delta) - 1e-9 and not stop_or_start:
        return False                      # too small to be worth a command
    if last_ts is None:
        return True
    return (now - last_ts).total_seconds() >= float(min_interval_s)


def ev_min_soc_deadline(session_start: datetime | None, min_since: datetime | None,
                        window_h: float = EV_MIN_SOC_WINDOW_H) -> datetime | None:
    """Fixed deadline for the minimum state of charge: 24 h after plug-in, or after the
    minimum was switched on during the session (whichever is later)."""
    starts = [t for t in (session_start, min_since) if t is not None]
    if not starts:
        return None
    return max(starts) + timedelta(hours=window_h)


def parse_transaction_id(state: str | None) -> int | None:
    """OCPP transaction id from a sensor state; None when there is no active transaction."""
    if state is None:
        return None
    s = str(state).strip()
    if s.lower() in ("", "unknown", "unavailable", "none", "null", "0"):
        return None
    try:
        tid = int(float(s))
    except (TypeError, ValueError):
        return None
    return tid if tid > 0 else None


def ocpp_charge_rate_data(limit_a: int, conn_id: int, transaction_id: int | None) -> dict:
    """Service data for ``ocpp.set_charge_rate``.

    With an active transaction a TxProfile (relative, stack level 0) bound to the transaction is
    sent – the same profile the owner's working automation used; many chargers ignore a plain
    ``limit_amps`` default profile. Without a transaction (before charging starts) ``limit_amps``
    is used as fallback.
    """
    limit_a = max(0, int(limit_a))
    conn_id = int(conn_id or 1)
    if transaction_id is None:
        return {"limit_amps": limit_a, "conn_id": conn_id}
    profile = {
        "transactionId": int(transaction_id),
        "chargingProfileId": 1,
        "stackLevel": 0,
        "chargingProfilePurpose": "TxProfile",
        "chargingProfileKind": "Relative",
        "chargingSchedule": {
            "chargingRateUnit": "A",
            "chargingSchedulePeriod": [{"startPeriod": 0, "limit": limit_a}],
        },
    }
    return {"custom_profile": json.dumps(profile), "conn_id": conn_id}
