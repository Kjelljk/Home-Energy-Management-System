"""Hourly energy accounting (pure Python, unit-tested).

Why this module exists (2.11.0): "Energy this hour" used the delta of the total import meter.
HAN/AMS meters often publish the cumulative register only once per hour, a few seconds AFTER
the hour change, and the controller closed the hour at :00:05 – after it had already reset the
counter. Result: "this hour" contained the previous hour, finished hours were booked as ~0 kWh,
and Month to date / top-3 / Norgespris never counted.

New design:
* :class:`HourEnergyTracker` integrates the grid power (kW) every control cycle – the same
  method as a kW→kWh utility meter that resets every hour. If such an hourly sensor is
  configured it is used directly (and trusted only once it has actually reset this hour).
* The hour is closed in the SAME step that detects the new hour, with the value of the hour that
  just ended, so nothing is lost between "close" and "reset".
* :func:`rebuild_month` re-creates the month (energy, cost, daily peaks, top-3, Norgespris) from
  the recorder's hourly statistics of the total meter – at start-up and every hour – so the
  numbers are right after a restart and a missed hour cannot stay wrong.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable


@dataclass
class ClosedHour:
    hour_start: datetime
    kwh: float
    source: str          # "hour_sensor" | "integrated"


@dataclass
class HourEnergyTracker:
    hour_start: datetime | None = None
    integral_kwh: float = 0.0            # ∫ grid power dt since hour_start
    last_ts: datetime | None = None
    last_kw: float = 0.0
    sensor_kwh: float | None = None      # last trusted value of the hourly sensor this hour
    sensor_seen_reset: bool = False
    # cumulative energy samples for the rolling 60-minute average
    _samples: list[tuple[datetime, float]] = field(default_factory=list)
    _cum_kwh: float = 0.0

    @staticmethod
    def _floor(ts: datetime) -> datetime:
        return ts.replace(minute=0, second=0, microsecond=0)

    def update(self, now: datetime, grid_kw: float | None, hour_sensor_kwh: float | None = None,
               hour_sensor_updated: datetime | None = None) -> ClosedHour | None:
        """Advance the tracker. Returns the closed hour when ``now`` is in a new clock hour."""
        kw = max(0.0, float(grid_kw)) if grid_kw is not None else self.last_kw
        start = self._floor(now)
        # rolling 60-minute average (cumulative energy, never reset)
        if self.last_ts is not None:
            self._cum_kwh += (self.last_kw + kw) / 2.0 * max(0.0, (now - self.last_ts).total_seconds()) / 3600.0
        closed: ClosedHour | None = None
        if self.hour_start is None:
            # first call (start-up): estimate what was used so far this hour from the current power
            self.hour_start = start
            self.integral_kwh = kw * (now - start).total_seconds() / 3600.0
        elif start > self.hour_start:
            # integrate up to the hour boundary at the previous power, then close the hour
            if self.last_ts is not None:
                self.integral_kwh += self.last_kw * max(0.0, (start - self.last_ts).total_seconds()) / 3600.0
            ended = self.hour_start
            if self.sensor_kwh is not None and self.sensor_seen_reset:
                # sensor value from the last read + integral of the few seconds after it
                kwh, src = max(self.sensor_kwh, 0.0), "hour_sensor"
                if self.last_ts is not None:
                    kwh += self.last_kw * max(0.0, (start - self.last_ts).total_seconds()) / 3600.0
            else:
                kwh, src = self.integral_kwh, "integrated"
            closed = ClosedHour(ended, round(kwh, 4), src)
            self.hour_start = start
            self.integral_kwh = kw * (now - start).total_seconds() / 3600.0
            self.sensor_kwh = None
            self.sensor_seen_reset = False
            self.last_ts = now
            self.last_kw = kw
        if self.last_ts is not None and closed is None:
            self.integral_kwh += (self.last_kw + kw) / 2.0 * max(0.0, (now - self.last_ts).total_seconds()) / 3600.0
        # hourly utility-meter sensor: trust it only after it has reset in this hour
        if hour_sensor_kwh is not None:
            fresh = hour_sensor_updated is None or hour_sensor_updated >= self.hour_start
            if fresh and (self.sensor_seen_reset or hour_sensor_kwh <= self.integral_kwh + 0.3):
                self.sensor_seen_reset = True
                self.sensor_kwh = float(hour_sensor_kwh)
        self._samples.append((now, self._cum_kwh))
        cutoff = now - timedelta(minutes=65)
        while len(self._samples) > 2 and self._samples[1][0] < cutoff:
            self._samples.pop(0)
        self.last_ts = now
        self.last_kw = kw
        return closed

    @property
    def energy_this_hour_kwh(self) -> float:
        if self.sensor_kwh is not None and self.sensor_seen_reset:
            return max(0.0, self.sensor_kwh)
        return max(0.0, self.integral_kwh)

    @property
    def source(self) -> str:
        return "hour_sensor" if (self.sensor_kwh is not None and self.sensor_seen_reset) else "integrated"

    def avg_60min_kw(self, now: datetime) -> float:
        """Average power over the last 60 minutes (or since start-up if shorter)."""
        if len(self._samples) < 2:
            return self.last_kw
        target = now - timedelta(minutes=60)
        base_ts, base_e = self._samples[0]
        for ts, e in self._samples:
            if ts <= target:
                base_ts, base_e = ts, e
            else:
                break
        hours = max(1.0 / 60.0, (now - base_ts).total_seconds() / 3600.0)
        return round((self._cum_kwh - base_e) / hours, 3)

    # ------------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return {"hour_start": self.hour_start.isoformat() if self.hour_start else None,
                "integral_kwh": self.integral_kwh, "last_ts": self.last_ts.isoformat() if self.last_ts else None,
                "last_kw": self.last_kw}

    def restore(self, data: dict | None, now: datetime) -> None:
        """Continue the same hour after a restart: add the gap at the last known power."""
        if not data or not data.get("hour_start"):
            return
        try:
            hs = datetime.fromisoformat(data["hour_start"])
            last = datetime.fromisoformat(data["last_ts"]) if data.get("last_ts") else hs
        except (TypeError, ValueError):
            return
        if hs != self._floor(now):
            return
        self.hour_start = hs
        self.last_kw = float(data.get("last_kw", 0.0))
        self.integral_kwh = float(data.get("integral_kwh", 0.0)) + self.last_kw * max(0.0, (now - last).total_seconds()) / 3600.0
        self.last_ts = now


def rebuild_month(engine, hours: list[tuple[datetime, float]], price_for_hour: Callable[[datetime, float], float],
                  now: datetime) -> dict:
    """Re-create the month on ``engine`` from finished hours [(hour_start, kWh)] of this month.

    ``price_for_hour(ts, month_kwh_before)`` returns the all-in price incl. VAT for that hour.
    The selected capacity step is kept; the active step is re-derived from the month's peaks.
    Returns a summary (hours, kwh, missing hours) for diagnostics.
    """
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    rows = sorted((ts, max(0.0, float(k))) for ts, k in hours if month_start <= ts < current_hour)
    engine.reset_monthly_counters()
    for ts, kwh in rows:
        price = price_for_hour(ts, engine.monthly_energy_kwh)
        engine.record_hourly_consumption(ts, kwh, price)
    expected = int((current_hour - month_start).total_seconds() // 3600)
    return {"hours": len(rows), "expected_hours": expected, "missing_hours": max(0, expected - len(rows)),
            "kwh": round(engine.monthly_energy_kwh, 3)}
