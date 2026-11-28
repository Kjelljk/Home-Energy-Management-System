"""DataUpdateCoordinator orchestrating the four execution loops of Power Controller (v2.2).

Loop 1  price & tariff engine      – hourly rollover, on new Nord Pool data, on setting changes
Loop 2  72h LP planner             – every hour at :59, on any user change (executor thread)
Loop 3  real-time distribution     – every 10 s + on grid sensor events; ALL actuation happens
                                     here, rate limited by anti-cycling timers (forced shedding
                                     bypasses them when an overrun is imminent)
Loop 4  slow learning              – every 60 s: RLS thermal calibration, legionella tracking
"""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACTIVE_POWER_SENSOR,
    CONF_COMFORT_TEMP_ENTITY,
    CONF_CURRENT_L1_SENSOR,
    CONF_CURRENT_L2_SENSOR,
    CONF_CURRENT_L3_SENSOR,
    CONF_COMFORT_DURING,
    CONF_DAILY_QUOTA_HOURS,
    CONF_DEVICE_TYPE,
    CONF_ECO_TEMP_ENTITY,
    CONF_ENABLE_PI_GOVERNOR,
    CONF_HEATER_KIND,
    HEATER_ON_OFF,
    CONF_EV_AVAILABILITY_SWITCH,
    CONF_EV_BATTERY_KWH,
    CONF_EV_CHARGE_SWITCH,
    CONF_EV_CONNECTED_SENSOR,
    CONF_EV_CONN_ID,
    CONF_EV_MIN_CURRENT_A,
    CONF_EV_SOC_SENSOR,
    CONF_EV_TARGET_SOC_ENTITY,
    CONF_EV_STATUS_SENSOR,
    CONF_EV_OFFERED_SENSOR,
    CONF_EV_SESSION_ENERGY_SENSOR,
    CONF_EV_CAR_LIMIT_ENTITY,
    CONF_EV_CURRENT_SENSOR,
    CONF_APPLIANCE_DEVICE,
    CONF_APPL_DEFAULT_KWH,
    CONF_APPL_DEFAULT_H,
    CONF_APPL_RUNNING_STATES,
    CONF_APPL_DELAYED_STATES,
    APPLIANCE_ROLE_CONF,
    EV_STATUS_TEXT,
    CONF_EV_TRANSACTION_SENSOR,
    CONF_EXTERNAL_LIMIT_SENSOR,
    CONF_HOUR_ENERGY_SENSOR,
    CONF_NOTIFY_QUIET_END,
    CONF_NOTIFY_QUIET_START,
    CONF_NOTIFY_SERVICE,
    CONF_HEATING_COEFFICIENT,
    CONF_INSULATION_CLASS,
    CONF_MIN_TEMP_ENTITY,
    CONF_MONTHLY_ENERGY_SENSOR,
    CONF_NOMINAL_CURRENT_A,
    CONF_NOMINAL_POWER_KW,
    CONF_NOMINAL_TEMP_ENTITY,
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
    CONF_ZONE_NAME,
    CONF_ZONE_ID,
    DOMAIN,
    VERSION,
    OA_CONF_MAP,
    DeviceAllocation,
    DeviceType,
    DistStatus,
    ENTRY_TYPE_DEVICE,
    ENTRY_TYPE_ZONE,
    GridMode,
    HOTWATER_LEGIONELLA_C,
    InsulationClass,
    MIN_CLIMATE_SWITCH_SECONDS,
    MIN_EV_PAUSE_SECONDS,
    MIN_OCPP_COMMAND_INTERVAL_S,
    MIN_RELAY_SWITCH_SECONDS,
    PLANNING_HORIZON_HOURS,
    PhasePair,
    PriceModel,
    PriorityProfile,
    SENSOR_STALE_DEFAULT_S,
    SAFE_MODE_LATCH_S,
    STARTUP_GRACE_S,
    phase_pair_conductors,
    power_kw_to_it_phase_amps,
)
from .distributor import PowerDistributor
from .limiters import GridAndTariffLimiters
from .overload import OAParams
from .planner import EnergyPlanner, PlanResult, aggregate_hourly
from .actuation import (
    ControlSettings,
    ev_min_soc_deadline,
    ocpp_charge_rate_data,
    parse_transaction_id,
    should_send_command,
)
from .grid_tariff import PERIOD_EDIT_ERRORS, GridTariffSchedule, edit_period
from .price_engine import EnergyPriceEngine, PriceSeries72h, build_workday_mask, is_norwegian_workday
from .card_model import EV_CHECK_DELAY_S, ev_checks, next_action, plan_index, plan_window, ui_status, value_at
from .energy_meter import ClosedHour, HourEnergyTracker, max_hour_kwh_for_fuse, rebuild_month
from .notifier import ACTION_IGNORE, ACTION_PRIORITY_NOW, NotifySettings, Notifier, parse_hhmm
from .occupancy import OCC_SELECT_OPTIONS, OccupancyProfile, apply_selector, effective_state
from .hotwater_model import HotWaterModel
from .appliance_model import ApplianceModel, classify as appl_classify, detect_roles, parse_duration_h, parse_timestamp
from .shadow import ShadowEvaluator
from .storage import PowerControllerStore
from .thermodynamics import ThermalZoneModel, seed_thermal_parameters

_LOGGER = logging.getLogger(__name__)

# price-engine attributes persisted as user settings (edited through number/select entities)
PRICE_SETTINGS = (
    "norgespris_nok", "norgespris_volume_kwh", "support_threshold_nok", "support_rate",
    "support_volume_kwh", "supplier_markup_nok", "vat_percent",
    "baseload_24h_kw", "baseload_daytime_kw",
)
EV_ACTIVE_STATES = {"on", "charging", "suspendedev", "suspendedevse", "preparing", "finishing", "occupied", "connected"}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class PowerControllerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Central HEMS coordinator."""

    def __init__(self, hass: HomeAssistant, hub_entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=10))
        self.hub_entry = hub_entry
        self.store = PowerControllerStore(hass)

        self.price_engine = EnergyPriceEngine()
        self.limiters = GridAndTariffLimiters()
        self.planner = EnergyPlanner()
        # shadow evaluation (never actuates): what would a spot-price customer have paid?
        self.shadow = ShadowEvaluator()
        self.shadow_planner = EnergyPlanner()
        self.shadow_plans: dict[str, list[float | None]] = {}
        self.shadow_plan_start: datetime | None = None
        self._hour_dev_kwh: dict[str, float] = {}
        self.distributor = PowerDistributor()

        self.zones: dict[str, ThermalZoneModel] = {}
        self.tanks: dict[str, HotWaterModel] = {}      # learned hot-water tank models per device
        self.appliances: dict[str, ApplianceModel] = {}  # program energy registers per appliance
        self._appl_roles: dict[str, dict[str, str]] = {}
        self._appl_last: dict[str, tuple] = {}
        self.zone_cfg: dict[str, dict] = {}            # name, area, insulation, window sensor
        self._zones_without_seed: set[str] = set()
        self.zone_windows: dict[str, str | None] = {}
        self.device_entries: dict[str, ConfigEntry] = {}

        # user/runtime state (persisted)
        self.mode: GridMode = GridMode.HOME
        self.priority_profile: PriorityProfile = PriorityProfile.NORMAL
        # expected home/awake times and home-office days (drives comfort when not out of town)
        self.occupancy = OccupancyProfile()
        self.occ_hour_states: list[set[str]] = []
        self.occ_start: datetime | None = None
        # current situation from the Occupancy select (set by the user/an automation);
        # None until first set -> initialised from the schedule at start-up
        self.occupancy_selected: str | None = None
        # plan vs. actual per finished hour (7 days)
        self.plan_history: list[dict] = []
        self.arrival_time: datetime | None = None
        self.departure_time: datetime | None = None
        self.committed: dict[str, bool] = {}
        self.priority_now: dict[str, bool] = {}
        self.plan_priority: dict[str, int] = {}
        self.dist_priority: dict[str, int] = {}
        self.target_soc: dict[str, float] = {}
        self.finish_by: dict[str, datetime | None] = {}
        self.runtime_hours: dict[str, float] = {}

        # outputs
        self.price_series: PriceSeries72h | None = None
        self.plan_result: PlanResult = PlanResult()
        self.plans_72h: dict[str, list[float | None]] = {}
        self.total_plan_72h: list[float] = [0.0] * PLANNING_HORIZON_HOURS
        self.baseload_72h: list[float] = [0.0] * PLANNING_HORIZON_HOURS
        self.allocations: dict[str, DeviceAllocation] = {}
        self.commanded_setpoint: dict[str, float] = {}

        self.grid_power_kw: float = 0.0
        self.phase_currents: dict[str, float] = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
        self.limit_now_kw: float = 5.0
        self.hour_allowance_kwh: float = 4.85
        self.p_avg_60m_kw: float = 0.0
        self.energy_this_hour_kwh: float = 0.0
        self.hour_energy_source: str = "integrated"
        self.energy_projected_kwh: float = 0.0
        self.warning_active: bool = False
        self.safe_mode_active: bool = False
        self.safe_mode_sensor_ok: bool = True
        self.startup_wait: bool = False
        self._started_at: datetime = dt_util.now()
        self._grid_seen_ok: bool = False
        self._grid_missing_since: datetime | None = None
        self._safe_mode_hour: str | None = None
        self._safe_mode_since: datetime | None = None
        self.last_plan_ts: datetime | None = None
        self.plan_start: datetime | None = None   # timestamp of index 0 in plans_72h
        # 15-minute plan (kW per slot) used by the real-time loop; plans_72h is the hourly view
        self.plans_15m: dict[str, list[float | None]] = {}
        self.plan_slots: PlanResult = PlanResult()
        self.plan_spp: int = 4
        self._spot_quarters: list[float | None] = []
        # command patience & EV options (persisted)
        self.control = ControlSettings()
        self.ev_deadline_enabled: dict[str, bool] = {}
        self.ev_min_soc_enabled: dict[str, bool] = {}
        self.ev_min_soc: dict[str, float] = {}
        self.ev_session_start: dict[str, datetime | None] = {}
        self.ev_min_since: dict[str, datetime | None] = {}
        self._ev_was_connected: dict[str, bool] = {}
        # status vs. measurement disagreements per EV (shown on the card after EV_CHECK_DELAY_S)
        self.ev_warnings: dict[str, dict[str, str]] = {}
        self._ev_check_since: dict[str, dict[str, datetime]] = {}
        self._entity_cache: dict[tuple[str, str], str | None] = {}

        # trackers
        self._hour_key: str | None = None
        self._hour_start_meter_kwh: float | None = None
        self._last_fast_ts: datetime | None = None
        self._last_ev_target_a: dict[str, int] = {}
        self._last_ocpp_cmd_ts: dict[str, datetime] = {}
        self._last_ev_cmd_kind: dict[str, str] = {}
        self._ev_last_action: dict[str, str] = {}
        self._last_switch_ts: dict[str, datetime] = {}
        self._last_zone_sample: dict[str, tuple[datetime, float]] = {}
        self._legionella_high_minutes: dict[str, float] = {}
        self._day_energy_kwh: dict[str, float] = {}
        self._day_runtime_h: dict[str, float] = {}
        self._day_key: str = ""
        self._last_tomorrow_len: int = -1
        self._planner_running = False
        self._tariff_version_id: str | None = None
        self._unsubs: list[Any] = []
        self._grid_unsubs: list[Any] = []
        # hourly energy (integrated grid power or hourly utility-meter sensor) and notifications
        self.hour_meter = HourEnergyTracker()
        self.month_source: dict = {"source": "live"}
        # hours measured by Power Controller itself this month (trusted for the capacity charge)
        self.own_hours: dict[str, float] = {}
        self.notifier = Notifier()
        self._setpoint_mismatch_since: dict[str, datetime] = {}
        self._ocpp_rejected: dict[str, datetime] = {}
        self._plan_failed = False
        self._stepped_up_at: datetime | None = None

    # ------------------------------------------------------------------
    # Setup / persistence
    # ------------------------------------------------------------------
    def _hub_cfg(self) -> dict[str, Any]:
        return {**self.hub_entry.data, **self.hub_entry.options}

    async def async_initialize(self) -> None:
        saved = await self.store.async_load()
        now = dt_util.now()

        if saved.get("current_month") == now.strftime("%Y-%m"):
            self.price_engine.monthly_energy_kwh = float(saved.get("monthly_energy_kwh", 0.0))
            self.price_engine.monthly_cost_nok = float(saved.get("monthly_cost_nok", 0.0))
            self.price_engine.daily_peaks = dict(saved.get("daily_peaks", {}))
            self.price_engine.active_tariff_kw = float(saved.get("active_tariff_kw", 5.0))
        self.price_engine.spot_history = dict(saved.get("spot_history", {}))
        self.shadow = ShadowEvaluator.from_dict(saved.get("shadow"))
        self.plan_history = list(saved.get("plan_history", []))[-168:]
        for aid, adata in saved.get("appliance_models", {}).items():
            try:
                self.appliances[aid] = ApplianceModel.from_dict(adata)
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Could not restore appliance register %s", aid)
        for tid, tdata in saved.get("hotwater_models", {}).items():
            try:
                self.tanks[tid] = HotWaterModel.from_dict(tdata)
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Could not restore hot-water model %s", tid)
        for zid, zdata in saved.get("thermal_zones", {}).items():
            self.zones[zid] = ThermalZoneModel.from_dict(zdata)
            if zdata.get("seed_alpha") is None:        # stored before 2.10: seed from config below
                self._zones_without_seed.add(zid)
        self._restore_settings(saved.get("settings", {}))
        self._restore_runtime(saved.get("runtime", {}))
        # stored peaks from older versions may contain lumped (implausible) hours: drop them and
        # re-derive the active step (the statistics rebuild below then gives the exact month)
        pe = self.price_engine
        limit = max_hour_kwh_for_fuse(self.limiters.internal_limit_a)
        bad = {k: v for k, v in pe.daily_peaks.items() if v > limit}
        if bad:
            _LOGGER.warning("Dropping implausible stored daily peaks (> %.1f kWh/h): %s", limit, bad)
            pe.daily_peaks = {k: v for k, v in pe.daily_peaks.items() if v <= limit}
            pe.active_tariff_kw = max(pe.selected_tariff_kw, pe.locked_tier_kw())

        hour = saved.get("hour", {})
        if hour.get("key") == now.strftime("%Y-%m-%dT%H"):
            self._hour_key = hour["key"]
            self._hour_dev_kwh = dict(hour.get("device_kwh", {}))
            self.hour_meter.restore(hour.get("tracker"), now)
        self.notifier.restore(saved.get("notifier"))
        self.own_hours = {k: float(v) for k, v in (saved.get("own_hours") or {}).items()
                          if k[:7] == now.strftime("%Y-%m")}
        counters = saved.get("day_counters", {})
        if counters.get("day") == now.strftime("%Y-%m-%d"):
            self._day_key = counters["day"]
            self._day_energy_kwh = dict(counters.get("energy", {}))
            self._day_runtime_h = dict(counters.get("runtime", {}))

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id != self.hub_entry.entry_id:
                self.register_subentry(entry)

        cfg = self._hub_cfg()
        self.limiters.oa.params = OAParams(**{
            key: float(cfg[conf]) for key, conf in OA_CONF_MAP.items() if cfg.get(conf) is not None
        })
        zid = cfg.get(CONF_ZONE_ID, "default_zone")
        self.zone_cfg.setdefault(zid, {"name": cfg.get(CONF_ZONE_NAME, "Main Living Zone"),
                                       "area": cfg.get(CONF_ZONE_AREA_M2, 40.0),
                                       "insulation": cfg.get(CONF_INSULATION_CLASS, InsulationClass.STANDARD),
                                       "window": cfg.get(CONF_WINDOW_SENSOR)})
        if zid not in self.zones:  # the hub's own initial zone
            alpha, beta = seed_thermal_parameters(
                cfg.get(CONF_ZONE_AREA_M2, 40.0), cfg.get(CONF_INSULATION_CLASS, InsulationClass.STANDARD)
            )
            self.zones[zid] = ThermalZoneModel(zone_id=zid, alpha=alpha, beta=beta)
        for z in list(self._zones_without_seed):
            seed = self.zone_seed(z)
            if seed and z in self.zones:
                self.zones[z].seed_alpha, self.zones[z].seed_beta = seed
        self._zones_without_seed.clear()

        self._unsubs += [
            # the hour is closed by the fast loop in the step that sees the new hour; this
            # trigger only makes that happen within a second of the hour change
            async_track_time_change(self.hass, self._handle_hour_tick, minute=0, second=1),
            # recorder statistics for the finished hour exist a few minutes after the hour
            async_track_time_change(self.hass, self._handle_month_reconcile, minute=6, second=0),
            async_track_time_change(self.hass, self._handle_price_check, hour=15, minute=0, second=0),
            async_track_time_change(self.hass, self._handle_scheduled_planning, minute=59, second=0),
            async_track_time_change(self.hass, self._handle_daily_price_update, hour=13, minute=5, second=0),
            async_track_interval(self.hass, self._handle_slow_loop, timedelta(seconds=60)),
        ]
        self._subscribe_hub_sensors()
        self._apply_notify_settings()
        self._unsubs.append(self.hass.bus.async_listen("mobile_app_notification_action", self._handle_notify_action))
        self._tariff_version_id = self.price_engine.tariff_version(now.date()).id
        await self.async_rebuild_month_from_statistics()
        await self.async_run_price_update()
        await self.async_run_planner()

    def _subscribe_hub_sensors(self) -> None:
        for unsub in self._grid_unsubs:
            unsub()
        self._grid_unsubs = []
        cfg = self._hub_cfg()
        watched = [cfg.get(k) for k in (CONF_ACTIVE_POWER_SENSOR, CONF_CURRENT_L1_SENSOR, CONF_CURRENT_L2_SENSOR, CONF_CURRENT_L3_SENSOR)]
        watched = [e for e in watched if e]
        if watched:
            self._grid_unsubs.append(async_track_state_change_event(self.hass, watched, self._handle_grid_sensor_event))
        if cfg.get(CONF_NORDPOOL_SENSOR):
            self._grid_unsubs.append(
                async_track_state_change_event(self.hass, [cfg[CONF_NORDPOOL_SENSOR]], self._handle_nordpool_event)
            )

    def _apply_notify_settings(self) -> None:
        from datetime import time as _time
        cfg = self._hub_cfg()
        self.notifier.settings = NotifySettings(
            service=cfg.get(CONF_NOTIFY_SERVICE) or None,
            quiet_start=parse_hhmm(cfg.get(CONF_NOTIFY_QUIET_START), _time(23, 0)),
            quiet_end=parse_hhmm(cfg.get(CONF_NOTIFY_QUIET_END), _time(7, 0)),
        )

    async def async_hub_reconfigured(self) -> None:
        """Hub options changed (sensors, overload, notifications): apply WITHOUT reloading."""
        cfg = self._hub_cfg()
        self.limiters.oa.params = OAParams(**{
            key: float(cfg[conf]) for key, conf in OA_CONF_MAP.items() if cfg.get(conf) is not None
        })
        self._subscribe_hub_sensors()
        self._apply_notify_settings()
        self._entity_cache.clear()
        await self.async_rebuild_month_from_statistics()
        await self.async_settings_changed()

    async def async_subentry_reconfigured(self, entry: ConfigEntry) -> None:
        """Device/zone options changed: re-read the entry and replan WITHOUT reloading."""
        self.register_subentry(entry)
        self._entity_cache.clear()
        await self.async_settings_changed(reprice=False)

    async def async_edit_tariff_period(self, version_id: str, period_id: str, data: dict) -> None:
        """Edit name, price, days, hours and months of one grid-tariff period from the panel's
        Settings page (2.13.1). No reload: the period keeps its id, so its price entity stays."""
        errors = edit_period(self.price_engine.grid_tariff, version_id, period_id, data)
        if errors:
            raise ValueError(" ".join(PERIOD_EDIT_ERRORS.get(e, e) for e in errors))
        await self.async_settings_changed()

    def tariff_editor_payload(self) -> dict[str, Any]:
        """All price-list versions with their periods (for the Settings page editor)."""
        today = dt_util.now().date()
        active = self.price_engine.tariff_version(today)
        return {
            "active_id": active.id,
            "versions": [{
                "id": v.id, "name": v.name, "valid_from": v.valid_from, "prices_include_vat": v.prices_include_vat,
                "default_price": v.default_price, "energy_fee": v.energy_fee,
                "periods": [{"id": p.id, "name": p.name, "price": p.price, "days": list(p.days),
                             "start_hour": int(p.start_hour), "end_hour": int(p.end_hour), "months": list(p.months),
                             "entity": self.own_entity_id("number", f"{self.hub_entry.entry_id}_grid_period_{p.id}")}
                            for p in v.periods],
            } for v in self.price_engine.grid_tariff.versions],
        }

    async def async_apply_grid_tariff(self, schedule: GridTariffSchedule) -> None:
        """Called by the options flow: store the edited tariff and reload the hub so that
        per-period price entities and capacity-step options are rebuilt."""
        schedule.sort()
        self.price_engine.grid_tariff = schedule
        steps = self.price_engine.capacity_uppers()
        if steps and self.price_engine.selected_tariff_kw not in steps:
            self.price_engine.set_selected_tariff_kw(min(steps, key=lambda u: abs(u - self.price_engine.selected_tariff_kw)))
        await self._async_persist_state()
        self.hass.async_create_task(self.hass.config_entries.async_reload(self.hub_entry.entry_id))

    def _restore_settings(self, settings: dict[str, Any]) -> None:
        # grid tariff first: the selected capacity step is validated against its steps
        self.price_engine.grid_tariff = GridTariffSchedule.from_list(settings.get("grid_tariff"))
        for key in PRICE_SETTINGS:
            if key in settings:
                setattr(self.price_engine, key, type(getattr(self.price_engine, key))(settings[key]))
        if "price_model" in settings:
            self.price_engine.price_model = PriceModel(settings["price_model"])
        if "selected_tariff_kw" in settings:
            self.price_engine.set_selected_tariff_kw(float(settings["selected_tariff_kw"]))
        if "internal_limit_a" in settings:
            self.limiters.internal_limit_a = float(settings["internal_limit_a"])
        if "priority_profile" in settings:
            self.priority_profile = PriorityProfile(settings["priority_profile"])
        self.occupancy = OccupancyProfile.from_dict(settings.get("occupancy"))
        self.control = ControlSettings.from_dict(settings.get("control"))

    def _restore_runtime(self, rt: dict[str, Any]) -> None:
        def _dt(val: str | None) -> datetime | None:
            return dt_util.parse_datetime(val) if val else None

        self.mode = GridMode(rt.get("mode", GridMode.HOME))
        self.arrival_time = _dt(rt.get("arrival_time"))
        self.departure_time = _dt(rt.get("departure_time"))
        self.committed.update({k: bool(v) for k, v in rt.get("committed", {}).items()})
        self.priority_now.update({k: bool(v) for k, v in rt.get("priority_now", {}).items()})
        self.target_soc.update({k: float(v) for k, v in rt.get("target_soc", {}).items()})
        self.finish_by.update({k: _dt(v) for k, v in rt.get("finish_by", {}).items()})
        self.runtime_hours.update({k: float(v) for k, v in rt.get("runtime_hours", {}).items()})
        self.ev_deadline_enabled.update({k: bool(v) for k, v in rt.get("ev_deadline_enabled", {}).items()})
        self.ev_min_soc_enabled.update({k: bool(v) for k, v in rt.get("ev_min_soc_enabled", {}).items()})
        self.ev_min_soc.update({k: float(v) for k, v in rt.get("ev_min_soc", {}).items()})
        self.ev_session_start.update({k: _dt(v) for k, v in rt.get("ev_session_start", {}).items()})
        self.ev_min_since.update({k: _dt(v) for k, v in rt.get("ev_min_since", {}).items()})
        self._ev_was_connected.update({k: bool(v) for k, v in rt.get("ev_connected", {}).items()})
        sel = rt.get("occupancy_selected")
        self.occupancy_selected = sel if sel in OCC_SELECT_OPTIONS else None

    def _collect_store_data(self) -> dict[str, Any]:
        now = dt_util.now()
        pe = self.price_engine
        d = self.store.data
        d["current_month"] = now.strftime("%Y-%m")
        d["monthly_energy_kwh"] = pe.monthly_energy_kwh
        d["monthly_cost_nok"] = pe.monthly_cost_nok
        d["daily_peaks"] = pe.daily_peaks
        d["active_tariff_kw"] = pe.active_tariff_kw
        d["spot_history"] = pe.spot_history
        d["thermal_zones"] = {zid: z.to_dict() for zid, z in self.zones.items()}
        d["hotwater_models"] = {tid: m.to_dict() for tid, m in self.tanks.items()}
        d["appliance_models"] = {aid: m.to_dict() for aid, m in self.appliances.items()}
        d["priorities_plan"] = dict(self.plan_priority)
        d["priorities_dist"] = dict(self.dist_priority)
        d["settings"] = {
            **{k: getattr(pe, k) for k in PRICE_SETTINGS},
            "price_model": pe.price_model.value,
            "selected_tariff_kw": pe.selected_tariff_kw,
            "internal_limit_a": self.limiters.internal_limit_a,
            "priority_profile": self.priority_profile.value,
            "grid_tariff": pe.grid_tariff.to_list(),
            "occupancy": self.occupancy.to_dict(),
            "control": self.control.to_dict(),
        }
        iso = lambda v: v.isoformat() if v else None  # noqa: E731
        d["runtime"] = {
            "mode": self.mode.value,
            "arrival_time": iso(self.arrival_time),
            "departure_time": iso(self.departure_time),
            "committed": dict(self.committed),
            "priority_now": dict(self.priority_now),
            "target_soc": dict(self.target_soc),
            "finish_by": {k: iso(v) for k, v in self.finish_by.items()},
            "runtime_hours": dict(self.runtime_hours),
            "occupancy_selected": self.occupancy_selected,
            "ev_deadline_enabled": dict(self.ev_deadline_enabled),
            "ev_min_soc_enabled": dict(self.ev_min_soc_enabled),
            "ev_min_soc": dict(self.ev_min_soc),
            "ev_session_start": {k: iso(v) for k, v in self.ev_session_start.items()},
            "ev_min_since": {k: iso(v) for k, v in self.ev_min_since.items()},
            "ev_connected": dict(self._ev_was_connected),
        }
        d["hour"] = {"key": self._hour_key, "device_kwh": dict(self._hour_dev_kwh),
                     "tracker": self.hour_meter.to_dict()}
        d["notifier"] = self.notifier.to_dict()
        d["own_hours"] = dict(self.own_hours)
        d["shadow"] = self.shadow.to_dict()
        d["plan_history"] = self.plan_history[-168:]
        d["day_counters"] = {"day": self._day_key, "energy": self._day_energy_kwh, "runtime": self._day_runtime_h}
        return d

    @callback
    def schedule_save(self) -> None:
        self.store.schedule_save(self._collect_store_data)

    async def _async_persist_state(self) -> None:
        self._collect_store_data()
        await self.store.async_save()

    async def async_settings_changed(self, reprice: bool = True, replan: bool = True) -> None:
        """Call from entities after any user change: persist + recompute."""
        self.schedule_save()
        if reprice:
            await self.async_run_price_update()
        if replan:
            await self.async_run_planner()
        await self.async_request_refresh()

    def register_subentry(self, entry: ConfigEntry) -> None:
        data = {**entry.data, **entry.options}
        etype = data.get("entry_type")
        if etype == ENTRY_TYPE_ZONE:
            zid = data.get(CONF_ZONE_ID, entry.entry_id)
            self.zone_windows[zid] = data.get(CONF_WINDOW_SENSOR)
            self.zone_cfg[zid] = {"name": data.get(CONF_ZONE_NAME, entry.title), "area": data.get(CONF_ZONE_AREA_M2, 30.0),
                                  "insulation": data.get(CONF_INSULATION_CLASS, InsulationClass.STANDARD),
                                  "window": data.get(CONF_WINDOW_SENSOR)}
            if zid not in self.zones:
                alpha, beta = seed_thermal_parameters(
                    data.get(CONF_ZONE_AREA_M2, 30.0), data.get(CONF_INSULATION_CLASS, InsulationClass.STANDARD)
                )
                self.zones[zid] = ThermalZoneModel(zone_id=zid, alpha=alpha, beta=beta)
        elif etype == ENTRY_TYPE_DEVICE:
            eid = entry.entry_id
            self.device_entries[eid] = entry
            n = len(self.device_entries)
            self.committed.setdefault(eid, False)
            self.priority_now.setdefault(eid, False)
            self.plan_priority.setdefault(eid, int(self.store.data.get("priorities_plan", {}).get(eid, n)))
            self.dist_priority.setdefault(eid, int(self.store.data.get("priorities_dist", {}).get(eid, n)))
            self.target_soc.setdefault(eid, 80.0)
            self.finish_by.setdefault(eid, None)
            self.runtime_hours.setdefault(eid, 2.0)
            self.ev_deadline_enabled.setdefault(eid, True)
            self.ev_min_soc_enabled.setdefault(eid, True)
            self.ev_min_soc.setdefault(eid, 30.0)
            if data.get(CONF_DEVICE_TYPE) == DeviceType.APPLIANCE:
                self.committed[eid] = False          # read only – never controlled
                self._appl_roles.pop(eid, None)      # re-detect after a configuration change
                m = self.appliances.get(eid)
                if m is None:
                    m = self.appliances[eid] = ApplianceModel(device_id=eid)
                m.default_kwh = float(data.get(CONF_APPL_DEFAULT_KWH) or 1.0)
                m.default_duration_h = float(data.get(CONF_APPL_DEFAULT_H) or 2.0)

    def unregister_subentry(self, entry_id: str) -> None:
        for store in (self.device_entries, self.committed, self.priority_now, self.plan_priority,
                      self.dist_priority, self.plans_72h, self.allocations):
            store.pop(entry_id, None)

    async def async_shutdown(self) -> None:
        for unsub in self._unsubs + self._grid_unsubs:
            unsub()
        self._unsubs.clear()
        self._grid_unsubs.clear()
        await self._async_persist_state()

    # ------------------------------------------------------------------
    # Sensor helpers
    # ------------------------------------------------------------------
    def _read_float(self, entity_id: str | None, max_age_s: int | None = None) -> float | None:
        """Numeric state normalised to kW / kWh / A / °C. Returns None if missing or stale."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", "none", ""):
            return None
        if max_age_s is not None and (dt_util.utcnow() - self._last_seen(state)).total_seconds() > max_age_s:
            return None
        try:
            val = float(state.state)
        except (ValueError, TypeError):
            return None
        unit = str(state.attributes.get("unit_of_measurement", "")).strip()
        factor = {"W": 0.001, "MW": 1000.0, "Wh": 0.001, "MWh": 1000.0, "mA": 0.001}.get(unit, 1.0)
        return val * factor

    @staticmethod
    def _last_seen(state) -> datetime:
        """When the source last REPORTED a value. HA only moves ``last_updated`` when the value
        changes; ``last_reported`` (HA 2024.3+) also moves when the same value is reported again.
        Using last_updated made stable readings (e.g. an EV at a steady 1.73 kW) look stale."""
        return getattr(state, "last_reported", None) or state.last_updated

    def _state_on(self, entity_id: str | None) -> bool | None:
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        if st is None or st.state in ("unknown", "unavailable"):
            return None
        return st.state.lower() in EV_ACTIVE_STATES

    def _timeout(self) -> int:
        return int(self._hub_cfg().get(CONF_SENSOR_TIMEOUT_S, SENSOR_STALE_DEFAULT_S))

    def device_cfg(self, entry_id: str) -> dict[str, Any]:
        entry = self.device_entries[entry_id]
        return {**entry.data, **entry.options}

    def get_device_live_power_kw(self, entry_id: str) -> float:
        return self.device_power(entry_id)[0]

    def device_power(self, entry_id: str) -> tuple[float, str]:
        """(live kW, source). Sources: ``measured`` (power sensor), ``measured_current`` (EV current
        sensor × 230 V), ``offered_current`` (current offered to the EV × 230 V), ``commanded``
        (current Power Controller sent × 230 V), ``assumed`` (nominal / granted), ``off``.

        Device power sensors have NO staleness gate: a value is valid until the sensor becomes
        unavailable (many integrations only report on change). Only the grid meter is gated."""
        d = self.device_cfg(entry_id)
        live = self._read_float(d.get(CONF_POWER_SENSOR))
        if live is not None:
            return max(0.0, live), "measured"
        if d.get(CONF_DEVICE_TYPE) == DeviceType.APPLIANCE:
            s_ = self.appliance_status(entry_id)
            if s_["state_class"] == "running" and s_["expected_duration_h"]:
                return round(float(s_["expected_kwh"] or 0) / float(s_["expected_duration_h"]), 3), "assumed"
            return 0.0, "off"
        if d.get(CONF_DEVICE_TYPE) == DeviceType.EV_CHARGER:
            charging = None
            raw, _ = self.ev_status(d)
            if raw is not None:
                info = EV_STATUS_TEXT.get(raw.replace(" ", "").replace("_", "").lower())
                charging = info[1] if info else None
            if charging is None:
                sw = self.hass.states.get(d.get(CONF_EV_CHARGE_SWITCH)) if d.get(CONF_EV_CHARGE_SWITCH) else None
                charging = bool(sw and sw.state == "on")
            if not charging:
                return 0.0, "off"
            for key, src in ((CONF_EV_CURRENT_SENSOR, "measured_current"), (CONF_EV_OFFERED_SENSOR, "offered_current")):
                a = self._read_float(d.get(key))
                if a is not None:
                    return round(max(0.0, a) * 230.0 / 1000.0, 3), src
            return round(float(self._last_ev_target_a.get(entry_id, 0)) * 230.0 / 1000.0, 3), "commanded"
        alloc = self.allocations.get(entry_id)
        if alloc and alloc.allowed_kw > 0 and alloc.status != DistStatus.REDISTRIBUTING:
            if d.get(CONF_HEATER_KIND) == "stepless":
                return float(alloc.allowed_kw), "assumed"
            return float((d.get(CONF_NOMINAL_POWER_KW) or 0.0)), "assumed"
        return 0.0, "off"

    async def _safe_call(self, domain: str, service: str, data: dict[str, Any], blocking: bool = False) -> bool:
        """Service call that never takes the control loop down. ``blocking`` waits for the
        result so a rejected command (e.g. OCPP) is detected."""
        if not self.hass.services.has_service(domain, service):
            _LOGGER.warning("Service %s.%s not available (integration not loaded?)", domain, service)
            return False
        try:
            await self.hass.services.async_call(domain, service, data, blocking=blocking)
            return True
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Service %s.%s failed: %s", domain, service, err)
            return False

    async def _call_with_response(self, domain: str, service: str, data: dict[str, Any]) -> dict | None:
        if not self.hass.services.has_service(domain, service):
            return None
        try:
            return await self.hass.services.async_call(domain, service, data, blocking=True, return_response=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("%s.%s with response failed: %s", domain, service, err)
            return None

    # ------------------------------------------------------------------
    # Schedules & weather (per hour, not "current state copied 72 times")
    # ------------------------------------------------------------------
    async def _async_schedule_masks(self, entity_ids: set[str], start: datetime) -> dict[str, list[bool]]:
        masks: dict[str, list[bool]] = {}
        schedule_ids = sorted(e for e in entity_ids if e.startswith("schedule."))
        weekly: dict[str, Any] = {}
        if schedule_ids:
            resp = await self._call_with_response("schedule", "get_schedule", {"entity_id": schedule_ids})
            weekly = resp or {}
        for ent in entity_ids:
            if ent in weekly and isinstance(weekly[ent], dict):
                masks[ent] = self._mask_from_weekly(weekly[ent], start)
            else:
                masks[ent] = self._mask_from_state(ent, start)
        return masks

    @staticmethod
    def _mask_from_weekly(week: dict[str, list[dict]], start: datetime) -> list[bool]:
        def _mins(s: str) -> int:
            parts = [int(p) for p in str(s).split(":")[:2]]
            return parts[0] * 60 + parts[1]

        mask = []
        for t in range(PLANNING_HORIZON_HOURS):
            ts = start + timedelta(hours=t)
            mid = ts.hour * 60 + 30
            blocks = week.get(WEEKDAYS[ts.weekday()], []) or []
            on = False
            for blk in blocks:
                frm, to = _mins(blk.get("from", "0:0")), _mins(blk.get("to", "0:0"))
                to = 24 * 60 if to == 0 else to
                if frm <= mid < to:
                    on = True
                    break
            mask.append(on)
        return mask

    def _mask_from_state(self, entity_id: str, start: datetime) -> list[bool]:
        """Fallback: current state until ``next_event``, opposite state after it."""
        st = self.hass.states.get(entity_id)
        if st is None:
            return [True] * PLANNING_HORIZON_HOURS
        cur = st.state == "on"
        nxt = st.attributes.get("next_event")
        nxt_dt = dt_util.parse_datetime(nxt) if isinstance(nxt, str) else nxt
        mask = []
        for t in range(PLANNING_HORIZON_HOURS):
            ts = start + timedelta(hours=t, minutes=30)
            mask.append(cur if (nxt_dt is None or ts < nxt_dt) else (not cur))
        return mask

    async def _async_outdoor_forecast(self, start: datetime) -> list[float]:
        cfg = self._hub_cfg()
        now_t = self._read_float(cfg.get(CONF_OUTDOOR_TEMP_SENSOR))
        series = [now_t if now_t is not None else 5.0] * PLANNING_HORIZON_HOURS
        weather = cfg.get(CONF_WEATHER_ENTITY)
        if not weather:
            return series
        resp = await self._call_with_response("weather", "get_forecasts", {"entity_id": weather, "type": "hourly"})
        fc = ((resp or {}).get(weather) or {}).get("forecast") or []
        points: dict[int, float] = {}
        for item in fc:
            ts = dt_util.parse_datetime(str(item.get("datetime", "")))
            if ts is None or item.get("temperature") is None:
                continue
            idx = int(round((dt_util.as_local(ts) - start).total_seconds() / 3600.0))
            if 0 <= idx < PLANNING_HORIZON_HOURS:
                points[idx] = float(item["temperature"])
        last = series[0]
        for t in range(PLANNING_HORIZON_HOURS):
            if t in points:
                last = points[t]
            series[t] = last if t > 0 or now_t is None else now_t
        return series

    def is_workday(self, day) -> bool:
        """Norwegian calendar, with the workday sensor overriding *today*."""
        if day == dt_util.now().date():
            st = self.hass.states.get(self._hub_cfg().get(CONF_WORKDAY_SENSOR) or "binary_sensor.workday_sensor")
            if st and st.state in ("on", "off"):
                return st.state == "on"
        return is_norwegian_workday(day)

    def _update_occupancy(self, start: datetime) -> None:
        self.occupancy.prune(start.date())
        if self.occupancy_selected is None:   # first start: begin in the schedule's state
            self.occupancy_selected = self.occupancy.state_at(dt_util.now(), self.is_workday)
        states = self.occupancy.hour_states(start, self.is_workday, PLANNING_HORIZON_HOURS)
        # Mode Away (out of town) ignores the selector; otherwise it decides the current hour(s)
        if self.mode != GridMode.AWAY:
            states = apply_selector(states, self.occupancy_selected)
        self.occ_hour_states = states
        self.occ_start = start

    def occupancy_scheduled_now(self) -> str:
        return self.occupancy.state_at(dt_util.now(), self.is_workday)

    def occupancy_state_now(self) -> str:
        """Effective occupancy now: selector combined with the schedule (see occupancy.effective_state)."""
        if self.mode == GridMode.AWAY:
            return "out_of_town"
        return effective_state(self.occupancy_selected, self.occupancy_scheduled_now())

    async def async_set_occupancy(self, state: str) -> None:
        if state not in OCC_SELECT_OPTIONS:
            raise ValueError(state)
        self.occupancy_selected = state
        await self.async_settings_changed()

    async def async_set_home_office(self, day, enabled: bool) -> None:
        """Quick override: e.g. 'tomorrow I work from home'. Replans immediately."""
        self.occupancy.set_home_office(day, enabled)
        await self.async_settings_changed()

    async def async_set_home_office_weekdays(self, weekdays: list[str]) -> None:
        self.occupancy.home_office_weekdays = list(weekdays)
        await self.async_settings_changed()

    def _workday_mask(self, start: datetime) -> list[bool]:
        st = self.hass.states.get(self._hub_cfg().get(CONF_WORKDAY_SENSOR) or "binary_sensor.workday_sensor")
        override = (st.state == "on") if st and st.state in ("on", "off") else None
        return build_workday_mask(start, PLANNING_HORIZON_HOURS, override)

    # ------------------------------------------------------------------
    # Loop 1: prices & billing
    # ------------------------------------------------------------------
    @callback
    def _handle_daily_price_update(self, _now: datetime) -> None:
        self.hass.async_create_task(self._async_reprice_and_replan())

    @callback
    def _handle_nordpool_event(self, event: Event) -> None:
        new = event.data.get("new_state")
        if new is None:
            return
        tomorrow = new.attributes.get("raw_tomorrow") or new.attributes.get("tomorrow") or []
        if len(tomorrow) != self._last_tomorrow_len:
            self._last_tomorrow_len = len(tomorrow)
            self.hass.async_create_task(self._async_reprice_and_replan())

    async def _async_reprice_and_replan(self) -> None:
        await self.async_run_price_update()
        await self.async_run_planner()

    @callback
    def _handle_hour_tick(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _handle_month_reconcile(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_rebuild_month_from_statistics())

    def _hour_price(self, ts: datetime, month_kwh_before: float) -> float:
        """All-in price incl. VAT for a past or current hour (spot from the stored history)."""
        pe = self.price_engine
        spot = pe._history_price(ts)
        if spot is None and self.price_series and self.price_series.timestamps:
            i = int((ts - self.price_series.timestamps[0]).total_seconds() // 3600)
            if 0 <= i < len(self.price_series.raw_spot_72h):
                spot = self.price_series.raw_spot_72h[i]
        if spot is None:
            spot = pe.norgespris_nok
        energy = pe.energy_price_after_support(float(spot), float(month_kwh_before))
        grid, fee, _ = pe.grid_price_at(ts, self.is_workday(ts.date()))
        return (energy + pe.supplier_markup_nok + grid + fee) * (1 + pe.vat_percent / 100.0)

    async def _async_meter_hour_stats(self, start: datetime, end: datetime) -> list[tuple[datetime, float]] | None:
        """Hourly energy (kWh) of the total import meter from the recorder's statistics."""
        meter = self._hub_cfg().get(CONF_MONTHLY_ENERGY_SENSOR)
        if not meter:
            return None
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import statistics_during_period
        except ImportError:
            return None
        try:
            res = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period, self.hass, dt_util.as_utc(start), dt_util.as_utc(end),
                {meter}, "hour", None, {"change"})
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not read statistics for %s", meter, exc_info=True)
            return None
        rows = (res or {}).get(meter) or []
        st = self.hass.states.get(meter)
        unit = str(st.attributes.get("unit_of_measurement", "")).strip() if st else ""
        factor = {"Wh": 0.001, "MWh": 1000.0}.get(unit, 1.0)
        out = []
        for r in rows:
            ts = r.get("start")
            if isinstance(ts, (int, float)):
                ts = dt_util.utc_from_timestamp(ts)
            elif isinstance(ts, str):
                ts = dt_util.parse_datetime(ts)
            if ts is None or r.get("change") is None:
                continue
            out.append((dt_util.as_local(ts), max(0.0, float(r["change"]) * factor)))
        return out

    async def async_rebuild_month_from_statistics(self) -> None:
        """Month to date, daily peaks, top-3 and Norgespris from the meter's recorder statistics.
        Runs at start-up and a few minutes after every hour, so restarts and missed hours heal."""
        now = dt_util.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        hours = await self._async_meter_hour_stats(month_start, now)
        trusted = {}
        for k, v in self.own_hours.items():
            ts = dt_util.parse_datetime(k)
            if ts is not None:
                trusted[dt_util.as_local(ts)] = float(v)
        if not hours and not trusted:
            self.month_source = {"source": "live", "reason": "no recorder statistics for the meter"}
            return
        before = self.price_engine.active_tariff_kw
        summary = rebuild_month(self.price_engine, hours or [], self._hour_price, now,
                                max_hour_kwh=max_hour_kwh_for_fuse(self.limiters.internal_limit_a),
                                trusted=trusted)
        if summary.get("corrected_hours"):
            _LOGGER.warning("Meter statistics: %s implausible hour(s) corrected (max %.1f kWh/h): %s",
                            summary["corrected_hours"], summary["max_hour_kwh"],
                            {k: summary[k] for k in ("replaced_by_measured", "spread_from", "excluded_from_peaks")})
        if self.price_engine.active_tariff_kw > before and self._stepped_up_at is None:
            self._stepped_up_at = now
        self.month_source = {"source": "statistics", **summary, "rebuilt": now.isoformat()}
        self.schedule_save()
        self.async_set_updated_data(self._build_state_snapshot())

    @callback
    def _handle_price_check(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_request_refresh())

    # ------------------------------------------------------------------ notifications
    def _tomorrow_prices_missing(self, now: datetime) -> bool:
        if now.hour < 15:
            return False
        st = self.hass.states.get(self._hub_cfg().get(CONF_NORDPOOL_SENSOR) or "")
        if st is None:
            return True
        tom = st.attributes.get("raw_tomorrow") or st.attributes.get("tomorrow") or []
        return len([x for x in tom if x is not None]) < 23

    def _notification_conditions(self, now: datetime) -> dict[str, dict]:
        """Every error / early warning that is true right now (see notifier.EVENTS)."""
        c: dict[str, dict] = {}
        name = lambda eid: self.device_entries[eid].title if eid in self.device_entries else eid  # noqa: E731
        if self.safe_mode_active:
            c["safe_mode"] = {"message": ("The grid power sensor has not updated. " if not self.safe_mode_sensor_ok else
                                          "The grid power sensor is back, but safe mode is held to the end of the hour. ")
                              + "Power Controller limits the house to the safe-mode budget. Check the meter/HAN reader."}
        for eid, ts in list(self._ocpp_rejected.items()):
            if (now - ts).total_seconds() < 1800:
                c[f"ocpp_rejected:{eid}"] = {"message": f"{name(eid)}: ocpp.set_charge_rate was not accepted. "
                                             "Charging may not follow the plan – check the OCPP integration/log."}
        if self.plan_result.solver == "fallback" or self._plan_failed:
            c["plan_fallback"] = {"message": "The optimiser failed; a simple fallback plan is used. See the log."}
        for eid in self.device_entries:
            d = self.device_cfg(eid)
            dtype = d.get(CONF_DEVICE_TYPE)
            if not self.committed.get(eid):
                continue
            if dtype in (DeviceType.CLIMATE_HEATING, DeviceType.CLIMATE_HOTWATER):
                cmd = self.commanded_setpoint.get(eid)
                st = self.hass.states.get(d.get(CONF_TARGET_ENTITY) or "")
                cur = st.attributes.get("temperature") if st else None
                try:
                    bad = cmd is not None and cur is not None and abs(float(cur) - float(cmd)) > 0.6
                except (TypeError, ValueError):
                    bad = False
                if bad:
                    since = self._setpoint_mismatch_since.setdefault(eid, now)
                    if (now - since).total_seconds() >= 900:
                        c[f"setpoint_ignored:{eid}"] = {"message": f"{name(eid)}: set to {float(cmd):.1f} °C but the device "
                                                        f"reports {float(cur):.1f} °C for 15 min. Is it controlled elsewhere?"}
                else:
                    self._setpoint_mismatch_since.pop(eid, None)
            if dtype == DeviceType.EV_CHARGER and self.ev_plugged_in(d):
                unmet = float(self.plan_result.unmet.get(eid) or 0.0)
                min_unmet = float(self.plan_result.ev_min_unmet.get(eid) or 0.0)
                if unmet > 0.5 or min_unmet > 0.5:
                    what = f"minimum SoC ({min_unmet:.1f} kWh short)" if min_unmet > 0.5 else f"target ({unmet:.1f} kWh short)"
                    c[f"ev_unmet:{eid}"] = {"message": f"{name(eid)} will not reach its {what} within the capacity step.",
                                            "actions": [{"action": f"{ACTION_PRIORITY_NOW}|{eid}", "title": "Priority now"}]}
        if self._tomorrow_prices_missing(now):
            c["prices_missing"] = {"message": "Nord Pool prices for tomorrow are not available yet. The plan uses a forecast."}
        minutes = now.minute + now.second / 60.0
        committed_live = sum(self.get_device_live_power_kw(e) for e in self.device_entries if self.committed.get(e))
        rest = self.energy_this_hour_kwh + max(0.0, self.grid_power_kw - committed_live) * (60.0 - minutes) / 60.0
        if minutes >= 15 and rest > self.hour_allowance_kwh + 0.05:
            c["hour_over"] = {"message": f"Even with all controlled devices off this hour ends at ≈ {rest:.2f} kWh "
                              f"(allowed {self.hour_allowance_kwh:.2f}). Turn off something that is not controlled."}
        if self._stepped_up_at and (now - self._stepped_up_at).total_seconds() < 3600:
            c[f"step_up:{now:%Y-%m}"] = {"message": f"The top-3 average is {self.price_engine.monthly_max_kw:.2f} kWh/h; "
                                         f"the {self.price_engine.active_tariff_kw:g} kW step applies for the rest of the month."}
        if self.limiters.oa.any_limiting():
            c["phase_limiting"] = {"message": "A phase has been over its rated current too long; power is being reduced."}
        if self.price_engine.price_model == PriceModel.NORGESPRIS:
            np_st = self.price_engine.norgespris_status(now, self.energy_this_hour_kwh)
            if np_st.get("projected_reach_date"):
                c[f"norgespris_volume:{now:%Y-%m}"] = {"message": f"At the current use the Norgespris volume runs out on "
                                                        f"{np_st['projected_reach_date']} (≈ {np_st['remaining_kwh']:.0f} kWh left). "
                                                        "Above it energy is billed at spot price."}
        return c

    async def _async_run_notifications(self, now: datetime) -> None:
        try:
            msgs = self.notifier.evaluate(self._notification_conditions(now), now)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Notification evaluation failed")
            return
        for msg in msgs:
            _LOGGER.info("Power Controller %s: %s – %s", msg["level"], msg["title"], msg["message"])
            call = self.notifier.service_call(msg)
            if call:
                await self._safe_call(*call)
        if msgs:
            self.schedule_save()

    @callback
    def _handle_notify_action(self, event: Event) -> None:
        action = str(event.data.get("action") or "")
        if not action.startswith("PC_"):
            return
        kind, _, arg = action.partition("|")
        now = dt_util.now()
        if kind == ACTION_IGNORE and arg:
            self.notifier.snooze_today(arg, now)
            self.schedule_save()
        elif kind == ACTION_PRIORITY_NOW and arg in self.device_entries:
            self.priority_now[arg] = True
            self.hass.async_create_task(self.async_settings_changed(reprice=False))

    async def async_test_notification(self) -> None:
        msg = {"key": "test", "level": "warning", "title": "Power Controller: test",
               "message": "Notifications from Power Controller reach this device.",
               "actions": [{"action": f"{ACTION_IGNORE}|test", "title": "Ignore today"}], "ts": dt_util.now().isoformat()}
        call = self.notifier.service_call(msg)
        if call is None:
            raise ValueError("No notify service configured (Configure → Sensors and general settings)")
        await self._safe_call(*call)

    async def _async_process_hourly_rollover(self, now: datetime, closed: ClosedHour) -> None:
        """Book the hour that just ended (called from the fast loop in the step that saw the
        new hour, with the energy of the ended hour – before anything is reset)."""
        self.warning_active = False
        prev_hour = closed.hour_start
        price_prev = self._hour_price(prev_hour, self.price_engine.monthly_energy_kwh)
        if prev_hour.month != now.month:
            self.price_engine.reset_monthly_counters()
            stepped_up = False
        else:
            # only full hours measured by Power Controller are trusted (not the start-up estimate)
            if closed.full:
                self.own_hours[prev_hour.isoformat()] = round(closed.kwh, 4)
                self.own_hours = {k: v for k, v in self.own_hours.items() if k[:7] == prev_hour.isoformat()[:7]}
            plausible = closed.kwh <= max_hour_kwh_for_fuse(self.limiters.internal_limit_a)
            stepped_up = self.price_engine.record_hourly_consumption(prev_hour, closed.kwh, price_prev,
                                                                     count_peak=plausible)
        if stepped_up:
            self._stepped_up_at = now
        if now.strftime("%Y-%m-%d") != self._day_key:
            self._day_key = now.strftime("%Y-%m-%d")
            self._day_energy_kwh.clear()
            self._day_runtime_h.clear()

        # A new tariff version took effect (e.g. 1 January): reload so the per-period
        # price entities and capacity-step options follow the new price list.
        active = self.price_engine.tariff_version(now.date()).id
        if self._tariff_version_id is not None and active != self._tariff_version_id:
            await self._async_persist_state()
            self.hass.async_create_task(self.hass.config_entries.async_reload(self.hub_entry.entry_id))
            return
        self._tariff_version_id = active

        if stepped_up:
            await self._safe_call("persistent_notification", "create", {
                "title": "Power Controller: capacity step increased",
                "message": (
                    f"The average of the three highest daily peaks is {self.price_engine.monthly_max_kw:.2f} kWh/h. "
                    f"The active step is now {self.price_engine.active_tariff_kw:.0f} kW for the rest of the month."
                ),
                "notification_id": "power_controller_tariff_stepup",
            })
        await self.async_run_price_update()
        if stepped_up:
            await self.async_run_planner()
        await self._async_persist_state()

    def _read_spot_prices(self, now: datetime) -> list[float]:
        np_entity = self._hub_cfg().get(CONF_NORDPOOL_SENSOR)
        st = self.hass.states.get(np_entity) if np_entity else None
        if st is None:
            return []

        def _vals(items: list[Any]) -> list[float]:
            out: list[float] = []
            for it in items or []:
                if isinstance(it, dict) and it.get("value") is not None:
                    out.append(float(it["value"]))
                elif isinstance(it, (int, float)):
                    out.append(float(it))
            return out

        today = _vals(st.attributes.get("raw_today") or st.attributes.get("today"))
        tomorrow = _vals(st.attributes.get("raw_tomorrow") or st.attributes.get("tomorrow"))
        scale = 0.01 if ("øre" in str(st.attributes.get("unit_of_measurement", "")).lower()
                         or "ore" in str(st.attributes.get("unit_of_measurement", "")).lower()
                         or (today and max(today) > 30)) else 1.0

        def _quarters(day: list[float]) -> list[float | None]:
            if len(day) >= 90:              # 15-minute prices (92/96/100 on DST days)
                return [v * scale for v in day[:96]] + [None] * max(0, 96 - len(day))
            return [None] * 96              # hourly only -> the planner uses the hourly price
        q = _quarters(today) + (_quarters(tomorrow) if tomorrow else [])
        self._spot_quarters = q[now.hour * 4:]
        if len(today) >= 90:
            today = self.price_engine.average_quarter_hourly_prices(today)
        if len(tomorrow) >= 90:
            tomorrow = self.price_engine.average_quarter_hourly_prices(tomorrow)
        unit = str(st.attributes.get("unit_of_measurement", "")).lower()
        if "øre" in unit or "ore" in unit or (today and max(today) > 30):
            today = [v / 100.0 for v in today]
            tomorrow = [v / 100.0 for v in tomorrow]
        if len(today) >= 24:
            self.price_engine.update_spot_history(now.date(), today[:24])
        if len(tomorrow) >= 24:
            self.price_engine.update_spot_history((now + timedelta(days=1)).date(), tomorrow[:24])
        return today[now.hour:] + tomorrow

    async def async_run_price_update(self) -> None:
        now = dt_util.now().replace(minute=0, second=0, microsecond=0)
        known = self._read_spot_prices(now)
        workday_mask = self._workday_mask(now)
        self._update_occupancy(now)
        self.baseload_72h = self.price_engine.build_baseload_series_72h(
            now, workday_mask, away_mask_72h=self.planner._away_mask(
                now, PLANNING_HORIZON_HOURS, self.mode, self.arrival_time, self.departure_time
            ),
            occupied_mask_72h=OccupancyProfile.occupied_mask(self.occ_hour_states),
        )
        expected = [
            self.baseload_72h[t] + (self.total_plan_72h[t] if t < len(self.total_plan_72h) else 0.0)
            for t in range(PLANNING_HORIZON_HOURS)
        ]
        self.price_series = self.price_engine.build_72h_price_series(
            start_time=now,
            spot_prices_72h=known,
            workday_mask_72h=workday_mask,
            monthly_kwh_used=self.price_engine.monthly_energy_kwh + self.energy_this_hour_kwh,
            expected_load_kwh_72h=expected,
            spot_quarters=self._spot_quarters,
        )
        self.async_set_updated_data(self._build_state_snapshot())

    # ------------------------------------------------------------------
    # Loop 2: planner
    # ------------------------------------------------------------------
    @callback
    def _handle_scheduled_planning(self, _now: datetime) -> None:
        self.hass.async_create_task(self._async_reprice_and_replan())

    async def async_run_planner(self) -> None:
        if self._planner_running:
            return
        self._planner_running = True
        try:
            await self._async_run_planner_inner()
            self._plan_failed = False
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Planner run failed; keeping previous plan")
            self._plan_failed = True
        finally:
            self._planner_running = False

    async def _async_run_planner_inner(self) -> None:
        if self.price_series is None:
            await self.async_run_price_update()
        assert self.price_series is not None
        now = dt_util.now().replace(minute=0, second=0, microsecond=0)
        if self.occ_start != now:
            self._update_occupancy(now)
        outdoor = await self._async_outdoor_forecast(now)
        sched_ids = {
            self.device_cfg(eid).get(CONF_SCHEDULE_ENTITY)
            for eid in self.device_entries
            if self.device_cfg(eid).get(CONF_SCHEDULE_ENTITY)
        }
        masks = await self._async_schedule_masks(sched_ids, now)

        specs = [self._device_spec(eid, masks) for eid in self.device_entries]
        caps = self.price_engine.hourly_cap_series_72h(now)
        prices = self.price_series.total_price_15m or self.price_series.total_price_72h
        result_plans = await self.hass.async_add_executor_job(
            lambda: self.planner.optimize_72h(
                now, self.mode, prices, outdoor, self.baseload_72h,
                self.price_engine.active_tariff_kw,
                min(self.limiters.effective_phase_limits_a().values()),
                specs, self.arrival_time, self.departure_time, caps, self.priority_profile,
                ev_delay_penalty=self.control.ev_delay_penalty_nok,
            )
        )
        self._apply_plan_result(self.planner.last_result, result_plans)
        self.plan_start = now
        self.last_plan_ts = dt_util.now()
        self.total_plan_72h = [
            round(self.baseload_72h[t] + sum(float(p[t]) for p in self.plans_72h.values() if t < len(p) and p[t] is not None), 3)
            for t in range(PLANNING_HORIZON_HOURS)
        ]
        _LOGGER.debug("Plan solved by %s in %.0f ms", self.plan_result.solver, self.plan_result.solve_ms)
        await self._async_run_shadow_planner(now, outdoor, specs)
        self.async_set_updated_data(self._build_state_snapshot())

    def _apply_plan_result(self, raw: PlanResult, plans: dict[str, list[float | None]]) -> None:
        """Keep the 15-minute plan for real-time control and an hourly view for the UI/history."""
        spp = raw.slots_per_hour or 1
        self.plan_spp = spp
        self.plan_slots = raw
        self.plans_15m = plans
        view = PlanResult(
            plans={k: aggregate_hourly(v, spp) for k, v in plans.items()},
            setpoints={k: aggregate_hourly(v, spp, "max") for k, v in raw.setpoints.items()},
            predicted_temps={k: aggregate_hourly(v, spp, "last") for k, v in raw.predicted_temps.items()},
            legionella_hours=dict(raw.legionella_hours),
            legionella_slots=dict(raw.legionella_slots),
            solver=raw.solver, solve_ms=raw.solve_ms, unmet=dict(raw.unmet),
            forecast_only=list(raw.forecast_only),
            uncontrolled_72h=aggregate_hourly(raw.uncontrolled_72h, spp) or [],
            slots_per_hour=1, ev_min_unmet=dict(raw.ev_min_unmet),
        )
        self.plan_result = view
        self.plans_72h = view.plans

    def slot_idx(self, now: datetime | None = None) -> int:
        """Index of the current slot in the 15-minute plan."""
        now = now or dt_util.now()
        if self.plan_start is None:
            return 0
        return max(0, int((now - self.plan_start).total_seconds() // (3600 / max(1, self.plan_spp))))

    def _device_spec(self, eid: str, masks: dict[str, list[bool]]) -> dict[str, Any]:
        d = self.device_cfg(eid)
        dtype = DeviceType(d[CONF_DEVICE_TYPE])
        sched = d.get(CONF_SCHEDULE_ENTITY)
        if sched and sched in masks:
            mask = masks[sched]                      # explicit schedule helper wins
        elif dtype in (DeviceType.CLIMATE_HEATING, DeviceType.CLIMATE_HOTWATER) and self.occ_hour_states:
            during = d.get(CONF_COMFORT_DURING) if dtype == DeviceType.CLIMATE_HEATING else None
            if dtype == DeviceType.CLIMATE_HOTWATER:
                # hot water at nominal temperature while people are home and awake
                mask = OccupancyProfile.occupied_mask(self.occ_hour_states)
            else:
                mask = OccupancyProfile.comfort_mask(self.occ_hour_states, during)
        else:
            mask = [True] * PLANNING_HORIZON_HOURS
        spec: dict[str, Any] = {
            "entry_id": eid,
            "device_type": dtype,
            "phase_pair": d.get(CONF_PHASE_PAIR, PhasePair.L1_L2),
            "nominal_power_kw": self.device_nominal_kw(d),
            "plan_priority": self.plan_priority.get(eid, 5),
            "committed": self.committed.get(eid, False),
            "priority_now": self.priority_now.get(eid, False),
        }
        if dtype == DeviceType.CLIMATE_HEATING:
            zid = d.get(CONF_ZONE_ID) or "default_zone"
            zone = self.zones.get(zid) or next(iter(self.zones.values()))
            comfort = self._read_float(d.get(CONF_COMFORT_TEMP_ENTITY)) or 22.0
            eco = self._read_float(d.get(CONF_ECO_TEMP_ENTITY)) or 18.0
            spec.update(
                zone_id=zid, alpha=zone.alpha, beta=zone.beta,
                heating_coefficient=float(d.get(CONF_HEATING_COEFFICIENT, 1.0)),
                current_temp=self._read_float(d.get(CONF_ROOM_TEMP_SENSOR)) or 20.0,
                oot_temp=self._read_float(d.get(CONF_OOT_TEMP_ENTITY)) or 10.0,
                target_temps_72h=[comfort if m else eco for m in mask],
                eco_temp=eco,
                heater_kind=d.get(CONF_HEATER_KIND, HEATER_ON_OFF),
                # used when NOT committed: the device's own setpoint decides its consumption
                hold_setpoint=self._climate_setpoint(d.get(CONF_TARGET_ENTITY)),
            )
        elif dtype == DeviceType.CLIMATE_HOTWATER:
            nom_t = self._read_float(d.get(CONF_NOMINAL_TEMP_ENTITY)) or 65.0
            min_t = self._read_float(d.get(CONF_MIN_TEMP_ENTITY)) or 50.0
            leg_iso = self.store.data.get("legionella_last_ok", {}).get(eid)
            spec.update(
                current_temp=self._read_float(d.get(CONF_WATER_TEMP_SENSOR)),  # None -> quota model
                min_temps_72h=[nom_t if m else min_t for m in mask],
                nominal_temp=nom_t, min_temp=min_t,
                oot_temp=self._read_float(d.get(CONF_OOT_TEMP_ENTITY)) or 15.0,
                tank_volume_l=float(d.get(CONF_TANK_VOLUME_L, 200.0)),
                daily_quota_hours=float(d.get(CONF_DAILY_QUOTA_HOURS, 3.0)),
                energy_done_today_kwh=self._day_energy_kwh.get(eid, 0.0),
                last_legionella_ts=dt_util.parse_datetime(leg_iso) if leg_iso else None,
            )
            tank = self.tanks.get(eid)
            if tank is not None and (tank.samples > 0 or tank.manual):
                spec.update(tank_loss_fraction=tank.loss_fraction_per_h,
                            draw_kw_72h=tank.draw_kw_series(dt_util.now(), PLANNING_HORIZON_HOURS, self.is_workday))
        elif dtype == DeviceType.EV_CHARGER:
            avail = self._state_on(d.get(CONF_EV_AVAILABILITY_SWITCH))
            connected = self.ev_plugged_in(d) and avail is not False
            soc = self._read_float(d.get(CONF_EV_SOC_SENSOR))
            cap_kwh = float((d.get(CONF_EV_BATTERY_KWH) or 75.0))
            tgt = self.ev_target_soc(eid)
            need = max(0.0, (tgt - (soc if soc is not None else 50.0)) / 100.0 * cap_kwh)
            min_soc = self.ev_min_soc.get(eid, 30.0)
            min_need = max(0.0, (min_soc - (soc if soc is not None else 50.0)) / 100.0 * cap_kwh)
            min_dl = ev_min_soc_deadline(self.ev_session_start.get(eid), self.ev_min_since.get(eid)) \
                if self.ev_min_soc_enabled.get(eid, True) and connected else None
            spec.update(
                connected=connected, energy_needed_kwh=need,
                live_power_kw=self.get_device_live_power_kw(eid),
                # a session only counts as charging when power actually flows
                charging=connected and self.ev_is_charging(eid, d),
                min_power_kw=float((d.get(CONF_EV_MIN_CURRENT_A) or 6)) * 230.0 / 1000.0,
                # "no deadline": charge as cheaply as possible within the horizon, sooner is better
                finish_by=self.finish_by.get(eid) if self.ev_deadline_enabled.get(eid, True) else None,
                min_energy_kwh=min_need if min_dl else 0.0,
                min_deadline=min_dl,
            )
        elif dtype == DeviceType.APPLIANCE:
            spec["committed"] = False
            spec["fixed_load_15m"] = self.appliance_expected_15m(eid)
        elif dtype == DeviceType.SWITCH:
            spec.update(
                runtime_hours=self.runtime_hours.get(eid, 2.0),
                runtime_done_today_h=self._day_runtime_h.get(eid, 0.0),
                schedule_mask_72h=mask,
            )
        return spec

    # ------------------------------------------------------------------
    # Shadow evaluation (Norgespris vs. spot) – never actuates
    # ------------------------------------------------------------------
    async def _async_run_shadow_planner(self, now: datetime, outdoor: list[float], specs: list[dict]) -> None:
        if not self.shadow.enabled or self.price_series is None:
            return
        try:
            eng = self.shadow.shadow_engine(self.price_engine)
            series = eng.build_72h_price_series(
                start_time=now,
                spot_prices_72h=self.price_series.raw_spot_72h,
                workday_mask_72h=self._workday_mask(now),
                monthly_kwh_used=eng.monthly_energy_kwh,
                expected_load_kwh_72h=self.total_plan_72h,
                spot_quarters=self._spot_quarters,
            )
            shadow_specs = self.shadow.shadow_specs(specs)
            plans = await self.hass.async_add_executor_job(
                lambda: self.shadow_planner.optimize_72h(
                    now, self.mode, series.total_price_15m or series.total_price_72h, outdoor, self.baseload_72h,
                    eng.active_tariff_kw, min(self.limiters.effective_phase_limits_a().values()),
                    shadow_specs, self.arrival_time, self.departure_time,
                    eng.hourly_cap_series_72h(now), self.priority_profile,
                    ev_delay_penalty=self.control.ev_delay_penalty_nok,
                )
            )
            spp = self.shadow_planner.last_result.slots_per_hour or 1
            # the shadow ledgers book whole hours -> hourly kWh
            self.shadow_plans = {k: aggregate_hourly(v, spp) for k, v in plans.items()}
            self.shadow_plan_start = now
        except Exception:  # noqa: BLE001 – the shadow must never disturb the real controller
            _LOGGER.exception("Shadow planner failed")

    def _close_shadow_hour(self, hour_ts: datetime) -> None:
        """Book the finished hour in the shadow ledgers (actual / passive / optimised)."""
        if not self.shadow.enabled or self.price_series is None or not self.price_series.timestamps:
            return
        try:
            hour_ts = hour_ts.replace(minute=0, second=0, microsecond=0)
            ps = self.price_series
            idx = int(round((hour_ts - ps.timestamps[0]).total_seconds() / 3600.0))
            if not 0 <= idx < len(ps.raw_spot_72h):
                return
            sidx = None
            if self.shadow_plan_start is not None:
                sidx = int(round((hour_ts - self.shadow_plan_start).total_seconds() / 3600.0))
            shadow_hour: dict[str, float | None] = {}
            for eid, plan in self.shadow_plans.items():
                shadow_hour[eid] = plan[sidx] if (sidx is not None and 0 <= sidx < len(plan)) else None
            specs = [self._device_spec(eid, {}) for eid in self.device_entries]
            self.shadow.close_hour(
                hour_ts=hour_ts,
                real_engine=self.price_engine,
                spot_nok=ps.raw_spot_72h[idx],
                spot_is_forecast=ps.spot_is_forecast_72h[idx],
                is_workday=self._workday_mask(hour_ts)[0],
                measured_kwh=self.energy_this_hour_kwh,
                actual_device_kwh=dict(self._hour_dev_kwh),
                shadow_device_kwh=shadow_hour,
                specs=specs,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Shadow bookkeeping failed")

    def _record_plan_history(self, hour_ts: datetime) -> None:
        """Plan vs. actual for the finished hour (kept 7 days) – the plan that was valid for it."""
        try:
            hour_ts = hour_ts.replace(minute=0, second=0, microsecond=0)
            idx = plan_index(self.plan_start, hour_ts) if self.plan_start else None
            planned = {eid: value_at(p, idx) for eid, p in self.plans_72h.items()} if idx is not None else {}
            self.plan_history.append({
                "ts": hour_ts.isoformat(),
                "planned_kwh": round(sum(float(v or 0.0) for v in planned.values())
                                     + float(value_at(self.baseload_72h, idx or 0) or 0.0), 3),
                "actual_kwh": round(self.energy_this_hour_kwh, 3),
                "devices": {eid: [round(float(planned.get(eid) or 0.0), 3), round(self._hour_dev_kwh.get(eid, 0.0), 3)]
                            for eid in self.device_entries},
                "price": round(float(self.price_series.total_price_72h[0]), 4) if self.price_series else None,
            })
            self.plan_history = self.plan_history[-168:]
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Plan history bookkeeping failed")

    async def async_set_shadow_model(self, model: str) -> None:
        self.shadow.shadow_model = model
        self.shadow.reset()
        self.shadow_plans = {}
        await self.async_settings_changed(reprice=False)

    async def async_reset_shadow(self) -> None:
        self.shadow.reset()
        self.shadow_plans = {}
        await self.async_settings_changed(reprice=False)

    # ------------------------------------------------------------------
    # Loop 3: fast distribution + all actuation
    # ------------------------------------------------------------------
    @callback
    def _handle_grid_sensor_event(self, _event: Event) -> None:
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> dict[str, Any]:
        now = dt_util.now()
        dt_s = 10.0 if self._last_fast_ts is None else max(0.5, min(60.0, (now - self._last_fast_ts).total_seconds()))
        self._last_fast_ts = now
        cfg = self._hub_cfg()
        timeout = self._timeout()

        active_kw = self._read_float(cfg.get(CONF_ACTIVE_POWER_SENSOR), max_age_s=timeout)
        grid_valid = active_kw is not None
        self.grid_power_kw = active_kw or 0.0

        # Energy this hour: integrated grid power (like a kW→kWh utility meter reset every
        # hour) or the configured hourly energy sensor. The ended hour is closed in this same
        # step, with its own energy, before anything is reset.
        hs_id = cfg.get(CONF_HOUR_ENERGY_SENSOR)
        hs_state = self.hass.states.get(hs_id) if hs_id else None
        hs_val = self._read_float(hs_id) if hs_id else None
        closed = self.hour_meter.update(now, active_kw if grid_valid else None, hs_val,
                                        dt_util.as_local(self._last_seen(hs_state)) if hs_state is not None else None)
        if closed is not None:
            self.energy_this_hour_kwh = closed.kwh
            self._close_shadow_hour(closed.hour_start)
            self._record_plan_history(closed.hour_start)
            self._hour_dev_kwh = {}
            self.hass.async_create_task(self._async_process_hourly_rollover(now, closed))
        self._hour_key = now.strftime("%Y-%m-%dT%H")
        self.energy_this_hour_kwh = round(self.hour_meter.energy_this_hour_kwh, 4)
        self.hour_energy_source = self.hour_meter.source

        minutes = now.minute + now.second / 60.0
        self.energy_projected_kwh = round(self.energy_this_hour_kwh + self.grid_power_kw * (60.0 - minutes) / 60.0, 3)
        self.p_avg_60m_kw = self.hour_meter.avg_60min_kw(now)

        self.limiters.external_limit_a = self._read_float(cfg.get(CONF_EXTERNAL_LIMIT_SENSOR), max_age_s=timeout)
        self.hour_allowance_kwh = self.price_engine.hourly_energy_allowance_kwh(now.date())
        raw_now = self.limiters.calculate_tariff_limit_now_kw(
            self.price_engine.active_tariff_kw, self.energy_this_hour_kwh, minutes, target_kwh=self.hour_allowance_kwh
        )
        budget_kw, sensor_bad = self.limiters.evaluate_total_budget_kw(grid_valid, raw_now)
        # Safe mode is latched until the end of the clock hour: when the grid measurement comes
        # back mid-hour the energy of the missing period is unknown, so the safe budget is kept.
        # 2.13.0: no safe mode during the start-up grace period (HA restart: the meter reader
        # needs a few seconds to reconnect; the hour's energy is restored from storage). During
        # the grace period nothing is switched – no more power is granted, nothing is cut.
        # Safe mode is latched to the end of the hour only after a REAL outage (> 2 min while
        # running). With an hourly energy sensor the hour's energy is known, so safe mode is
        # released as soon as the measurement is back.
        hour_key_now = now.strftime("%Y-%m-%dT%H")
        if grid_valid:
            self._grid_seen_ok = True
            self._grid_missing_since = None
        elif self._grid_missing_since is None:
            self._grid_missing_since = now
        self.startup_wait = (not self._grid_seen_ok
                             and (now - self._started_at).total_seconds() < STARTUP_GRACE_S)
        missing_s = (now - self._grid_missing_since).total_seconds() if self._grid_missing_since else 0.0
        if sensor_bad and not self.startup_wait:
            if missing_s >= SAFE_MODE_LATCH_S:
                self._safe_mode_hour = hour_key_now
            self._safe_mode_since = self._safe_mode_since or now
        if not sensor_bad:
            if self._safe_mode_hour is not None and (
                    self._safe_mode_hour != hour_key_now or self.hour_energy_source == "hour_sensor"):
                self._safe_mode_hour = None
            if self._safe_mode_hour is None:
                self._safe_mode_since = None
        self.safe_mode_active = (sensor_bad and not self.startup_wait) or self._safe_mode_hour is not None
        self.safe_mode_sensor_ok = not sensor_bad
        if self.safe_mode_active and not sensor_bad:
            from .const import SAFE_MODE_MAX_KW
            budget_kw = min(budget_kw, SAFE_MODE_MAX_KW)
        self.limit_now_kw = budget_kw

        self.phase_currents = {
            ph: self._read_float(cfg.get(key), max_age_s=timeout) or 0.0
            for ph, key in (("L1", CONF_CURRENT_L1_SENSOR), ("L2", CONF_CURRENT_L2_SENSOR), ("L3", CONF_CURRENT_L3_SENSOR))
        }
        # Overload Acceptance: tolerate short/small overloads, then ramp back to the rated limit
        phase_limits = self.limiters.apply_overload_acceptance(self.phase_currents, dt_s) if grid_valid \
            else self.limiters.effective_phase_limits_a()
        phase_over = self.limiters.oa.any_limiting()
        self.warning_active = (self.energy_projected_kwh > self.hour_allowance_kwh) or phase_over

        self._update_day_counters(dt_s)
        self._update_mode_transitions(now)
        self._track_ev_sessions(now)
        self._update_ev_checks(now)
        self._update_appliances(now)

        dist_inputs = []
        for eid in self.device_entries:
            d = self.device_cfg(eid)
            planned_now = float(value_at(self.plans_15m.get(eid), self.slot_idx(now)) or 0.0)
            dist_inputs.append({
                "entry_id": eid,
                "device_type": d[CONF_DEVICE_TYPE],
                "phase_pair": d.get(CONF_PHASE_PAIR, PhasePair.L1_L2),
                "nominal_power_kw": self.device_nominal_kw(d),
                "min_current_a": float((d.get(CONF_EV_MIN_CURRENT_A) or 6)),
                "live_power_kw": self.get_device_live_power_kw(eid),
                "dist_priority": self.dist_priority.get(eid, 5),
                "committed": self.committed.get(eid, False),
                "priority_now": self.priority_now.get(eid, False),
                "planned_kw": planned_now,
                "heater_kind": d.get(CONF_HEATER_KIND, HEATER_ON_OFF),
                # EVs that the plan cannot finish in time may borrow spare capacity
                "opportunistic_need": self.plan_result.unmet.get(eid, 0.0) > 0.1,
            })
        self.allocations = self.distributor.distribute(dist_inputs, budget_kw, phase_limits, self.phase_currents)
        if self.startup_wait:
            # waiting for the first grid reading after start-up: send nothing
            return self._build_state_snapshot()

        for eid in list(self.device_entries):
            if not self.committed.get(eid, False):
                continue
            try:
                await self._actuate_device(now, eid, phase_limits, budget_kw)
            except Exception:  # noqa: BLE001 – one bad device must not stop the loop
                _LOGGER.exception("Actuation failed for %s", eid)
        await self._async_run_notifications(now)
        return self._build_state_snapshot()

    def _update_day_counters(self, dt_s: float) -> None:
        for eid in self.device_entries:
            kw = self.get_device_live_power_kw(eid)
            self._day_energy_kwh[eid] = self._day_energy_kwh.get(eid, 0.0) + kw * dt_s / 3600.0
            self._hour_dev_kwh[eid] = self._hour_dev_kwh.get(eid, 0.0) + kw * dt_s / 3600.0
            if kw > 0.1:
                self._day_runtime_h[eid] = self._day_runtime_h.get(eid, 0.0) + dt_s / 3600.0

    def _track_ev_sessions(self, now: datetime) -> None:
        """Plug-in time per EV: fixes the deadline for the minimum state of charge."""
        for eid in self.device_entries:
            d = self.device_cfg(eid)
            if d.get(CONF_DEVICE_TYPE) != DeviceType.EV_CHARGER:
                continue
            connected = self.ev_plugged_in(d)
            was = self._ev_was_connected.get(eid, False)
            if connected and not was:
                self.ev_session_start[eid] = now
                self.ev_min_since[eid] = None
                self.schedule_save()
                self.hass.async_create_task(self.async_run_planner())
            elif not connected and was:
                self.ev_session_start[eid] = None
                self.ev_min_since[eid] = None
                self.schedule_save()
            self._ev_was_connected[eid] = connected

    def _update_ev_checks(self, now: datetime) -> None:
        """Connector status vs. measurements (2.13.1). A disagreement is reported only when it has
        lasted EV_CHECK_DELAY_S, so the ramp-up after a start does not trigger it."""
        for eid in self.device_entries:
            d = self.device_cfg(eid)
            if d.get(CONF_DEVICE_TYPE) != DeviceType.EV_CHARGER:
                continue
            raw, text = self.ev_status(d)
            info = EV_STATUS_TEXT.get(raw.replace(" ", "").replace("_", "").lower()) if raw else None
            found = ev_checks(raw, text, info[1] if info else None,
                              self._read_float(d.get(CONF_POWER_SENSOR)), self._read_float(d.get(CONF_EV_CURRENT_SENSOR)),
                              self._read_float(d.get(CONF_EV_OFFERED_SENSOR)),
                              self._last_ev_target_a.get(eid) if self.committed.get(eid) else None,
                              bool(self.committed.get(eid)))
            since = self._ev_check_since.setdefault(eid, {})
            for code in list(since):
                if code not in found:
                    since.pop(code)
            for code in found:
                since.setdefault(code, now)
            self.ev_warnings[eid] = {c: m for c, m in found.items()
                                     if (now - since[c]).total_seconds() >= EV_CHECK_DELAY_S}

    def _update_mode_transitions(self, now: datetime) -> None:
        """Away -> Arriving (pre-heat started) -> Home (arrival time reached)."""
        if self.mode not in (GridMode.AWAY, GridMode.ARRIVING) or self.arrival_time is None:
            return
        if now >= self.arrival_time:
            self.mode = GridMode.HOME
            self.arrival_time = None
            self.schedule_save()
            self.hass.async_create_task(self._async_reprice_and_replan())
            return
        if self.mode == GridMode.AWAY:
            for eid, sp in self.plan_slots.setpoints.items():
                d = self.device_cfg(eid) if eid in self.device_entries else {}
                sp_now = value_at(sp, self.slot_idx(now))
                if d.get(CONF_DEVICE_TYPE) == DeviceType.CLIMATE_HEATING and sp_now is not None:
                    oot = self._read_float(d.get(CONF_OOT_TEMP_ENTITY)) or 10.0
                    if sp_now > oot + 0.4:
                        self.mode = GridMode.ARRIVING
                        self.schedule_save()
                        break

    def _can_switch(self, eid: str, now: datetime, min_s: int) -> bool:
        last = self._last_switch_ts.get(eid)
        return last is None or (now - last).total_seconds() >= min_s

    async def _actuate_device(self, now: datetime, eid: str, phase_limits: dict[str, float], budget_kw: float) -> None:
        d = self.device_cfg(eid)
        dtype = DeviceType(d[CONF_DEVICE_TYPE])
        alloc = self.allocations.get(eid)
        if dtype == DeviceType.EV_CHARGER:
            await self._actuate_ev_charger(now, eid, d, phase_limits, budget_kw)
            return
        target = d.get(CONF_TARGET_ENTITY)
        if not target or alloc is None:
            return
        blocked = alloc.status == DistStatus.HOLD and alloc.reason == "blocked"
        shed = self.warning_active and alloc.status == DistStatus.HOLD
        allowed = alloc.allowed_kw > 0.0
        if dtype == DeviceType.CLIMATE_HEATING:
            await self._actuate_climate_heating(now, eid, d, target, blocked or shed)
        elif dtype == DeviceType.CLIMATE_HOTWATER:
            await self._actuate_hotwater(now, eid, d, target, allowed, blocked or shed)
        elif dtype == DeviceType.SWITCH:
            await self._actuate_switch(now, eid, target, allowed, force=self.warning_active and not allowed)

    def _climate_setpoint(self, entity_id: str | None) -> float | None:
        st = self.hass.states.get(entity_id) if entity_id else None
        if st is None or st.state in ("unavailable", "unknown"):
            return None
        if st.state == "off":
            return -100.0   # switched off -> no consumption in the forecast
        try:
            return float(st.attributes.get("temperature"))
        except (TypeError, ValueError):
            return None

    def plan_idx(self, now: datetime | None = None) -> int:
        """Index of the current hour in plans made at xx:59 (fixes the <=2.5.0 off-by-one)."""
        return plan_index(self.plan_start, now or dt_util.now())

    def _planned_setpoint(self, eid: str) -> float | None:
        """Planned setpoint for the CURRENT CLOCK HOUR (highest quarter of the hour), so the
        plan changes a device's setpoint at most once per hour even with 15-minute planning."""
        sp = value_at(self.plan_result.setpoints.get(eid), self.plan_idx())
        if sp is None:
            sp = value_at(self.plan_slots.setpoints.get(eid), self.slot_idx())
        return float(sp) if sp is not None else None

    def zone_seed(self, zid: str) -> tuple[float, float] | None:
        cfg = self.zone_cfg.get(zid)
        if not cfg:
            return None
        return seed_thermal_parameters(cfg.get("area", 30.0), cfg.get("insulation", InsulationClass.STANDARD))

    async def async_thermal_model(self, zone_id: str, action: str, heat_capacity_kwh_per_c: float | None = None,
                                  heat_loss_w_per_c: float | None = None, freeze: bool | None = None) -> None:
        """reset | set | freeze | unfreeze a zone's thermal model (service power_controller.thermal_model)."""
        from .thermodynamics import params_from_physical

        zone = self.zones.get(zone_id)
        if zone is None:
            raise ValueError(f"Unknown thermal zone {zone_id}")
        if action == "reset":
            seed = self.zone_seed(zone_id)
            if seed:
                zone.seed_alpha, zone.seed_beta = seed
            zone.reset()
        elif action == "set":
            cur = zone.summary()
            alpha, beta = params_from_physical(
                heat_capacity_kwh_per_c if heat_capacity_kwh_per_c is not None else cur["heat_capacity_kwh_per_c"],
                heat_loss_w_per_c if heat_loss_w_per_c is not None else cur["heat_loss_w_per_c"])
            zone.set_manual(alpha, beta, freeze=True if freeze is None else bool(freeze))
        elif action == "freeze":
            zone.frozen = True
        elif action == "unfreeze":
            zone.frozen = False
        else:
            raise ValueError(f"Unknown action {action}")
        self.schedule_save()
        await self.async_run_planner()
        await self.async_request_refresh()

    def thermal_zones_payload(self) -> list[dict]:
        out = []
        for zid, zone in self.zones.items():
            cfg = self.zone_cfg.get(zid, {})
            devs = [eid for eid in self.device_entries
                    if self.device_cfg(eid).get(CONF_DEVICE_TYPE) == DeviceType.CLIMATE_HEATING
                    and (self.device_cfg(eid).get(CONF_ZONE_ID) or "") == zid]
            temps = [self._read_float(self.device_cfg(e).get(CONF_ROOM_TEMP_SENSOR)) for e in devs]
            temps = [t for t in temps if t is not None]
            win = cfg.get("window")
            wst = self.hass.states.get(win) if win else None
            pred = None
            for e in devs:
                p = self.plan_result.predicted_temps.get(e)
                if p:
                    pred = p[:24]
                    break
            out.append({
                "zone_id": zid, "name": cfg.get("name") or zid, "area_m2": cfg.get("area"),
                "insulation": str(cfg.get("insulation") or ""),
                "devices": [{"entry_id": e, "name": self.device_entries[e].title,
                             "heating_coefficient": float(self.device_cfg(e).get(CONF_HEATING_COEFFICIENT, 1.0)),
                             "heater_kind": self.device_cfg(e).get(CONF_HEATER_KIND, HEATER_ON_OFF)} for e in devs],
                "room_temp": round(sum(temps) / len(temps), 2) if temps else None,
                "window_sensor": win, "window_open": bool(wst and wst.state == "on"),
                "predicted_24h": pred,
                **zone.summary(),
            })
        return out

    async def async_hotwater_model(self, device_id: str, action: str, loss_w_per_c: float | None = None,
                                   daily_kwh: float | None = None, freeze: bool | None = None) -> None:
        """reset | set | freeze | unfreeze a hot-water tank model (service power_controller.hotwater_model)."""
        tank = self.tanks.get(device_id)
        if tank is None:
            raise ValueError(f"No hot-water model for {device_id} (needs a water temperature and a power sensor)")
        if action == "reset":
            tank.reset()
        elif action == "set":
            tank.set_manual(loss_w_per_c, daily_kwh, freeze=True if freeze is None else bool(freeze))
        elif action == "freeze":
            tank.frozen = True
        elif action == "unfreeze":
            tank.frozen = False
        else:
            raise ValueError(f"Unknown action {action}")
        self.schedule_save()
        await self.async_run_planner()
        await self.async_request_refresh()

    def hotwater_models_payload(self) -> list[dict]:
        out = []
        for eid in self.device_entries:
            d = self.device_cfg(eid)
            if d.get(CONF_DEVICE_TYPE) != DeviceType.CLIMATE_HOTWATER:
                continue
            tank = self.tanks.get(eid)
            base = {"device_id": eid, "name": self.device_entries[eid].title,
                    "water_temp": self._read_float(d.get(CONF_WATER_TEMP_SENSOR)),
                    "has_temp_sensor": bool(d.get(CONF_WATER_TEMP_SENSOR)), "has_power_sensor": bool(d.get(CONF_POWER_SENSOR))}
            out.append({**base, **(tank.summary() if tank else {"status": "no_model"})})
        return out

    # ------------------------------------------------------------------ household appliances
    def appliance_roles(self, eid: str) -> dict[str, str]:
        """Role -> entity id. Explicit choices in the configuration win; the rest is found in
        the entities of the chosen HA device (Home Connect Alt naming)."""
        d = self.device_cfg(eid)
        roles = self._appl_roles.get(eid)
        if roles is None:
            roles = {}
            dev_id = d.get(CONF_APPLIANCE_DEVICE)
            if dev_id:
                try:
                    reg = er.async_get(self.hass)
                    ents = [(e.entity_id, f"{e.translation_key or ''} {e.original_name or ''}")
                            for e in er.async_entries_for_device(reg, dev_id)]
                    roles = detect_roles(ents)
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Could not read the entities of device %s", dev_id, exc_info=True)
            self._appl_roles[eid] = roles
        out = dict(roles)
        for role, key in APPLIANCE_ROLE_CONF.items():
            if d.get(key):
                out[role] = d[key]
        return out

    def appliance_status(self, eid: str, now: datetime | None = None) -> dict[str, Any]:
        now = now or dt_util.now()
        d = self.device_cfg(eid)
        r = self.appliance_roles(eid)

        def st(role):
            ent = r.get(role)
            s_ = self.hass.states.get(ent) if ent else None
            return s_ if s_ is not None and s_.state not in ("unknown", "unavailable") else None
        extra = lambda key: tuple(x.strip() for x in str(d.get(key) or "").split(",") if x.strip())  # noqa: E731
        op = st("operation_state")
        raw = op.state if op else None
        cls = appl_classify(raw, extra(CONF_APPL_RUNNING_STATES), extra(CONF_APPL_DELAYED_STATES))
        prog_st = st("active_program") or st("selected_program")
        program = prog_st.state if prog_st else None
        start_at = None
        s_in = st("start_in")
        if s_in is not None:
            h = parse_duration_h(s_in.state, s_in.attributes.get("unit_of_measurement"))
            if h:
                start_at = now + timedelta(hours=h)
        fin = st("finish_at")
        finish_at = parse_timestamp(fin.state, now) if fin else None
        rem = st("remaining")
        remaining_h = parse_duration_h(rem.state, rem.attributes.get("unit_of_measurement")) if rem else None
        if remaining_h is None and finish_at is not None and cls == "running":
            remaining_h = max(0.0, (finish_at - now).total_seconds() / 3600)
        model = self.appliances.get(eid)
        stats = model.stats(program) if model else None
        if start_at is None and cls == "delayed" and finish_at is not None and stats:
            start_at = finish_at - timedelta(hours=stats.duration_h)       # fall back to finish − duration
        if finish_at is None and start_at is not None and stats:
            finish_at = start_at + timedelta(hours=stats.duration_h)
        pr = st("progress")
        try:
            progress = float(pr.state) if pr else None
        except ValueError:
            progress = None
        return {"state": raw, "state_class": cls, "program": program,
                "program_name": model.label(program) if model else program, "start_at": start_at,
                "finish_at": finish_at, "remaining_h": remaining_h, "progress": progress, "roles": r,
                "expected_kwh": round(stats.kwh, 3) if stats else None,
                "expected_duration_h": round(stats.duration_h, 3) if stats else None,
                "learned": bool(stats and stats.runs),
                "source": ("manual" if stats and stats.manual else "learned" if stats and stats.runs else "default")}

    def appliance_expected_15m(self, eid: str) -> list[float]:
        """Expected load (kW per 15 min, 288 values from the start of the current hour)."""
        model = self.appliances.get(eid)
        if model is None:
            return []
        now = dt_util.now()
        s_ = self.appliance_status(eid, now)
        start = now.replace(minute=0, second=0, microsecond=0)
        return model.expected_load(now, start, PLANNING_HORIZON_HOURS * 4, 0.25, s_["state_class"], s_["program"],
                                   s_["start_at"], s_["remaining_h"], s_["progress"])

    def _update_appliances(self, now: datetime) -> None:
        """Learn the program register and replan when a program is set/started/ended."""
        for eid in self.device_entries:
            d = self.device_cfg(eid)
            if d.get(CONF_DEVICE_TYPE) != DeviceType.APPLIANCE:
                continue
            model = self.appliances.setdefault(eid, ApplianceModel(device_id=eid))
            s_ = self.appliance_status(eid, now)
            power = self._read_float(d.get(CONF_POWER_SENSOR))
            done = model.update(now, s_["state_class"], s_["program"], power)
            if done:
                _LOGGER.info("Appliance %s: %s used %.2f kWh in %.1f h", self.device_entries[eid].title,
                             done["program"], done["kwh"], done["duration_h"])
                self.schedule_save()
            start_key = s_["start_at"].replace(second=0, microsecond=0) if s_["start_at"] else None
            key = (s_["state_class"], s_["program"])
            last = self._appl_last.get(eid)
            moved = bool(last and last[2] and start_key and abs((last[2] - start_key).total_seconds()) > 600)
            if last is None or last[:2] != key or moved:
                self._appl_last[eid] = (*key, start_key)
                if last is not None:
                    self.hass.async_create_task(self.async_run_planner())

    async def async_appliance_program(self, device_id: str, action: str, program: str | None = None,
                                      kwh: float | None = None, duration_h: float | None = None,
                                      name: str | None = None) -> None:
        """set | name | delete | reset (all) for an appliance's program register.
        ``name`` gives the program its own display name (e.g. the code "22" -> "Bomull 40")."""
        model = self.appliances.get(device_id)
        if model is None:
            raise ValueError(f"{device_id} is not a household appliance")
        if action == "set":
            if not program:
                raise ValueError("program is required")
            if kwh is not None or duration_h is not None:
                model.set_manual(program, kwh, duration_h)
            if name is not None:
                model.set_name(program, name)
        elif action == "name":
            model.set_name(program or "", name)
            self.schedule_save()
            await self.async_request_refresh()
            return
        elif action == "delete":
            model.programs.pop(program or "", None)
        elif action == "reset":
            model.programs.clear()
        else:
            raise ValueError(f"Unknown action {action}")
        self.schedule_save()
        await self.async_run_planner()
        await self.async_request_refresh()

    def appliances_payload(self) -> list[dict]:
        out = []
        for eid in self.device_entries:
            if self.device_cfg(eid).get(CONF_DEVICE_TYPE) != DeviceType.APPLIANCE:
                continue
            m = self.appliances.get(eid)
            s_ = self.appliance_status(eid)
            out.append({"device_id": eid, "name": self.device_entries[eid].title,
                        "has_power_sensor": bool(self.device_cfg(eid).get(CONF_POWER_SENSOR)),
                        "state": s_["state"], "state_class": s_["state_class"], "program": s_["program"],
                        "program_name": s_["program_name"],
                        "roles": s_["roles"], **(m.summary() if m else {})})
        return out

    def command_interval_s(self, eid: str) -> float:
        """Minimum time between two commands to a device. Heaters regulated by an external
        room sensor (PI governor, typically heat pumps) get their own, longer interval to
        avoid wearing the compressor with frequent setpoint changes."""
        d = self.device_cfg(eid) if eid in self.device_entries else {}
        if d.get(CONF_DEVICE_TYPE) == DeviceType.CLIMATE_HEATING and d.get(CONF_ENABLE_PI_GOVERNOR):
            return self.control.regulated_min_interval_s
        return self.control.min_interval_s

    def urgent_now(self) -> bool:
        """True when a command must go out at once: the hour allowance is about to be
        exceeded or a phase is beyond overload acceptance."""
        return bool(self.warning_active or self.limiters.oa.any_limiting()
                    or self.energy_projected_kwh > self.hour_allowance_kwh)

    async def _set_climate(self, now: datetime, eid: str, entity: str, temp: float, force: bool) -> None:
        st = self.hass.states.get(entity)
        cur = st.attributes.get("temperature") if st else None
        cur_f = float(cur) if cur is not None else None
        if not should_send_command(cur_f, temp, self._last_switch_ts.get(eid), now,
                                   self.control.temp_min_delta_c, self.command_interval_s(eid),
                                   urgent=force, lower_is_relief=True):
            return
        if await self._safe_call("climate", "set_temperature", {"entity_id": entity, "temperature": temp}):
            self._last_switch_ts[eid] = now
            self.commanded_setpoint[eid] = temp

    async def _actuate_climate_heating(self, now, eid, d, entity, setback: bool) -> None:
        eco = self._read_float(d.get(CONF_ECO_TEMP_ENTITY)) or 18.0
        oot = self._read_float(d.get(CONF_OOT_TEMP_ENTITY)) or 10.0
        desired = self._planned_setpoint(eid)
        if desired is None:
            desired = oot if self.mode == GridMode.AWAY else (self._read_float(d.get(CONF_COMFORT_TEMP_ENTITY)) or 21.0)
        # never lower the room below comfort while the occupancy selector says we are in a
        # state this room needs comfort for (e.g. still home and awake after the scheduled bedtime)
        comfort_states = d.get(CONF_COMFORT_DURING) or ["home_awake", "home_office"]
        if self.mode != GridMode.AWAY and self.occupancy_state_now() in comfort_states:
            comfort = self._read_float(d.get(CONF_COMFORT_TEMP_ENTITY))
            if comfort is not None:
                desired = max(desired, comfort)
        if setback:
            desired = min(desired, eco)   # capacity/phase protection still wins
        elif d.get(CONF_ENABLE_PI_GOVERNOR):
            room = self._read_float(d.get(CONF_ROOM_TEMP_SENSOR))
            if room is not None:
                desired = self.distributor.compute_heatpump_governor_setpoint(eid, desired, room, 10.0)
        await self._set_climate(now, eid, entity, desired, force=setback and self.warning_active)

    async def _actuate_hotwater(self, now, eid, d, entity, allowed: bool, setback: bool) -> None:
        if entity.startswith("switch."):
            await self._actuate_switch(now, eid, entity, allowed, min_interval_s=MIN_CLIMATE_SWITCH_SECONDS,
                                       force=setback and self.warning_active)
            return
        min_t = self._read_float(d.get(CONF_MIN_TEMP_ENTITY)) or 50.0
        desired = self._planned_setpoint(eid) or min_t
        if setback:
            desired = min_t
        await self._set_climate(now, eid, entity, desired, force=setback and self.warning_active)

    async def _actuate_switch(self, now, eid, entity, allowed: bool, min_interval_s: int = MIN_RELAY_SWITCH_SECONDS, force: bool = False) -> None:
        st = self.hass.states.get(entity)
        if st is None or st.state in ("unknown", "unavailable"):
            return
        if not force and not self._can_switch(eid, now, min_interval_s):
            return
        domain = entity.split(".", 1)[0]
        if allowed and st.state == "off":
            if await self._safe_call(domain, "turn_on", {"entity_id": entity}):
                self._last_switch_ts[eid] = now
        elif not allowed and st.state == "on":
            if await self._safe_call(domain, "turn_off", {"entity_id": entity}):
                self._last_switch_ts[eid] = now

    async def _actuate_ev_charger(self, now, eid, d, phase_limits, budget_kw) -> None:
        alloc = self.allocations.get(eid)
        if alloc is None:
            return
        ev_kw = self.get_device_live_power_kw(eid)
        min_a = int((d.get(CONF_EV_MIN_CURRENT_A) or 6))
        max_a = int(self.ev_max_current_a(d))
        if alloc.allowed_kw <= 0.0:
            target_a = 0
        else:
            target_a = self.distributor.compute_ev_feedforward_amps(
                prev_target_a=self._last_ev_target_a.get(eid, min_a),
                ev_live_power_kw=ev_kw,
                ev_phase_pair=d.get(CONF_PHASE_PAIR, PhasePair.L1_L2),
                grid_phase_currents_a=self.phase_currents,
                phase_limits_a=phase_limits,
                tariff_limit_now_kw=budget_kw,
                house_non_ev_kw=max(0.0, self.grid_power_kw - ev_kw),
                min_ev_a=min_a,
                # never above what the distributor granted (= plan share or lent capacity)
                max_ev_a=max(min_a, min(max_a, int(alloc.allowed_amps))),
                ramp=False,   # command patience limits how often the current changes
            )
            if 0 < target_a < min_a:
                target_a = 0  # the charger cannot run below its minimum current

        charge_sw = d.get(CONF_EV_CHARGE_SWITCH)
        sw_state = self.hass.states.get(charge_sw) if charge_sw else None
        charging_on = bool(sw_state and sw_state.state == "on")
        pnow = self.priority_now.get(eid, False)
        if target_a == 0:
            # stop: 0 A on the running transaction first, then switch charging off
            if charging_on and (self._can_switch(eid, now, MIN_EV_PAUSE_SECONDS) or self.urgent_now()):
                if self._last_ev_target_a.get(eid) != 0:
                    await self._send_ev_limit(eid, d, 0, now)
                if await self._safe_call("switch", "turn_off", {"entity_id": charge_sw}):
                    self._last_switch_ts[eid] = now
                    self._ev_last_action[eid] = f"stop {now:%H:%M}"
            self._last_ev_target_a[eid] = 0
            return

        if not charging_on:
            # start: availability on, current limit first (default profile – no transaction yet),
            # then the charge switch. Priority now and the first start skip the pause timer.
            if not (pnow or self._can_switch(eid, now, MIN_EV_PAUSE_SECONDS)):
                self._ev_last_action[eid] = "waiting for restart pause"
                return
            avail = d.get(CONF_EV_AVAILABILITY_SWITCH)
            av_state = self.hass.states.get(avail) if avail else None
            if av_state is not None and av_state.state == "off":
                await self._safe_call("switch", "turn_on", {"entity_id": avail})
            await self._send_ev_limit(eid, d, target_a, now)
            if charge_sw and await self._safe_call("switch", "turn_on", {"entity_id": charge_sw}):
                self._last_switch_ts[eid] = now
                self._ev_last_action[eid] = f"start {target_a} A {now:%H:%M}"
            elif not charge_sw:
                self._ev_last_action[eid] = "no charge switch configured"
            return

        prev = self._last_ev_target_a.get(eid)
        last_cmd = self._last_ocpp_cmd_ts.get(eid)
        # a transaction appeared since the last command (charging just started): bind the
        # current to it at once with a TxProfile
        tx_new = self.ev_transaction_id(d) is not None and self._last_ev_cmd_kind.get(eid) != "tx_profile"
        # patient: only meaningful changes, at most every few minutes; reductions go out at
        # once only when the hour allowance or a phase is at risk (never increases)
        if tx_new or should_send_command(prev, target_a, last_cmd, now, self.control.ev_min_delta_a,
                                         self.control.min_interval_s, urgent=self.urgent_now() or pnow,
                                         lower_is_relief=True):
            await self._send_ev_limit(eid, d, target_a, now)

    # ------------------------------------------------------------------ EV status helpers
    def ev_status(self, d: dict) -> tuple[str | None, str | None]:
        """(raw OCPP connector status, readable text) or (None, None) without a status sensor."""
        ent = d.get(CONF_EV_STATUS_SENSOR)
        st = self.hass.states.get(ent) if ent else None
        if st is None or st.state in ("unknown", "unavailable", ""):
            return None, None
        raw = str(st.state)
        info = EV_STATUS_TEXT.get(raw.replace(" ", "").replace("_", "").lower())
        return raw, (info[0] if info else raw.replace("_", " ").capitalize())

    def ev_plugged_in(self, d: dict) -> bool:
        raw, _ = self.ev_status(d)
        if raw is not None:
            info = EV_STATUS_TEXT.get(raw.replace(" ", "").replace("_", "").lower())
            if info is not None:
                return info[2]
        return bool(self._state_on(d.get(CONF_EV_CONNECTED_SENSOR)))

    def ev_is_charging(self, eid: str, d: dict) -> bool:
        """Connector status decides when configured (Charging = energy flows); otherwise the
        measured power (> 0.5 kW)."""
        raw, _ = self.ev_status(d)
        if raw is not None:
            info = EV_STATUS_TEXT.get(raw.replace(" ", "").replace("_", "").lower())
            if info is not None:
                return info[1]
        return self.get_device_live_power_kw(eid) > 0.5

    def ev_max_current_a(self, d: dict) -> float:
        """Charger rating, further limited by the car's own current limiter when configured."""
        a = float(d.get(CONF_NOMINAL_CURRENT_A) or 32)
        car = self._read_float(d.get(CONF_EV_CAR_LIMIT_ENTITY))
        if car is not None and car > 0:
            a = min(a, car)
        return a

    def device_nominal_kw(self, d: dict) -> float:
        """Nominal power; for an EV limited by the car's current limiter (230 V IT pair)."""
        p = float(d.get(CONF_NOMINAL_POWER_KW) or 1.0)
        if d.get(CONF_DEVICE_TYPE) == DeviceType.EV_CHARGER and d.get(CONF_EV_CAR_LIMIT_ENTITY):
            p = min(p, self.ev_max_current_a(d) * 230.0 / 1000.0)
        return p

    def ev_transaction_id(self, d: dict) -> int | None:
        ent = d.get(CONF_EV_TRANSACTION_SENSOR)
        st = self.hass.states.get(ent) if ent else None
        return parse_transaction_id(st.state if st else None)

    async def _send_ev_limit(self, eid: str, d: dict, limit_a: int, now: datetime) -> bool:
        """ocpp.set_charge_rate: TxProfile on the active transaction, limit_amps before one exists."""
        data = ocpp_charge_rate_data(limit_a, int(d.get(CONF_EV_CONN_ID) or 1), self.ev_transaction_id(d))
        ok = await self._safe_call("ocpp", "set_charge_rate", data, blocking=True)
        if ok:
            self._last_ev_target_a[eid] = int(limit_a)
            self._last_ocpp_cmd_ts[eid] = now
            self._last_ev_cmd_kind[eid] = "tx_profile" if "custom_profile" in data else "limit_amps"
        else:
            _LOGGER.warning("EV %s: set_charge_rate %s A was not accepted (data: %s)", eid, limit_a, data)
            self._ocpp_rejected[eid] = now
        return ok

    # ------------------------------------------------------------------ EV target SoC
    def ev_target_soc(self, eid: str) -> float:
        """Charge target: the car's own target SoC entity if configured, else the PC number."""
        d = self.device_cfg(eid)
        ext = self._read_float(d.get(CONF_EV_TARGET_SOC_ENTITY))
        return float(ext) if ext is not None else float(self.target_soc.get(eid, 80.0))

    async def async_set_ev_target_soc(self, eid: str, value: float) -> None:
        d = self.device_cfg(eid)
        ext = d.get(CONF_EV_TARGET_SOC_ENTITY)
        if ext:
            await self._safe_call(ext.split(".", 1)[0], "set_value", {"entity_id": ext, "value": float(value)})
        self.target_soc[eid] = float(value)
        await self.async_settings_changed(reprice=False)

    async def async_release_device(self, eid: str) -> None:
        """Called when a device is set to Uncommitted: hand it back in a sane state."""
        if eid not in self.device_entries:
            return
        d = self.device_cfg(eid)
        dtype = DeviceType(d[CONF_DEVICE_TYPE])
        target = d.get(CONF_TARGET_ENTITY)
        if dtype == DeviceType.CLIMATE_HEATING and target:
            temp = self._read_float(d.get(CONF_COMFORT_TEMP_ENTITY)) or 21.0
            await self._safe_call("climate", "set_temperature", {"entity_id": target, "temperature": temp})
        elif dtype == DeviceType.CLIMATE_HOTWATER and target:
            if target.startswith("climate."):
                temp = self._read_float(d.get(CONF_NOMINAL_TEMP_ENTITY)) or 65.0
                await self._safe_call("climate", "set_temperature", {"entity_id": target, "temperature": temp})
            else:
                await self._safe_call(target.split(".", 1)[0], "turn_on", {"entity_id": target})
        elif dtype == DeviceType.SWITCH and target:
            await self._safe_call(target.split(".", 1)[0], "turn_on", {"entity_id": target})
        elif dtype == DeviceType.EV_CHARGER:
            await self._send_ev_limit(eid, d, int(d.get(CONF_NOMINAL_CURRENT_A) or 32), dt_util.now())
        self._last_ev_target_a.pop(eid, None)

    # ------------------------------------------------------------------
    # Loop 4: learning & legionella
    # ------------------------------------------------------------------
    @callback
    def _handle_slow_loop(self, now: datetime) -> None:
        self.hass.async_create_task(self._async_run_slow_loop(dt_util.now()))

    async def _async_run_slow_loop(self, now: datetime) -> None:
        t_out = self._read_float(self._hub_cfg().get(CONF_OUTDOOR_TEMP_SENSOR))
        t_out = 5.0 if t_out is None else t_out
        for zid, zone in self.zones.items():
            zone_devs = [eid for eid in self.device_entries if self.device_cfg(eid).get(CONF_ZONE_ID) == zid]
            temps = [self._read_float(self.device_cfg(e).get(CONF_ROOM_TEMP_SENSOR)) for e in zone_devs]
            temps = [t for t in temps if t is not None]
            if not temps:
                continue
            t_curr = sum(temps) / len(temps)
            win = self.zone_windows.get(zid)
            window_open = bool(win and self.hass.states.get(win) and self.hass.states.get(win).state == "on")
            prev = self._last_zone_sample.get(zid)
            if prev is None or window_open:
                if window_open:
                    zone.skipped += 1
                self._last_zone_sample[zid] = (now, t_curr)
                continue
            dt_h = (now - prev[0]).total_seconds() / 3600.0
            if dt_h >= 0.24:
                heat_kw = sum(
                    self.get_device_live_power_kw(e) * float(self.device_cfg(e).get(CONF_HEATING_COEFFICIENT, 1.0))
                    for e in zone_devs
                )
                zone.update_rls(prev[1], t_curr, t_out, heat_kw, dt_h, window_open=False, now=now)
                self._last_zone_sample[zid] = (now, t_curr)

        for eid in self.device_entries:
            d = self.device_cfg(eid)
            if d.get(CONF_DEVICE_TYPE) != DeviceType.CLIMATE_HOTWATER:
                continue
            water = self._read_float(d.get(CONF_WATER_TEMP_SENSOR))
            # hot-water model learns when the tank has a temperature AND a power sensor
            if d.get(CONF_WATER_TEMP_SENSOR) and d.get(CONF_POWER_SENSOR):
                tank = self.tanks.get(eid)
                if tank is None:
                    nominal = float(d.get(CONF_NOMINAL_POWER_KW) or 2.0)
                    tank = self.tanks[eid] = HotWaterModel(
                        device_id=eid, volume_l=float(d.get(CONF_TANK_VOLUME_L) or 200.0),
                        seed_daily_kwh=float(d.get(CONF_DAILY_QUOTA_HOURS) or 3.0) * nominal * 0.85)
                tank.volume_l = float(d.get(CONF_TANK_VOLUME_L) or tank.volume_l)
                tank.update(now, water, self._read_float(d.get(CONF_POWER_SENSOR)),
                            self.is_workday(now.date()))
            reserved = self.plan_result.legionella_hours.get(eid) or []
            if water is not None:
                hot = water >= HOTWATER_LEGIONELLA_C
            else:
                # No sensor: count the reserved cycle as done when the element actually ran at power
                hot = 0 in reserved and self.get_device_live_power_kw(eid) > 0.5 * float((d.get(CONF_NOMINAL_POWER_KW) or 2.0))
            self._legionella_high_minutes[eid] = (self._legionella_high_minutes.get(eid, 0.0) + 1.0) if hot else 0.0
            if self._legionella_high_minutes[eid] >= 120.0:
                self.store.data.setdefault("legionella_last_ok", {})[eid] = now.isoformat()
                self._legionella_high_minutes[eid] = 0.0
                self.hass.async_create_task(self.async_run_planner())
        self.schedule_save()

    # ------------------------------------------------------------------
    def own_entity_id(self, domain: str, unique_id: str) -> str | None:
        """entity_id of one of our own entities (cached; follows user renames)."""
        key = (domain, unique_id)
        if self._entity_cache.get(key) is None:
            self._entity_cache[key] = er.async_get(self.hass).async_get_entity_id(domain, DOMAIN, unique_id)
        return self._entity_cache[key]

    def device_attributes(self, eid: str) -> dict[str, Any]:
        """Single attribute contract used by sensor.pc_<slug>_plan and the Lovelace cards.

        Time series are 24 h windows starting at the CURRENT hour (``window_start``); the full
        72-hour arrays are kept for the overview panel.
        """
        d = self.device_cfg(eid)
        dtype = d.get(CONF_DEVICE_TYPE)
        alloc = self.allocations.get(eid)
        live = self.get_device_live_power_kw(eid)
        p1, p2 = phase_pair_conductors(d.get(CONF_PHASE_PAIR, PhasePair.L1_L2))
        amps = round(power_kw_to_it_phase_amps(live), 2)
        # time until the next (non-urgent) command may be sent – command patience
        last = (self._last_ocpp_cmd_ts.get(eid) if dtype == DeviceType.EV_CHARGER else None) or self._last_switch_ts.get(eid)
        hold_s = 0
        if last is not None:
            min_s = MIN_RELAY_SWITCH_SECONDS if dtype == DeviceType.SWITCH else self.command_interval_s(eid)
            hold_s = max(0, int(min_s - (dt_util.now() - last).total_seconds()))
        now = dt_util.now()
        committed = self.committed.get(eid, False)
        pnow = self.priority_now.get(eid, False)
        plan = self.plans_72h.get(eid, [None] * PLANNING_HORIZON_HOURS)
        setpoints = self.plan_result.setpoints.get(eid)
        status = alloc.status.value if alloc else DistStatus.UNCOMMITTED.value
        reason = alloc.reason if alloc else None
        window_start = now.replace(minute=0, second=0, microsecond=0)

        def cfg_value(key: str) -> float | None:
            return self._read_float(d.get(key))

        entities = {
            "plan": self.own_entity_id("sensor", f"{eid}_plan"),
            "committed": self.own_entity_id("switch", f"{eid}_committed"),
            "priority_now": self.own_entity_id("switch", f"{eid}_priority_now"),
            "plan_priority": self.own_entity_id("number", f"{eid}_plan_priority"),
            "control": d.get(CONF_TARGET_ENTITY),
            "comfort_temp": d.get(CONF_COMFORT_TEMP_ENTITY),
            "eco_temp": d.get(CONF_ECO_TEMP_ENTITY),
            "nominal_temp": d.get(CONF_NOMINAL_TEMP_ENTITY),
            "min_temp": d.get(CONF_MIN_TEMP_ENTITY),
            "room_temp": d.get(CONF_ROOM_TEMP_SENSOR),
            "water_temp": d.get(CONF_WATER_TEMP_SENSOR),
            "power": d.get(CONF_POWER_SENSOR),
        }
        extra: dict[str, Any] = {}
        if dtype == DeviceType.CLIMATE_HEATING:
            extra = {"current_temp": cfg_value(CONF_ROOM_TEMP_SENSOR), "comfort_temp": cfg_value(CONF_COMFORT_TEMP_ENTITY),
                     "eco_temp": cfg_value(CONF_ECO_TEMP_ENTITY), "comfort_during": d.get(CONF_COMFORT_DURING),
                     "heater_kind": d.get(CONF_HEATER_KIND, HEATER_ON_OFF)}
        elif dtype == DeviceType.CLIMATE_HOTWATER:
            extra = {"current_temp": cfg_value(CONF_WATER_TEMP_SENSOR), "nominal_temp": cfg_value(CONF_NOMINAL_TEMP_ENTITY),
                     "min_temp": cfg_value(CONF_MIN_TEMP_ENTITY),
                     "legionella_last_ok": self.store.data.get("legionella_last_ok", {}).get(eid)}
        elif dtype == DeviceType.EV_CHARGER:
            fin = self.finish_by.get(eid)
            dl_on = self.ev_deadline_enabled.get(eid, True)
            min_on = self.ev_min_soc_enabled.get(eid, True)
            min_dl = ev_min_soc_deadline(self.ev_session_start.get(eid), self.ev_min_since.get(eid)) if min_on else None
            entities.update(target_soc=d.get(CONF_EV_TARGET_SOC_ENTITY) or self.own_entity_id("number", f"{eid}_target_soc"),
                            finish_by=self.own_entity_id("datetime", f"{eid}_finish_by"),
                            deadline_enabled=self.own_entity_id("switch", f"{eid}_deadline_enabled"),
                            min_soc_enabled=self.own_entity_id("switch", f"{eid}_min_soc_enabled"),
                            min_soc=self.own_entity_id("number", f"{eid}_min_soc"),
                            soc=d.get(CONF_EV_SOC_SENSOR), connected=d.get(CONF_EV_CONNECTED_SENSOR),
                            car_current_limit=d.get(CONF_EV_CAR_LIMIT_ENTITY),
                            connector_status=d.get(CONF_EV_STATUS_SENSOR),
                            offered_current=d.get(CONF_EV_OFFERED_SENSOR),
                            session_energy=d.get(CONF_EV_SESSION_ENERGY_SENSOR),
                            charging_current=d.get(CONF_EV_CURRENT_SENSOR),
                            charge_switch=d.get(CONF_EV_CHARGE_SWITCH),
                            transaction=d.get(CONF_EV_TRANSACTION_SENSOR),
                            availability_switch=d.get(CONF_EV_AVAILABILITY_SWITCH))
            extra = {"soc": cfg_value(CONF_EV_SOC_SENSOR), "target_soc": self.ev_target_soc(eid),
                     "target_soc_source": "car" if d.get(CONF_EV_TARGET_SOC_ENTITY) else "power_controller",
                     "transaction_id": self.ev_transaction_id(d),
                     "last_command_kind": self._last_ev_cmd_kind.get(eid),
                     "last_action": self._ev_last_action.get(eid),
                     "charge_switch_on": bool(d.get(CONF_EV_CHARGE_SWITCH) and self.hass.states.get(d.get(CONF_EV_CHARGE_SWITCH))
                                              and self.hass.states.get(d.get(CONF_EV_CHARGE_SWITCH)).state == "on"),
                     "finish_by": fin.isoformat() if (fin and dl_on) else None,
                     "deadline_enabled": dl_on,
                     "min_soc_enabled": min_on, "min_soc": self.ev_min_soc.get(eid, 30.0),
                     "min_soc_deadline": min_dl.isoformat() if min_dl else None,
                     "min_soc_unmet_kwh": self.plan_result.ev_min_unmet.get(eid),
                     "session_start": self.ev_session_start.get(eid).isoformat() if self.ev_session_start.get(eid) else None,
                     "connected": self.ev_plugged_in(d),
                     "charging": self.ev_is_charging(eid, d),
                     "connector_status": self.ev_status(d)[0],
                     "connector_status_text": self.ev_status(d)[1],
                     "offered_current_a": cfg_value(CONF_EV_OFFERED_SENSOR),
                     "charging_current_a": cfg_value(CONF_EV_CURRENT_SENSOR),
                     "session_energy_kwh": cfg_value(CONF_EV_SESSION_ENERGY_SENSOR),
                     "car_current_limit_a": cfg_value(CONF_EV_CAR_LIMIT_ENTITY),
                     "max_current_a": self.ev_max_current_a(d),
                     "ev_warnings": list(self.ev_warnings.get(eid, {}).values()),
                     "commanded_current_a": self._last_ev_target_a.get(eid)}
        elif dtype == DeviceType.APPLIANCE:
            s_ = self.appliance_status(eid, now)
            entities.update(committed=None, priority_now=None, **{f"appl_{k}": v for k, v in s_["roles"].items()})
            iso = lambda v: v.isoformat() if v else None  # noqa: E731
            extra = {"appliance_state": s_["state"], "appliance_state_class": s_["state_class"],
                     "program": s_["program"], "program_name": s_["program_name"],
                     "start_at": iso(s_["start_at"]), "finish_at": iso(s_["finish_at"]),
                     "remaining_h": s_["remaining_h"], "progress": s_["progress"],
                     "expected_kwh": s_["expected_kwh"], "expected_duration_h": s_["expected_duration_h"],
                     "program_learned": s_["learned"], "expected_source": s_["source"],
                     "programs_known": len(self.appliances[eid].programs) if eid in self.appliances else 0}
        elif dtype == DeviceType.SWITCH:
            entities.update(runtime_hours=self.own_entity_id("number", f"{eid}_runtime_hours"))
            extra = {"runtime_required_h": self.runtime_hours.get(eid, 2.0),
                     "runtime_today_h": round(self._day_runtime_h.get(eid, 0.0), 2)}

        return {
            "entry_id": eid,
            "name": self.device_entries[eid].title,
            "device_type": dtype,
            "committed": committed,
            "priority_now": pnow,
            "plan_priority": self.plan_priority.get(eid, 5),
            "dist_priority": self.dist_priority.get(eid, 5),
            "dist_status": status,
            "dist_reason": reason,
            "ui_status": ui_status(committed, pnow, status if committed else None, reason, live, self.warning_active),
            "allocated_kw": alloc.allowed_kw if alloc else 0.0,
            "lent_kw": round(alloc.lent_kw, 3) if alloc else 0.0,
            "live_kw": round(live, 3),
            "nominal_kw": float((d.get(CONF_NOMINAL_POWER_KW) or 0.0)),
            "phase_pair": d.get(CONF_PHASE_PAIR, PhasePair.L1_L2),
            "phase_currents": {"L1": 0.0, "L2": 0.0, "L3": 0.0} | {p1: amps, p2: amps},
            "hold_remaining_s": hold_s,
            "window_start": window_start.isoformat(),
            "plan_24h": plan_window(plan, self.plan_start, now, 24),
            "setpoint_24h": plan_window(setpoints, self.plan_start, now, 24) if setpoints else None,
            "predicted_temp_24h": plan_window(self.plan_result.predicted_temps.get(eid), self.plan_start, now, 24)
            if self.plan_result.predicted_temps.get(eid) else None,
            # from the 15-minute plan: "next 21:30" / "planned now" instead of the hour start
            "next_action": next_action(self.plans_15m.get(eid) or plan,
                                       self.plan_slots.setpoints.get(eid) if self.plans_15m.get(eid) else setpoints,
                                       self.plan_start, now, 1.0 / max(1, self.plan_spp) if self.plans_15m.get(eid) else 1.0),
            "has_power_sensor": bool(d.get(CONF_POWER_SENSOR)),
            "power_source": self.device_power(eid)[1],
            "plan_start": self.plan_start.isoformat() if self.plan_start else None,
            "plan_15m_24h": (self.plans_15m.get(eid) or [])[self.slot_idx(now) - self.slot_idx(now) % self.plan_spp:][:24 * self.plan_spp],
            "slots_per_hour": self.plan_spp,
            "last_command": self._last_command_info(eid),
            "plan_72h": plan,
            "setpoint_72h": setpoints,
            "predicted_temp_72h": self.plan_result.predicted_temps.get(eid),
            "commanded_setpoint": self.commanded_setpoint.get(eid),
            "legionella_hours": self.plan_result.legionella_hours.get(eid),
            "unmet": self.plan_result.unmet.get(eid),
            "energy_today_kwh": round(self._day_energy_kwh.get(eid, 0.0), 3),
            "entities": entities,
            "forecast_only": eid in self.plan_result.forecast_only,
            "governor": self.distributor.pi_diag.get(eid) if d.get(CONF_ENABLE_PI_GOVERNOR) else None,
            "governor_enabled": bool(d.get(CONF_ENABLE_PI_GOVERNOR)),
            **extra,
        }

    def _aligned_15m(self, arr: list | None, start: datetime) -> list:
        """15-minute series aligned with the plan start (288 entries)."""
        ps = self.price_series
        if not arr or ps is None or not ps.timestamps:
            return []
        off = int((start - ps.timestamps[0]).total_seconds() // 900)
        return [arr[off + i] if 0 <= off + i < len(arr) else None for i in range(PLANNING_HORIZON_HOURS * 4)]

    def _settings_entities(self) -> list[dict]:
        """Groups of the hub's own editable entities for the panel's Settings page."""
        hub = self.hub_entry.entry_id

        def ent(domain: str, key: str) -> str | None:
            return self.own_entity_id(domain, f"{hub}_{key}")

        groups = [
            ("Price", [("select", "price_model"), ("number", "norgespris"), ("number", "norgespris_volume"),
                       ("number", "supplier_markup"), ("number", "support_threshold"), ("number", "support_rate"),
                       ("number", "vat"), ("select", "tariff"), ("select", "shadow_model"), ("number", "shadow_extra_fee")]),
            ("Grid tariff (active price list)", [("number", "grid_default"), ("number", "grid_fee")]),
            ("Times (schedule)", [("time", "workday_wake"), ("time", "workday_leave"), ("time", "workday_back"),
                                  ("time", "workday_sleep"), ("time", "free_day_wake"), ("time", "free_day_sleep")]),
            ("House", [("select", "mode"), ("select", "occupancy_select"), ("select", "priority_profile"),
                       ("datetime", "arrival_time"), ("datetime", "departure_time"),
                       ("number", "baseload_24h"), ("number", "baseload_daytime")]),
            ("Limits & command patience", [("number", "internal_limit"), ("number", "cmd_interval"), ("number", "regulated_interval"),
                                           ("number", "ev_min_step"), ("number", "setpoint_min_step"),
                                           ("number", "ev_delay_penalty")]),
        ]
        version = self.price_engine.tariff_version()
        out = []
        for title, items in groups:
            ids = [{"id": ent(dom, key), "key": key} for dom, key in items]
            if title.startswith("Grid tariff"):
                ids += [{"id": self.own_entity_id("number", f"{hub}_grid_period_{p.id}"), "key": "grid_period",
                         "label": f"Grid price {p.name}"} for p in version.periods]
            out.append({"title": title, "entities": [i for i in ids if i["id"]]})
        return out

    def _last_command_info(self, eid: str) -> dict | None:
        ts = self._last_ocpp_cmd_ts.get(eid) or self._last_switch_ts.get(eid)
        if ts is None:
            return None
        val = self._last_ev_target_a.get(eid)
        return {"ts": ts.isoformat(), "value": val if val is not None else self.commanded_setpoint.get(eid),
                "unit": "A" if val is not None else "°C"}

    def overview_payload(self) -> dict[str, Any]:
        """Everything the Power Controller overview panel shows, in one JSON message
        (websocket ``power_controller/overview``). Read-only."""
        from .const import PRICE_MODEL_LABELS

        now = dt_util.now()
        pe = self.price_engine
        ps = self.price_series
        pidx = self.plan_idx(now)
        start = self.plan_start or now.replace(minute=0, second=0, microsecond=0)
        horizon = PLANNING_HORIZON_HOURS

        price_now: dict[str, Any] = {}
        prices_72h: list[float | None] = []
        spot_72h: list[float | None] = []
        energy_72h: list[float | None] = []
        periods_72h: list[str | None] = []
        if ps is not None and ps.timestamps:
            off = int((start - ps.timestamps[0]).total_seconds() // 3600)

            def aligned(arr: list[Any]) -> list[Any]:
                return [arr[off + t] if 0 <= off + t < len(arr) else None for t in range(horizon)]

            prices_72h = aligned(ps.total_price_72h)
            vat = 1 + pe.vat_percent / 100.0
            spot_72h = [None if v is None else round(v * vat, 4) for v in aligned(ps.raw_spot_72h)]
            energy_72h = [None if v is None else round((v + pe.supplier_markup_nok) * vat, 4) for v in aligned(ps.spot_after_support_72h)]
            periods_72h = aligned(ps.grid_period_72h) if ps.grid_period_72h else []
            i = plan_index(ps.timestamps[0], now)
            if i < len(ps.total_price_72h):
                price_now = {
                    "total": ps.total_price_72h[i],
                    "energy": round(ps.spot_after_support_72h[i] * vat, 4),
                    "grid": round((ps.total_price_72h[i] - ps.spot_after_support_72h[i] * vat), 4),
                    "period": ps.grid_period_72h[i] if ps.grid_period_72h else None,
                }

        version = pe.tariff_version(now.date())
        vatm = 1 + pe.vat_percent / 100.0
        today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tariff_today = []
        for h in range(24):
            ts = today0 + timedelta(hours=h)
            grid, fee, name = pe.grid_price_at(ts, self.is_workday(ts.date()))
            tariff_today.append({"hour": h, "price": round((grid + fee) * vatm, 4), "period": name})
        steps = sorted(version.capacity_steps, key=lambda st: (st.upper_kw <= 0, st.upper_kw))
        cap_steps = [{"upper_kw": st.upper_kw, "monthly_nok": round(version._ex_vat(st.monthly_nok, pe.vat_percent) * vatm, 0)}
                     for st in steps]

        occ_days = []
        for offset in range(7):
            day = (now + timedelta(days=offset)).date()
            base = today0 + timedelta(days=offset)
            sw = None
            if offset < 2:
                sw = self.own_entity_id("switch", f"{self.hub_entry.entry_id}_home_office_{'today' if offset == 0 else 'tomorrow'}")
            occ_days.append({
                "date": day.isoformat(),
                "workday": self.is_workday(day),
                "home_office": self.occupancy.is_home_office(day, True),
                "switch": sw,
                "quarters": [self.occupancy.state_at(base + timedelta(minutes=15 * q), self.is_workday) for q in range(96)],
            })

        rated = self.limiters.effective_phase_limits_a()
        shadow = None
        if self.shadow.enabled and self.shadow.month:
            shadow = self.shadow.month_summary(pe)
            shadow["rolling_12_months"] = self.shadow.rolling_12m(pe)

        cfg = self._hub_cfg()
        from .const import (
            CONF_VOLTAGE_L12_SENSOR, CONF_VOLTAGE_L23_SENSOR, CONF_VOLTAGE_L31_SENSOR, OCCUPANCY_LABELS,
        )
        # live flow (distribution module): grid -> limiters -> distribution -> devices
        oa = self.limiters.oa.snapshot()
        committed_live = sum(self.get_device_live_power_kw(e) for e in self.device_entries if self.committed.get(e))
        uncommitted_live = sum(self.get_device_live_power_kw(e) for e in self.device_entries if not self.committed.get(e))
        flow = {
            "grid_kw": round(self.grid_power_kw, 3),
            "phase_a": {ph: round(self.phase_currents.get(ph, 0.0), 2) for ph in ("L1", "L2", "L3")},
            "grid_limiter": {
                "internal_a": self.limiters.internal_limit_a,
                "external_a": self.limiters.external_limit_a,
                "rated_a": rated,
                "binding": "external" if (self.limiters.external_limit_a and self.limiters.external_limit_a
                                          < self.limiters.internal_limit_a) else "internal",
            },
            "tariff_limiter": {
                "hour_allowance_kwh": self.hour_allowance_kwh,
                "energy_this_hour_kwh": round(self.energy_this_hour_kwh, 3),
                "limit_now_kw": self.limit_now_kw,
                "safe_mode": self.safe_mode_active,
            },
            "overload": oa,
            "baseload_kw": round(max(0.0, self.grid_power_kw - committed_live - uncommitted_live), 3),
            "uncommitted_kw": round(uncommitted_live, 3),
            "committed_kw": round(committed_live, 3),
            "distribution": self.distributor.last_summary,
            "edges": self.distributor.last_edges,
        }
        history_entities = {
            "grid_power": cfg.get(CONF_ACTIVE_POWER_SENSOR),
            "currents": {ph: cfg.get(k) for ph, k in (("L1", CONF_CURRENT_L1_SENSOR), ("L2", CONF_CURRENT_L2_SENSOR), ("L3", CONF_CURRENT_L3_SENSOR))},
            "voltages": {lbl: cfg.get(k) for lbl, k in (("L1-L2", CONF_VOLTAGE_L12_SENSOR), ("L2-L3", CONF_VOLTAGE_L23_SENSOR), ("L3-L1", CONF_VOLTAGE_L31_SENSOR))},
            "devices": {eid: self.own_entity_id("sensor", f"{eid}_power") for eid in self.device_entries},
        }
        devices = []
        for eid in self.device_entries:
            a = self.device_attributes(eid)
            devices.append({k: a.get(k) for k in (
                "entry_id", "name", "device_type", "committed", "priority_now", "plan_priority", "dist_priority",
                "ui_status", "live_kw", "allocated_kw", "lent_kw", "nominal_kw", "phase_pair", "next_action", "hold_remaining_s",
                "current_temp", "comfort_temp", "eco_temp", "nominal_temp", "soc", "target_soc", "finish_by",
                "runtime_required_h", "runtime_today_h", "energy_today_kwh", "entities", "forecast_only",
                "governor", "governor_enabled", "dist_status", "dist_reason", "has_power_sensor", "power_source")})
            devices[-1]["plan_72h"] = self.plans_72h.get(eid)
            devices[-1]["heater_kind"] = a.get("heater_kind")
            devices[-1]["deadline_enabled"] = a.get("deadline_enabled")
            devices[-1]["min_soc"] = a.get("min_soc") if a.get("min_soc_enabled") else None
            devices[-1]["last_command"] = a.get("last_command")

        return {
            "now": now.isoformat(),
            "version": VERSION,
            "mode": self.mode.value,
            "occupancy": self.occupancy_state_now(),
            "occupancy_selected": self.occupancy_selected,
            "occupancy_scheduled": self.occupancy_scheduled_now(),
            "occupancy_labels": OCCUPANCY_LABELS,
            "entities": {
                "occupancy_select": self.own_entity_id("select", f"{self.hub_entry.entry_id}_occupancy_select"),
                "mode_select": self.own_entity_id("select", f"{self.hub_entry.entry_id}_mode"),
                "shadow_select": self.own_entity_id("select", f"{self.hub_entry.entry_id}_shadow_model"),
            },
            "norgespris": pe.norgespris_status(now, self.energy_this_hour_kwh),
            "flow": flow,
            "history_entities": history_entities,
            "plan_history": self.plan_history[-168:],
            "price_model": PRICE_MODEL_LABELS.get(pe.price_model, str(pe.price_model)),
            "plan": {
                "start": start.isoformat(),
                "index_now": pidx,
                "solved": self.last_plan_ts.isoformat() if self.last_plan_ts else None,
                "solver": self.plan_result.solver,
                "solve_ms": self.plan_result.solve_ms,
                "baseload_72h": self.baseload_72h,
                "price_72h": prices_72h,
                "spot_72h": spot_72h,
                "energy_72h": energy_72h,
                "grid_period_72h": periods_72h,
                "hour_cap_72h": pe.hourly_cap_series_72h(start),
                "uncontrolled_72h": self.plan_result.uncontrolled_72h,
                "occupancy_72h": [OccupancyProfile.dominant_state(x) for x in self.occ_hour_states],
                "slots_per_hour": self.plan_spp,
                "price_15m": self._aligned_15m(ps.total_price_15m if ps else None, start),
                "spot_15m": [None if v is None else round(v * (1 + pe.vat_percent / 100.0), 4)
                             for v in self._aligned_15m(ps.raw_spot_15m if ps else None, start)],
            },
            "control": self.control.to_dict(),
            "urgent": self.urgent_now(),
            "settings": self._settings_entities(),
            "thermal_zones": self.thermal_zones_payload(),
            "hotwater_models": self.hotwater_models_payload(),
            "appliances": self.appliances_payload(),
            "tariff_editor": self.tariff_editor_payload(),
            "kpi": {
                "grid_power_kw": round(self.grid_power_kw, 3),
                "limit_now_kw": self.limit_now_kw,
                "energy_this_hour_kwh": round(self.energy_this_hour_kwh, 3),
                "energy_projected_kwh": self.energy_projected_kwh,
                "hour_allowance_kwh": self.hour_allowance_kwh,
                "active_step_kw": pe.active_tariff_kw,
                "selected_step_kw": pe.selected_tariff_kw,
                "top3_mean_kw": round(pe.monthly_max_kw, 3),
                "capacity_charge_nok": pe.capacity_charge_nok(),
                "price_now": price_now,
                "month_cost_nok": round(pe.monthly_cost_nok, 2),
                "month_energy_kwh": round(pe.monthly_energy_kwh, 2),
                "shadow_benefit_nok": shadow["benefit_nok"] if shadow else None,
                "warning": self.warning_active,
                "safe_mode": self.safe_mode_active,
                "safe_mode_sensor_ok": self.safe_mode_sensor_ok,
                "startup_wait": self.startup_wait,
                "safe_mode_latched": self._safe_mode_hour is not None,
                "safe_mode_since": self._safe_mode_since.isoformat() if self._safe_mode_since else None,
                "step_locked": pe.active_tariff_kw > pe.selected_tariff_kw,
                "hour_energy_source": self.hour_energy_source,
                "p_avg_60m_kw": self.p_avg_60m_kw,
                "month_source": self.month_source,
            },
            "notifications": {"service": self.notifier.settings.service, "log": self.notifier.log[-10:],
                              "active": sorted(self.notifier.active)},
            "phases": {
                ph: {"amps": round(self.phase_currents.get(ph, 0.0), 2), "rated_a": rated.get(ph),
                     **self.limiters.oa.snapshot().get(ph, {})}
                for ph in ("L1", "L2", "L3")
            },
            "devices": devices,
            "occupancy_days": occ_days,
            "shadow": shadow,
            "tariff": {"name": version.name, "valid_from": version.valid_from, "today": tariff_today,
                       "capacity_steps": cap_steps},
        }

    def _build_state_snapshot(self) -> dict[str, Any]:
        return {
            "grid_power_kw": round(self.grid_power_kw, 3),
            "limit_now_kw": self.limit_now_kw,
            "hour_allowance_kwh": self.hour_allowance_kwh,
            "p_avg_60m_kw": self.p_avg_60m_kw,
            "energy_this_hour_kwh": round(self.energy_this_hour_kwh, 3),
            "energy_projected_kwh": self.energy_projected_kwh,
            "monthly_max_kw": round(self.price_engine.monthly_max_kw, 3),
            "monthly_energy_kwh": round(self.price_engine.monthly_energy_kwh, 3),
            "monthly_cost_nok": round(self.price_engine.monthly_cost_nok, 2),
            "current_l1": round(self.phase_currents["L1"], 2),
            "current_l2": round(self.phase_currents["L2"], 2),
            "current_l3": round(self.phase_currents["L3"], 2),
            "capacity_charge_nok": self.price_engine.capacity_charge_nok(),
            "occupancy": self.occupancy_state_now(),
            "overload": self.limiters.oa.snapshot(),
            "warning": self.warning_active,
            "safe_mode": self.safe_mode_active,
        }


def async_track_interval(hass: HomeAssistant, action, interval: timedelta):
    """Small alias kept for readability in async_initialize."""
    return async_track_time_interval(hass, action, interval)


def get_hub_coordinator(hass: HomeAssistant) -> PowerControllerCoordinator | None:
    """Return the (single) hub coordinator, or None if the hub is not set up yet."""
    for val in hass.data.get(DOMAIN, {}).values():
        if isinstance(val, PowerControllerCoordinator):
            return val
    return None
