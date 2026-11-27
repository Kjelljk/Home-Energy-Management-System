"""2.10.1: OCPP TxProfile on the active transaction, robust EV options form."""
import json

from custom_components.power_controller.actuation import ocpp_charge_rate_data, parse_transaction_id


def test_transaction_id_parsing():
    assert parse_transaction_id("1234") == 1234
    assert parse_transaction_id("1234.0") == 1234
    for s in (None, "", "unknown", "unavailable", "0", "abc"):
        assert parse_transaction_id(s) is None


def test_tx_profile_matches_owner_automation():
    data = ocpp_charge_rate_data(16, 1, 4711)
    assert data["conn_id"] == 1 and "limit_amps" not in data
    p = json.loads(data["custom_profile"])
    assert p == {
        "transactionId": 4711, "chargingProfileId": 1, "stackLevel": 0,
        "chargingProfilePurpose": "TxProfile", "chargingProfileKind": "Relative",
        "chargingSchedule": {"chargingRateUnit": "A", "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 16}]},
    }


def test_fallback_without_transaction_and_stop():
    assert ocpp_charge_rate_data(10, 2, None) == {"limit_amps": 10, "conn_id": 2}
    stop = json.loads(ocpp_charge_rate_data(0, 1, 9)["custom_profile"])
    assert stop["chargingSchedule"]["chargingSchedulePeriod"][0]["limit"] == 0
