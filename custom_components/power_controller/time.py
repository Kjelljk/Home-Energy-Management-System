"""Time platform: expected wake / leave / return / sleep times (occupancy profile)."""
from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ENTRY_TYPE_HUB
from .coordinator import PowerControllerCoordinator, get_hub_coordinator
from .occupancy import fmt_hhmm, parse_hhmm

# (key, name, day type attribute, field)
TIME_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("workday_wake", "Workday Wake Time", "workday", "wake"),
    ("workday_leave", "Workday Leave Time", "workday", "leave"),
    ("workday_back", "Workday Return Time", "workday", "back"),
    ("workday_sleep", "Workday Bedtime", "workday", "sleep"),
    ("free_day_wake", "Day Off Wake Time", "free_day", "wake"),
    ("free_day_sleep", "Day Off Bedtime", "free_day", "sleep"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if entry.data.get("entry_type", ENTRY_TYPE_HUB) != ENTRY_TYPE_HUB:
        return
    coordinator = get_hub_coordinator(hass)
    if coordinator is None:
        return
    async_add_entities([OccupancyTimeEntity(coordinator, *spec) for spec in TIME_SPECS])


class OccupancyTimeEntity(CoordinatorEntity[PowerControllerCoordinator], TimeEntity):
    """One clock time of the occupancy profile. Changing it replans immediately."""

    _attr_icon = "mdi:home-clock"

    def __init__(self, coordinator: PowerControllerCoordinator, key: str, name: str, day: str, fld: str) -> None:
        super().__init__(coordinator)
        self._day, self._fld = day, fld
        self._attr_unique_id = f"{coordinator.hub_entry.entry_id}_{key}"
        self._attr_name = f"Power Controller {name}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.hub_entry.entry_id)}, name="Power Controller")

    @property
    def native_value(self) -> time:
        minutes = parse_hhmm(getattr(getattr(self.coordinator.occupancy, self._day), self._fld))
        return time(minutes // 60, minutes % 60)

    async def async_set_value(self, value: time) -> None:
        setattr(getattr(self.coordinator.occupancy, self._day), self._fld, fmt_hhmm(value.hour * 60 + value.minute))
        await self.coordinator.async_settings_changed()
