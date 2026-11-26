from datetime import datetime, timezone
import pytest

from custom_components.power_controller.const import DeviceType, PhasePair
from custom_components.power_controller.grid_tariff import (
    CapacityStep,
    GridTariffSchedule,
    TariffPeriod,
    TariffVersion,
)


@pytest.fixture
def base_timestamp() -> datetime:
    """Wednesday, Nov 25, 2026 at 08:00 UTC (weekday)."""
    return datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_price_config() -> dict:
    return {
        "norgespris_nok": 0.50,
        "norgespris_volume_kwh": 4000.0,
        "grid_tariff": GridTariffSchedule([
            TariffVersion(
                name="Test", valid_from="2026-01-01", default_price=0.15,
                periods=[TariffPeriod(name="Peak", price=0.40, start_hour=6, end_hour=22)],
                energy_fee=0.16,
                capacity_steps=[CapacityStep(2, 100), CapacityStep(5, 200), CapacityStep(10, 300), CapacityStep(0, 500)],
            )
        ]),
        "vat_percent": 25.0,
    }
