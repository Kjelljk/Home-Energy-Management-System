# Changelog

## 2.13.1

### Live power flow (Distribution page)
- The right half is ordered top-down: **Baseload** (arrow from Grid), then the **devices that are not
  controlled** directly below it (each with a dashed arrow from Grid and its measured kW), then the
  **controlled devices** fed from Distribution as before. The separate "Not controlled" box is removed –
  the devices stand there themselves. The narrow (phone) layout follows the same order.

### Appliance card
- Collapsible *Details* like the EV card: the plan strip and progress bar stay visible; state, program,
  start/finish, expected energy, power and register size are in *Details* with a one-line summary
  ("Bomull 40 · 0.95 kWh · 0.00 kW"). Open/closed is remembered per card in the browser.

### EV card
- **Warning when status and measurement disagree** (shown when it has lasted 2 minutes, removed as soon
  as it is resolved): status *Charging* but ~0 kW measured; status not charging but the car draws power;
  power sensor and charging-current sensor do not match (P ≠ I × 230 V – typically a sensor from another
  device); the charger offers a different current than Power Controller set (committed only).
- **Sensors in the configuration** under *Details*: every sensor/switch chosen for the charger with its
  entity id and its value now (live from Home Assistant); *unavailable* / *not found* in red; tap a row to
  open the entity. Also on the uncommitted card.
- Card contract: `entities.charge_switch`, `entities.transaction`, `entities.availability_switch` and
  `ev_warnings` (list of messages).

### Grid tariff on the Settings page
- New section *Grid tariff periods*: for each period of a price list edit **name, price, from/to hour,
  days and months** (none = all year), with a plain summary ("Applies Mon–Fri, 06:00–22:00, all year.").
  Save per period; unsaved periods are marked and not overwritten by the 10-s refresh. Price-list
  selector when there is more than one version. No reload – the period keeps its id and its price
  entity. Adding/removing periods, capacity steps and new price lists stay under Configure → Grid tariff.
  New admin websocket command `power_controller/tariff_period`.

### Program names (household appliances)
- Readable names instead of keys: `Dishcare.Dishwasher.Program.Eco50` → "Eco 50",
  `LaundryCare.Washer.Program.Cotton` → "Cotton", `quick_wash` → "Quick wash". Numeric codes stay.
- **Own name per program** in the register on the Settings page (e.g. the code "22" → "Bomull 40"),
  saved when you leave the field; the current program can be named before it has been learned. The name
  is used on the card, in the panel and in the register; the key is shown small underneath.
- Service `power_controller.appliance_program`: new action `name` and field `name` (also accepted by `set`).

### Tests
- 5 new tests (128 total): program names and persistence, tariff period edit and validation, EV
  status/measurement checks, coordinator end-to-end (warning only after 2 min, sensor entities in the
  contract, program name, tariff edit + error). Frontend checked in Chromium with a mock hass (wide and
  390 px flow, tariff editor incl. save/validation, register rename, cards light/dark).

## 2.13.0

### Safe mode after a restart (fix)
- Start-up grace period of 3 minutes: while waiting for the first grid reading after a restart
  there is no safe mode and nothing is switched (no more power is granted, nothing is cut). The
  hour's energy is continued from storage. The Overview shows "STARTING · waiting for the first
  grid reading" and a pill in the header.
- Safe mode is latched to the end of the hour only after a real outage (grid reading missing for
  more than 2 minutes while running). A short gap gives the safe budget only while it lasts.
- With an *Energy this hour sensor* configured the hour's energy is known, so safe mode is released
  as soon as the measurement is back.

### Household appliances (new device type, read only)
- New type *Household appliance* for dishwasher, oven, washing machine … Power Controller never
  controls or interrupts it; it plans the other devices around it.
- Home Connect Alt: choose the HA device and the entities are found automatically (operation state,
  active/selected program, start time / start in relative, finish at, remaining time, progress,
  door, remote start …). Every role can be overridden, so devices from other integrations (e.g.
  a washer from LocalThings) can be set up manually; extra state values for "running" and
  "delayed start" can be added.
- Home Connect "Start time" 17:42 is read as *starts in 17 h 42 min*; numbers use the sensor's unit
  (oven "Start In Relative" in seconds). Unavailable sensors (normal for Home Connect when idle) mean
  "not in use", not an error.
- **Program energy register** per appliance: every finished run is recorded per program with
  energy, duration and the power profile over the run, from a power sensor that may belong to
  another device (e.g. a smart plug). Until a program is learned a configurable default
  (kWh and hours) is used; programs can be added or adjusted manually and deleted.
- Planning: a program waiting for its delayed start, or running, is added as a fixed load with the
  program's learned profile (from the start time, or the remaining part from progress / remaining
  time). The plan is recalculated when a program is set, starts, ends or the start time moves.
- Object card: state, program, start and finish time, progress, expected energy (learned / manual /
  default), live power and the expected load in the 24 h strip; "Read only" instead of the
  Committed badge.
- Settings page: program register per appliance (runs, kWh, hours, last run), manual entry, delete,
  reset, and the list of entities used. Service `power_controller.appliance_program`.

### Tests
- 6 new tests (123 total): start-up grace and latching after a real outage, state/time parsing,
  role detection from Home Connect names, program register learning, expected load for delayed and
  running programs, planner around a delayed dishwasher, coordinator end-to-end (delayed start →
  run measured on a plug → register).

## 2.12.1

### Live power was replaced by an estimate (bug fix)
- **Cause:** a reading counted as stale when HA's `last_updated` was older than 2 minutes. HA only
  moves `last_updated` when the VALUE changes, so a stable reading (e.g. the EV at a steady
  1.73 kW) – or a sensor that only reports on change – was thrown away and silently replaced by
  the commanded current × 230 V (25 A → 5.75 kW). The card still said "Power".
- Freshness now uses `last_reported` (HA 2024.3+), with `last_updated` as fallback. This also
  protects the grid meter against false safe mode.
- Device power sensors have no staleness gate any more: a value is valid until the sensor is
  unavailable. Only the grid meter keeps the gate (needed for safe mode).
- New `power_source` per device: measured · measured current · offered current · commanded ·
  assumed · off. The card and panel label estimates (`*` and a tooltip) instead of pretending.
- EV without a power sensor: estimate from the new optional *Measured charging current* sensor,
  then the offered current, and only then the current Power Controller sent.

### Live values update when they change in Home Assistant
- The object card reads power, charging current, offered current, connector status and session
  energy directly from HA states (re-renders on change). The 10-s plan-sensor values are only the
  fallback.
- Panel: grid power KPI, the power-per-device legend and the device table's *Now* column are
  updated from HA states within a second of a change.

### Layout
- EV card: actual power, plan strip and SoC bar always visible; everything else in a collapsible
  *Details* section with a one-line summary ("Charging · 7.6 A · 1.73 kW · 0.31 kWh"). Open/closed
  is remembered per card in the browser. About 380 px tall collapsed (was ~660 px).
- Devices page: masonry columns – a tall card no longer leaves empty space next to it.

### Tests
- 1 new test (117 total): stable/on-change power readings stay measured; estimate chain measured
  current → offered current → commanded; off when the connector status says so.

## 2.12.0

### Capacity step selector stays fixed
- The *Tariff* select is the **desired** step and is never changed by Power Controller. The step
  in use is internal: the higher of the selection and what the month's top-3 has locked.
- When they differ, the Capacity-step KPI shows "selected 2–5 kW · in use 5–10 kW (locked by this
  month's top-3)" in warning colour, and the header shows a *Step locked* pill. Attributes
  `active_kw` / `locked_by_month` on the select.

### Safe mode
- Safe mode is held until the end of the clock hour, also when the grid measurement comes back
  (the energy of the missing period is unknown). Clear indicators: red banner on the Overview
  (since when, sensor back or not), *SAFE MODE* pill in the header, red tariff-limiter box on the
  Distribution page; the Warning binary sensor is on during safe mode.

### Hot-water model (new, learning)
- With a tank temperature sensor **and** a power sensor the water heater learns: standby loss
  (W/°C, from quiet periods), heat-up rate, and the hourly hot-water use profile for workdays and
  days off. The planner uses the learned loss and profile instead of the fixed 0.8 %/h and
  40 % morning / 60 % evening defaults.
- Settings page: one card per water heater with status, values, the hourly use profile, manual
  adjustment (loss, daily use; pauses learning), pause/resume and reset. Service
  `power_controller.hotwater_model`.

### EV charger
- New optional sensors: *Connector status* (OCPP: Charging, SuspendedEV, SuspendedEVSE,
  Preparing, Available …), *Current offered to EV*, *Energy this session*. The connector status
  decides whether the car is plugged in and charging (shown as readable text).
- New optional *Car's current limiter* (number entity): shown and adjustable on the card (+/−),
  and the plan and distribution never assume more current than this limit.
- The card shows connector status, offered current, current set by Power Controller, car limit,
  power and session energy. Power without a sensor is marked with * (estimated = set current × 230 V).

### Dashboards
- All panel charts are zoomable: scroll or pinch to zoom the time axis, drag to pan, scroll or drag
  on the left axis (or Shift+scroll) to stretch the value axis, double-click or *Reset zoom*.
- Power per device · last hour: fine resolution (step values ~every 5 s plus live samples between
  history fetches), stacked by plan priority, live value of each device in the legend.
- Phases · last 24 h: half width next to the power chart; 5-minute means from the recorder
  statistics (no more "filled band" from fast voltage swings).
- Energy plan device list sorted by plan priority (1 on top).
- Live power flow: Baseload and Not controlled at the top of the right half, fed from the Grid box;
  controlled devices below; uncommitted devices listed separately at the bottom. Arrowheads no
  longer scale with the line width.
- Object card: grid lines visible in dark themes.

### Tests
- 5 new tests (116 total): hot-water learning (loss, profile, heat-up), manual/reset/persistence,
  planner using the learned profile, safe-mode latch to the end of the hour + fixed selector,
  EV connector status / car current limit.

## 2.11.1

### Capacity step jumped to the top step (bug fix)
- **Cause:** the month is rebuilt from the meter's recorder statistics. After Home Assistant has
  been down (restarts, updates) the statistics put the energy of the whole outage into the first
  hour afterwards. Three such hours on three days gave a top-3 average of 31 kWh/h, which locked
  the 25–50 kW step: limit now ≈ 60 kW, hour allowance 49.85 kWh.
- **Plausibility check:** an hour can never exceed what the main fuse delivers
  (√3 × 230 V × fuse current, +10 %; 32 A ≈ 14 kWh/h).
  - An implausible hour right after missing hours is spread evenly over the gap.
  - Power Controller's own fully measured hours are kept (per month, persisted) and replace an
    implausible statistics hour or fill a missing one.
  - An implausible hour that cannot be explained counts for energy and cost, but not for daily
    peaks / top-3 / capacity step.
  - Start-up hours (observed only partly) are never trusted as peaks.
- Stored peaks from 2.11.0 above the fuse limit are dropped at start-up and the active step is
  re-derived; the statistics rebuild then gives the exact month.
- The Month-to-date KPI shows how many hours were corrected; a warning with the details is written
  to the log.
- `manifest.json`: `after_dependencies: [recorder]` (hassfest).

### Tests
- 5 new tests (111 total): fuse limit, lumped hours after gaps (the reported case), implausible
  hour without gap, own measured hours replacing/filling statistics, partial start-up hours.

## 2.11.0

### Energy this hour, month to date, top-3 and Norgespris (bug fix)
- **Cause:** "this hour" was the delta of the total import meter. HAN/AMS meters publish the
  register about once per hour, just after the hour, and the hour was closed at :00:05 – after
  the counter had already been reset. "This hour" therefore contained the previous hour (e.g.
  3.64 instead of 0.59 kWh), "projected" and "limit now" were wrong, and every finished hour was
  booked with ~0 kWh, so Month to date, the top-3 average and the Norgespris volume never counted
  (and seemed to restart after a restart).
- **This hour** now integrates the grid power every 10 s (like a kW→kWh utility meter that resets
  every hour). Optional hub field *Energy this hour sensor*: your own hourly utility meter is used
  directly (only after it has really reset in the new hour).
- The hour is closed in the **same step** that sees the new hour, with the energy of the hour
  that ended. A restart inside an hour continues that hour.
- **Rebuilt from the meter's history:** at start-up and 6 minutes after every hour the month
  (energy, cost, daily peaks, top-3, capacity step, Norgespris used/remaining) is rebuilt from the
  recorder's hourly statistics of the total meter. Correct after every restart and self-healing.
  The KPI shows "from meter statistics" (and missing hours) or "live".
- *P Average 60 min* is now a true rolling 60-minute average.

### Configuration changes no longer reload the integration
- Device and zone settings are re-read and replanned in place; hub settings re-subscribe the
  sensors and apply overload/notification settings in place. Only saving the grid tariff still
  reloads (it rebuilds the per-period price entities).

### Notifications (new)
- Hub settings: *Notification service* (e.g. `notify.mobile_app_pixel`), quiet hours (default
  23:00–07:00, only errors are sent). Same message at most once per hour (daily events once a day).
- Errors: grid sensor stale (safe mode), OCPP command not accepted, planner fallback, device not
  following its setpoint for 15 min, tomorrow's prices missing after 15:00.
- Early warnings: the hour will exceed the step even with all controlled devices off, new capacity
  step locked, EV will not reach its minimum/target in time, phase overload limiting, Norgespris
  volume runs out this month.
- Buttons in the app: *Ignore today* on every message, *Priority now* for an EV that will not make
  it. Settings page: notification log, active conditions, *Send test* (service `test_notification`).
- OCPP commands are now sent as blocking calls so a rejection is detected.

### Plan and cards
- EV without deadline: waiting is free for the first 24 h; the delay cost only applies to waiting
  beyond 24 h (so tonight's cheaper hours are used even with a small price difference).
- "Next …" on the cards uses the 15-minute plan ("next 21:30", "planned now" when the block has
  started) – no more "Next 21:00" at 21:35.
- Devices that are not controlled show only measured power ("0.75 kW measured" / "no power
  sensor") instead of "0.00 / 0.00 kW".
- "Lent" renamed to "Lent out" (power a device does not use and gives to others); a running
  stepless device shows "lends x kW".

### Tests
- 9 new tests (106 total): integrated hour and closing, the meter-after-the-hour bug scenario,
  hourly sensor reset handling, restart inside an hour, month rebuild from statistics,
  notification dedupe/quiet/snooze/re-arm, daily events, quarter-based next action, and a
  coordinator test that runs the real fast loop across an hour change.

## 2.10.2

### EV charger: start/stop fixed
- **Bug fix:** after a stop the next start ramped from 0 A in 2 A steps, so the first command was
  2 A – below the minimum current – and the charger never started. A start now always begins at
  least at the minimum current, and the charging current goes straight to the granted value
  (command patience limits how often it changes; the slow ramp is no longer used).
- Start sequence: availability switch on (if configured and off) → current limit (`limit_amps`, no
  transaction yet) → charge switch on. *Priority now* skips the restart pause.
- As soon as a transaction id appears, the current is re-sent at once as a TxProfile on it.
- Stop: 0 A on the transaction, then charge switch off.
- Without a power sensor the EV's live power is 0 while the charge switch is off (it showed the
  nominal power, e.g. "5.00 / 5.0 kW", although nothing was charging).
- The card shows the last EV action (start / stop / waiting for restart pause).

### EV: the car's own target SoC
- New optional field *Car's target SoC* (number / input_number). When set, it is the charge target
  for the plan, the card's +/- buttons change it (label "Car target"), and the Power Controller
  *Target SoC* number mirrors it.

### Includes 2.10.1
- OCPP current as a TxProfile on the active transaction (as the owner's automation), `limit_amps`
  fallback before a transaction exists.
- EV settings form: optional transaction/availability/power/SoC fields, robust numeric defaults;
  the settings dialog shows the real error instead of "Unknown error".

### Tests
- 5 new tests (97 total): TxProfile, transaction id, start never below minimum, full start
  sequence with Priority now (limit → switch → TxProfile), car target SoC.

## 2.10.0

### Redistribution: the unused part is lent
- Previously a heater only lent its reservation when it used less than 15 % of it – and then all of
  it. Now a **stepless** heater lends the unused part: grant − live − margin (margin = max(0.2 kW,
  15 % of the grant)). A heat pump using 0.6 of 1.5 kW lends about 0.7 kW. **On/off** heaters still
  lend everything while their own thermostat has switched them off, and nothing while running.
- The lender keeps its grant; when it needs more, the next 10-second cycle shrinks the loan.
- Each allocation reports the amount actually lent (`lent_kw`).

### Calmer heat pump (heaters regulated by an external room sensor)
- Own minimum interval between setpoint changes: **30 minutes** (number *Regulated Heater Minimum
  Interval*). Other devices keep *Command Minimum Interval*. Urgent reductions (tariff step / phase)
  still go out at once.
- PI regulator hysteresis: the offset is held in 0.5 °C steps and only moves when the PI output has
  drifted ≥ 0.75 °C away – no more 21.0 ↔ 21.5 flipping every 10 s. Target changes pass through.
- The plan changes a setpoint at most once per clock hour (highest quarter of the hour), and a
  pre-heat block is one step to its highest value instead of a 0.5 °C staircase.

### Thermal models (new view on the Settings page)
- One card per zone: status (learning / settling / stable / manual), room temperature, window state,
  heaters with type and heating coefficient, and the learned values in plain units:
  heat loss (W/°C), heat capacity (kWh/°C), time constant (h), power to hold 21 °C at 0/−10 °C,
  cooling in 2 h without heat, prediction error and number of learning steps – each compared with
  the start values and explained with a "?".
- 7-day learning history chart (heat loss and time constant).
- **Adjust manually** (heat capacity + heat loss; pauses learning), **pause/resume learning** and
  **reset to start values**. New service `power_controller.thermal_model` (reset/set/freeze/unfreeze).
- The model stores its bookkeeping (start values, samples, skipped steps, error, history).
  Zones stored by older versions get their start values from area and insulation.

### Settings page
- A **?** next to every parameter with an explanation (hover, tap or keyboard).
- Selects with profiles (price model, shadow model, mode, occupancy, priority profile) list every
  option with a description in the help box, and show the description of the selected option.

### Dashboards
- Energy plan stacked bottom-up: baseload, devices that are not controlled, then **priority 1, 2, …**
  The legend and tooltip follow the same order.
- Distribution decisions: columns **Grant / Live / Lent** (kW).
- Phase chart: voltages below 100 V are treated as missing (a dead or faulty sensor can no longer
  squash the 215–230 V window). A series without valid values is marked "no data" with a warning.

### Tests
- 11 new tests (92 total): partial lending, PI hysteresis, one-step pre-heat, setpoint changes per
  hour, regulated-heater interval, thermal model bookkeeping/reset/manual/persistence, payload.

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
