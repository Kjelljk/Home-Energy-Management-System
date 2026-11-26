"""Persistent storage for Power Controller (survives Home Assistant restarts)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 2
STORAGE_KEY = f"{DOMAIN}.state"
SAVE_DELAY_S = 5


class PowerControllerStore:
    """Billing/peaks, thermal models, legionella history AND all user settings/runtime state."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: dict[str, Any] = {
            "current_month": "",
            "monthly_energy_kwh": 0.0,
            "monthly_cost_nok": 0.0,
            "daily_peaks": {},
            "active_tariff_kw": 5.0,
            "spot_history": {},
            "legionella_last_ok": {},
            "thermal_zones": {},
            "priorities_plan": {},
            "priorities_dist": {},
            "settings": {},   # price/limiter/baseload numbers, price model, selected tariff, profile
            "runtime": {},    # committed, priority_now, mode, arrival/departure, EV targets, ...
            "hour": {},       # {"key": "2026-11-26T09", "meter_start": 12345.6}
            "day_counters": {},
        }

    async def async_load(self) -> dict[str, Any]:
        loaded = await self._store.async_load()
        if isinstance(loaded, dict):
            self.data.update(loaded)
        return self.data

    async def async_save(self) -> None:
        await self._store.async_save(self.data)

    def schedule_save(self, collect: Callable[[], dict[str, Any]]) -> None:
        """Debounced save; ``collect`` returns the up-to-date data dict."""
        self._store.async_delay_save(collect, SAVE_DELAY_S)
