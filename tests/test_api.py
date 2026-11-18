"""Tests for the Solar of Things API client (no Home Assistant instance needed)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from custom_components.solar_of_things.api import (
    AuthenticationError,
    SettingsWriteError,
    SolarOfThingsAPI,
    TokenExpiredError,
    _compute_iot_sign,
    _decrypt_app_secret,
)
from custom_components.solar_of_things.const import (
    API_BASE_URL,
    API_DEVICE_LIST,
    API_LATEST_STATE,
    API_LOGIN,
    API_MONTHLY_SUMMARY,
    API_REFRESH_TOKEN,
    API_SETTINGS_SET,
    API_TIME_SERIES,
    IOT_APP_ID,
    IOT_APP_SECRET_ENC,
)

LOGIN_URL = f"{API_BASE_URL}{API_LOGIN}"
MONTHLY_URL = f"{API_BASE_URL}{API_MONTHLY_SUMMARY}"
REFRESH_URL = f"{API_BASE_URL}{API_REFRESH_TOKEN}"
DEVICE_LIST_URL = f"{API_BASE_URL}{API_DEVICE_LIST}"
LATEST_STATE_URL = f"{API_BASE_URL}{API_LATEST_STATE}"
TIME_SERIES_URL = f"{API_BASE_URL}{API_TIME_SERIES}"
SETTINGS_SET_URL = f"{API_BASE_URL}{API_SETTINGS_SET}"


def _iso(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat()


def _token_payload(access: str, refresh: str = "refresh-1") -> dict:
    return {
        "code": 0,
        "data": {
            "accessToken": access,
            "refreshToken": refresh,
            "accessTokenWillExpiredAt": _iso(timedelta(hours=2)),
            "refreshTokenWillExpiredAt": _iso(timedelta(days=30)),
        },
    }


def _empty_device_list() -> dict:
    return {"code": 0, "data": {"total": 0, "list": []}}


# ─── Signing ───────────────────────────────────────────────────────────────────


def test_app_secret_decrypts_to_text() -> None:
    secret = _decrypt_app_secret(IOT_APP_ID, IOT_APP_SECRET_ENC)
    assert secret
    assert secret.isprintable()


def test_iot_sign_is_deterministic_md5_hex() -> None:
    sign1 = _compute_iot_sign("app", "nonce", "hash", "secret")
    sign2 = _compute_iot_sign("app", "nonce", "hash", "secret")
    other = _compute_iot_sign("app", "nonce2", "hash", "secret")
    assert sign1 == sign2
    assert sign1 != other
    assert len(sign1) == 32
    int(sign1, 16)


# ─── Login ─────────────────────────────────────────────────────────────────────


def test_login_stores_tokens_and_notifies(requests_mock) -> None:
    requests_mock.post(LOGIN_URL, json=_token_payload("access-1"))
    refreshed: list[tuple] = []
    api = SolarOfThingsAPI(
        user_id="user",
        password="secret",
        on_token_refreshed=lambda *args: refreshed.append(args),
    )

    api.login()

    request = requests_mock.request_history[0]
    assert request.json() == {
        "account": "user",
        "password": hashlib.md5(b"secret").hexdigest(),
    }
    for header in ("IOT-Open-AppID", "IOT-Open-Nonce", "IOT-Open-Body-Hash", "IOT-Open-Sign"):
        assert header in request.headers
    assert api.access_token == "access-1"
    assert api.refresh_token == "refresh-1"
    assert api.session.headers["IOT-Token"] == "access-1"
    assert refreshed and refreshed[0][0] == "access-1"


def test_login_rejected_raises_authentication_error(requests_mock) -> None:
    requests_mock.post(LOGIN_URL, json={"code": 7, "message": "password error"})
    api = SolarOfThingsAPI(user_id="user", password="wrong")

    with pytest.raises(AuthenticationError):
        api.login()


# ─── Token handling on data requests ───────────────────────────────────────────


def test_legacy_token_rejected_raises_token_expired(requests_mock) -> None:
    """A legacy token cannot be refreshed, so 401 must surface as TokenExpiredError."""
    requests_mock.post(DEVICE_LIST_URL, status_code=401)
    api = SolarOfThingsAPI(iot_token="legacy")

    with pytest.raises(TokenExpiredError):
        api.list_devices("station")


def test_token_invalid_code_raises_token_expired(requests_mock) -> None:
    """HTTP 200 with a token-invalid business code is treated like 401."""
    requests_mock.post(DEVICE_LIST_URL, json={"code": 10002, "message": "token invalid"})
    api = SolarOfThingsAPI(iot_token="legacy")

    with pytest.raises(TokenExpiredError):
        api.list_devices("station")


def test_rejected_token_is_refreshed_and_request_retried(requests_mock) -> None:
    requests_mock.post(REFRESH_URL, json=_token_payload("access-2", "refresh-2"))
    requests_mock.post(
        DEVICE_LIST_URL,
        [{"status_code": 401}, {"json": _empty_device_list()}],
    )
    api = SolarOfThingsAPI(
        user_id="user",
        password="secret",
        iot_token="access-1",
        refresh_token="refresh-1",
        access_token_expires=_iso(timedelta(hours=1)),
    )

    assert api.list_devices("station") == []

    data_requests = [r for r in requests_mock.request_history if r.url.startswith(DEVICE_LIST_URL)]
    assert [r.headers["IOT-Token"] for r in data_requests] == ["access-1", "access-2"]
    assert api.refresh_token == "refresh-2"


def test_failed_refresh_falls_back_to_login(requests_mock) -> None:
    requests_mock.post(REFRESH_URL, status_code=401)
    requests_mock.post(LOGIN_URL, json=_token_payload("access-login"))
    requests_mock.post(
        DEVICE_LIST_URL,
        [{"json": {"code": 10003}}, {"json": _empty_device_list()}],
    )
    api = SolarOfThingsAPI(
        user_id="user",
        password="secret",
        iot_token="access-1",
        refresh_token="refresh-1",
        access_token_expires=_iso(timedelta(hours=1)),
    )

    api.list_devices("station")

    assert api.access_token == "access-login"


def test_token_rejected_after_refresh_raises(requests_mock) -> None:
    requests_mock.post(REFRESH_URL, json=_token_payload("access-2"))
    requests_mock.post(DEVICE_LIST_URL, status_code=401)
    api = SolarOfThingsAPI(
        iot_token="access-1",
        refresh_token="refresh-1",
        access_token_expires=_iso(timedelta(hours=1)),
    )

    with pytest.raises(TokenExpiredError):
        api.list_devices("station")


def test_token_refreshed_proactively_before_expiry(requests_mock) -> None:
    requests_mock.post(REFRESH_URL, json=_token_payload("access-2"))
    requests_mock.post(DEVICE_LIST_URL, json=_empty_device_list())
    api = SolarOfThingsAPI(
        iot_token="access-1",
        refresh_token="refresh-1",
        access_token_expires=_iso(timedelta(minutes=1)),
    )

    api.list_devices("station")

    assert requests_mock.request_history[0].url == REFRESH_URL
    assert requests_mock.request_history[1].headers["IOT-Token"] == "access-2"


# ─── Data parsing ──────────────────────────────────────────────────────────────


def test_list_devices_paginates(requests_mock) -> None:
    requests_mock.post(
        DEVICE_LIST_URL,
        [
            {"json": {"code": 0, "data": {"total": 3, "list": [{"id": 1}, {"id": 2}]}}},
            {"json": {"code": 0, "data": {"total": 3, "list": [{"id": 3}]}}},
        ],
    )
    api = SolarOfThingsAPI(iot_token="t")

    devices = api.list_devices("station", page_size=2)

    assert [d["id"] for d in devices] == [1, 2, 3]
    assert [r.json()["page"] for r in requests_mock.request_history] == [1, 2]


def test_fetch_latest_data_derives_power(requests_mock) -> None:
    requests_mock.post(
        TIME_SERIES_URL,
        json={
            "code": 0,
            "data": {
                "payload": {
                    "fields": {
                        "pvInputPower": [100, 1500],
                        "pv2InputPower": [500],
                        "acOutputActivePower": [1.2],  # kW
                        "batteryVoltage": [50],
                        "batteryChargingCurrent": [10],
                        "batteryDischargeCurrent": [0],
                        "feedInPower": [0],
                    }
                }
            },
        },
    )
    api = SolarOfThingsAPI(iot_token="t")

    values = api.fetch_latest_data("dev")

    assert values["pvInputPower"] == 1500
    assert values["totalPvPower"] == 2000
    assert values["acOutputActivePower"] == pytest.approx(1200)
    assert values["batteryChargingPower"] == 500
    assert values["batteryDischargePower"] == 0
    assert values["batteryPower"] == -500
    assert values["loadPower"] == pytest.approx(1200)


def test_fetch_latest_data_requests_short_window(requests_mock) -> None:
    requests_mock.post(TIME_SERIES_URL, json={"code": 0, "data": {}})
    api = SolarOfThingsAPI(iot_token="t", time_zone="UTC")

    api.fetch_latest_data("dev")

    body = requests_mock.request_history[0].json()
    window = datetime.fromisoformat(body["toTime"]) - datetime.fromisoformat(body["fromTime"])
    assert window <= timedelta(minutes=15)
    assert body["count"] <= 100
    assert body["orderByTimeAsc"] is True


def test_fetch_latest_state_retries_then_raises(requests_mock) -> None:
    requests_mock.get(LATEST_STATE_URL, json={"code": 20000, "message": "busy"})
    api = SolarOfThingsAPI(iot_token="t")

    with patch("custom_components.solar_of_things.api.time.sleep"), pytest.raises(RuntimeError):
        api.fetch_latest_state("dev")

    assert requests_mock.call_count == 2


def test_fetch_latest_state_parses_fields(requests_mock) -> None:
    requests_mock.get(
        LATEST_STATE_URL,
        json={
            "code": 0,
            "data": {"fields": {"batterySOC": {"value": 80, "unit": "%", "nameDisplay": "SOC"}}},
        },
    )
    api = SolarOfThingsAPI(iot_token="t")

    state = api.fetch_latest_state("dev")

    assert state == {"batterySOC": {"value": 80, "unit": "%", "nameDisplay": "SOC"}}
    assert requests_mock.request_history[0].qs == {"deviceid": ["dev"]}


def test_write_setting_sends_key_and_value(requests_mock) -> None:
    requests_mock.post(SETTINGS_SET_URL, json={"code": 0})
    api = SolarOfThingsAPI(iot_token="t")

    api.write_setting("dev", "ecoMode", 1)

    request = requests_mock.request_history[0]
    assert request.qs == {"deviceid": ["dev"]}
    assert request.json() == {"deviceId": "dev", "key": "ecoMode", "value": 1}


def test_write_setting_error_code_raises(requests_mock) -> None:
    requests_mock.post(SETTINGS_SET_URL, json={"code": 500, "message": "fail"})
    api = SolarOfThingsAPI(iot_token="t")

    with pytest.raises(SettingsWriteError):
        api.write_setting("dev", "ecoMode", 0)


# ─── Monthly summary ───────────────────────────────────────────────────────────


def _monthly_property(key: str, points: dict[str, float], real: bool = True) -> dict:
    return {
        "property": {"key": key, "name": key},
        "timePoints": [
            {"time": time, "value": value, "isRealValue": real} for time, value in points.items()
        ],
    }


def test_fetch_monthly_summary_parses_current_month(requests_mock) -> None:
    requests_mock.post(
        MONTHLY_URL,
        json={
            "code": 0,
            "data": {
                "properties": [
                    _monthly_property("pvGeneratedEnergy", {"2026-10": 300.0, "2026-11": 201.06}),
                    _monthly_property("consumeElectricityQuantity", {"2026-11": 250.0}),
                    _monthly_property("buyElectricityQuantity", {"2026-11": 50.0}),
                    _monthly_property("sellElectricityQuantity", {"2026-11": 5.5}),
                    _monthly_property("chargeElectricityQuantity", {"2026-11": 80.0}),
                    _monthly_property("dischargeElectricityQuantity", {"2026-11": 70.0}, real=False),
                ]
            },
        },
    )
    api = SolarOfThingsAPI(iot_token="t", time_zone="UTC")

    with patch.object(
        SolarOfThingsAPI,
        "_now",
        return_value=datetime(2026, 11, 17, 12, tzinfo=UTC),
    ):
        monthly = api.fetch_monthly_summary("st1")

    request = requests_mock.request_history[0]
    assert request.json() == {"time": "2026"}
    assert request.qs["summarycategorykey"] == ["pvinverterelectricityquantityclass"]
    assert monthly == {
        "monthly_pv_generated": 201.06,
        "monthly_total_consumption": 250.0,
        "monthly_grid_import": 50.0,
        "monthly_grid_export": 5.5,
        "monthly_battery_charge": 80.0,
        "monthly_battery_discharge": 0.0,  # isRealValue == False
        "monthly_solar_percentage": 80.0,  # (250 - 50) / 250
    }


def test_fetch_monthly_summary_missing_properties_are_unknown(requests_mock) -> None:
    requests_mock.post(
        MONTHLY_URL,
        json={
            "code": 0,
            "data": {"properties": [_monthly_property("pvGeneratedEnergy", {"2026-01": 1.0})]},
        },
    )
    api = SolarOfThingsAPI(iot_token="t", time_zone="UTC")

    with patch.object(
        SolarOfThingsAPI,
        "_now",
        return_value=datetime(2026, 11, 17, 12, tzinfo=UTC),
    ):
        monthly = api.fetch_monthly_summary("st1")

    assert monthly["monthly_pv_generated"] == 0.0  # present, no value this month
    assert monthly["monthly_grid_import"] is None
    assert monthly["monthly_total_consumption"] is None
    assert monthly["monthly_solar_percentage"] is None
