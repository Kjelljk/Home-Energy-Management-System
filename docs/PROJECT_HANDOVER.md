# Power Controller – project handover

**Purpose:** give a new developer (or a new AI chat session) everything needed to continue
without the earlier conversation. Read this first, then `README.md` (features) and
`CHANGELOG.md` (per version).

**Current version:** 2.10.0. **Repository:** https://github.com/Kjelljk/Home-Energy-Management-System (public). **Status:**
- Pure-Python engines: unit-tested (92 tests pass, ~5 s).
- HA layer: syntax/undefined-name checked; options flow and the card contract/panel feed are
  exercised with HA stubs. **Never run in a real Home Assistant instance yet.**
- Frontend JS: exercised in Chromium with a mock `hass` (all buttons/steppers/selects, panel tabs,
  drag/arrow priorities, light/dark/custom themes, 390 px phone width). Not yet in real HA.

---

## 1. Goal and context
- Home energy management (HEMS) as a HA custom integration (`power_controller`), HACS repo.
- Owner: household in Bergen, Norway. **230 V IT grid** (single-phase loads sit between two phases).
  Grid operator BKK. Currently on **Norgespris**, but must work on spot (with/without strømstøtte).
- Controls EV charger (OCPP), water heater, panel heaters/heat pumps (climate), generic switches.
- Objective: minimise electricity + grid tariff (energy charge + capacity charge) with comfort,
  within the capacity step and main fuse / phase currents.
- Project language: **English only** (code, UI, docs).

### History (short)
1. v1 spec by the owner → Gemini v2.1 spec + first code (MILP, untested, many critical bugs).
2. 2.2 rewrite (LP planner, editable grid tariff, persistence, robustness) → 2.3 overload
   acceptance + English only → 2.4 shadow evaluation (Norgespris vs. spot) → 2.5 occupancy and
   home office → 2.6 object card, overview panel, plan-index bug fix → 2.6.1 hassfest/HACS fixes and brand icon → (2.7.0 = owner's own upload) → 2.8.0 uncommitted devices in the plan, occupancy selector, Norgespris remaining, panel split per module with live flow and history, optional-field fix → 2.9.0 15-minute planning, command patience, EV no-deadline + minimum SoC, heater type, Settings page, full-screen charts → 2.10.0 partial lending, calmer heat pump (30 min, PI hysteresis, hourly setpoints), thermal-model view/reset/manual, help texts, priority-stacked plan.

---

## 2. Architecture
Four loops in `coordinator.py`:

| Loop | Frequency | Does |
|---|---|---|
| 1 Price & tariff | hourly :00, new Nord Pool data, setting changes | 72-h price series, hourly consumption, capacity step |
| 2 Planner | hourly :59, any user change | 72-h LP per device + shadow plan (executor thread) |
| 3 Real-time | every 10 s + grid sensor events | limits, overload acceptance, distribution, **all actuation** |
| 4 Slow | every 60 s | RLS thermal learning, legionella tracking, persistence |

**Plan time base:** plans are solved at xx:59 with index 0 = the hour being solved (`plan_start`).
The planner runs in 15-minute slots (`plans_15m`, `plan_slots`, `slot_idx(now)` – used by the
real-time loop); `plans_72h` / `plan_result` are the hourly view for UI, history and the shadow
(`_apply_plan_result`). Never read "now" with `[0]`.

### Pure Python (no HA imports, unit-tested)
| Module | Responsibility |
|---|---|
| `const.py` | enums, config keys, IT-grid helpers, `VERSION` |
| `price_engine.py` | price models, 72-h series, spot forecast, holidays, capacity peaks, hourly allowance, baseload |
| `grid_tariff.py` | editable grid tariff (versions, periods, fees, VAT, capacity steps, presets) |
| `planner.py` | 72-h LP (scipy HiGHS, time limit) – zones, setpoints, EV, hot water, switches, legionella; fallback |
| `distributor.py` | real-time allocation by priority/plan, lending, EV feed-forward |
| `limiters.py`, `overload.py` | tariff limit now, phase limits, overload acceptance state machine |
| `thermodynamics.py` | 1R1C zone model + RLS |
| `shadow.py` | Norgespris vs. spot shadow evaluation |
| `occupancy.py` | occupancy states, home-office days, comfort masks |
| `card_model.py` | card status mapping, next action, 24-h windows, plan index |
| `actuation.py` | command patience (`should_send_command`), control settings, EV minimum-SoC deadline |

### Home Assistant layer
| Module | Responsibility |
|---|---|
| `__init__.py` | setup/unload, `ConfigEntryNotReady`, services, websocket `power_controller/overview`, static path, card auto-load (`add_extra_js_url`), admin panel (`panel_custom`) |
| `coordinator.py` | loops, persistence, actuation, shadow, occupancy, `device_attributes()` (card contract), `overview_payload()` (panel feed) |
| `config_flow.py` | hub → zones → devices; options: hub settings, home-office weekdays, grid tariff editor |
| `storage.py` | `Store` wrapper (settings, runtime, peaks, shadow, occupancy, thermal models) |
| `sensor/number/select/switch/datetime/time/binary_sensor.py` | entities |
| `www/power-controller-object-card.js` | object card + visual editor (vanilla web component) |
| `www/power-controller-panel.js` | overview panel (Overview / Devices / Priorities) |

### Frontend contracts
- **Object card** reads only `sensor.pc_<device>_plan` attributes (`coordinator.device_attributes`):
  `name, device_type, committed, priority_now, ui_status, dist_status, dist_reason, live_kw,
  allocated_kw, nominal_kw, plan_24h, setpoint_24h, predicted_temp_24h, window_start, next_action,
  plan_start, legionella_hours, hold_remaining_s, plan_priority, entities{committed, priority_now,
  control, comfort_temp, eco_temp, nominal_temp, target_soc, finish_by, runtime_hours, …}` plus
  per-type values, `forecast_only`, `governor` (PI diagnostics), `governor_enabled`. Actions are plain services: `homeassistant.toggle`, `input_number/number.set_value`,
  `datetime.set_value`, `climate.*`, `water_heater.*`.
- **Panel** has one page per module (Overview / Price / Plan / Distribution / Devices / Priorities).
  It reads `power_controller/overview` (admin websocket: kpi, plan, `flow` incl. lending `edges`,
  `norgespris`, 7 `occupancy_days`, `plan_history`, `history_entities`) plus recorder history and
  statistics, and calls `select.select_option`, `switch.toggle` and
  `power_controller.set_priorities`.
- Styling: HA theme variables only (`--primary-color`, `--card-background-color`,
  `--ha-card-border-radius`, `--ha-card-box-shadow`, `--state-climate-heat-color`, …).

---

## 3. Key design decisions (and why)
1. **LP instead of MILP** – the MILP hung for minutes; LP + post-processing solves 10 heaters + EV < 1 s.
2. **Priority = penalty weight**, not planning order; equal priorities rotate daily.
3. **Heaters get setpoint trajectories** (pre-heat ≤ +2 °C); heaters in a zone share one temperature.
4. **Hot water:** tank model with sensor, daily energy quota without; legionella in cheapest 2 h block.
5. **Cost-neutral capacity limit:** max(step − buffer, today's peak, 3rd-highest other day).
6. **Only planned loads get power**; idle reservations are lent; EV behind deadline may borrow.
7. **Grid tariff is data** (versions per year, editable in UI); BKK 2026 only a preset.
8. **Overload acceptance** inverse-time per phase; planner uses rated limits.
9. **Departure time is for planning only**; Away→Arriving when pre-heat starts, Arriving→Home at arrival.
10. **Shadow evaluation never actuates** and carries its own state (no "saving" by being colder).
11. **Occupancy drives comfort** when not out of town; a device's own schedule helper wins.
    Two separate inputs: **Mode** (Home/Away/Arriving = days ahead) and the **Occupancy select**
    (Home and awake / Sleeping / Away (work) / Home office = now, set by the owner's automation).
    The select decides the first 2 plan hours, the Power Controller times decide later hours.
    Home and awake never lowers comfort rooms (also in real time); Away (work) after the scheduled
    return keeps the house warm; Mode Away ignores the select. Persisted; initialised from the schedule.
12b. **15-minute LP with hourly capacity rows**; thermal/tank temperatures are sparse state variables.
12c. **Command patience**: min step + min interval; urgent (warning / OA limiting / projected over
    allowance) only for reductions. EV without deadline = delay penalty; minimum SoC deadline fixed
    per plug-in session. Heater type stepless/on_off affects distribution and live-power fallback.
12d. **Lending:** stepless heaters lend grant − live − margin (`distributor.lendable_kw`); on/off lend all
    when idle. Regulated heaters (PI governor) use `ControlSettings.regulated_min_interval_s` (30 min) via
    `coordinator.command_interval_s`; PI offset hysteresis 0.75 °C; `_planned_setpoint` uses the hourly
    max; pre-heat blocks are one step (`planner._heating_setpoints`).
12e. **Thermal models** keep bookkeeping (seed, samples, error EMA, hourly history, frozen/manual);
    `coordinator.thermal_zones_payload()` and service `thermal_model` (reset/set/freeze/unfreeze).
12a. **Uncommitted devices are planned as a forecast** (flat, never shifted) and added to the
    baseload the optimiser sees. EV only when connected AND charging.
12. **Committed card hides mode buttons** (targets only); uncommitted card = normal controls.
13. **Super-user vs. users:** overview panel is admin-only; object cards go on everyday dashboards.
14. **Robustness:** service calls via `_safe_call`; one failing device never stops the loop.
15. **Everything persists** across restarts.

---

## 4. Owner preferences (working style)
- Practical/operational; traceable status; no silent merging of features.
- Rigorous testing and honest statement of what is and isn't tested.
- Each version delivered as a complete ZIP with updated CHANGELOG (and README when features change).
- Recommendations tied to the actual problem with a concrete proposal.
- Theme-aware, polished UI; mobile matters (uses the HA app on Android).

---

## 5. Development workflow
```bash
pip install pytest numpy scipy
pytest -q                     # 66 tests
node --check custom_components/power_controller/www/*.js
```
- HA-stub tests (`test_options_flow_tariff.py`, `test_coordinator_card_contract.py`) skip when real
  HA is installed.
- Per change: update `CHANGELOG.md`, bump `manifest.json` **and** `const.VERSION` (cache busting),
  keep `strings.json` and `translations/en.json` identical.
- CI: `.github/workflows/validate.yml` (pytest, hassfest, HACS).
- CI status after 2.6.1: tests pass; hassfest and HACS issues fixed (description, topics and MIT license are set on GitHub). Brand icon in `custom_components/power_controller/brand/`, logo SVGs in `docs/logo/`.

---

## 6. Known gaps / next steps (priority order)
1. **Run in a real Home Assistant** and fix what breaks; add `pytest-homeassistant-custom-component`
   tests (coordinator, config flow, entities, websocket, panel registration).
2. Verify OCPP service names/parameters against the installed OCPP integration.
3. Object card: optional editing of *Comfort temperature during* (stored in config entry options).
3b. Owner's plan: calendar/schedule entity for home office and home/away events feeding the planner.
4. EV: 3-phase chargers not supported; SoC defaults to 50 % without a sensor.
5. Water heater without sensor: legionella completion inferred from power draw.
6. Consider HA config subentries instead of separate zone/device entries.
7. Validate the shadow evaluation against real bills over a few months.

---

## 7. Entity overview (hub)
- Sensors: energy price, grid tariff, limit now, hour allowance, energy this hour, monthly max,
  monthly energy/cost, capacity charge, grid power, currents L1–L3, total plan, occupancy,
  Norgespris benefit.
- Selects: mode, tariff step, priority profile, price model, shadow comparison model.
- Numbers: price-model values, mark-up, VAT, baseload, internal limit, grid-tariff periods/default/fee,
  shadow extra fee, overload settings (options).
- Switches: Home Office Today/Tomorrow; per device: Committed, Priority Now.
- Time: workday wake/leave/return/bedtime, day-off wake/bedtime. Datetime: arrival, departure, EV finish-by.
- Binary sensor: warning (overload state per phase, safe mode).
- Services: `set_priorities`, `recalculate_plan`, `reset_shadow_evaluation`, `set_home_office`.
- Websocket (admin): `power_controller/overview`. Panel: `/power-controller`.

---

## 8. How to start a new chat
Upload the latest project ZIP (rename to `.txt` if needed) and write:

> "This is my Home Assistant integration Power Controller. Read `docs/PROJECT_HANDOVER.md`, run the
> tests, and then: <request>. Deliver a new ZIP with the version bumped and CHANGELOG updated."
