# Changelog

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
