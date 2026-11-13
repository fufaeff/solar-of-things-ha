"""Test the Solar of Things config flow."""
from unittest.mock import patch, MagicMock

import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_of_things.const import DOMAIN
from custom_components.solar_of_things.api import AuthenticationError


async def test_form_user_step(hass: HomeAssistant) -> None:
    """Test user step shows auth mode form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_form_token_cannot_connect(hass: HomeAssistant) -> None:
    """Test token auth mode handles cannot connect error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"auth_mode": "token"},
    )
    assert result2["type"] == data_entry_flow.FlowResultType.FORM
    assert result2["step_id"] == "token"

    with patch(
        "custom_components.solar_of_things.api.SolarOfThingsAPI.test_connection",
        return_value=False,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {
                "iot_token": "test_token",
                "station_id": "876543210987654321",
            },
        )

    assert result3["type"] == data_entry_flow.FlowResultType.FORM
    assert result3["errors"] == {"base": "cannot_connect"}


async def test_form_token_success(hass: HomeAssistant) -> None:
    """Test successful configuration via legacy token."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"auth_mode": "token"},
    )

    with patch(
        "custom_components.solar_of_things.api.SolarOfThingsAPI.fetch_latest_data",
        return_value={"pvInputPower": 2500},
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {
                "iot_token": "test_token",
                "station_id": "876543210987654321",
                "device_id": "123456789012345678",
                "time_zone": "Asia/Manila",
            },
        )

    assert result3["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result3["title"] == "Solar Station 876543210987654321"
    assert result3["data"] == {
        "iot_token": "test_token",
        "station_id": "876543210987654321",
        "device_id": "123456789012345678",
        "time_zone": "Asia/Manila",
    }


async def test_form_password_invalid_auth(hass: HomeAssistant) -> None:
    """Test user_id/password auth mode handles invalid credentials."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"auth_mode": "password"},
    )
    assert result2["type"] == data_entry_flow.FlowResultType.FORM
    assert result2["step_id"] == "password"

    with patch(
        "custom_components.solar_of_things.api.SolarOfThingsAPI.login",
        side_effect=AuthenticationError("Invalid credentials"),
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {
                "user_id": "test_user",
                "password": "wrong_password",
                "station_id": "876543210987654321",
            },
        )

    assert result3["type"] == data_entry_flow.FlowResultType.FORM
    assert result3["errors"] == {"base": "invalid_auth"}


async def test_options_flow(hass: HomeAssistant) -> None:
    """Test options flow to change update interval."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "iot_token": "test_token",
            "station_id": "876543210987654321",
        },
        options={"update_interval": 1},
        entry_id="test_opt_entry",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"update_interval": 5},
    )
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["data"] == {"update_interval": 5}


async def test_reauth_confirm_password_success(hass: HomeAssistant) -> None:
    """Test reauth flow succeeds with updated password."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "user_id": "test_user",
            "password": "old_password",
            "station_id": "876543210987654321",
            "iot_token": "old_token",
        },
        entry_id="test_reauth_entry",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch(
        "custom_components.solar_of_things.api.SolarOfThingsAPI.login",
        return_value=None,
    ), patch(
        "custom_components.solar_of_things.api.SolarOfThingsAPI.test_connection",
        return_value=True,
    ), patch.object(
        hass.config_entries, "async_reload", return_value=True
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"user_id": "test_user", "password": "new_password"},
        )

    assert result2["type"] == data_entry_flow.FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"


async def test_reauth_confirm_token_success(hass: HomeAssistant) -> None:
    """Test reauth flow succeeds with fresh IOT token."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "iot_token": "expired_token",
            "station_id": "876543210987654321",
        },
        entry_id="test_reauth_token_entry",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch(
        "custom_components.solar_of_things.api.SolarOfThingsAPI.test_connection",
        return_value=True,
    ), patch.object(
        hass.config_entries, "async_reload", return_value=True
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"iot_token": "fresh_new_token"},
        )

    assert result2["type"] == data_entry_flow.FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"

