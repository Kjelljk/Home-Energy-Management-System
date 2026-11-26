"""DateTime platform for Arrival/Departure times and EV Finish-By deadlines."""
from __future__ import annotations

from datetime import datetime
from homeassistant.components.datetime import DateTimeEntity
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
        async_add_entities(
            [
                HubDateTimeEntity(coordinator, "arrival_time", "Arrival Time"),
                HubDateTimeEntity(coordinator, "departure_time", "Departure Time"),
            ]
        )
    elif etype == ENTRY_TYPE_DEVICE and entry.data.get(CONF_DEVICE_TYPE) == DeviceType.EV_CHARGER.value:
        async_add_entities([DeviceEvFinishByDateTime(coordinator, entry)])


class HubDateTimeEntity(CoordinatorEntity[PowerControllerCoordinator], DateTimeEntity):
    """Expected arrival or departure timestamp on the central Power Controller."""

    def __init__(self, coordinator: PowerControllerCoordinator, kind: str, name: str) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._attr_unique_id = f"{coordinator.hub_entry.entry_id}_{kind}"
        self._attr_name = f"Power Controller {name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.hub_entry.entry_id)},
            name="Power Controller",
        )

    @property
    def native_value(self) -> datetime | None:
        return (
            self.coordinator.arrival_time
            if self._kind == "arrival_time"
            else self.coordinator.departure_time
        )

    async def async_set_value(self, value: datetime) -> None:
        if self._kind == "arrival_time":
            self.coordinator.arrival_time = value
        else:
            self.coordinator.departure_time = value
        await self.coordinator.async_settings_changed()


class DeviceEvFinishByDateTime(CoordinatorEntity[PowerControllerCoordinator], DateTimeEntity):
    """Target finish-by deadline for EV charging."""

    def __init__(self, coordinator: PowerControllerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._eid = entry.entry_id
        slug = slugify(entry.title)
        self._attr_unique_id = f"{self._eid}_finish_by"
        self._attr_name = f"pc_{slug}_finish_by"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._eid)},
            name=entry.title,
            via_device=(DOMAIN, coordinator.hub_entry.entry_id),
        )

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.finish_by.get(self._eid)

    async def async_set_value(self, value: datetime) -> None:
        self.coordinator.finish_by[self._eid] = value
        await self.coordinator.async_settings_changed(reprice=False)
