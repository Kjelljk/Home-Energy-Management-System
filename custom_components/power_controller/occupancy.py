"""Occupancy model: when is somebody home and awake, and when is it a home-office day?

The planner uses this instead of a fixed comfort schedule (unless a device has its own
``schedule`` helper, which still wins). Out-of-town (Away/Arriving mode + arrival time)
is handled by the planner on top of this.

States per point in time:
* ``sleep``        – at home, asleep (between sleep and wake time)
* ``home_awake``   – at home and awake (mornings, evenings, free days)
* ``away_work``    – out at work/school on a normal workday (between leave and return)
* ``home_office``  – home-office day, during the normal work hours (leave → return)

Day type comes from the workday mask (Norwegian holidays + workday sensor). A home-office
day is either a specific date (one-off, e.g. "tomorrow") or a recurring weekday. Home office
only changes something on workdays – on free days you are home anyway.

Each heating device chooses in which states it needs comfort temperature, e.g.
living room = home_awake + home_office, office = home_office, bedroom = sleep.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from collections.abc import Callable

from .const import PLANNING_HORIZON_HOURS

STATE_SLEEP = "sleep"
STATE_HOME = "home_awake"
STATE_AWAY_WORK = "away_work"
STATE_HOME_OFFICE = "home_office"
ALL_STATES = (STATE_SLEEP, STATE_HOME, STATE_AWAY_WORK, STATE_HOME_OFFICE)
COMFORT_STATES = (STATE_HOME, STATE_HOME_OFFICE, STATE_SLEEP)   # selectable per device
DEFAULT_COMFORT_DURING = [STATE_HOME, STATE_HOME_OFFICE]
OCCUPIED_STATES = (STATE_HOME, STATE_HOME_OFFICE)                # people active in the house

# Values of the "Occupancy" select (the *current* situation, set by the user or an automation)
OCC_SELECT_OPTIONS = (STATE_HOME, STATE_SLEEP, STATE_AWAY_WORK, STATE_HOME_OFFICE)
OVERRIDE_HOURS = 2   # plans are solved at xx:59 -> index 0 = ending hour, index 1 = the next hour


def effective_state(selected: str | None, scheduled: str) -> str:
    """Combine the occupancy selector (now) with the time schedule.

    * no selection          -> the schedule
    * away_work selected but the schedule already expects us home -> keep the schedule's
      comfort (we may arrive any minute, so the house is kept warm)
    * otherwise             -> the selector wins for the current hour(s)
    """
    if not selected:
        return scheduled
    if selected == STATE_AWAY_WORK and scheduled in (STATE_HOME, STATE_HOME_OFFICE):
        return scheduled
    return selected


def apply_selector(hour_states: list[set[str]], selected: str | None, hours: int = OVERRIDE_HOURS) -> list[set[str]]:
    """Return a copy where the first ``hours`` entries follow the selector (see effective_state)."""
    out = [set(s) for s in hour_states]
    if not selected:
        return out
    for i in range(min(hours, len(out))):
        out[i] = {effective_state(selected, OccupancyProfile.dominant_state(out[i]))}
    return out


WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SAMPLES_PER_HOUR = (0, 15, 30, 45)   # an hour needs comfort if ANY quarter needs it


def parse_hhmm(value: str | time) -> int:
    """'06:30' / '6:30:00' / time(6, 30) -> minutes after midnight."""
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    parts = [int(p) for p in str(value).strip().split(":")[:2]]
    hours, minutes = parts[0], (parts[1] if len(parts) > 1 else 0)
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"invalid time {value!r}")
    return hours * 60 + minutes


def fmt_hhmm(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass
class DayTimes:
    """Clock times for one day type. ``leave``/``back`` are ignored on free days."""

    wake: str = "06:30"
    leave: str = "07:45"
    back: str = "16:30"
    sleep: str = "23:00"   # may be after midnight, e.g. "00:30"

    def awake_interval(self) -> tuple[int, int]:
        """(start, end) in minutes from this day's midnight; end may exceed 24 h."""
        wake, sleep = parse_hhmm(self.wake), parse_hhmm(self.sleep)
        if sleep <= wake:
            sleep += 24 * 60
        return wake, sleep

    def work_interval(self) -> tuple[int, int]:
        leave, back = parse_hhmm(self.leave), parse_hhmm(self.back)
        if back <= leave:
            back = leave  # invalid/empty -> no work period
        return leave, back


@dataclass
class OccupancyProfile:
    workday: DayTimes = field(default_factory=DayTimes)
    free_day: DayTimes = field(default_factory=lambda: DayTimes(wake="08:00", leave="00:00", back="00:00", sleep="23:30"))
    home_office_weekdays: list[str] = field(default_factory=list)   # recurring, e.g. ["wed"]
    home_office_dates: dict[str, bool] = field(default_factory=dict)  # one-off: True = home office, False = cancel recurring

    # ------------------------------------------------------------------ home office
    def is_home_office(self, day: date, is_workday: bool) -> bool:
        if not is_workday:
            return False
        override = self.home_office_dates.get(day.isoformat())
        if override is not None:
            return bool(override)
        return WEEKDAY_KEYS[day.weekday()] in self.home_office_weekdays

    def set_home_office(self, day: date, enabled: bool) -> None:
        """One-off override. Setting a date back to its recurring default removes the override."""
        recurring = WEEKDAY_KEYS[day.weekday()] in self.home_office_weekdays
        if enabled == recurring:
            self.home_office_dates.pop(day.isoformat(), None)
        else:
            self.home_office_dates[day.isoformat()] = bool(enabled)

    def prune(self, today: date) -> None:
        """Forget one-off overrides older than yesterday."""
        limit = (today - timedelta(days=1)).isoformat()
        for key in [k for k in self.home_office_dates if k < limit]:
            self.home_office_dates.pop(key, None)

    def upcoming_home_office(self, today: date, workday: Callable[[date], bool], days: int = 14) -> list[str]:
        return [
            (today + timedelta(days=i)).isoformat()
            for i in range(days)
            if self.is_home_office(today + timedelta(days=i), workday(today + timedelta(days=i)))
        ]

    # ------------------------------------------------------------------ states
    def _times(self, is_workday: bool) -> DayTimes:
        return self.workday if is_workday else self.free_day

    def state_at(self, ts: datetime, workday: Callable[[date], bool]) -> str:
        """Occupancy state at ``ts`` (local time). ``workday(date)`` gives the day type."""
        minute = ts.hour * 60 + ts.minute
        today = ts.date()
        yesterday = today - timedelta(days=1)

        t_today = self._times(workday(today))
        w0, s0 = t_today.awake_interval()
        t_yday = self._times(workday(yesterday))
        _, s_y = t_yday.awake_interval()
        awake = (w0 <= minute < s0) or (minute + 24 * 60 < s_y)
        if not awake:
            return STATE_SLEEP

        is_wd = workday(today)
        if is_wd and w0 <= minute < s0:
            leave, back = t_today.work_interval()
            if leave <= minute < back:
                return STATE_HOME_OFFICE if self.is_home_office(today, True) else STATE_AWAY_WORK
        return STATE_HOME

    def hour_states(self, start: datetime, workday: Callable[[date], bool],
                    horizon: int = PLANNING_HORIZON_HOURS) -> list[set[str]]:
        """Set of states occurring in each hour (sampled every 15 min)."""
        out: list[set[str]] = []
        for t in range(horizon):
            base = (start + timedelta(hours=t)).replace(minute=0, second=0, microsecond=0)
            out.append({self.state_at(base + timedelta(minutes=m), workday) for m in SAMPLES_PER_HOUR})
        return out

    @staticmethod
    def comfort_mask(hour_states: list[set[str]], comfort_during: list[str] | None) -> list[bool]:
        wanted = set(comfort_during or DEFAULT_COMFORT_DURING)
        return [bool(states & wanted) for states in hour_states]

    @staticmethod
    def occupied_mask(hour_states: list[set[str]]) -> list[bool]:
        return [bool(states & set(OCCUPIED_STATES)) for states in hour_states]

    @staticmethod
    def dominant_state(states: set[str]) -> str:
        for st in (STATE_HOME_OFFICE, STATE_HOME, STATE_AWAY_WORK, STATE_SLEEP):
            if st in states:
                return st
        return STATE_SLEEP

    # ------------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> OccupancyProfile:
        if not data:
            return cls()
        return cls(
            workday=DayTimes(**data.get("workday", {})),
            free_day=DayTimes(**data.get("free_day", {})) if data.get("free_day") else cls().free_day,
            home_office_weekdays=[d for d in data.get("home_office_weekdays", []) if d in WEEKDAY_KEYS],
            home_office_dates={str(k): bool(v) for k, v in data.get("home_office_dates", {}).items()},
        )
