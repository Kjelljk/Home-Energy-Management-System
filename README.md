<p align="center"><img src="docs/logo/logo.svg" width="160" alt="Power Controller logo"></p>

# Power Controller (Home Assistant)

> ⚠️ **Work in progress – not yet tested in a real Home Assistant instance.**
> The calculation and planning engines are covered by unit tests. The integration layer
> and the frontend have been checked with stubs/mocks only.

Home energy management for a Norwegian household on a 230 V IT grid. The integration plans
and controls EV charging, water heaters, panel heaters/heat pumps and switches so that the
total cost of **electricity + grid tariff** is minimised without exceeding the selected
capacity step or the main fuse / phase current limits.

## Features
- **Price models:** Norgespris (fixed state price), spot with electricity support
  (strømstøtte), or plain spot. Missing spot prices are forecast from the same weekday last
  week, scaled to today's price level.
- **Editable grid tariff:** price-list versions with a valid-from date, time-of-use periods
  (weekdays, hours, months), fees, VAT handling, holiday rule and capacity steps – all
  edited in the UI. Templates (e.g. BKK 2026) are only starting points.
- **72-hour plan:** linear optimisation (HiGHS) with a thermal model per zone, pre-heating,
  EV deadlines, legionella cycle and switch runtimes.
- **Real-time distribution** every 10 s by priority and plan, with lending of unused capacity.
- **Cost-neutral hourly limit:** uses today's peak and the month's top-3 rule (kapasitetsledd).
- **Overload acceptance:** small overloads are tolerated longer than large ones, then the
  limit ramps down to the rated current.
- **Occupancy-based planning:** expected wake/leave/return/bedtimes for workdays and days off.
  Each heater chooses when it needs comfort (home & awake, home office, night).
- **Home office days:** one-tap *Home Office Today/Tomorrow* switches, recurring weekdays and a
  `set_home_office` service for several days.
- **Shadow evaluation (Norgespris vs. spot):** a second, non-controlling planner optimises
  for spot prices so you can see over time whether leaving Norgespris would lower the bill.
- **Object card** for normal dashboards and an **admin overview panel** in the sidebar.
  Both follow your Home Assistant theme (also custom themes).
- All settings survive a restart. Norwegian public holidays are built in.

## Installation
1. HACS → Custom repositories → add this repository (category *Integration*).
   Alternatively copy `custom_components/power_controller` to `/config/custom_components/`.
2. Restart Home Assistant.
3. *Settings → Devices & services → Add integration → Power Controller.*
   The first entry is the hub (grid sensors). Then add thermal zones and one entry per load.
4. *Configure → Grid tariff:* verify prices and capacity steps against your grid operator's
   price list.

Requires `numpy` and `scipy` (installed automatically by Home Assistant from `manifest.json`).

## Overload acceptance (defaults)
| Overload | Tolerated before ramp-down | Ramp-down to rated current |
|---|---|---|
| 5 % | 10 min | 10 min |
| 20 % | 2 min | 5 min |
| ≥ 50 % | 0 (immediate) | – |

The budget refills in 20 min below 80 % load and in 60 min at 100 % load. All values can be
changed under *Configure → Sensors and general settings*. Per-phase status is available as
an attribute on `binary_sensor.power_controller_warning`.

## Dashboards

### Object card (for everyday dashboards)
Loaded automatically by the integration – no Lovelace resource needed. Add a card of type
**Power Controller object** in the dashboard editor (visual editor included), or in YAML:

```yaml
type: custom:power-controller-object-card
entity: sensor.pc_heat_pump_plan        # the device's plan sensor
control_entity: climate.heat_pump       # optional, auto-detected from the device setup
show_plan: true                         # 24-hour plan strip + next action
show_distribution: true                 # real-time distribution status line
show_targets: true                      # comfort/eco, nominal temp, runtime, EV target
```

- **Committed:** status line from the real-time distribution (Running, Idle · capacity lent,
  Waiting, Blocked, Shedding, Priority now), 24-hour plan, next action, target controls and
  *Priority now*. Mode buttons are hidden so users don't fight the controller.
- **Uncommitted:** a normal control card for the entity (HVAC modes, setpoint, presets,
  fan/swing, water-heater operation, on/off).
- Tap the badge to commit/uncommit; tap the icon or name for the more-info dialog.
- See `docs/dashboard-example.yaml`.

### Overview panel (super user)
**Power Controller** appears in the sidebar for administrators: KPIs, 72-hour plan with prices,
phases and overload acceptance, all devices, occupancy with home-office toggles, Norgespris vs.
spot and the grid tariff. Tabs *Devices* (all object cards) and *Priorities* (drag to reorder).

## Occupancy and home office
When the house is not in *Away* (out of town) mode, comfort temperatures follow the expected
occupancy instead of a fixed schedule: `home_awake`, `away_work`, `home_office`, `sleep`.

- **Times:** six time entities on the Power Controller device (workday wake/leave/return/bedtime,
  day-off wake/bedtime). Days off = weekends, Norwegian public holidays and the workday sensor.
- **Per heater:** *Comfort temperature during* (home & awake / home office / night). Outside
  these states the eco temperature applies; the planner pre-heats so rooms are warm in time.
- **Home office:** `switch.power_controller_home_office_today` / `_tomorrow`, recurring weekdays
  under *Configure → Home office weekdays*, or `power_controller.set_home_office`.
- `sensor.power_controller_occupancy` shows the current state and the next 72 hours.

## Shadow evaluation (Norgespris vs. spot)
Runs next to the real controller and never switches anything. Each hour is priced as *actual*,
*passive spot* (same usage on spot) and *optimised spot* (a second planner optimising for spot).
The shadow carries its own room/tank temperatures and EV energy forward, so it cannot "save"
by being colder or charging less. Sensor: `sensor.power_controller_norgespris_benefit_this_month`
(positive = Norgespris is cheaper). Settings: *Shadow Comparison Model*, *Shadow Extra Monthly Fee*.
Service: `power_controller.reset_shadow_evaluation`.

## Development
See [`docs/PROJECT_HANDOVER.md`](docs/PROJECT_HANDOVER.md) for architecture, design decisions,
known gaps and how to continue development.

```bash
pip install pytest numpy scipy
pytest -q
```
GitHub Actions runs the tests, hassfest and HACS validation on every push.
