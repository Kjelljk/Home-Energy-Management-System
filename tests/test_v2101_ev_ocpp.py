"""2.10.1/2.10.2: OCPP TxProfile on the active transaction, EV start at minimum current."""
import json

from custom_components.power_controller.actuation import ocpp_charge_rate_data, parse_transaction_id
from custom_components.power_controller.const import PhasePair
from custom_components.power_controller.distributor import PowerDistributor


def test_transaction_id_parsing():
    assert parse_transaction_id("1234") == 1234
    assert parse_transaction_id("1234.0") == 1234
    for s in (None, "", "unknown", "unavailable", "0", "abc"):
        assert parse_transaction_id(s) is None


def test_tx_profile_matches_owner_automation():
    data = ocpp_charge_rate_data(16, 1, 4711)
    assert data["conn_id"] == 1 and "limit_amps" not in data
    assert json.loads(data["custom_profile"]) == {
        "transactionId": 4711, "chargingProfileId": 1, "stackLevel": 0,
        "chargingProfilePurpose": "TxProfile", "chargingProfileKind": "Relative",
        "chargingSchedule": {"chargingRateUnit": "A", "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 16}]},
    }


def test_fallback_without_transaction_and_stop():
    assert ocpp_charge_rate_data(10, 2, None) == {"limit_amps": 10, "conn_id": 2}
    stop = json.loads(ocpp_charge_rate_data(0, 1, 9)["custom_profile"])
    assert stop["chargingSchedule"]["chargingSchedulePeriod"][0]["limit"] == 0


def _ff(prev, ramp=True, max_a=21):
    return PowerDistributor(ev_ramp_up_step_a=2.0).compute_ev_feedforward_amps(
        prev_target_a=prev, ev_live_power_kw=0.0, ev_phase_pair=PhasePair.L1_L2,
        grid_phase_currents_a={"L1": 3.0, "L2": 3.0, "L3": 3.0}, phase_limits_a={"L1": 32, "L2": 32, "L3": 32},
        tariff_limit_now_kw=10.0, house_non_ev_kw=0.5, min_ev_a=6, max_ev_a=max_a, ramp=ramp)


def test_start_from_zero_never_below_minimum():
    # 2.10.1 bug: prev 0 + ramp step 2 -> 2 A (below the 6 A minimum, charger never starts)
    assert _ff(0) == 8                 # minimum + one ramp step
    assert _ff(0, ramp=False) == 21    # coordinator: patience instead of ramp -> granted current
    assert _ff(10, ramp=True) == 12    # normal upward slew unchanged
