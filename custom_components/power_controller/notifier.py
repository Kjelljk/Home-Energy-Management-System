"""Notifications (pure Python decision logic, unit-tested).

Sends errors and early warnings to one ``notify.*`` service chosen in the hub settings
(e.g. ``notify.mobile_app_pixel``), with actionable buttons in the Home Assistant app.

Rules
* every event has a stable key (e.g. ``ev_unmet:<entry_id>``); the same key is sent at most
  once per ``repeat_minutes`` (default 60)
* quiet hours (default 23:00–07:00): only ERRORS are sent; warnings wait until the quiet
  period ends if the condition still holds
* "Ignore today" snoozes the key until midnight
* when a condition clears, the key is re-armed (the next occurrence is sent at once)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"

ACTION_PREFIX = "PC_"
ACTION_IGNORE = "PC_IGNORE_TODAY"
ACTION_PRIORITY_NOW = "PC_PRIORITY_NOW"
ACTION_OPEN = "PC_OPEN_PANEL"

# event kinds (key prefix) -> (level, title)
EVENTS: dict[str, tuple[str, str]] = {
    "safe_mode": (LEVEL_ERROR, "Grid sensor stale – safe mode"),
    "ocpp_rejected": (LEVEL_ERROR, "EV charger did not accept the command"),
    "plan_fallback": (LEVEL_ERROR, "Planner failed – fallback plan in use"),
    "setpoint_ignored": (LEVEL_ERROR, "Device does not follow its setpoint"),
    "prices_missing": (LEVEL_ERROR, "Tomorrow's prices are missing"),
    "hour_over": (LEVEL_WARNING, "This hour will exceed the capacity step"),
    "step_up": (LEVEL_WARNING, "New capacity step locked for the month"),
    "ev_unmet": (LEVEL_WARNING, "EV will not reach its target in time"),
    "phase_limiting": (LEVEL_WARNING, "Phase overload – power is being limited"),
    "norgespris_volume": (LEVEL_WARNING, "Norgespris volume runs out this month"),
}


# minimum minutes between two messages for the same key (default: settings.repeat_minutes)
EVENT_REPEAT_MIN: dict[str, float] = {"norgespris_volume": 24 * 60, "step_up": 24 * 60, "prices_missing": 180}


def parse_hhmm(value: str | None, default: time) -> time:
    try:
        h, m = (int(x) for x in str(value).strip().split(":")[:2])
        return time(h, m)
    except (TypeError, ValueError, AttributeError):
        return default


def in_quiet(now: datetime, start: time, end: time) -> bool:
    t = now.time()
    if start == end:
        return False
    return (start <= t or t < end) if start > end else (start <= t < end)


@dataclass
class NotifySettings:
    service: str | None = None             # "notify.mobile_app_x"
    quiet_start: time = time(23, 0)
    quiet_end: time = time(7, 0)
    repeat_minutes: float = 60.0


@dataclass
class Notifier:
    settings: NotifySettings = field(default_factory=NotifySettings)
    last_sent: dict[str, datetime] = field(default_factory=dict)
    snoozed_until: dict[str, datetime] = field(default_factory=dict)
    active: set[str] = field(default_factory=set)
    log: list[dict] = field(default_factory=list)          # last messages (for the panel)

    def should_send(self, key: str, now: datetime) -> bool:
        kind = key.split(":", 1)[0]
        level = EVENTS.get(kind, (LEVEL_WARNING, ""))[0]
        until = self.snoozed_until.get(key)
        if until and now < until:
            return False
        if level != LEVEL_ERROR and in_quiet(now, self.settings.quiet_start, self.settings.quiet_end):
            return False
        last = self.last_sent.get(key)
        repeat = EVENT_REPEAT_MIN.get(kind, self.settings.repeat_minutes)
        return last is None or (now - last) >= timedelta(minutes=repeat)

    def evaluate(self, conditions: dict[str, dict], now: datetime) -> list[dict]:
        """``conditions``: {key: {"message": str, "actions": [...], "data": {...}}} for every
        condition that is TRUE right now. Returns the messages to send."""
        out = []
        for key in list(self.active):
            if key not in conditions:              # cleared -> re-arm
                self.active.discard(key)
                self.last_sent.pop(key, None)
        for key, cond in conditions.items():
            self.active.add(key)
            if not self.should_send(key, now):
                continue
            kind = key.split(":", 1)[0]
            level, title = EVENTS.get(kind, (LEVEL_WARNING, "Power Controller"))
            actions = [{"action": f"{ACTION_IGNORE}|{key}", "title": "Ignore today"}]
            actions = list(cond.get("actions", [])) + actions
            msg = {"key": key, "level": level, "title": f"Power Controller: {title}",
                   "message": cond.get("message", ""), "actions": actions[:3],
                   "ts": now.isoformat()}
            self.last_sent[key] = now
            self.log = (self.log + [msg])[-30:]
            out.append(msg)
        return out

    def snooze_today(self, key: str, now: datetime) -> None:
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        self.snoozed_until[key] = midnight
        # prune old snoozes
        self.snoozed_until = {k: v for k, v in self.snoozed_until.items() if v > now}

    def service_call(self, msg: dict) -> tuple[str, str, dict] | None:
        """(domain, service, data) for the configured notify service, or None."""
        svc = (self.settings.service or "").strip()
        if not svc:
            return None
        domain, _, name = svc.partition(".")
        if domain != "notify" or not name:
            domain, name = "notify", svc
        data = {"title": msg["title"], "message": msg["message"],
                "data": {"tag": f"power_controller_{msg['key'].replace(':', '_')}",
                         "group": "power_controller", "actions": msg["actions"],
                         "clickAction": "/power-controller", "url": "/power-controller"}}
        if msg["level"] == LEVEL_ERROR:
            data["data"].update(ttl=0, priority="high", importance="high")
        return domain, name, data

    def to_dict(self) -> dict:
        return {"last_sent": {k: v.isoformat() for k, v in self.last_sent.items()},
                "snoozed_until": {k: v.isoformat() for k, v in self.snoozed_until.items()},
                "log": self.log[-30:]}

    def restore(self, data: dict | None) -> None:
        if not data:
            return
        for attr in ("last_sent", "snoozed_until"):
            vals = {}
            for k, v in (data.get(attr) or {}).items():
                try:
                    vals[k] = datetime.fromisoformat(v)
                except (TypeError, ValueError):
                    pass
            setattr(self, attr, vals)
        self.log = list(data.get("log") or [])[-30:]
