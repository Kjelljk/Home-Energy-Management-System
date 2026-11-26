"""Sensor platform for Power Controller central hub and controllable devices."""
from __future__ import annotations

from typing import Any
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    DOMAIN,
    DistStatus,
    ENTRY_TYPE_DEVICE,
    ENTRY_TYPE_HUB,
    power_kw_to_it_phase_amps,
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
                HubEnergyPriceSensor(coordinator),
                HubMetricSensor(coordinator, "grid_power", "Grid Power", "grid_power_kw", UnitOfPower.KILO_WATT, SensorDeviceClass.POWER),
                HubMetricSensor(coordinator, "hour_allowance", "Hour Allowance", "hour_allowance_kwh", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY),
                HubMetricSensor(coordinator, "current_l1", "Current L1", "current_l1", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT),
                HubMetricSensor(coordinator, "current_l2", "Current L2", "current_l2", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT),
                HubMetricSensor(coordinator, "current_l3", "Current L3", "current_l3", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT),
                HubMetricSensor(coordinator, "limit_now", "Limit Now", "limit_now_kw", UnitOfPower.KILO_WATT, SensorDeviceClass.POWER),
                HubMetricSensor(coordinator, "p_avg_60m", "P Average 60 min", "p_avg_60m_kw", UnitOfPower.KILO_WATT, SensorDeviceClass.POWER),
                HubMetricSensor(coordinator, "energy_this_hour", "Energy This Hour", "energy_this_hour_kwh", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY),
                HubMetricSensor(coordinator, "energy_projected", "Energy Projected This Hour", "energy_projected_kwh", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY),
                HubMetricSensor(coordinator, "monthly_max", "Monthly Max", "monthly_max_kw", UnitOfPower.KILO_WATT, SensorDeviceClass.POWER),
                HubMetricSensor(coordinator, "monthly_energy", "Monthly Energy Consumption", "monthly_energy_kwh", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
                HubMetricSensor(coordinator, "monthly_cost", "Monthly Energy Cost", "monthly_cost_nok", "NOK", SensorDeviceClass.MONETARY),
                HubTotalPlanSensor(coordinator),
                HubGridTariffSensor(coordinator),
                HubShadowBenefitSensor(coordinator),
                HubOccupancySensor(coordinator),
                HubMetricSensor(coordinator, "capacity_charge", "Capacity Charge This Month", "capacity_charge_nok", "NOK", SensorDeviceClass.MONETARY),
            ]
        )
    elif etype == ENTRY_TYPE_DEVICE:
        async_add_entities(
            [
                DevicePlanSensor(coordinator, entry),
                DevicePowerSensor(coordinator, entry),
                DeviceCurrentSensor(coordinator, entry),
                DeviceDistStatusSensor(coordinator, entry),
            ]
        )


def _hub_device_info(coordinator: PowerControllerCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.hub_entry.entry_id)},
        name="Power Controller",
        manufacturer="Power Controller HEMS",
        model="Central Controller",
    )


def _child_device_info(coordinator: PowerControllerCoordinator, entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Power Controller HEMS",
        model=entry.data.get("device_type", "Controllable Object"),
        via_device=(DOMAIN, coordinator.hub_entry.entry_id),
    )


class HubEnergyPriceSensor(CoordinatorEntity[PowerControllerCoordinator], SensorEntity):
    """Current hourly electricity price (NOK/kWh) with 72h array breakdown attributes."""

    _attr_native_unit_of_measurement = "NOK/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: PowerControllerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.hub_entry.entry_id}_energy_price"
        self._attr_name = "Power Controller Energy Price"
        self._attr_device_info = _hub_device_info(coordinator)

    @property
    def native_value(self) -> float | None:
        ps = self.coordinator.price_series
        return ps.total_price_72h[0] if (ps and ps.total_price_72h) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ps = self.coordinator.price_series
        if not ps:
            return {}
        return {
            "spot_after_support_this_hour": ps.spot_after_support_72h[0],
            "grid_cost_this_hour": ps.grid_price_72h[0],
            "fees_and_vat_this_hour": ps.fee_and_vat_72h[0],
            "hourly_average_spot_prices": ps.raw_spot_72h,
            "hourly_prices": ps.spot_after_support_72h,
            "hourly_grid_price": ps.grid_price_72h,
            "hourly_energy_fee_and_vat_price": ps.fee_and_vat_72h,
            "hourly_total_price": ps.total_price_72h,
            "hourly_spot_is_forecast": ps.spot_is_forecast_72h,
            "timestamps": [t.isoformat() for t in ps.timestamps],
            "price_model": ps.price_model,
        }


class HubMetricSensor(CoordinatorEntity[PowerControllerCoordinator], SensorEntity):
    """Generic numeric sensor reading a key from the coordinator data snapshot."""

    def __init__(
        self,
        coordinator: PowerControllerCoordinator,
        key: str,
        name: str,
        data_key: str,
        unit: str,
        device_class: SensorDeviceClass,
        state_class: SensorStateClass = SensorStateClass.MEASUREMENT,
    ) -> None:
        super().__init__(coordinator)
        self._data_key = data_key
        self._attr_unique_id = f"{coordinator.hub_entry.entry_id}_{key}"
        self._attr_name = f"Power Controller {name}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        if device_class == SensorDeviceClass.ENERGY and state_class == SensorStateClass.MEASUREMENT:
            state_class = SensorStateClass.TOTAL_INCREASING  # HA rejects energy + measurement
        if device_class == SensorDeviceClass.MONETARY:
            state_class = SensorStateClass.TOTAL
        self._attr_state_class = state_class
        self._attr_device_info = _hub_device_info(coordinator)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get(self._data_key) if self.coordinator.data else None


class HubTotalPlanSensor(CoordinatorEntity[PowerControllerCoordinator], SensorEntity):
    """Combined 72h power plan sensor (kW)."""

    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: PowerControllerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.hub_entry.entry_id}_total_plan"
        self._attr_name = "Power Controller Total Plan"
        self._attr_device_info = _hub_device_info(coordinator)

    @property
    def native_value(self) -> float:
        return self.coordinator.total_plan_72h[0] if self.coordinator.total_plan_72h else 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        res = self.coordinator.plan_result
        return {
            "plan_72h": self.coordinator.total_plan_72h,
            "baseload_72h": self.coordinator.baseload_72h,
            "solver": res.solver,
            "solve_ms": res.solve_ms,
            "unmet": res.unmet,
            "last_plan": self.coordinator.last_plan_ts.isoformat() if self.coordinator.last_plan_ts else None,
        }


class DevicePlanSensor(CoordinatorEntity[PowerControllerCoordinator], SensorEntity):
    """Per-device planned power (kW) with 72h array attribute (None when uncommitted)."""

    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER

    def __init__(self, coordinator: PowerControllerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._eid = entry.entry_id
        slug = slugify(entry.title)
        self._attr_unique_id = f"{self._eid}_plan"
        self._attr_name = f"pc_{slug}_plan"
        self._attr_device_info = _child_device_info(coordinator, entry)

    @property
    def native_value(self) -> float | None:
        arr = self.coordinator.plans_72h.get(self._eid)
        return arr[0] if arr else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # Single attribute contract shared with the Lovelace cards (see docs/ENTITY_CONTRACT.md)
        if self._eid not in self.coordinator.device_entries:
            return {}
        return self.coordinator.device_attributes(self._eid)


class DevicePowerSensor(CoordinatorEntity[PowerControllerCoordinator], SensorEntity):
    """Per-device active power (kW) with automatic nominal-power fallback."""

    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: PowerControllerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._eid = entry.entry_id
        slug = slugify(entry.title)
        self._attr_unique_id = f"{self._eid}_power"
        self._attr_name = f"pc_{slug}_power"
        self._attr_device_info = _child_device_info(coordinator, entry)

    @property
    def native_value(self) -> float:
        return round(self.coordinator.get_device_live_power_kw(self._eid), 3)


class DeviceCurrentSensor(CoordinatorEntity[PowerControllerCoordinator], SensorEntity):
    """Per-device 230V IT phase current (A)."""

    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: PowerControllerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._eid = entry.entry_id
        slug = slugify(entry.title)
        self._attr_unique_id = f"{self._eid}_current"
        self._attr_name = f"pc_{slug}_current"
        self._attr_device_info = _child_device_info(coordinator, entry)

    @property
    def native_value(self) -> float:
        kw = self.coordinator.get_device_live_power_kw(self._eid)
        return round(power_kw_to_it_phase_amps(kw), 2)


class DeviceDistStatusSensor(CoordinatorEntity[PowerControllerCoordinator], SensorEntity):
    """Per-device real-time distribution status (Prioritized, Redistributing, Hold, Uncommitted)."""

    def __init__(self, coordinator: PowerControllerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._eid = entry.entry_id
        slug = slugify(entry.title)
        self._attr_unique_id = f"{self._eid}_dist_status"
        self._attr_name = f"pc_{slug}_dist_status"
        self._attr_device_info = _child_device_info(coordinator, entry)

    @property
    def native_value(self) -> str:
        alloc = self.coordinator.allocations.get(self._eid)
        return alloc.status.value if alloc else DistStatus.UNCOMMITTED.value


class HubGridTariffSensor(CoordinatorEntity[PowerControllerCoordinator], SensorEntity):
    """Current grid energy charge (energiledd incl. fees and VAT) and the active period."""

    _attr_native_unit_of_measurement = "NOK/kWh"

    def __init__(self, coordinator: PowerControllerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.hub_entry.entry_id}_grid_tariff"
        self._attr_name = "Power Controller Grid Tariff"
        self._attr_device_info = _hub_device_info(coordinator)

    @property
    def native_value(self) -> float | None:
        ps = self.coordinator.price_series
        if not ps:
            return None
        vat = 1 + self.coordinator.price_engine.vat_percent / 100.0
        version = self.coordinator.price_engine.tariff_version(ps.timestamps[0].date())
        return round((ps.grid_price_72h[0] + version.energy_fee_ex_vat(self.coordinator.price_engine.vat_percent)) * vat, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pe = self.coordinator.price_engine
        ps = self.coordinator.price_series
        version = pe.tariff_version()
        return {
            "period": ps.grid_period_72h[0] if ps and ps.grid_period_72h else None,
            "hourly_period": ps.grid_period_72h if ps else [],
            "tariff_version": version.name,
            "valid_from": version.valid_from,
            "periods": [p.describe() for p in version.periods],
            "default_price": version.default_price,
            "prices_include_vat": version.prices_include_vat,
            "capacity_steps": [{"upper_kw": st.upper_kw, "monthly_nok": st.monthly_nok} for st in version.capacity_steps],
            "versions": [{"name": v.name, "valid_from": v.valid_from} for v in pe.grid_tariff.versions],
        }


class HubShadowBenefitSensor(CoordinatorEntity[PowerControllerCoordinator], SensorEntity):
    """Norgespris benefit this month (NOK): optimised-for-spot total minus actual total.

    Positive = the current price model (Norgespris) is cheaper. Negative = an optimiser
    planning for spot prices would have given a lower bill."""

    _attr_native_unit_of_measurement = "NOK"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator: PowerControllerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.hub_entry.entry_id}_shadow_benefit"
        self._attr_name = "Power Controller Norgespris Benefit This Month"
        self._attr_device_info = _hub_device_info(coordinator)

    @property
    def available(self) -> bool:
        return self.coordinator.shadow.enabled

    @property
    def native_value(self) -> float | None:
        sh = self.coordinator.shadow
        if not sh.enabled or not sh.month:
            return None
        return sh.month_summary(self.coordinator.price_engine)["benefit_nok"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sh = self.coordinator.shadow
        pe = self.coordinator.price_engine
        if not sh.enabled or not sh.month:
            return {"shadow_model": sh.shadow_model}
        summary = sh.month_summary(pe)
        summary["rolling_12_months"] = sh.rolling_12m(pe)
        summary["history"] = {m: {"benefit_nok": h["benefit_nok"],
                                  "actual_nok": h["actual"]["total_nok"],
                                  "optimised_spot_nok": h["optimised_spot"]["total_nok"],
                                  "passive_spot_nok": h["passive_spot"]["total_nok"]}
                              for m, h in sorted(sh.history.items())}
        summary["shadow_plan_72h"] = {k: v for k, v in self.coordinator.shadow_plans.items()}
        return summary


class HubOccupancySensor(CoordinatorEntity[PowerControllerCoordinator], SensorEntity):
    """Expected occupancy now (sleep / home_awake / away_work / home_office) with the
    72-hour expectation and upcoming home-office days as attributes."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["sleep", "home_awake", "away_work", "home_office", "out_of_town"]
    _attr_icon = "mdi:home-account"

    def __init__(self, coordinator: PowerControllerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.hub_entry.entry_id}_occupancy"
        self._attr_name = "Power Controller Occupancy"
        self._attr_device_info = _hub_device_info(coordinator)

    @property
    def native_value(self) -> str:
        from .const import GridMode
        if self.coordinator.mode == GridMode.AWAY:
            return "out_of_town"
        return self.coordinator.occupancy_state_now()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        from homeassistant.util import dt as dt_util
        from .occupancy import OccupancyProfile
        occ = self.coordinator.occupancy
        return {
            "hourly_state_72h": [OccupancyProfile.dominant_state(s) for s in self.coordinator.occ_hour_states],
            "start": self.coordinator.occ_start.isoformat() if self.coordinator.occ_start else None,
            "home_office_weekdays": occ.home_office_weekdays,
            "home_office_upcoming": occ.upcoming_home_office(dt_util.now().date(), self.coordinator.is_workday),
            "home_office_overrides": occ.home_office_dates,
            "workday_times": occ.workday.__dict__,
            "day_off_times": occ.free_day.__dict__,
        }
