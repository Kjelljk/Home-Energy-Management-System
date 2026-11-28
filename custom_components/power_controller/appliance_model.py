"""Household appliances (dishwasher, oven, washing machine …) – pure Python, unit-tested.

Power Controller only READS these devices (2.13.0). It must know:

* the state: off / delayed start (waiting) / running / finished
* when a delayed program will start (e.g. "Start time 17:42" on Home Connect = starts in
  17 h 42 min) and, while running, how much is left
* which program is selected / running

and it keeps an **energy register per program** (runs, average kWh, duration and the power
profile over the run), learned from a power sensor (which may belong to another device, e.g. a
smart plug). The planner adds the expected load of a waiting or running program as a fixed,
never-shifted load, so the other devices are planned around it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re

SLOT_MIN = 15                      # profile resolution
PROFILE_SLOTS = 32                 # normalised profile: 32 points over the run
EMA_NEW = 0.3                      # weight of a new run once a program has >= 3 runs
MIN_RUN_KWH = 0.03

DEFAULT_RUNNING = ("run", "running", "on", "wash", "washing", "rinse", "rinsing", "spin", "spinning",
                   "drying", "dry", "heating", "cooking", "preheating", "inuse", "busy", "active")
DEFAULT_DELAYED = ("delayedstart", "delayed", "delay", "scheduled", "reserved", "reservation", "waiting")
DEFAULT_FINISHED = ("finished", "end", "done", "complete", "completed")
IDLE = ("ready", "inactive", "off", "standby", "idle", "unavailable", "unknown", "", "none", "pause", "paused",
        "actionrequired", "aborting", "error")

ROLE_KEYS = ("operation_state", "active_program", "selected_program", "start_in", "finish_at",
             "remaining", "progress", "door", "remote_start", "energy_forecast", "water_forecast",
             "cavity_temp", "setpoint_temp", "power_status")

# Home Connect (Alt) entity-id / translation-key fragments per role, most specific first
HC_HINTS: dict[str, tuple[str, ...]] = {
    "operation_state": ("operation_state", "operationstate"),
    "active_program": ("active_program", "activeprogram"),
    "selected_program": ("selected_program", "selectedprogram"),
    "start_in": ("start_in_relative", "startinrelative", "start_time", "starttime", "start_in"),
    "finish_at": ("finish_at", "finishat", "finish_in", "finishinrelative"),
    "remaining": ("remaining_program_time", "remainingprogramtime", "remaining"),
    "progress": ("program_progress", "programprogress", "current_programme_progress", "progress"),
    "door": ("door",),
    "remote_start": ("remote_start", "remotecontrolstartallowed", "remotestart"),
    "energy_forecast": ("energy_forecast", "energyforecast", "_energy"),
    "water_forecast": ("water_forecast", "waterforecast", "_water"),
    "cavity_temp": ("cavity_temperature", "cavitytemperature", "current_cavity"),
    "setpoint_temp": ("setpoint_temperature", "setpointtemperature", "target_temperature"),
    "power_status": ("power_status", "powerstate", "power_state"),
}


def norm(state: str | None) -> str:
    return re.sub(r"[\s_\-.]", "", str(state or "")).lower()


def classify(state: str | None, extra_running: tuple = (), extra_delayed: tuple = ()) -> str:
    """'running' | 'delayed' | 'finished' | 'idle' from an operation-state value."""
    n = norm(state)
    # Home Connect sends full keys like BSH.Common.EnumType.OperationState.DelayedStart
    n = n.rsplit("operationstate", 1)[-1]
    if n in {norm(x) for x in (*DEFAULT_DELAYED, *extra_delayed)}:
        return "delayed"
    if n in {norm(x) for x in (*DEFAULT_RUNNING, *extra_running)}:
        return "running"
    if n in {norm(x) for x in DEFAULT_FINISHED}:
        return "finished"
    return "idle"


def parse_duration_h(state: str | None, unit: str | None = None) -> float | None:
    """Relative time as hours. '17:42' = 17 h 42 min, '1:05:30' = h:m:s, numbers use the unit
    (s / min / h, default seconds as Home Connect reports). '0:00' / 0 -> None."""
    s = str(state or "").strip()
    if s.lower() in ("", "unknown", "unavailable", "none"):
        return None
    if ":" in s:
        try:
            parts = [float(x) for x in s.split(":")]
        except ValueError:
            return None
        h = parts[0] + (parts[1] / 60 if len(parts) > 1 else 0) + (parts[2] / 3600 if len(parts) > 2 else 0)
        return h if h > 0 else None
    try:
        v = float(s)
    except ValueError:
        return None
    u = str(unit or "s").strip().lower()
    h = v / 3600 if u in ("s", "sec", "seconds") else v / 60 if u in ("min", "minutes", "m") else v if u in ("h", "hours") else v / 3600
    return h if h > 0 else None


def parse_timestamp(state: str | None, now: datetime) -> datetime | None:
    s = str(state or "").strip()
    if not s or s.lower() in ("unknown", "unavailable", "none"):
        return None
    try:
        ts = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None and now.tzinfo is not None:
        ts = ts.replace(tzinfo=now.tzinfo)
    return ts


def detect_roles(entities: list[tuple[str, str]]) -> dict[str, str]:
    """Map roles to entity ids from (entity_id, translation_key/original name) of one device."""
    out: dict[str, str] = {}
    for role, hints in HC_HINTS.items():
        best = None
        for hint in hints:
            for eid, key in entities:
                dom = eid.split(".", 1)[0]
                if dom not in ("sensor", "binary_sensor", "select", "switch"):
                    continue
                if role in ("door", "remote_start") and dom not in ("binary_sensor", "sensor"):
                    continue
                if role in ("selected_program",) and dom not in ("sensor", "select"):
                    continue
                hay = f"{eid.lower()} {str(key or '').lower()}"
                if hint in hay.replace(" ", "_") or hint in hay.replace(" ", ""):
                    best = eid
                    break
            if best:
                break
        if best and best not in out.values():
            out[role] = best
    return out


@dataclass
class ProgramStats:
    runs: int = 0
    kwh: float = 0.0              # average energy per run
    duration_h: float = 0.0       # average duration
    profile: list[float] = field(default_factory=list)   # PROFILE_SLOTS shares (sum 1) of the energy
    last_kwh: float | None = None
    last_duration_h: float | None = None
    last_ts: str | None = None
    manual: bool = False

    def to_dict(self) -> dict:
        return dict(runs=self.runs, kwh=round(self.kwh, 4), duration_h=round(self.duration_h, 4),
                    profile=[round(x, 5) for x in self.profile], last_kwh=self.last_kwh,
                    last_duration_h=self.last_duration_h, last_ts=self.last_ts, manual=self.manual)


def _resample(values: list[float], n: int) -> list[float]:
    """Energy per bucket -> n equal parts of the run, as shares summing to 1 (cumulative
    energy interpolated linearly)."""
    tot = sum(values)
    if not values or tot <= 0:
        return [1.0 / n] * n
    m = len(values)
    cum = [0.0]
    for v in values:
        cum.append(cum[-1] + v)

    def at(x: float) -> float:
        i = min(m - 1, int(x))
        return cum[i] + (cum[i + 1] - cum[i]) * (x - i)

    pts = [at(k / n * m) for k in range(n + 1)]
    return [(pts[k + 1] - pts[k]) / tot for k in range(n)]


@dataclass
class ApplianceModel:
    device_id: str
    default_kwh: float = 1.0
    default_duration_h: float = 2.0
    programs: dict[str, ProgramStats] = field(default_factory=dict)
    # running session
    run_program: str | None = None
    run_start: str | None = None
    run_buckets: list[float] = field(default_factory=list)   # kWh per 15-min bucket since start
    last_ts: str | None = None
    last_kw: float = 0.0
    last_state: str = "idle"

    # ------------------------------------------------------------------ register
    def stats(self, program: str | None) -> ProgramStats:
        st = self.programs.get(program or "")
        if st and (st.runs > 0 or st.manual):
            return st
        return ProgramStats(kwh=self.default_kwh, duration_h=self.default_duration_h)

    def record_run(self, program: str | None, kwh: float, duration_h: float, buckets: list[float], ts: str) -> None:
        key = program or "Unknown program"
        st = self.programs.setdefault(key, ProgramStats())
        prof = _resample(buckets, PROFILE_SLOTS)
        a = 1.0 if st.runs == 0 else max(EMA_NEW, 1.0 / (st.runs + 1))
        st.kwh = kwh if st.runs == 0 else (1 - a) * st.kwh + a * kwh
        st.duration_h = duration_h if st.runs == 0 else (1 - a) * st.duration_h + a * duration_h
        st.profile = prof if not st.profile else [(1 - a) * x + a * y for x, y in zip(st.profile, prof)]
        st.runs += 1
        st.last_kwh, st.last_duration_h, st.last_ts = round(kwh, 3), round(duration_h, 3), ts
        st.manual = False

    def set_manual(self, program: str, kwh: float | None, duration_h: float | None) -> None:
        st = self.programs.setdefault(program, ProgramStats())
        if kwh is not None:
            st.kwh = max(0.0, float(kwh))
        if duration_h is not None:
            st.duration_h = max(0.05, float(duration_h))
        st.manual = True

    # ------------------------------------------------------------------ learning from live data
    def update(self, now: datetime, state_class: str, program: str | None, power_kw: float | None) -> dict | None:
        """Call every control cycle. Returns the finished run (dict) when a run ends."""
        finished = None
        kw = max(0.0, float(power_kw)) if power_kw is not None else None
        if state_class == "running" and self.run_start is None:
            self.run_start = now.isoformat()
            self.run_program = program
            self.run_buckets = []
            self.last_ts = now.isoformat()
            self.last_kw = kw or 0.0
        elif state_class == "running":
            if program and not self.run_program:
                self.run_program = program
            prev = datetime.fromisoformat(self.last_ts) if self.last_ts else now
            dt_h = max(0.0, min(0.1, (now - prev).total_seconds() / 3600))
            if kw is not None:
                e = (self.last_kw + kw) / 2 * dt_h
                idx = int((now - datetime.fromisoformat(self.run_start)).total_seconds() // (SLOT_MIN * 60))
                while len(self.run_buckets) <= idx:
                    self.run_buckets.append(0.0)
                self.run_buckets[idx] += e
                self.last_kw = kw
            self.last_ts = now.isoformat()
        elif self.run_start is not None:
            start = datetime.fromisoformat(self.run_start)
            dur = (now - start).total_seconds() / 3600
            kwh = sum(self.run_buckets)
            if kwh >= MIN_RUN_KWH and dur >= 0.05:
                self.record_run(self.run_program, kwh, dur, list(self.run_buckets), now.isoformat())
                finished = {"program": self.run_program or "Unknown program", "kwh": round(kwh, 3),
                            "duration_h": round(dur, 3)}
            self.run_start, self.run_program, self.run_buckets = None, None, []
        self.last_state = state_class
        return finished

    # ------------------------------------------------------------------ expected load
    def expected_load(self, now: datetime, plan_start: datetime, slots: int, slot_h: float,
                      state_class: str, program: str | None, start_at: datetime | None,
                      remaining_h: float | None, progress_pct: float | None) -> list[float]:
        """kW per plan slot for a waiting (delayed) or running program; zeros otherwise."""
        out = [0.0] * slots
        st = self.stats(program)
        if st.kwh <= 0 or st.duration_h <= 0:
            return out
        prof = st.profile or [1.0 / PROFILE_SLOTS] * PROFILE_SLOTS
        if state_class == "delayed" and start_at is not None:
            run_from, frac0 = start_at, 0.0
            dur = st.duration_h
        elif state_class == "running":
            if progress_pct is not None and 0 < progress_pct < 100:
                frac0 = progress_pct / 100
            elif remaining_h is not None and st.duration_h > 0:
                frac0 = max(0.0, min(0.98, 1 - remaining_h / st.duration_h))
            elif self.run_start:
                frac0 = max(0.0, min(0.98, (now - datetime.fromisoformat(self.run_start)).total_seconds() / 3600 / st.duration_h))
            else:
                frac0 = 0.0
            dur = remaining_h if remaining_h is not None else st.duration_h * (1 - frac0)
            run_from = now
        else:
            return out
        if dur <= 0:
            return out
        k0 = int(frac0 * PROFILE_SLOTS)
        rest = prof[k0:] or [1.0]
        share = sum(rest) or 1.0
        energy = st.kwh * share if state_class == "running" else st.kwh
        n = len(rest)
        for i, p in enumerate(rest):
            a = run_from + timedelta(hours=dur * i / n)
            b = run_from + timedelta(hours=dur * (i + 1) / n)
            e = energy * p / share
            # spread e over the plan slots overlapping [a, b)
            t = a
            while t < b:
                idx = int((t - plan_start).total_seconds() // (slot_h * 3600))
                slot_end = plan_start + timedelta(hours=slot_h * (idx + 1))
                seg_end = min(b, slot_end)
                part = (seg_end - t).total_seconds() / max(1.0, (b - a).total_seconds())
                if 0 <= idx < slots:
                    out[idx] += e * part / slot_h
                t = seg_end
        return [round(x, 4) for x in out]

    # ------------------------------------------------------------------ persistence / view
    def to_dict(self) -> dict:
        return {"device_id": self.device_id, "default_kwh": self.default_kwh, "default_duration_h": self.default_duration_h,
                "programs": {k: v.to_dict() for k, v in self.programs.items()},
                "run_program": self.run_program, "run_start": self.run_start, "run_buckets": self.run_buckets,
                "last_ts": self.last_ts, "last_kw": self.last_kw, "last_state": self.last_state}

    @classmethod
    def from_dict(cls, d: dict) -> ApplianceModel:
        m = cls(device_id=d["device_id"], default_kwh=float(d.get("default_kwh", 1.0)),
                default_duration_h=float(d.get("default_duration_h", 2.0)))
        for k, v in (d.get("programs") or {}).items():
            m.programs[k] = ProgramStats(**{f: v.get(f) for f in ProgramStats.__dataclass_fields__ if f in v})
        m.run_program, m.run_start = d.get("run_program"), d.get("run_start")
        m.run_buckets = list(d.get("run_buckets") or [])
        m.last_ts, m.last_kw, m.last_state = d.get("last_ts"), float(d.get("last_kw") or 0.0), d.get("last_state", "idle")
        return m

    def summary(self) -> dict:
        rows = []
        for name, st in sorted(self.programs.items(), key=lambda kv: -kv[1].runs):
            rows.append({"program": name, **st.to_dict(),
                         "avg_kw": round(st.kwh / st.duration_h, 3) if st.duration_h else None})
        return {"programs": rows, "default_kwh": self.default_kwh, "default_duration_h": self.default_duration_h,
                "running": self.run_start is not None, "run_program": self.run_program,
                "run_kwh_so_far": round(sum(self.run_buckets), 3), "run_start": self.run_start}
