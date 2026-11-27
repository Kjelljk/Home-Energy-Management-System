# Changelog

## 2.9.0

### 15-minute planning
- The planner works in 15-minute slots over the whole 72 hours (288 slots) using the Nord Pool
  quarter prices. The capacity charge is still enforced per clock hour (sum of the four quarters
  ≤ hour allowance), as the grid operator bills it. Hourly inputs are expanded to quarters.
- Temperatures are now explicit state variables (sparse model): 10 heaters + EV in ~0.3 s.
- The real-time loop uses the current quarter of the plan; the dashboards keep the hourly view
  (kWh per hour). The Price page shows 15-minute prices.

### Command patience
- New setpoints / charging currents are only sent when the change is meaningful (EV ≥ 2 A,
  setpoint ≥ 0.5 °C) and at most every 5 minutes. Reductions go out at once only when the hour
  allowance or a phase is at risk; increases are always patient. Common settings as number
  entities: *Command Minimum Interval*, *EV Current Minimum Step*, *Setpoint Minimum Step*.
- The object card shows the last command and when the next change is allowed.

### EV: no deadline and minimum state of charge
- New switch *Deadline enabled* per EV. Off = no deadline: charge to the target as cheaply as
  possible within 72 h, with a small cost per hour of delay (*EV Delay Penalty*, default
  2 øre/kWh/h) so it does not wait days for a few øre.
- New *Minimum state of charge* (default 30 %, switch + number): reached within 24 h of plugging
  in. The deadline is fixed per session (or from when it was switched on), so it cannot slide.

### Heater type
- Heating devices have a *Heater type*: **stepless** (modulates, e.g. heat pump) or **on/off**
  (full power or nothing). The distribution grants stepless heaters part-load power, assumes
  on/off heaters at full power, and detects idle reservations accordingly.

### Dashboards
- Full-screen button on every chart and on the power-flow diagram.
- Distribution flow: limiters in the left half, devices in the right half; connections run in the
  gap between them and are drawn below the boxes. Lending arcs on the right.
- Phase chart: voltage axis fixed to 215–230 V and widened only when a phase leaves the window.
- New **Settings** page in the panel for the values you change often (price model and rates,
  grid-tariff prices, times, mode/occupancy, baseload, limits and command patience) with full
  entity IDs; link to the integration for sensors, devices and the full tariff editor.

### Tests
- 9 new tests (81 total): quarter prices, hour cap with quarters, quarter vs. hourly steady state,
  planner speed at 288 slots, EV without deadline, minimum SoC, fixed session deadline,
  command patience, stepless vs. on/off distribution.

## 2.8.0

### Plan includes every device
- Devices that are **not committed** are now part of the 72 h plan as a flat, never-shifted
  forecast (previously they were ignored, so plan and capacity were too optimistic):
  heating = steady-state power for the device's own setpoint, hot water / switches = daily amount
  spread evenly, EV = only when connected **and** charging. The forecast is treated like baseload,
  so committed devices are planned in the capacity that is really left.
- Object card and panel show the forecast for uncommitted devices (lighter colour).

### Occupancy selector (new) – separate from Mode
- New select **Power Controller Occupancy**: Home and awake / Sleeping / Away (work) / Home office.
  Meant to be set by the user or an automation; it stays until changed and is persisted.
- The selector decides the current hour(s), the Power Controller times decide later hours.
  Home and awake never lowers rooms that need comfort (also in real time); Away (work) after the
  scheduled return keeps the house warm; Mode Away ignores the selector. On first start the
  selector takes the schedule's current state.

### Price
- Norgespris remaining this month: new sensor *Norgespris Remaining* (used, remaining, average per
  day, projected month total and the date the volume is expected to run out).

### Distribution diagnostics
- The distributor records **who lends to whom** (free headroom first, then idle reservations) and a
  summary (budget, reserved, lent, unused).
- PI regulator diagnostics per device (room temperature, target, error, P/I terms, offset,
  saturation, setpoint sent). The regulator state is now per device instead of per zone.

### Panel (super user) – one page per module
- **Overview** split into *Now & history* (KPIs, power per device last hour incl. "Other",
  phase currents and voltages last 24 h, consumption 3 days back / 3 days ahead) and *Plan*.
- **Price**: price split, 72 h price, Norgespris volume gauge and projection, grid tariff,
  Norgespris vs. spot.
- **Plan**: 72 h plan incl. forecasts, occupancy selector, 7-day occupancy schedule (home-office
  switches for today and tomorrow), **plan vs. actual** (7 days, stored by Power Controller).
- **Distribution**: live flow diagram grid → grid limiter → tariff limiter → overload acceptance →
  distribution → devices, with lending arrows, phases and the decision per device.
- "How this module works" diagrams on each page (also in the README).

### Object card
- 24 h strip now has an axis, units (kWh per hour), the setpoint line (°C) for heating/hot water
  and shows the value for the hour you tap or hover.
- Expandable **Regulator** section when an external room sensor controls the device.

### Configuration
- **Bug fix:** optional entity fields (e.g. the zone's window/door sensor) could not be left empty
  because they were given an empty default. All optional entity fields are now truly optional
  and can also be cleared again.
- The configure dialogs list the selected entities with their **full entity ID**.
- Optional phase voltage sensors (L1-L2, L2-L3, L3-L1) on the hub for the history charts.

### Documentation
- README rewritten around the three modules; technical block diagrams in `docs/img/`.
- 6 new tests (72 total).

## 2.6.1

### Validation fixes (hassfest / HACS)
- Translations: `EV Battery SoC Sensor [%]` → `(%)` (hassfest read `[%` as a combined
  translation) and the `{name}` placeholder in the delete-version text is no longer in quotes.
- `manifest.json`: correct repository links (`documentation`, new `issue_tracker`), codeowner
  `@Kjelljk`, version 2.6.1.
- Repository link in the object card's card-picker entry updated.
- Brand icon added: `custom_components/power_controller/brand/icon.png` (256 px) and
  `icon@2x.png` (512 px). Logo as SVG in `docs/logo/` (with background, transparent, square).

## 2.6.0 (draft)

### Object card for everyday dashboards (new)
- `custom:power-controller-object-card` – one card per device, designed after the owner's own
  template cards: header with icon, name and state line; Committed/Uncommitted badge (tap to toggle).
- New **distribution status line**: Running, Idle · capacity lent, Waiting (next start), Blocked,
  Shedding, Priority now, Manual – with live kW against granted kW.
- Committed: 24-hour plan strip, next action ("Next heat 03:00–06:00 to 22.0 °C", legionella,
  EV charging window, switch runtime), target controls (comfort/eco via the configured
  input_number/number entities, nominal hot-water temperature, required runtime, EV target %
  and ready-by time), *Priority now*, anti-cycling countdown.
- Uncommitted: full manual controls built from the entity's own attributes (HVAC modes,
  setpoint, presets, fan, swing, water-heater operation, on/off).
- Visual editor (entity pickers), stub config for the card picker, more-info on tap.
- Steppers are debounced (one service call after 0.7 s) with optimistic display.
- Loaded automatically via `add_extra_js_url` (cache-busted per version).

### Overview panel (new, admin only)
- Sidebar panel *Power Controller*: KPIs, 72-hour stacked plan with all-in price, spot and hour
  allowance, phases with overload-acceptance state, device table, occupancy with home-office
  toggles, Norgespris vs. spot, grid tariff and capacity steps.
- Tabs: *Devices* (all object cards) and *Priorities* (drag/arrow reordering → `set_priorities`).
- Data via new admin websocket command `power_controller/overview` (one JSON message, every 10 s).
- Everything uses Home Assistant theme variables only – custom themes are respected.

### Backend
- **Bug fix:** plans are solved at xx:59 with hour 0 = the current hour, but the real-time loop,
  setpoints and Away→Arriving used index 0 during the *next* hour (one hour late). New
  `plan_start` + `plan_idx()` select the right hour everywhere.
- New pure module `card_model.py` (status mapping, next action, 24 h windows) with tests.
- `sensor.pc_<device>_plan` attributes extended (single card contract): `name`, `ui_status`,
  `dist_reason`, `next_action`, `window_start`, `plan_24h`, `setpoint_24h`, `predicted_temp_24h`,
  `plan_start`, related `entities`, and per-type values (temperatures, SoC/target/finish-by,
  runtime). Full 72-hour arrays are kept.
- `manifest.json`: dependencies `frontend`, `http`, `panel_custom`, `websocket_api`.
- 7 new tests (66 total) incl. an HA-stub smoke test of the card contract and the panel feed.
  The JavaScript was exercised in Chromium with a mock `hass` (all actions, light/dark/custom themes,
  phone width) – not yet in a real Home Assistant.

## 2.5.0 (draft)

### Occupancy-based planning and home office
- New module `occupancy.py`: per 15 minutes `sleep`, `home_awake`, `away_work` or `home_office`
  from workday/day-off clock times, the holiday calendar, the workday sensor and home-office days.
- Heating devices get *Comfort temperature during*. A device's own schedule helper still wins.
- Hot water nominal temperature and the baseload follow occupancy.
- *Home Office Today/Tomorrow* switches, recurring weekdays (options flow), `set_home_office` service.
- Six time entities; sensor *Occupancy*. 8 new tests.

## 2.4.0 (draft)

### Shadow evaluation: Norgespris vs. spot
- New module `shadow.py`: second, non-actuating planner for spot + electricity support (or spot).
- Three ledgers per hour (actual, passive spot, optimised spot) with own capacity peaks.
- Carried shadow state (zone/tank offsets, EV balance, daily counters); EV settled at unplug;
  open balances valued at the current shadow price.
- Sensor *Norgespris Benefit This Month*, select *Shadow Comparison Model*, number *Shadow Extra
  Monthly Fee*, service `reset_shadow_evaluation`. 6 new tests.

## 2.3.0 (draft)

### Overload acceptance (new)
- New module `overload.py`: per-phase state machine (ok → accepting → limiting).
  - Small overloads are tolerated longer than large ones (default: 5 % for 10 min, 20 % for 2 min).
  - Then the limit ramps down to the rated current (10 min / 5 min).
  - At ≥ 1.5 × rated current the limit is applied immediately.
  - The budget refills faster the lower the load.
- While an overload is being accepted, nothing is cut, but no additional power is granted.
- Replaces the old "overload credit" counter, which used a fixed dt and was hard to tune.
- All parameters are configurable under *Sensors and general settings*; per-phase status is an
  attribute on the warning binary sensor.

### Project
- Project is English only (UI, code, docs). Norwegian translation removed.
- README, GitHub Actions (pytest, hassfest, HACS) and `.gitignore`.

## 2.2.0 (draft)

### Editable grid tariff (nothing hard-coded)
- New module `grid_tariff.py`: the tariff is a list of **price-list versions**, each with a
  valid-from date, so next year's prices can be entered in advance. The 72-hour plan switches
  version at midnight (e.g. 1 January).
- Each version has:
  - a default price for all hours not covered by a period
  - **price periods** with name, price, weekdays, hours (from–to, may cross midnight) and
    months (season). The first matching period wins; order is adjustable.
  - consumption tax + Enova fee
  - public holidays: "same as Sunday" or "normal weekday"
  - prices entered incl. or excl. VAT (converted automatically)
  - **capacity steps** (upper kW limit and monthly price, any number of steps) and whether
    hitting the limit exactly counts in the next step
- Editing: *Settings → Devices & services → Power Controller → Configure → Grid tariff*.
  Changes are stored on "Save and close", after which the integration reloads.
- Quick price changes from the dashboard: one `number` entity per period of the active version,
  plus "Grid Price Other Hours" and "Grid Energy Fee".
- New sensors: `sensor.power_controller_grid_tariff` (current energy charge + period; all
  periods and steps as attributes) and `sensor.power_controller_capacity_charge_this_month`.
- The capacity step selector gets its options from the active price list.
- Templates: "BKK 2026", "Day/night" and "Flat" – starting points only.

### Price model (works without Norgespris)
- New `select`: Norgespris / Spot + electricity support / Spot. Rates and volume caps editable.
- Spot price forecast for unknown hours: same weekday last week, scaled by today's level.
- Supplier mark-up can be entered.

### Planner
- MILP replaced by LP (HiGHS) with a time limit. 10 heaters + EV: < 1 s (previously minutes or hang).
- One shared temperature model per zone. The plan produces per-hour setpoints (pre-heating),
  not just comfort/eco.
- Water heater: tank model with sensor, daily energy quota without sensor, legionella in the
  cheapest 2-hour block.
- Independent fallback for all device types.

### Power and real-time control
- Cost-neutral hourly limit: uses today's peak and the 3rd-highest peak of other days.
- No capacity step-up before three distinct days exist.
- Unplanned loads no longer get "free" power.

### Robustness in Home Assistant
- All settings and runtime state survive a restart.
- `ConfigEntryNotReady` when the hub entry is not loaded yet.
- Service calls (OCPP, climate, switch) can no longer stop the control loop.
- Schedules are read per hour via `schedule.get_schedule`; weather forecast via `weather.get_forecasts`.

### Not done yet
- Lovelace cards and dashboard YAML are not yet updated to the new entity contract.
- Coordinator and entity platforms have not been run against a real Home Assistant
  (only syntax/name checks and the options flow with stubs).
