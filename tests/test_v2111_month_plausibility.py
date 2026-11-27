"""2.11.1: implausible hours from the meter statistics must not lock a high capacity step."""
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.power_controller.energy_meter import (
    HourEnergyTracker,
    clean_hours,
    max_hour_kwh_for_fuse,
    rebuild_month,
)
from custom_components.power_controller.price_engine import EnergyPriceEngine

NOW = datetime(2026, 11, 27, 22, 20, tzinfo=timezone.utc)
MS = NOW.replace(day=1, hour=0, minute=0)


def _month(normal=2.0, holes=(), lumps=None):
    """Hourly statistics for 1..27 Nov; ``holes`` are missing hours, ``lumps`` {ts: kWh}."""
    rows = []
    t = MS
    while t < NOW.replace(minute=0):
        if t not in holes:
            rows.append((t, (lumps or {}).get(t, normal)))
        t += timedelta(hours=1)
    return rows


def test_fuse_limit():
    assert max_hour_kwh_for_fuse(32) == pytest.approx(14.02, abs=0.05)


def test_lump_after_gap_is_spread_and_does_not_lock_the_step():
    """The reported case: restarts left gaps, the first hour after each gap got the whole gap
    (31 kWh/h top-3 -> 25–50 kW step). The energy is spread over the gap instead."""
    holes, lumps = set(), {}
    for day in (10, 15, 22):                                    # three days, 15 h outage each
        start = datetime(2026, 11, day, 3, tzinfo=timezone.utc)
        holes |= {start + timedelta(hours=h) for h in range(15)}
        lumps[start + timedelta(hours=15)] = 16 * 2.0           # whole gap in one hour
    e = EnergyPriceEngine()
    e.set_selected_tariff_kw(5.0)
    s = rebuild_month(e, _month(holes=holes, lumps=lumps), lambda ts, b: 1.0, NOW, max_hour_kwh_for_fuse(32))
    assert e.monthly_max_kw == pytest.approx(2.0)               # not 32
    assert e.active_tariff_kw == pytest.approx(5.0)
    assert s["spread_hours"] == 45 and len(s["spread_from"]) == 3
    assert e.monthly_energy_kwh == pytest.approx(len(_month()) * 2.0)   # energy kept


def test_without_gap_implausible_hour_counts_energy_but_not_peak():
    bad = datetime(2026, 11, 5, 18, tzinfo=timezone.utc)
    e = EnergyPriceEngine()
    e.set_selected_tariff_kw(5.0)
    s = rebuild_month(e, _month(lumps={bad: 40.0}), lambda ts, b: 1.0, NOW, 14.0)
    assert s["excluded_from_peaks"] == [bad.isoformat()]
    assert e.daily_peaks["2026-11-05"] == pytest.approx(2.0)
    assert e.monthly_energy_kwh == pytest.approx(len(_month()) * 2.0 + 38.0)


def test_trusted_own_hour_replaces_implausible_statistics_and_fills_gaps():
    bad = datetime(2026, 11, 5, 18, tzinfo=timezone.utc)
    hole = datetime(2026, 11, 6, 9, tzinfo=timezone.utc)
    rows, rep = clean_hours(_month(holes={hole}, lumps={bad: 40.0}), MS, NOW.replace(minute=0), 14.0,
                            trusted={bad: 4.2, hole: 3.1})
    d = {ts: (k, pk) for ts, k, pk in rows}
    assert d[bad] == (4.2, True) and d[hole] == (3.1, True)
    assert rep["replaced_by_measured"] == [bad.isoformat()]


def test_closed_hour_marks_partial_start_up_hours():
    tr = HourEnergyTracker()
    t = NOW.replace(minute=30)
    closed = None
    for i in range(200):                                        # start at :30, cross the hour
        closed = tr.update(t + timedelta(seconds=10 * i), 2.0) or closed
    assert closed is not None and closed.full is False          # estimate, not trusted
    for i in range(200, 600):
        c = tr.update(t + timedelta(seconds=10 * i), 2.0)
        if c:
            closed = c
    assert closed.full is True
