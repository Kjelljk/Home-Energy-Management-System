"""Number platform for Power Controller pricing/limiter parameters and device priorities."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    CONF_DEVICE_TYPE,
    DOMAIN,
    DeviceType,
    ENTRY_TYPE_DEVICE,
    ENTRY_TYPE_HUB,
)
from .coordinator import PowerControllerCoordinator, get_hub_coordinator


@dataclass(frozen=True)
class _HubNumberSpec:
    key: str
    name: str
    unit: str
    min_val: float
    max_val: float
    step: float
    getter: Callable[[PowerControllerCoordinator], float]
    setter: Callable[[PowerControllerCoordinator, float], None]
    triggers_price: bool = True


HUB_NUMBERS: tuple[_HubNumberSpec, ...] = (
    _HubNumberSpec(
        "internal_limit",
        "Internal Grid Limiter",
        "A",
        10.0,
        200.0,
        1.0,
        lambda c: c.limiters.internal_limit_a,
        lambda c, v: setattr(c.limiters, "internal_limit_a", v),
        triggers_price=False,
    ),
    _HubNumberSpec(
        "supplier_markup",
        "Supplier Markup",
        "NOK/kWh",
        0.0,
        2.0,
        0.001,
        lambda c: c.price_engine.supplier_markup_nok,
        lambda c, v: setattr(c.price_engine, "supplier_markup_nok", v),
    ),
    _HubNumberSpec(
        "support_threshold",
        "Stromstotte Threshold",
        "NOK/kWh",
        0.0,
        5.0,
        0.01,
        lambda c: c.price_engine.support_threshold_nok,
        lambda c, v: setattr(c.price_engine, "support_threshold_nok", v),
    ),
    _HubNumberSpec(
        "support_rate",
        "Stromstotte Rate",
        "%",
        0.0,
        100.0,
        1.0,
        lambda c: c.price_engine.support_rate * 100.0,
        lambda c, v: setattr(c.price_engine, "support_rate", v / 100.0),
    ),
    _HubNumberSpec(
        "norgespris",
        "Norway Price",
        "NOK/kWh",
        0.0,
        10.0,
        0.01,
        lambda c: c.price_engine.norgespris_nok,
        lambda c, v: setattr(c.price_engine, "norgespris_nok", v),
    ),
    _HubNumberSpec(
        "norgespris_volume",
        "Norway Price Volume",
        "kWh",
        0.0,
        20000.0,
        50.0,
        lambda c: c.price_engine.norgespris_volume_kwh,
        lambda c, v: setattr(c.price_engine, "norgespris_volume_kwh", v),
    ),
    _HubNumberSpec(
        "shadow_extra_fee",
        "Shadow Extra Monthly Fee",
        "NOK",
        -500.0,
        500.0,
        1.0,
        lambda c: c.shadow.extra_monthly_fee_nok,
        lambda c, v: setattr(c.shadow, "extra_monthly_fee_nok", v),
        triggers_price=False,
    ),
    _HubNumberSpec(
        "cmd_interval",
        "Command Minimum Interval",
        "min",
        0.5,
        30.0,
        0.5,
        lambda c: c.control.min_interval_s / 60.0,
        lambda c, v: setattr(c.control, "min_interval_s", v * 60.0),
        triggers_price=False,
    ),
    _HubNumberSpec(
        "ev_min_step",
        "EV Current Minimum Step",
        "A",
        1.0,
        10.0,
        1.0,
        lambda c: c.control.ev_min_delta_a,
        lambda c, v: setattr(c.control, "ev_min_delta_a", v),
        triggers_price=False,
    ),
    _HubNumberSpec(
        "setpoint_min_step",
        "Setpoint Minimum Step",
        "°C",
        0.1,
        3.0,
        0.1,
        lambda c: c.control.temp_min_delta_c,
        lambda c, v: setattr(c.control, "temp_min_delta_c", v),
        triggers_price=False,
    ),
    _HubNumberSpec(
        "ev_delay_penalty",
        "EV Delay Penalty",
        "øre/kWh/h",
        0.0,
        50.0,
        0.5,
        lambda c: c.control.ev_delay_penalty_nok * 100.0,
        lambda c, v: setattr(c.control, "ev_delay_penalty_nok", v / 100.0),
        triggers_price=False,
    ),
    _HubNumberSpec(
        "vat",
        "VAT",
        "%",
        0.0,
        50.0,
        1.0,
        lambda c: c.price_engine.vat_percent,
        lambda c, v: setattr(c.price_engine, "vat_percent", v),
    ),
    _HubNumberSpec(
        "baseload_24h",
        "Baseload 24h",
        "kW",
        0.0,
        10.0,
        0.1,
        lambda c: c.price_engine.baseload_24h_kw,
        lambda c, v: setattr(c.price_engine, "baseload_24h_kw", v),
    ),
    _HubNumberSpec(
        "baseload_daytime",
        "Baseload Daytime",
        "kW",
        0.0,
        10.0,
        0.1,
        lambda c: c.price_engine.baseload_daytime_kw,
        lambda c, v: setattr(c.price_engine, "baseload_daytime_kw", v),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = get_hub_coordinator(hass)
    if coordinator is None:
        return
    etype = entry.data.get("entry_type", ENTRY_TYPE_HUB)

    if etype == ENTRY_TYPE_HUB:
        entities: list[NumberEntity] = [HubConfigNumber(coordinator, spec) for spec in HUB_NUMBERS]
        # Grid tariff prices of the CURRENTLY ACTIVE price list version: one entity per
        # period + "other hours" + fees. Hours/days/months/steps/new versions are edited in
        # Settings → Devices & services → Power Controller → Configure → Grid tariff.
        version = coordinator.price_engine.tariff_version()
        entities.append(GridTariffPriceNumber(coordinator, version.id, "default", "Grid Price Other Hours"))
        entities.append(GridTariffPriceNumber(coordinator, version.id, "fee", "Grid Energy Fee"))
        for period in version.periods:
            entities.append(GridTariffPriceNumber(coordinator, version.id, period.id, f"Grid Price {period.name}"))
        async_add_entities(entities)
    elif etype == ENTRY_TYPE_DEVICE:
        entities: list[NumberEntity] = [
            DevicePriorityNumber(coordinator, entry, "plan_priority"),
            DevicePriorityNumber(coordinator, entry, "dist_priority"),
        ]
        dtype = DeviceType(entry.data[CONF_DEVICE_TYPE])
        if dtype == DeviceType.EV_CHARGER:
            entities.append(DeviceEvTargetSocNumber(coordinator, entry))
            entities.append(DeviceEvMinSocNumber(coordinator, entry))
        elif dtype == DeviceType.SWITCH:
            entities.append(DeviceSwitchRuntimeNumber(coordinator, entry))
        async_add_entities(entities)


class HubConfigNumber(CoordinatorEntity[PowerControllerCoordinator], NumberEntity):
    """Central Power Controller numeric configuration entity."""

    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: PowerControllerCoordinator, spec: _HubNumberSpec) -> None:
        super().__init__(coordinator)
        self._spec = spec
        self._attr_unique_id = f"{coordinator.hub_entry.entry_id}_{spec.key}"
        self._attr_name = f"Power Controller {spec.name}"
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_native_min_value = spec.min_val
        self._attr_native_max_value = spec.max_val
        self._attr_native_step = spec.step
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.hub_entry.entry_id)},
            name="Power Controller",
        )

    @property
    def native_value(self) -> float:
        return self._spec.getter(self.coordinator)

    async def async_set_native_value(self, value: float) -> None:
        self._spec.setter(self.coordinator, value)
        await self.coordinator.async_settings_changed(reprice=self._spec.triggers_price, replan=True)


class _DeviceBaseNumber(CoordinatorEntity[PowerControllerCoordinator], NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: PowerControllerCoordinator, entry: ConfigEntry, suffix: str) -> None:
        super().__init__(coordinator)
        self._eid = entry.entry_id
        slug = slugify(entry.title)
        self._attr_unique_id = f"{self._eid}_{suffix}"
        self._attr_name = f"pc_{slug}_{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._eid)},
            name=entry.title,
            via_device=(DOMAIN, coordinator.hub_entry.entry_id),
        )


class DevicePriorityNumber(_DeviceBaseNumber):
    """Per-device planning or distribution priority rank (1 = highest)."""

    _attr_native_min_value = 1.0
    _attr_native_max_value = 50.0
    _attr_native_step = 1.0

    def __init__(self, coordinator: PowerControllerCoordinator, entry: ConfigEntry, kind: str) -> None:
        super().__init__(coordinator, entry, kind)
        self._kind = kind

    @property
    def native_value(self) -> float:
        store = (
            self.coordinator.plan_priority
            if self._kind == "plan_priority"
            else self.coordinator.dist_priority
        )
        return float(store.get(self._eid, 5))

    async def async_set_native_value(self, value: float) -> None:
        if self._kind == "plan_priority":
            self.coordinator.plan_priority[self._eid] = int(value)
        else:
            self.coordinator.dist_priority[self._eid] = int(value)
        await self.coordinator.async_settings_changed(reprice=False, replan=self._kind == "plan_priority")


class DeviceEvTargetSocNumber(_DeviceBaseNumber):
    """Target State of Charge (%) for EV Charger."""

    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 10.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0

    def __init__(self, coordinator: PowerControllerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "target_soc")

    @property
    def native_value(self) -> float:
        return self.coordinator.target_soc.get(self._eid, 80.0)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.target_soc[self._eid] = float(value)
        await self.coordinator.async_settings_changed(reprice=False)


class DeviceSwitchRuntimeNumber(_DeviceBaseNumber):
    """Required runtime hours within scheduled windows for Switch objects."""

    _attr_native_unit_of_measurement = "h"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 24.0
    _attr_native_step = 1.0

    def __init__(self, coordinator: PowerControllerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "runtime_hours")

    @property
    def native_value(self) -> float:
        return self.coordinator.runtime_hours.get(self._eid, 2.0)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.runtime_hours[self._eid] = float(value)
        await self.coordinator.async_settings_changed(reprice=False)


class GridTariffPriceNumber(CoordinatorEntity[PowerControllerCoordinator], NumberEntity):
    """Price of one grid-tariff period (or the default/fee) in the active tariff version.

    The value is shown exactly as entered in the tariff version (incl. or excl. VAT,
    see the ``prices_include_vat`` attribute)."""

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0.0
    _attr_native_max_value = 10.0
    _attr_native_step = 0.0001
    _attr_native_unit_of_measurement = "NOK/kWh"

    def __init__(self, coordinator: PowerControllerCoordinator, version_id: str, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._version_id = version_id
        self._key = key
        # unique per period id -> renaming a period keeps history; new year = new entity
        self._attr_unique_id = f"{coordinator.hub_entry.entry_id}_grid_{key}" if key in ("default", "fee") \
            else f"{coordinator.hub_entry.entry_id}_grid_period_{key}"
        self._attr_name = f"Power Controller {name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.hub_entry.entry_id)},
            name="Power Controller",
        )

    def _version(self):
        return self.coordinator.price_engine.grid_tariff.get(self._version_id)

    @property
    def available(self) -> bool:
        v = self._version()
        if v is None:
            return False
        return self._key in ("default", "fee") or any(p.id == self._key for p in v.periods)

    @property
    def native_value(self) -> float | None:
        v = self._version()
        if v is None:
            return None
        if self._key == "default":
            return v.default_price
        if self._key == "fee":
            return v.energy_fee
        period = next((p for p in v.periods if p.id == self._key), None)
        return period.price if period else None

    @property
    def extra_state_attributes(self) -> dict:
        v = self._version()
        if v is None:
            return {}
        attrs = {"tariff_version": v.name, "valid_from": v.valid_from, "prices_include_vat": v.prices_include_vat}
        period = next((p for p in v.periods if p.id == self._key), None)
        if period:
            attrs.update(days=period.days, start_hour=period.start_hour, end_hour=period.end_hour,
                         months=period.months or "all")
        return attrs

    async def async_set_native_value(self, value: float) -> None:
        v = self._version()
        if v is None:
            return
        if self._key == "default":
            v.default_price = float(value)
        elif self._key == "fee":
            v.energy_fee = float(value)
        else:
            for p in v.periods:
                if p.id == self._key:
                    p.price = float(value)
        await self.coordinator.async_settings_changed()


class DeviceEvMinSocNumber(_DeviceBaseNumber):
    """Minimum state of charge (%) to reach within 24 h of plugging in (can be switched off)."""

    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 5.0
    _attr_icon = "mdi:battery-alert-variant-outline"

    def __init__(self, coordinator: PowerControllerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "min_soc")

    @property
    def native_value(self) -> float:
        return self.coordinator.ev_min_soc.get(self._eid, 30.0)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.ev_min_soc[self._eid] = float(value)
        await self.coordinator.async_settings_changed(reprice=False)
