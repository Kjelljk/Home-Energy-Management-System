"""Switch platform for per-device Committed and Priority Now toggles."""
from __future__ import annotations

from typing import Any
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from datetime import timedelta

from homeassistant.util import dt as dt_util

from .const import DOMAIN, ENTRY_TYPE_DEVICE, ENTRY_TYPE_HUB
from .coordinator import PowerControllerCoordinator, get_hub_coordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = get_hub_coordinator(hass)
    if coordinator is None:
        return
    if entry.data.get("entry_type", ENTRY_TYPE_HUB) == ENTRY_TYPE_HUB:
        async_add_entities([HomeOfficeSwitch(coordinator, 0), HomeOfficeSwitch(coordinator, 1)])
        return
    if entry.data.get("entry_type") != ENTRY_TYPE_DEVICE:
        return
    async_add_entities(
        [
            DeviceCommittedSwitch(coordinator, entry),
            DevicePriorityNowSwitch(coordinator, entry),
        ]
    )


class _DeviceBaseSwitch(CoordinatorEntity[PowerControllerCoordinator], SwitchEntity):
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


class DeviceCommittedSwitch(_DeviceBaseSwitch):
    """Toggle between Committed (managed by Power Controller) and Uncommitted (manual)."""

    def __init__(self, coordinator: PowerControllerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "committed")

    @property
    def is_on(self) -> bool:
        return self.coordinator.committed.get(self._eid, False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.committed[self._eid] = True
        await self.coordinator.async_settings_changed(reprice=False)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.committed[self._eid] = False
        await self.coordinator.async_release_device(self._eid)  # hand back in a sane state
        await self.coordinator.async_settings_changed(reprice=False)


class DevicePriorityNowSwitch(_DeviceBaseSwitch):
    """Temporary top-priority override for immediate power & schedule priority."""

    def __init__(self, coordinator: PowerControllerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "priority_now")

    @property
    def is_on(self) -> bool:
        return self.coordinator.priority_now.get(self._eid, False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.priority_now[self._eid] = True
        await self.coordinator.async_settings_changed(reprice=False)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.priority_now[self._eid] = False
        await self.coordinator.async_settings_changed(reprice=False)


class HomeOfficeSwitch(CoordinatorEntity[PowerControllerCoordinator], SwitchEntity):
    """Quick toggle: home office today / tomorrow. On = rooms with 'home_office' comfort are
    heated during work hours and the house counts as occupied all day."""

    _attr_icon = "mdi:laptop-account"

    def __init__(self, coordinator: PowerControllerCoordinator, offset_days: int) -> None:
        super().__init__(coordinator)
        self._offset = offset_days
        label = "Today" if offset_days == 0 else "Tomorrow"
        self._attr_unique_id = f"{coordinator.hub_entry.entry_id}_home_office_{label.lower()}"
        self._attr_name = f"Power Controller Home Office {label}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.hub_entry.entry_id)}, name="Power Controller")

    def _day(self):
        return dt_util.now().date() + timedelta(days=self._offset)

    @property
    def is_on(self) -> bool:
        return self.coordinator.occupancy.is_home_office(self._day(), True)

    @property
    def extra_state_attributes(self) -> dict:
        day = self._day()
        return {"date": day.isoformat(), "workday": self.coordinator.is_workday(day),
                "note": None if self.coordinator.is_workday(day) else "Day off – home anyway, no effect"}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_home_office(self._day(), True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_home_office(self._day(), False)
