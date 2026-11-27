"""Select platform for Power Controller Mode, Tariff tier, and Priority Profile."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    ENTRY_TYPE_HUB,
    GridMode,
    OCCUPANCY_LABELS,
    PRICE_MODEL_LABELS,
    PriceModel,
    PriorityProfile,
    SHADOW_MODEL_LABELS,
)
from .coordinator import PowerControllerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if entry.data.get("entry_type", ENTRY_TYPE_HUB) != ENTRY_TYPE_HUB:
        return
    coordinator: PowerControllerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            HubModeSelect(coordinator),
            HubTariffSelect(coordinator),
            HubPriorityProfileSelect(coordinator),
            HubPriceModelSelect(coordinator),
            HubShadowModelSelect(coordinator),
            HubOccupancySelect(coordinator),
        ]
    )


class _HubBaseSelect(CoordinatorEntity[PowerControllerCoordinator], SelectEntity):
    def __init__(self, coordinator: PowerControllerCoordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.hub_entry.entry_id}_{key}"
        self._attr_name = f"Power Controller {name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.hub_entry.entry_id)},
            name="Power Controller",
        )


class HubModeSelect(_HubBaseSelect):
    """House operating mode selector (Home, Away, Arriving)."""

    _attr_options = [m.value for m in GridMode]

    def __init__(self, coordinator: PowerControllerCoordinator) -> None:
        super().__init__(coordinator, "mode", "Mode")

    @property
    def current_option(self) -> str:
        return self.coordinator.mode.value

    async def async_select_option(self, option: str) -> None:
        self.coordinator.mode = GridMode(option)
        if self.coordinator.mode == GridMode.HOME:
            self.coordinator.arrival_time = None
        await self.coordinator.async_settings_changed()


def _kw_label(kw: float) -> str:
    return f"{kw:g} kW"


class HubTariffSelect(_HubBaseSelect):
    """Target kapasitetsledd step (upper bound), options taken from the active grid tariff."""

    def __init__(self, coordinator: PowerControllerCoordinator) -> None:
        super().__init__(coordinator, "tariff", "Tariff")

    @property
    def options(self) -> list[str]:
        uppers = self.coordinator.price_engine.capacity_uppers()
        active = self.coordinator.price_engine.active_tariff_kw
        opts = [_kw_label(u) for u in uppers]
        if _kw_label(active) not in opts:  # e.g. locked above the last finite step
            opts.append(_kw_label(active))
        return opts

    @property
    def current_option(self) -> str:
        """Shows the ACTIVE step (may be higher than selected if the month's peaks locked it)."""
        return _kw_label(self.coordinator.price_engine.active_tariff_kw)

    @property
    def extra_state_attributes(self) -> dict:
        pe = self.coordinator.price_engine
        version = pe.tariff_version()
        return {
            "selected_kw": pe.selected_tariff_kw,
            "locked_kw": pe.locked_tier_kw(),
            "tariff_version": version.name,
            "capacity_charge_this_month_nok": pe.capacity_charge_nok(),
        }

    async def async_select_option(self, option: str) -> None:
        kw = float(option.replace("kW", "").strip())
        self.coordinator.price_engine.set_selected_tariff_kw(kw)
        await self.coordinator.async_settings_changed()


class HubPriorityProfileSelect(_HubBaseSelect):
    """Priority profile selector (Normal, Comfort, EV Boost, Economy)."""

    _attr_options = [p.value for p in PriorityProfile]

    def __init__(self, coordinator: PowerControllerCoordinator) -> None:
        super().__init__(coordinator, "priority_profile", "Priority Profile")

    @property
    def current_option(self) -> str:
        return self.coordinator.priority_profile.value

    async def async_select_option(self, option: str) -> None:
        self.coordinator.priority_profile = PriorityProfile(option)
        await self.coordinator.async_settings_changed(reprice=False)


class HubPriceModelSelect(_HubBaseSelect):
    """Energy price regime: Norgespris, spot + strømstøtte, or plain spot."""

    _attr_options = [PRICE_MODEL_LABELS[m] for m in PriceModel]

    def __init__(self, coordinator: PowerControllerCoordinator) -> None:
        super().__init__(coordinator, "price_model", "Price Model")

    @property
    def current_option(self) -> str:
        return PRICE_MODEL_LABELS[self.coordinator.price_engine.price_model]

    async def async_select_option(self, option: str) -> None:
        model = next(m for m, label in PRICE_MODEL_LABELS.items() if label == option)
        self.coordinator.price_engine.price_model = PriceModel(model)
        await self.coordinator.async_settings_changed()


class HubShadowModelSelect(_HubBaseSelect):
    """Price model the shadow evaluation compares against (Off disables it).
    Changing it restarts the running month's comparison (history is kept)."""

    _attr_options = list(SHADOW_MODEL_LABELS.values())

    def __init__(self, coordinator: PowerControllerCoordinator) -> None:
        super().__init__(coordinator, "shadow_model", "Shadow Comparison Model")

    @property
    def current_option(self) -> str:
        return SHADOW_MODEL_LABELS.get(self.coordinator.shadow.shadow_model, "Off")

    async def async_select_option(self, option: str) -> None:
        model = next(k for k, v in SHADOW_MODEL_LABELS.items() if v == option)
        await self.coordinator.async_set_shadow_model(model)


class HubOccupancySelect(_HubBaseSelect):
    """The CURRENT situation (home and awake / sleeping / away at work / home office).

    Meant to be set by the user or by an automation (e.g. a presence/sleep function). It stays
    until changed. Mode (Home/Away/Arriving) is separate: Away ignores this selector.
    The planner uses it for the current hour(s); the time schedule is used for later hours.
    """

    _attr_icon = "mdi:account-clock"
    _attr_options = list(OCCUPANCY_LABELS.values())

    def __init__(self, coordinator: PowerControllerCoordinator) -> None:
        super().__init__(coordinator, "occupancy_select", "Occupancy")

    @property
    def current_option(self) -> str | None:
        return OCCUPANCY_LABELS.get(self.coordinator.occupancy_selected or "")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "state_key": self.coordinator.occupancy_selected,
            "scheduled_now": self.coordinator.occupancy_scheduled_now(),
            "effective_now": self.coordinator.occupancy_state_now(),
            "ignored": self.coordinator.mode == GridMode.AWAY,
        }

    async def async_select_option(self, option: str) -> None:
        key = next((k for k, v in OCCUPANCY_LABELS.items() if v == option or k == option), None)
        if key is None:
            raise ValueError(option)
        await self.coordinator.async_set_occupancy(key)
