"""Binary sensor platform for Power Controller grid/tariff overrun warning."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ENTRY_TYPE_HUB
from .coordinator import PowerControllerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if entry.data.get("entry_type", ENTRY_TYPE_HUB) != ENTRY_TYPE_HUB:
        return
    coordinator: PowerControllerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HubWarningBinarySensor(coordinator)])


class HubWarningBinarySensor(CoordinatorEntity[PowerControllerCoordinator], BinarySensorEntity):
    """High when grid phase or hourly tariff overrun is imminent; auto-resets at :00."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: PowerControllerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.hub_entry.entry_id}_warning"
        self._attr_name = "Power Controller Warning"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.hub_entry.entry_id)},
            name="Power Controller",
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.warning_active or self.coordinator.safe_mode_active

    @property
    def extra_state_attributes(self) -> dict:
        """Overload Acceptance per phase: state (ok/accepting/limiting) and remaining budget %."""
        return {
            "overload": self.coordinator.limiters.oa.snapshot(),
            "safe_mode": self.coordinator.safe_mode_active,
            "safe_mode_sensor_ok": self.coordinator.safe_mode_sensor_ok,
        }
