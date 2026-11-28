"""Constants, enumerations, and 230V IT electrical helpers for Power Controller."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
try:
    from enum import StrEnum
except ImportError:
    class StrEnum(str, Enum):
        pass

DOMAIN = "power_controller"

VERSION = "2.12.0"  # keep in sync with manifest.json (used for frontend cache busting)


PLATFORMS: list[str] = [
    "sensor",
    "binary_sensor",
    "switch",
    "number",
    "select",
    "datetime",
    "time",
]

# Entry types inside ConfigEntry.data["entry_type"]
ENTRY_TYPE_HUB = "hub"
ENTRY_TYPE_DEVICE = "device"
ENTRY_TYPE_ZONE = "thermal_zone"

# Grid electrical constants (Norwegian 230V IT/TT delta system)
NOMINAL_VOLTAGE_IT = 230.0
PLANNING_HORIZON_HOURS = 72
TARIFF_BUFFER_KWH = 0.15
SAFE_MODE_MAX_KW = 3.0

# Anti-cycling & OCPP guardrails
MIN_CLIMATE_SWITCH_SECONDS = 300   # 5 minutes
MIN_RELAY_SWITCH_SECONDS = 120     # 2 minutes
MIN_EV_PAUSE_SECONDS = 600         # 10 minutes
MIN_OCPP_COMMAND_INTERVAL_S = 15   # 15 seconds between set_charge_rate calls
SENSOR_STALE_DEFAULT_S = 120       # 2 minutes stale sensor fallback

# Tariff tiers (Norwegian kapasitetsledd steps in kW)
# Capacity steps (kapasitetsledd) are NOT hard-coded: they come from the user-editable
# grid tariff (grid_tariff.py). The tariff select shows the upper bound of the step the
# user wants to stay inside ("5 kW" = stay in the 2–5 kW step).

# Planner
PLANNER_TIME_LIMIT_S = 20.0        # hard cap on solver wall time
MAX_PREHEAT_C = 2.0                # max degrees above target the planner may pre-heat a zone
HOTWATER_AMBIENT_C = 20.0
HOTWATER_LEGIONELLA_C = 70.0
HOTWATER_LEGIONELLA_HOURS = 2


class GridMode(StrEnum):
    """House operating mode."""
    HOME = "Home"
    AWAY = "Away"
    ARRIVING = "Arriving"


class PriceModel(StrEnum):
    """Household energy price regime."""
    NORGESPRIS = "norgespris"
    SPOT_SUPPORT = "spot_support"   # spot + strømstøtte
    SPOT = "spot"


PRICE_MODEL_LABELS: dict[str, str] = {
    PriceModel.NORGESPRIS: "Norgespris",
    PriceModel.SPOT_SUPPORT: "Spot + electricity support",
    PriceModel.SPOT: "Spot",
}


class PriorityProfile(StrEnum):
    """Preset priority profiles."""
    NORMAL = "Normal"
    COMFORT = "Comfort"
    EV_BOOST = "EV Boost"
    ECONOMY = "Economy"


class DeviceType(StrEnum):
    """Controllable load types."""
    EV_CHARGER = "ev_charger"
    CLIMATE_HEATING = "climate_heating"
    CLIMATE_HOTWATER = "climate_hotwater"
    SWITCH = "switch"


class PhasePair(StrEnum):
    """230V IT delta conductor pairs."""
    L1_L2 = "L1-L2"
    L2_L3 = "L2-L3"
    L1_L3 = "L1-L3"


class DistStatus(StrEnum):
    """Real-time distribution state per object."""
    PRIORITIZED = "Prioritized"
    REDISTRIBUTING = "Redistributing"
    HOLD = "Hold"
    UNCOMMITTED = "Uncommitted"


class InsulationClass(StrEnum):
    """Building envelope presets for seeding 1R1C thermal parameters."""
    OLD = "Old"
    STANDARD = "Standard"
    TEK17 = "TEK17"


# Config keys - Hub sensors
CONF_ACTIVE_POWER_SENSOR = "active_power_sensor"
CONF_MONTHLY_ENERGY_SENSOR = "monthly_energy_sensor"
CONF_CURRENT_L1_SENSOR = "current_l1_sensor"
CONF_CURRENT_L2_SENSOR = "current_l2_sensor"
CONF_CURRENT_L3_SENSOR = "current_l3_sensor"
CONF_NORDPOOL_SENSOR = "nordpool_sensor"
CONF_WORKDAY_SENSOR = "workday_sensor"
CONF_OUTDOOR_TEMP_SENSOR = "outdoor_temp_sensor"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_EXTERNAL_LIMIT_SENSOR = "external_limit_sensor"
CONF_SENSOR_TIMEOUT_S = "sensor_timeout_s"
CONF_HOUR_ENERGY_SENSOR = "hour_energy_sensor"     # optional kWh utility meter that resets every hour
CONF_NOTIFY_SERVICE = "notify_service"              # e.g. notify.mobile_app_pixel
CONF_NOTIFY_QUIET_START = "notify_quiet_start"
CONF_NOTIFY_QUIET_END = "notify_quiet_end"
CONF_VOLTAGE_L12_SENSOR = "voltage_l1_l2_sensor"
CONF_VOLTAGE_L23_SENSOR = "voltage_l2_l3_sensor"
CONF_VOLTAGE_L31_SENSOR = "voltage_l3_l1_sensor"

# Config keys - Overload Acceptance (minutes / ratio), hub options
CONF_OA_DELAY_5 = "oa_delay_5_min"
CONF_OA_DELAY_20 = "oa_delay_20_min"
CONF_OA_RAMP_5 = "oa_ramp_5_min"
CONF_OA_RAMP_20 = "oa_ramp_20_min"
CONF_OA_RECOVER_FAST = "oa_recover_fast_min"
CONF_OA_RECOVER_SLOW = "oa_recover_slow_min"
CONF_OA_HARD_RATIO = "oa_hard_ratio"
OA_CONF_MAP: dict[str, str] = {
    "delay_5_min": CONF_OA_DELAY_5,
    "delay_20_min": CONF_OA_DELAY_20,
    "ramp_5_min": CONF_OA_RAMP_5,
    "ramp_20_min": CONF_OA_RAMP_20,
    "recover_fast_min": CONF_OA_RECOVER_FAST,
    "recover_slow_min": CONF_OA_RECOVER_SLOW,
    "hard_ratio": CONF_OA_HARD_RATIO,
}

# Config keys - Thermal Zone
CONF_ZONE_ID = "zone_id"
CONF_ZONE_NAME = "zone_name"
CONF_ZONE_AREA_M2 = "zone_area_m2"
CONF_INSULATION_CLASS = "insulation_class"
CONF_WINDOW_SENSOR = "window_sensor"

# Config keys - Common Device
CONF_DEVICE_TYPE = "device_type"
CONF_PHASE_PAIR = "phase_pair"
CONF_NOMINAL_POWER_KW = "nominal_power_kw"
CONF_NOMINAL_CURRENT_A = "nominal_current_a"
CONF_TARGET_ENTITY = "target_entity"
CONF_POWER_SENSOR = "power_sensor"
CONF_SCHEDULE_ENTITY = "schedule_entity"
CONF_HEATING_COEFFICIENT = "heating_coefficient"
# Lending margin for stepless heaters: keep max(0.2 kW, 15 % of the grant) as headroom
LEND_MARGIN_KW = 0.2
LEND_MARGIN_FRACTION = 0.15
CONF_HEATER_KIND = "heater_kind"     # how the device delivers power (information for PC)
HEATER_STEPLESS = "stepless"         # modulates its power (heat pump, inverter)
HEATER_ON_OFF = "on_off"             # full power or nothing (panel heater, cable, element)

# Config keys - EV Charger
CONF_EV_AVAILABILITY_SWITCH = "ev_availability_switch"
CONF_EV_CHARGE_SWITCH = "ev_charge_switch"
CONF_EV_TRANSACTION_SENSOR = "ev_transaction_sensor"
CONF_EV_TARGET_SOC_ENTITY = "ev_target_soc_entity"
CONF_EV_STATUS_SENSOR = "ev_status_sensor"                 # OCPP connector status (Charging, SuspendedEV, …)
CONF_EV_OFFERED_SENSOR = "ev_offered_current_sensor"       # current offered to the EV [A]
CONF_EV_SESSION_ENERGY_SENSOR = "ev_session_energy_sensor" # energy this session [kWh]
CONF_EV_CAR_LIMIT_ENTITY = "ev_car_current_limit_entity"   # car-side ampere limiter (number/input_number)

# OCPP connector status -> readable text and whether energy flows / the car is plugged in
EV_STATUS_TEXT: dict[str, tuple[str, bool, bool]] = {
    # key (lower case): (text, charging, plugged in)
    "available": ("Not connected", False, False),
    "preparing": ("Connected – waiting to start", False, True),
    "charging": ("Charging", True, True),
    "suspendedev": ("Paused by the car", False, True),
    "suspendedevse": ("Paused by the charger", False, True),
    "finishing": ("Finishing", False, True),
    "reserved": ("Reserved", False, False),
    "unavailable": ("Charger unavailable", False, False),
    "faulted": ("Charger fault", False, False),
}   # the car's own charge limit (number/input_number)
CONF_EV_CONNECTED_SENSOR = "ev_connected_sensor"
CONF_EV_SOC_SENSOR = "ev_soc_sensor"
CONF_EV_BATTERY_KWH = "ev_battery_kwh"
CONF_EV_MIN_CURRENT_A = "ev_min_current_a"
CONF_EV_CONN_ID = "ev_conn_id"

# Config keys - Climate Heating & Hotwater
CONF_ROOM_TEMP_SENSOR = "room_temp_sensor"
CONF_WATER_TEMP_SENSOR = "water_temp_sensor"
CONF_COMFORT_TEMP_ENTITY = "comfort_temp_entity"
CONF_ECO_TEMP_ENTITY = "eco_temp_entity"
CONF_OOT_TEMP_ENTITY = "oot_temp_entity"
CONF_NOMINAL_TEMP_ENTITY = "nominal_temp_entity"
CONF_MIN_TEMP_ENTITY = "min_temp_entity"
CONF_ENABLE_PI_GOVERNOR = "enable_pi_governor"
CONF_DAILY_QUOTA_HOURS = "daily_quota_hours"
CONF_TANK_VOLUME_L = "tank_volume_l"
CONF_COMFORT_DURING = "comfort_during"   # occupancy states needing comfort (heating)
CONF_HOME_OFFICE_WEEKDAYS = "home_office_weekdays"

# Service names
SERVICE_SET_PRIORITIES = "set_priorities"
SERVICE_RECALCULATE_PLAN = "recalculate_plan"
SERVICE_RESET_SHADOW = "reset_shadow_evaluation"
SERVICE_SET_HOME_OFFICE = "set_home_office"
SERVICE_THERMAL_MODEL = "thermal_model"
SERVICE_TEST_NOTIFICATION = "test_notification"
SERVICE_HOTWATER_MODEL = "hotwater_model"

OCCUPANCY_LABELS: dict[str, str] = {
    "home_awake": "Home and awake",
    "sleep": "Sleeping",
    "away_work": "Away (work)",
    "home_office": "Home office",
}

SHADOW_MODEL_LABELS: dict[str, str] = {
    "off": "Off",
    "spot_support": "Spot + electricity support",
    "spot": "Spot",
}


def phase_pair_conductors(pair: PhasePair | str) -> tuple[str, str]:
    """Return the two phase conductor names ('L1', 'L2', 'L3') for a 230V IT pair."""
    p = PhasePair(pair)
    if p == PhasePair.L1_L2:
        return ("L1", "L2")
    if p == PhasePair.L2_L3:
        return ("L2", "L3")
    return ("L1", "L3")


def power_kw_to_it_phase_amps(power_kw: float, voltage: float = NOMINAL_VOLTAGE_IT) -> float:
    """Convert active power (kW) to line current (A) on each conductor of a 230V IT pair."""
    if power_kw <= 0.0:
        return 0.0
    return (power_kw * 1000.0) / voltage


def it_phase_amps_to_power_kw(amps: float, voltage: float = NOMINAL_VOLTAGE_IT) -> float:
    """Convert line current (A) on a 230V IT phase pair to active power (kW)."""
    if amps <= 0.0:
        return 0.0
    return (amps * voltage) / 1000.0


@dataclass
class DeviceAllocation:
    """Real-time distribution decision for a single controllable device."""
    entry_id: str
    allowed_kw: float
    allowed_amps: float
    status: DistStatus
    is_lent_capacity: bool = False
    setpoint_override_c: float | None = None
    reason: str = ""
    lent_kw: float = 0.0          # part of the grant that is lent to other devices right now
