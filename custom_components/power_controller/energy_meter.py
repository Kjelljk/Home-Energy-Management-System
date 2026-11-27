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

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable


@dataclass
class ClosedHour:
    hour_start: datetime
    kwh: float
    source: str          # "hour_sensor" | "integrated"
    full: bool = True    # observed for the whole hour (False after a start-up mid-hour)


@dataclass
class HourEnergyTracker:
    hour_start: datetime | None = None
    integral_kwh: float = 0.0            # ∫ grid power dt since hour_start
    last_ts: datetime | None = None
    last_kw: float = 0.0
    sensor_kwh: float | None = None      # last trusted value of the hourly sensor this hour
    sensor_seen_reset: bool = False
    started_mid_hour: bool = False
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
            self.started_mid_hour = (now - start).total_seconds() > 60
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
            closed = ClosedHour(ended, round(kwh, 4), src, full=src == "hour_sensor" or not self.started_mid_hour)
            self.started_mid_hour = False
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
                "last_kw": self.last_kw, "started_mid_hour": self.started_mid_hour}

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
        # a short restart is bridged at the last power; a long outage makes the hour an estimate
        self.started_mid_hour = (now - last).total_seconds() > 300 or bool(data.get("started_mid_hour"))


def max_hour_kwh_for_fuse(main_fuse_a: float, voltage: float = 230.0, margin: float = 1.10) -> float:
    """Most energy one hour can physically take through the main fuse (3-phase IT/TN:
    sqrt(3) x U x I), with a margin for short overloads. 32 A at 230 V -> about 14 kWh."""
    return round(math.sqrt(3) * voltage * max(1.0, float(main_fuse_a)) / 1000.0 * margin, 3)


def clean_hours(hours: list[tuple[datetime, float]], month_start: datetime, current_hour: datetime,
                max_hour_kwh: float | None = None,
                trusted: dict[datetime, float] | None = None) -> tuple[list[tuple[datetime, float, bool]], dict]:
    """Plausibility check of hourly statistics before they are used for the capacity charge.

    Returns ([(hour_start, kWh, count_peak)], report).

    * A **trusted** value (Power Controller's own integrated hour) replaces an implausible
      statistics value, and fills missing hours.
    * A value above ``max_hour_kwh`` (more than the main fuse can deliver in one hour) right after
      missing hours is the energy of the whole gap lumped into one hour (Home Assistant down,
      meter offline): it is spread evenly over the gap. Spread hours count as estimated.
    * If it is still implausible (no gap to spread over), the energy is kept for the month total
      and cost, but the hour is excluded from daily peaks / top-3 / capacity step.
    """
    trusted = dict(trusted or {})
    stats = {ts: max(0.0, float(k)) for ts, k in hours if month_start <= ts < current_hour}
    for ts, k in trusted.items():
        if month_start <= ts < current_hour and ts not in stats:
            stats[ts] = max(0.0, float(k))
    report = {"replaced_by_measured": [], "spread_hours": 0, "spread_from": [], "excluded_from_peaks": []}
    out: list[tuple[datetime, float, bool]] = []
    prev = month_start - timedelta(hours=1)
    for ts in sorted(stats):
        kwh = stats[ts]
        limit = max_hour_kwh if max_hour_kwh else float("inf")
        if kwh > limit and ts in trusted and trusted[ts] <= limit:
            report["replaced_by_measured"].append(ts.isoformat())
            kwh = trusted[ts]
        if kwh > limit:
            gap = int(round((ts - prev).total_seconds() / 3600.0)) - 1
            if gap > 0:
                share = kwh / (gap + 1)
                for g in range(1, gap + 1):
                    out.append((prev + timedelta(hours=g), share, share <= limit))
                report["spread_hours"] += gap
                report["spread_from"].append(ts.isoformat())
                kwh = share
            if kwh > limit:
                report["excluded_from_peaks"].append(ts.isoformat())
                out.append((ts, kwh, False))
                prev = ts
                continue
        out.append((ts, kwh, True))
        prev = ts
    report["corrected_hours"] = (len(report["replaced_by_measured"]) + len(report["spread_from"])
                                 + len(report["excluded_from_peaks"]))
    return out, report


def rebuild_month(engine, hours: list[tuple[datetime, float]], price_for_hour: Callable[[datetime, float], float],
                  now: datetime, max_hour_kwh: float | None = None,
                  trusted: dict[datetime, float] | None = None) -> dict:
    """Re-create the month on ``engine`` from finished hours [(hour_start, kWh)] of this month.

    ``price_for_hour(ts, month_kwh_before)`` returns the all-in price incl. VAT for that hour.
    The selected capacity step is kept; the active step is re-derived from plausible peaks only
    (see :func:`clean_hours`). Returns a summary for diagnostics.
    """
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    rows, report = clean_hours(hours, month_start, current_hour, max_hour_kwh, trusted)
    engine.reset_monthly_counters()
    for ts, kwh, count_peak in rows:
        price = price_for_hour(ts, engine.monthly_energy_kwh)
        engine.record_hourly_consumption(ts, kwh, price, count_peak=count_peak)
    expected = int((current_hour - month_start).total_seconds() // 3600)
    return {"hours": len(rows), "expected_hours": expected, "missing_hours": max(0, expected - len(rows)),
            "kwh": round(engine.monthly_energy_kwh, 3), "max_hour_kwh": max_hour_kwh, **report}
