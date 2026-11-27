"""Integration entry point for Power Controller."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import voluptuous as vol
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall, callback
    from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
    from homeassistant.helpers import config_validation as cv
    HAS_HOMEASSISTANT = True
except ImportError:  # Allows standalone pytest execution of the pure-Python engines
    HAS_HOMEASSISTANT = False

if TYPE_CHECKING:
    from .coordinator import PowerControllerCoordinator

import logging

from .const import (
    DOMAIN,
    VERSION,
    ENTRY_TYPE_HUB,
    PLATFORMS,
    SERVICE_RECALCULATE_PLAN,
    SERVICE_RESET_SHADOW,
    SERVICE_SET_HOME_OFFICE,
    SERVICE_THERMAL_MODEL,
    SERVICE_TEST_NOTIFICATION,
    SERVICE_SET_PRIORITIES,
)

_LOGGER = logging.getLogger(__name__)

FRONTEND_URL = "/power_controller_frontend"
PANEL_URL_PATH = "power-controller"
CARD_FILES = ("power-controller-object-card.js",)
PANEL_FILE = "power-controller-panel.js"


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the frontend from the integration folder and load it automatically.

    * The object card is injected on every dashboard via ``add_extra_js_url`` – no manual
      Lovelace resource needed.
    * The overview is an admin-only sidebar panel ("Power Controller").
    """
    if hass.data.get(f"{DOMAIN}_frontend"):
        return
    www = Path(__file__).parent / "www"
    if not www.is_dir():
        return
    try:
        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths([StaticPathConfig(FRONTEND_URL, str(www), False)])
        for name in CARD_FILES:
            add_extra_js_url(hass, f"{FRONTEND_URL}/{name}?v={VERSION}")
        hass.data[f"{DOMAIN}_frontend"] = True
    except Exception:  # noqa: BLE001 – older cores / no http in tests
        _LOGGER.warning("Power Controller frontend could not be registered", exc_info=True)


async def _async_register_panel(hass: HomeAssistant) -> None:
    if hass.data.get(f"{DOMAIN}_panel"):
        return
    try:
        from homeassistant.components import panel_custom

        await panel_custom.async_register_panel(
            hass,
            webcomponent_name="power-controller-panel",
            frontend_url_path=PANEL_URL_PATH,
            module_url=f"{FRONTEND_URL}/{PANEL_FILE}?v={VERSION}",
            sidebar_title="Power Controller",
            sidebar_icon="mdi:transmission-tower",
            require_admin=True,
            config={},
        )
        hass.data[f"{DOMAIN}_panel"] = True
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Power Controller panel could not be registered", exc_info=True)


def _async_remove_panel(hass: HomeAssistant) -> None:
    if not hass.data.pop(f"{DOMAIN}_panel", False):
        return
    try:
        from homeassistant.components.frontend import async_remove_panel

        async_remove_panel(hass, PANEL_URL_PATH)
    except Exception:  # noqa: BLE001
        pass


def _register_websocket(hass: HomeAssistant) -> None:
    if hass.data.get(f"{DOMAIN}_ws"):
        return
    from homeassistant.components import websocket_api

    from .coordinator import get_hub_coordinator

    @websocket_api.require_admin
    @websocket_api.websocket_command({vol.Required("type"): "power_controller/overview"})
    @callback
    def ws_overview(hass: HomeAssistant, connection, msg: dict) -> None:
        coordinator = get_hub_coordinator(hass)
        if coordinator is None:
            connection.send_error(msg["id"], "not_loaded", "Power Controller hub is not loaded")
            return
        connection.send_result(msg["id"], coordinator.overview_payload())

    websocket_api.async_register_command(hass, ws_overview)
    hass.data[f"{DOMAIN}_ws"] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the hub, or a child device/zone entry once the hub is running."""
    from .coordinator import PowerControllerCoordinator, get_hub_coordinator

    hass.data.setdefault(DOMAIN, {})

    if entry.data.get("entry_type", ENTRY_TYPE_HUB) == ENTRY_TYPE_HUB:
        coordinator = PowerControllerCoordinator(hass, entry)
        await coordinator.async_initialize()
        hass.data[DOMAIN][entry.entry_id] = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _register_services(hass)
        _register_websocket(hass)
        await _async_register_frontend(hass)
        await _async_register_panel(hass)
        entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
        return True

    coordinator = get_hub_coordinator(hass)
    if coordinator is None:
        # HA retries automatically with back-off until the hub entry is loaded
        raise ConfigEntryNotReady("Power Controller hub is not loaded yet")
    coordinator.register_subentry(entry)
    hass.data[DOMAIN].setdefault("_children", set()).add(entry.entry_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hass.async_create_task(coordinator.async_run_planner())
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Options changed. Apply them WITHOUT reloading the integration (2.11.0): devices and zones
    are re-read and replanned, hub sensors are re-subscribed. Only a missing hub falls back to a
    reload. (Saving the grid tariff still reloads the hub on purpose: it rebuilds the per-period
    price entities.)"""
    from .coordinator import get_hub_coordinator

    coordinator = get_hub_coordinator(hass)
    if coordinator is None:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    if entry.data.get("entry_type", ENTRY_TYPE_HUB) == ENTRY_TYPE_HUB:
        await coordinator.async_hub_reconfigured()
    else:
        await coordinator.async_subentry_reconfigured(entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .coordinator import get_hub_coordinator

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    if entry.data.get("entry_type", ENTRY_TYPE_HUB) == ENTRY_TYPE_HUB:
        coordinator: Any = hass.data[DOMAIN].pop(entry.entry_id, None)
        if coordinator:
            await coordinator.async_shutdown()
        _async_remove_panel(hass)
        # children must be reloaded once the hub is back
        for child_id in list(hass.data[DOMAIN].get("_children", set())):
            hass.async_create_task(hass.config_entries.async_reload(child_id))
    else:
        hass.data[DOMAIN].get("_children", set()).discard(entry.entry_id)
        coordinator = get_hub_coordinator(hass)
        if coordinator:
            coordinator.unregister_subentry(entry.entry_id)
            hass.async_create_task(coordinator.async_run_planner())
    return True


def _resolve_device_ids(coordinator: PowerControllerCoordinator, items: list[str]) -> list[str]:
    """Accept config entry IDs, slugs ('vvb') or entity IDs ('sensor.pc_vvb_plan')."""
    from homeassistant.util import slugify

    by_slug = {slugify(e.title): eid for eid, e in coordinator.device_entries.items()}
    out: list[str] = []
    for raw in items:
        key = str(raw)
        if key in coordinator.device_entries:
            out.append(key)
            continue
        if "." in key:
            key = key.split(".", 1)[1]
        if key.startswith("pc_"):
            key = key[3:]
        for suffix in ("_plan", "_committed", "_power", "_dist_status"):
            if key.endswith(suffix):
                key = key[: -len(suffix)]
        if key in by_slug:
            out.append(by_slug[key])
        else:
            raise ServiceValidationError(f"Unknown Power Controller device: {raw}")
    return out


def _register_services(hass: HomeAssistant) -> None:
    from .coordinator import get_hub_coordinator

    if hass.services.has_service(DOMAIN, SERVICE_SET_PRIORITIES):
        return

    async def handle_set_priorities(call: ServiceCall) -> None:
        coordinator = get_hub_coordinator(hass)
        if coordinator is None:
            raise ServiceValidationError("Power Controller hub is not loaded")
        raw = call.data.get("ordered_entry_ids") or call.data.get("order") or []
        ordered = _resolve_device_ids(coordinator, raw)
        target = call.data.get("target", "both")
        for idx, eid in enumerate(ordered, start=1):
            if target in ("plan", "both"):
                coordinator.plan_priority[eid] = idx
            if target in ("distribution", "both"):
                coordinator.dist_priority[eid] = idx
        await coordinator.async_settings_changed(reprice=False)

    async def handle_recalculate_plan(_call: ServiceCall) -> None:
        coordinator = get_hub_coordinator(hass)
        if coordinator is not None:
            await coordinator.async_run_price_update()
            await coordinator.async_run_planner()

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_PRIORITIES,
        handle_set_priorities,
        schema=vol.Schema(
            vol.All(
                {
                    vol.Optional("ordered_entry_ids"): vol.All(cv.ensure_list, [cv.string]),
                    vol.Optional("order"): vol.All(cv.ensure_list, [cv.string]),
                    vol.Optional("target", default="both"): vol.In(["plan", "distribution", "both"]),
                },
                cv.has_at_least_one_key("ordered_entry_ids", "order"),
            )
        ),
    )
    hass.services.async_register(DOMAIN, SERVICE_RECALCULATE_PLAN, handle_recalculate_plan)

    async def handle_reset_shadow(_call: ServiceCall) -> None:
        coordinator = get_hub_coordinator(hass)
        if coordinator is not None:
            await coordinator.async_reset_shadow()

    hass.services.async_register(DOMAIN, SERVICE_RESET_SHADOW, handle_reset_shadow)

    async def handle_test_notification(_call: ServiceCall) -> None:
        coordinator = get_hub_coordinator(hass)
        if coordinator is None:
            raise ServiceValidationError("Power Controller hub is not loaded")
        try:
            await coordinator.async_test_notification()
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    hass.services.async_register(DOMAIN, SERVICE_TEST_NOTIFICATION, handle_test_notification)

    async def handle_thermal_model(call: ServiceCall) -> None:
        coordinator = get_hub_coordinator(hass)
        if coordinator is None:
            raise ServiceValidationError("Power Controller hub is not loaded")
        try:
            await coordinator.async_thermal_model(
                call.data["zone_id"], call.data["action"],
                call.data.get("heat_capacity_kwh_per_c"), call.data.get("heat_loss_w_per_c"), call.data.get("freeze"))
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_THERMAL_MODEL,
        handle_thermal_model,
        schema=vol.Schema({
            vol.Required("zone_id"): cv.string,
            vol.Required("action"): vol.In(["reset", "set", "freeze", "unfreeze"]),
            vol.Optional("heat_capacity_kwh_per_c"): vol.All(vol.Coerce(float), vol.Range(min=0.05, max=100)),
            vol.Optional("heat_loss_w_per_c"): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=2000)),
            vol.Optional("freeze"): cv.boolean,
        }),
    )

    async def handle_set_home_office(call: ServiceCall) -> None:
        from datetime import timedelta
        from homeassistant.util import dt as dt_util

        coordinator = get_hub_coordinator(hass)
        if coordinator is None:
            raise ServiceValidationError("Power Controller hub is not loaded")
        start = call.data.get("date") or (dt_util.now().date() + timedelta(days=1))
        for i in range(int(call.data.get("days", 1))):
            coordinator.occupancy.set_home_office(start + timedelta(days=i), call.data.get("enabled", True))
        await coordinator.async_settings_changed()

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_HOME_OFFICE,
        handle_set_home_office,
        schema=vol.Schema({
            vol.Optional("date"): cv.date,
            vol.Optional("days", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=31)),
            vol.Optional("enabled", default=True): cv.boolean,
        }),
    )
