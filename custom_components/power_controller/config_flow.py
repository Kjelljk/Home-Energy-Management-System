"""Multi-step ConfigFlow and OptionsFlow for Power Controller hub, zones, and devices."""
from __future__ import annotations

from typing import Any
import uuid
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ACTIVE_POWER_SENSOR,
    CONF_COMFORT_DURING,
    CONF_COMFORT_TEMP_ENTITY,
    CONF_CURRENT_L1_SENSOR,
    CONF_CURRENT_L2_SENSOR,
    CONF_CURRENT_L3_SENSOR,
    CONF_DAILY_QUOTA_HOURS,
    CONF_DEVICE_TYPE,
    CONF_ECO_TEMP_ENTITY,
    CONF_ENABLE_PI_GOVERNOR,
    CONF_EV_AVAILABILITY_SWITCH,
    CONF_EV_BATTERY_KWH,
    CONF_EV_CHARGE_SWITCH,
    CONF_EV_CONNECTED_SENSOR,
    CONF_EV_CONN_ID,
    CONF_EV_MIN_CURRENT_A,
    CONF_EV_SOC_SENSOR,
    CONF_EV_TRANSACTION_SENSOR,
    CONF_EXTERNAL_LIMIT_SENSOR,
    CONF_HEATING_COEFFICIENT,
    CONF_INSULATION_CLASS,
    CONF_MIN_TEMP_ENTITY,
    CONF_MONTHLY_ENERGY_SENSOR,
    CONF_NOMINAL_CURRENT_A,
    CONF_NOMINAL_POWER_KW,
    CONF_NOMINAL_TEMP_ENTITY,
    CONF_OA_DELAY_5,
    CONF_OA_DELAY_20,
    CONF_OA_HARD_RATIO,
    CONF_OA_RAMP_5,
    CONF_OA_RAMP_20,
    CONF_OA_RECOVER_FAST,
    CONF_OA_RECOVER_SLOW,
    CONF_NORDPOOL_SENSOR,
    CONF_OOT_TEMP_ENTITY,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_PHASE_PAIR,
    CONF_POWER_SENSOR,
    CONF_ROOM_TEMP_SENSOR,
    CONF_SCHEDULE_ENTITY,
    CONF_SENSOR_TIMEOUT_S,
    CONF_TANK_VOLUME_L,
    CONF_TARGET_ENTITY,
    CONF_WATER_TEMP_SENSOR,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_SENSOR,
    CONF_WORKDAY_SENSOR,
    CONF_ZONE_AREA_M2,
    CONF_ZONE_ID,
    CONF_ZONE_NAME,
    DOMAIN,
    DeviceType,
    ENTRY_TYPE_DEVICE,
    ENTRY_TYPE_HUB,
    ENTRY_TYPE_ZONE,
    InsulationClass,
    PhasePair,
    SENSOR_STALE_DEFAULT_S,
)
from .overload import OAParams
from .occupancy import DEFAULT_COMFORT_DURING, WEEKDAY_KEYS
from .grid_tariff import (
    DAY_KEYS,
    PRESETS,
    GridTariffSchedule,
    TariffPeriod,
    TariffVersion,
    preset,
    steps_from_text,
    steps_to_text,
)


def _hub_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_ACTIVE_POWER_SENSOR, default=d.get(CONF_ACTIVE_POWER_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_MONTHLY_ENERGY_SENSOR, default=d.get(CONF_MONTHLY_ENERGY_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_CURRENT_L1_SENSOR, default=d.get(CONF_CURRENT_L1_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_CURRENT_L2_SENSOR, default=d.get(CONF_CURRENT_L2_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_CURRENT_L3_SENSOR, default=d.get(CONF_CURRENT_L3_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_NORDPOOL_SENSOR, default=d.get(CONF_NORDPOOL_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_WORKDAY_SENSOR,
                default=d.get(CONF_WORKDAY_SENSOR, "binary_sensor.workday_sensor"),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
            vol.Required(CONF_OUTDOOR_TEMP_SENSOR, default=d.get(CONF_OUTDOOR_TEMP_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_WEATHER_ENTITY, default=d.get(CONF_WEATHER_ENTITY)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="weather")
            ),
            vol.Optional(CONF_EXTERNAL_LIMIT_SENSOR, default=d.get(CONF_EXTERNAL_LIMIT_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_SENSOR_TIMEOUT_S, default=d.get(CONF_SENSOR_TIMEOUT_S, SENSOR_STALE_DEFAULT_S)
            ): selector.NumberSelector(selector.NumberSelectorConfig(min=10, max=900, step=10, mode="box")),
            vol.Optional(CONF_ZONE_NAME, default=d.get(CONF_ZONE_NAME, "Main Living Zone")): str,
            vol.Optional(CONF_ZONE_AREA_M2, default=d.get(CONF_ZONE_AREA_M2, 45.0)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=4, max=500, step=1, mode="box")
            ),
            vol.Optional(
                CONF_INSULATION_CLASS, default=d.get(CONF_INSULATION_CLASS, InsulationClass.STANDARD)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[c.value for c in InsulationClass],
                    mode="dropdown",
                )
            ),
            **_oa_fields(d),
        }
    )


def _oa_fields(d: dict[str, Any]) -> dict:
    """Overload Acceptance settings (minutes; hard ratio as multiple of the rated limit)."""
    defaults = OAParams()
    fields = {}
    for conf, attr, lo, hi, step in (
        (CONF_OA_DELAY_5, "delay_5_min", 0.5, 60, 0.5),
        (CONF_OA_DELAY_20, "delay_20_min", 0.1, 30, 0.1),
        (CONF_OA_RAMP_5, "ramp_5_min", 0.5, 60, 0.5),
        (CONF_OA_RAMP_20, "ramp_20_min", 0.5, 60, 0.5),
        (CONF_OA_RECOVER_FAST, "recover_fast_min", 1, 240, 1),
        (CONF_OA_RECOVER_SLOW, "recover_slow_min", 1, 480, 1),
        (CONF_OA_HARD_RATIO, "hard_ratio", 1.1, 3.0, 0.05),
    ):
        fields[vol.Optional(conf, default=d.get(conf, getattr(defaults, attr)))] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=lo, max=hi, step=step, mode="box")
        )
    return fields


def _zone_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_ZONE_NAME, default=d.get(CONF_ZONE_NAME, "")): str,
            vol.Required(CONF_ZONE_AREA_M2, default=d.get(CONF_ZONE_AREA_M2, 20.0)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=4, max=500, step=1, mode="box")
            ),
            vol.Required(
                CONF_INSULATION_CLASS, default=d.get(CONF_INSULATION_CLASS, InsulationClass.STANDARD)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[c.value for c in InsulationClass], mode="dropdown")
            ),
            vol.Optional(CONF_WINDOW_SENSOR, default=d.get(CONF_WINDOW_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
            ),
        }
    )


def _device_schema(
    device_type: DeviceType,
    zone_options: list[selector.SelectOptionDict],
    defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    d = defaults or {}
    phase_sel = selector.SelectSelector(
        selector.SelectSelectorConfig(options=[p.value for p in PhasePair], mode="dropdown")
    )

    if device_type == DeviceType.EV_CHARGER:
        return vol.Schema(
            {
                vol.Required("name", default=d.get("name", "EV Charger")): str,
                vol.Required(CONF_PHASE_PAIR, default=d.get(CONF_PHASE_PAIR, PhasePair.L1_L2)): phase_sel,
                vol.Required(CONF_NOMINAL_CURRENT_A, default=d.get(CONF_NOMINAL_CURRENT_A, 32.0)): float,
                vol.Required(CONF_NOMINAL_POWER_KW, default=d.get(CONF_NOMINAL_POWER_KW, 7.36)): float,
                vol.Required(CONF_EV_MIN_CURRENT_A, default=d.get(CONF_EV_MIN_CURRENT_A, 6)): int,
                vol.Required(CONF_EV_BATTERY_KWH, default=d.get(CONF_EV_BATTERY_KWH, 75.0)): float,
                vol.Required(CONF_EV_AVAILABILITY_SWITCH, default=d.get(CONF_EV_AVAILABILITY_SWITCH)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="switch")
                ),
                vol.Required(CONF_EV_CHARGE_SWITCH, default=d.get(CONF_EV_CHARGE_SWITCH)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="switch")
                ),
                vol.Required(CONF_EV_TRANSACTION_SENSOR, default=d.get(CONF_EV_TRANSACTION_SENSOR)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(CONF_POWER_SENSOR, default=d.get(CONF_POWER_SENSOR)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(CONF_EV_CONNECTED_SENSOR, default=d.get(CONF_EV_CONNECTED_SENSOR)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor")
                ),
                vol.Required(CONF_EV_SOC_SENSOR, default=d.get(CONF_EV_SOC_SENSOR)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_EV_CONN_ID, default=d.get(CONF_EV_CONN_ID, 1)): int,
            }
        )

    if device_type == DeviceType.CLIMATE_HEATING:
        return vol.Schema(
            {
                vol.Required("name", default=d.get("name", "Heater")): str,
                vol.Required(CONF_TARGET_ENTITY, default=d.get(CONF_TARGET_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="climate")
                ),
                vol.Required(CONF_ROOM_TEMP_SENSOR, default=d.get(CONF_ROOM_TEMP_SENSOR)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_POWER_SENSOR, default=d.get(CONF_POWER_SENSOR)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(CONF_PHASE_PAIR, default=d.get(CONF_PHASE_PAIR, PhasePair.L1_L2)): phase_sel,
                vol.Required(CONF_NOMINAL_CURRENT_A, default=d.get(CONF_NOMINAL_CURRENT_A, 8.7)): float,
                vol.Required(CONF_NOMINAL_POWER_KW, default=d.get(CONF_NOMINAL_POWER_KW, 2.0)): float,
                vol.Required(CONF_ZONE_ID, default=d.get(CONF_ZONE_ID, zone_options[0]["value"])): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=zone_options, mode="dropdown")
                ),
                vol.Optional(CONF_HEATING_COEFFICIENT, default=d.get(CONF_HEATING_COEFFICIENT, 1.0)): float,
                vol.Required(CONF_COMFORT_TEMP_ENTITY, default=d.get(CONF_COMFORT_TEMP_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["number", "input_number"])
                ),
                vol.Required(CONF_ECO_TEMP_ENTITY, default=d.get(CONF_ECO_TEMP_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["number", "input_number"])
                ),
                vol.Required(CONF_OOT_TEMP_ENTITY, default=d.get(CONF_OOT_TEMP_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["number", "input_number"])
                ),
                vol.Optional(CONF_SCHEDULE_ENTITY, default=d.get(CONF_SCHEDULE_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="schedule")
                ),
                vol.Required(
                    CONF_COMFORT_DURING, default=d.get(CONF_COMFORT_DURING, list(DEFAULT_COMFORT_DURING))
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "home_awake", "label": "Home and awake (mornings, evenings, days off)"},
                            {"value": "home_office", "label": "Home office hours"},
                            {"value": "sleep", "label": "Night (sleeping)"},
                        ],
                        multiple=True,
                        mode="list",
                    )
                ),
                vol.Optional(CONF_ENABLE_PI_GOVERNOR, default=d.get(CONF_ENABLE_PI_GOVERNOR, False)): bool,
            }
        )

    if device_type == DeviceType.CLIMATE_HOTWATER:
        return vol.Schema(
            {
                vol.Required("name", default=d.get("name", "Hot Water Tank")): str,
                vol.Required(CONF_TARGET_ENTITY, default=d.get(CONF_TARGET_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["climate", "switch"])
                ),
                vol.Optional(CONF_WATER_TEMP_SENSOR, default=d.get(CONF_WATER_TEMP_SENSOR)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_POWER_SENSOR, default=d.get(CONF_POWER_SENSOR)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(CONF_PHASE_PAIR, default=d.get(CONF_PHASE_PAIR, PhasePair.L2_L3)): phase_sel,
                vol.Required(CONF_NOMINAL_CURRENT_A, default=d.get(CONF_NOMINAL_CURRENT_A, 8.7)): float,
                vol.Required(CONF_NOMINAL_POWER_KW, default=d.get(CONF_NOMINAL_POWER_KW, 2.0)): float,
                vol.Optional(CONF_HEATING_COEFFICIENT, default=d.get(CONF_HEATING_COEFFICIENT, 1.0)): float,
                vol.Optional(CONF_NOMINAL_TEMP_ENTITY, default=d.get(CONF_NOMINAL_TEMP_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["number", "input_number"])
                ),
                vol.Optional(CONF_MIN_TEMP_ENTITY, default=d.get(CONF_MIN_TEMP_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["number", "input_number"])
                ),
                vol.Optional(CONF_OOT_TEMP_ENTITY, default=d.get(CONF_OOT_TEMP_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["number", "input_number"])
                ),
                vol.Optional(CONF_DAILY_QUOTA_HOURS, default=d.get(CONF_DAILY_QUOTA_HOURS, 3.0)): float,
                vol.Optional(CONF_TANK_VOLUME_L, default=d.get(CONF_TANK_VOLUME_L, 200.0)): float,
                vol.Optional(CONF_SCHEDULE_ENTITY, default=d.get(CONF_SCHEDULE_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="schedule")
                ),
            }
        )

    # DeviceType.SWITCH
    return vol.Schema(
        {
            vol.Required("name", default=d.get("name", "Controllable Switch")): str,
            vol.Required(CONF_TARGET_ENTITY, default=d.get(CONF_TARGET_ENTITY)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch")
            ),
            vol.Optional(CONF_POWER_SENSOR, default=d.get(CONF_POWER_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_PHASE_PAIR, default=d.get(CONF_PHASE_PAIR, PhasePair.L1_L2)): phase_sel,
            vol.Required(CONF_NOMINAL_POWER_KW, default=d.get(CONF_NOMINAL_POWER_KW, 1.5)): float,
            vol.Optional(CONF_SCHEDULE_ENTITY, default=d.get(CONF_SCHEDULE_ENTITY)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="schedule")
            ),
        }
    )


class PowerControllerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handles initial central hub setup and subsequent device / thermal zone additions."""

    VERSION = 1

    def __init__(self) -> None:
        self._selected_type: str | None = None

    def _get_hub_entry(self) -> ConfigEntry | None:
        for entry in self._async_current_entries():
            if entry.data.get("entry_type") == ENTRY_TYPE_HUB:
                return entry
        return None

    def _get_zone_options(self) -> list[selector.SelectOptionDict]:
        opts: list[selector.SelectOptionDict] = []
        for entry in self._async_current_entries():
            d = {**entry.data, **entry.options}
            if d.get("entry_type") in (ENTRY_TYPE_HUB, ENTRY_TYPE_ZONE):
                zid = d.get(CONF_ZONE_ID, "default_zone")
                zname = d.get(CONF_ZONE_NAME, "Main Living Zone")
                opts.append({"value": zid, "label": zname})
        return opts or [{"value": "default_zone", "label": "Main Living Zone"}]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: Set up central Power Controller if absent, else pick object/zone type."""
        hub = self._get_hub_entry()
        if hub is None:
            if user_input is not None:
                user_input["entry_type"] = ENTRY_TYPE_HUB
                user_input[CONF_ZONE_ID] = f"zone_{uuid.uuid4().hex[:8]}"
                return self.async_create_entry(title="Power Controller", data=user_input)
            return self.async_show_form(step_id="user", data_schema=_hub_schema())

        if user_input is not None:
            self._selected_type = user_input["object_type"]
            if self._selected_type == ENTRY_TYPE_ZONE:
                return await self.async_step_thermal_zone()
            return await self.async_step_device_details()

        type_options = [
            {"value": DeviceType.EV_CHARGER.value, "label": "EV Charger (OCPP)"},
            {"value": DeviceType.CLIMATE_HEATING.value, "label": "Climate (Heating)"},
            {"value": DeviceType.CLIMATE_HOTWATER.value, "label": "Climate (Hotwater)"},
            {"value": DeviceType.SWITCH.value, "label": "Switch"},
            {"value": ENTRY_TYPE_ZONE, "label": "Thermal Zone (Thermodynamic Model)"},
        ]
        schema = vol.Schema(
            {
                vol.Required("object_type"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=type_options, mode="dropdown")
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_thermal_zone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 2a: Configure a new Thermodynamic Model (Thermal Zone)."""
        if user_input is not None:
            user_input["entry_type"] = ENTRY_TYPE_ZONE
            user_input[CONF_ZONE_ID] = f"zone_{uuid.uuid4().hex[:8]}"
            return self.async_create_entry(
                title=f"Zone: {user_input[CONF_ZONE_NAME]}",
                data=user_input,
            )
        return self.async_show_form(step_id="thermal_zone", data_schema=_zone_schema())

    async def async_step_device_details(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 2b: Configure parameters for the selected controllable device type."""
        assert self._selected_type is not None
        dtype = DeviceType(self._selected_type)
        if user_input is not None:
            title = user_input.pop("name")
            user_input["entry_type"] = ENTRY_TYPE_DEVICE
            user_input[CONF_DEVICE_TYPE] = dtype.value
            return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="device_details",
            data_schema=_device_schema(dtype, self._get_zone_options()),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PowerControllerOptionsFlow(config_entry)


MONTH_OPTIONS = [
    {"value": str(i), "label": lbl}
    for i, lbl in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
    )
]
DAY_OPTIONS = [
    {"value": k, "label": lbl}
    for k, lbl in zip(DAY_KEYS, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
]


def _num(min_v: float, max_v: float, step: float, unit: str | None = None) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=min_v, max=max_v, step=step, mode="box", unit_of_measurement=unit)
    )


def _select(options: list, multiple: bool = False, mode: str = "dropdown") -> selector.SelectSelector:
    return selector.SelectSelector(selector.SelectSelectorConfig(options=options, multiple=multiple, mode=mode))


class PowerControllerOptionsFlow(OptionsFlow):
    """Edit hub sensors, the grid tariff, any thermal zone or any controllable device."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        self._tariff: GridTariffSchedule | None = None
        self._vid: str | None = None
        self._pid: str | None = None

    # ------------------------------------------------------------------ entry point
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        merged = {**self._entry.data, **self._entry.options}
        etype = merged.get("entry_type", ENTRY_TYPE_HUB)
        if etype == ENTRY_TYPE_HUB:
            return self.async_show_menu(step_id="init", menu_options=["hub_settings", "occupancy", "grid_tariff"])
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        if etype == ENTRY_TYPE_ZONE:
            return self.async_show_form(step_id="init", data_schema=_zone_schema(merged))

        dtype = DeviceType(merged[CONF_DEVICE_TYPE])
        zone_opts: list[selector.SelectOptionDict] = []
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            d = {**entry.data, **entry.options}
            if d.get("entry_type") in (ENTRY_TYPE_HUB, ENTRY_TYPE_ZONE):
                zone_opts.append({"value": d.get(CONF_ZONE_ID, "default_zone"), "label": d.get(CONF_ZONE_NAME, "Zone")})
        if not zone_opts:
            zone_opts = [{"value": "default_zone", "label": "Main Living Zone"}]
        merged.setdefault("name", self._entry.title)
        return self.async_show_form(step_id="init", data_schema=_device_schema(dtype, zone_opts, merged))

    async def async_step_hub_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        merged = {**self._entry.data, **self._entry.options}
        if user_input is not None:
            return self.async_create_entry(title="", data={**self._entry.options, **user_input})
        return self.async_show_form(step_id="hub_settings", data_schema=_hub_schema(merged))

    async def async_step_occupancy(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Recurring home-office weekdays. Clock times are time entities on the hub device;
        one-off days use the 'Home Office Today/Tomorrow' switches or the set_home_office service."""
        coord = self._coordinator()
        if coord is None:
            return self.async_abort(reason="hub_not_loaded")
        if user_input is not None:
            days = [d for d in WEEKDAY_KEYS if d in (user_input.get("home_office_weekdays") or [])]
            await coord.async_set_home_office_weekdays(days)
            return self.async_create_entry(title="", data=dict(self._entry.options))
        return self.async_show_form(
            step_id="occupancy",
            data_schema=vol.Schema({
                vol.Optional("home_office_weekdays", default=list(coord.occupancy.home_office_weekdays)):
                    _select(DAY_OPTIONS[:5], multiple=True, mode="list"),
            }),
        )

    # ------------------------------------------------------------------ grid tariff
    def _coordinator(self):
        from .coordinator import get_hub_coordinator

        return get_hub_coordinator(self.hass)

    def _schedule(self) -> GridTariffSchedule:
        if self._tariff is None:
            coord = self._coordinator()
            data = coord.price_engine.grid_tariff.to_list() if coord else None
            self._tariff = GridTariffSchedule.from_list(data)  # working copy
        return self._tariff

    def _version(self) -> TariffVersion:
        sched = self._schedule()
        version = sched.get(self._vid or "")
        if version is None:
            version = sched.versions[-1]
            self._vid = version.id
        return version

    def _versions_text(self) -> str:
        return "\n".join(f"• {v.valid_from}: {v.name}" for v in self._schedule().versions)

    def _version_text(self) -> str:
        v = self._version()
        vat = "incl. VAT" if v.prices_include_vat else "excl. VAT"
        lines = [f"**{v.name}** – valid from {v.valid_from} ({vat})",
                 f"• Default price (all other hours): {v.default_price:.4f} NOK/kWh",
                 f"• Fees (consumption tax + Enova): {v.energy_fee:.4f} NOK/kWh"]
        lines += [f"• {p.describe()}" for p in v.periods] or ["• (no periods – flat price)"]
        lines.append("• Capacity steps: " + ", ".join(
            (f"<{st.upper_kw:g} kW" if st.upper_kw > 0 else "over") + f" = {st.monthly_nok:g} NOK" for st in
            sorted(v.capacity_steps, key=lambda st: (st.upper_kw <= 0, st.upper_kw))))
        return "\n".join(lines)

    async def async_step_grid_tariff(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._coordinator() is None:
            return self.async_abort(reason="hub_not_loaded")
        return self.async_show_menu(
            step_id="grid_tariff",
            menu_options=["tariff_pick_version", "tariff_new_version", "tariff_save"],
            description_placeholders={"versions": self._versions_text()},
        )

    async def async_step_tariff_pick_version(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        sched = self._schedule()
        if user_input is not None:
            self._vid = user_input["version"]
            return await self.async_step_tariff_version()
        opts = [{"value": v.id, "label": f"{v.valid_from} – {v.name}"} for v in sched.versions]
        return self.async_show_form(
            step_id="tariff_pick_version",
            data_schema=vol.Schema({vol.Required("version", default=sched.versions[-1].id): _select(opts)}),
        )

    async def async_step_tariff_new_version(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        sched = self._schedule()
        errors: dict[str, str] = {}
        if user_input is not None:
            valid_from = str(user_input["valid_from"])
            if any(v.valid_from == valid_from for v in sched.versions):
                errors["valid_from"] = "duplicate_date"
            else:
                base = user_input["based_on"]
                src = sched.get(base)
                new = src.copy_as(user_input["name"], valid_from) if src else preset(base, valid_from)
                new.name = user_input["name"]
                sched.versions.append(new)
                sched.sort()
                self._vid = new.id
                return await self.async_step_tariff_version()
        base_opts = [{"value": v.id, "label": f"Copy of {v.name} ({v.valid_from})"} for v in sched.versions]
        base_opts += [{"value": k, "label": f"Template: {lbl}"} for k, lbl in PRESETS.items()]
        latest = sched.versions[-1]
        next_year = f"{int(latest.valid_from[:4]) + 1}-01-01"
        return self.async_show_form(
            step_id="tariff_new_version",
            data_schema=vol.Schema({
                vol.Required("name", default=f"Grid tariff {next_year[:4]}"): str,
                vol.Required("valid_from", default=next_year): selector.DateSelector(),
                vol.Required("based_on", default=latest.id): _select(base_opts),
            }),
            errors=errors,
        )

    async def async_step_tariff_version(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="tariff_version",
            menu_options=[
                "tariff_general", "tariff_pick_period", "tariff_add_period",
                "tariff_capacity", "tariff_delete_version", "grid_tariff",
            ],
            description_placeholders={"summary": self._version_text()},
        )

    async def async_step_tariff_general(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        v = self._version()
        errors: dict[str, str] = {}
        if user_input is not None:
            valid_from = str(user_input["valid_from"])
            if any(o.valid_from == valid_from and o.id != v.id for o in self._schedule().versions):
                errors["valid_from"] = "duplicate_date"
            else:
                v.name = user_input["name"]
                v.valid_from = valid_from
                v.default_price = float(user_input["default_price"])
                v.energy_fee = float(user_input["energy_fee"])
                v.prices_include_vat = bool(user_input["prices_include_vat"])
                v.holidays_as = user_input["holidays_as"]
                self._schedule().sort()
                return await self.async_step_tariff_version()
        return self.async_show_form(
            step_id="tariff_general",
            data_schema=vol.Schema({
                vol.Required("name", default=v.name): str,
                vol.Required("valid_from", default=v.valid_from): selector.DateSelector(),
                vol.Required("prices_include_vat", default=v.prices_include_vat): bool,
                vol.Required("default_price", default=v.default_price): _num(0, 10, 0.0001, "NOK/kWh"),
                vol.Required("energy_fee", default=v.energy_fee): _num(0, 5, 0.0001, "NOK/kWh"),
                vol.Required("holidays_as", default=v.holidays_as): _select(
                    [{"value": "sun", "label": "Same as Sunday (weekend price)"},
                     {"value": "none", "label": "Normal weekday"}]),
            }),
            errors=errors,
        )

    async def async_step_tariff_pick_period(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        v = self._version()
        if not v.periods:
            return await self.async_step_tariff_add_period()
        if user_input is not None:
            self._pid = user_input["period"]
            return await self.async_step_tariff_period()
        opts = [{"value": p.id, "label": f"{i + 1}. {p.describe()}"} for i, p in enumerate(v.periods)]
        return self.async_show_form(
            step_id="tariff_pick_period",
            data_schema=vol.Schema({vol.Required("period", default=v.periods[0].id): _select(opts)}),
        )

    async def async_step_tariff_add_period(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        v = self._version()
        period = TariffPeriod(name=f"Period {len(v.periods) + 1}", price=v.default_price)
        v.periods.append(period)
        self._pid = period.id
        return await self.async_step_tariff_period()

    async def async_step_tariff_period(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        v = self._version()
        period = next((p for p in v.periods if p.id == self._pid), None)
        if period is None:
            return await self.async_step_tariff_version()
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("delete"):
                v.periods = [p for p in v.periods if p.id != period.id]
                return await self.async_step_tariff_version()
            days = list(user_input.get("days") or [])
            start, end = int(user_input["start_hour"]), int(user_input["end_hour"])
            if not days:
                errors["days"] = "no_days"
            elif not 0 <= start <= 23 or not 0 <= end <= 24:
                errors["base"] = "invalid_hours"
            else:
                period.name = user_input["name"]
                period.price = float(user_input["price"])
                period.days = [d for d in DAY_KEYS if d in days]
                period.start_hour, period.end_hour = start, end
                period.months = sorted(int(m) for m in user_input.get("months") or [])
                # position: 1 = evaluated first (first matching period wins)
                pos = max(1, min(len(v.periods), int(user_input.get("position", 1)))) - 1
                v.periods.remove(period)
                v.periods.insert(pos, period)
                return await self.async_step_tariff_version()
        return self.async_show_form(
            step_id="tariff_period",
            data_schema=vol.Schema({
                vol.Required("name", default=period.name): str,
                vol.Required("price", default=period.price): _num(0, 10, 0.0001, "NOK/kWh"),
                vol.Required("days", default=period.days): _select(DAY_OPTIONS, multiple=True, mode="list"),
                vol.Required("start_hour", default=period.start_hour): _num(0, 23, 1, "h"),
                vol.Required("end_hour", default=period.end_hour): _num(0, 24, 1, "h"),
                vol.Optional("months", default=[str(m) for m in period.months]): _select(MONTH_OPTIONS, multiple=True),
                vol.Required("position", default=v.periods.index(period) + 1): _num(1, max(1, len(v.periods)), 1),
                vol.Optional("delete", default=False): bool,
            }),
            errors=errors,
            description_placeholders={"vat": "incl. VAT" if v.prices_include_vat else "excl. VAT"},
        )

    async def async_step_tariff_capacity(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        v = self._version()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                steps = steps_from_text(user_input["steps"])
                if len({st.upper_kw for st in steps if st.upper_kw > 0}) != len([st for st in steps if st.upper_kw > 0]):
                    raise ValueError("duplicate")
            except ValueError:
                errors["steps"] = "invalid_steps"
            else:
                v.capacity_steps = steps
                v.threshold_in_next_step = bool(user_input["threshold_in_next_step"])
                return await self.async_step_tariff_version()
        return self.async_show_form(
            step_id="tariff_capacity",
            data_schema=vol.Schema({
                vol.Required("steps", default=steps_to_text(v.capacity_steps)): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)),
                vol.Required("threshold_in_next_step", default=v.threshold_in_next_step): bool,
            }),
            errors=errors,
        )

    async def async_step_tariff_delete_version(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        sched = self._schedule()
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get("confirm"):
                return await self.async_step_tariff_version()
            if len(sched.versions) <= 1:
                errors["base"] = "last_version"
            else:
                sched.remove(self._version().id)
                self._vid = None
                return await self.async_step_grid_tariff()
        return self.async_show_form(
            step_id="tariff_delete_version",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
            errors=errors,
            description_placeholders={"name": self._version().name},
        )

    async def async_step_tariff_save(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        sched = self._schedule()
        problems = {f"{v.name}: {e}" for v in sched.versions for e in v.validate()}
        if problems:
            return self.async_show_menu(
                step_id="grid_tariff",
                menu_options=["tariff_pick_version", "tariff_new_version", "tariff_save"],
                description_placeholders={"versions": self._versions_text() + "\n\n⚠ " + "; ".join(sorted(problems))},
            )
        coord = self._coordinator()
        if coord is None:
            return self.async_abort(reason="hub_not_loaded")
        await coord.async_apply_grid_tariff(sched)
        # options unchanged -> no extra reload from the update listener
        return self.async_create_entry(title="", data=dict(self._entry.options))
