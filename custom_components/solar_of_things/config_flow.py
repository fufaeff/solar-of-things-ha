"""Config flow for Solar of Things integration.

Supports two setup modes:
  1. User-ID + Password  (recommended) – integration logs in automatically and
     refreshes the token without any user intervention.
  2. IOT Token (legacy / advanced) – user pastes the token from DevTools.
     A re-auth flow is triggered when the token expires.

Re-auth flow:  HA calls async_step_reauth when a TokenExpiredError is caught by
the coordinator.  The user is asked only for the field(s) that need updating
(usually just a fresh token, or new credentials).
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import SolarOfThingsAPI, AuthenticationError, TokenExpiredError
from .const import (
    DOMAIN,
    CONF_USER_ID,
    CONF_PASSWORD,
    CONF_IOT_TOKEN,
    CONF_STATION_ID,
    CONF_DEVICE_ID,
    CONF_TIME_ZONE,
    CONF_REFRESH_TOKEN,
    CONF_ACCESS_TOKEN_EXPIRES,
    CONF_REFRESH_TOKEN_EXPIRES,
)

_LOGGER = logging.getLogger(__name__)

# ─── Validation helpers ────────────────────────────────────────────────────────

async def _validate_password_auth(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate user-ID/password auth, return title, station ID, and tokens."""
    api = SolarOfThingsAPI(
        user_id=data[CONF_USER_ID],
        password=data[CONF_PASSWORD],
        time_zone=data.get(CONF_TIME_ZONE),
    )
    try:
        await hass.async_add_executor_job(api.login)
    except AuthenticationError as err:
        raise InvalidAuth(str(err)) from err
    except Exception as err:
        raise CannotConnect(str(err)) from err

    station_id = (data.get(CONF_STATION_ID) or "").strip()
    stations: list[dict[str, Any]] = []

    # Auto-discover station if not explicitly provided
    if not station_id:
        try:
            stations = await hass.async_add_executor_job(api.discover_stations)
        except Exception as err:
            _LOGGER.error("Failed to discover stations: %s", err)
            raise CannotConnect(f"Failed to discover stations: {err}") from err

        if not stations:
            raise CannotConnect("No stations or devices found for this account.")

        if len(stations) == 1:
            station_id = stations[0]["station_id"]

    # If station_id is resolved, validate connection
    if station_id:
        ok = await hass.async_add_executor_job(api.test_connection, station_id)
        if not ok:
            raise CannotConnect("Station/device unreachable after login.")

    title = f"Solar Station {station_id}" if station_id else "Solar Station"
    if stations and len(stations) == 1 and stations[0].get("station_name"):
        title = stations[0]["station_name"]

    return {
        "title": title,
        "station_id": station_id,
        "stations": stations,
        CONF_IOT_TOKEN: api.access_token,
        CONF_REFRESH_TOKEN: api.refresh_token,
        CONF_ACCESS_TOKEN_EXPIRES: api.access_token_expires_iso,
        CONF_REFRESH_TOKEN_EXPIRES: api.refresh_token_expires_iso,
    }


async def _validate_token_auth(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate legacy IOT-token auth, return title and station ID."""
    api = SolarOfThingsAPI(
        iot_token=data[CONF_IOT_TOKEN],
        time_zone=data.get(CONF_TIME_ZONE),
    )
    station_id = (data.get(CONF_STATION_ID) or "").strip()
    device_id = (data.get(CONF_DEVICE_ID) or "").strip()
    stations: list[dict[str, Any]] = []

    # Auto-discover station if not explicitly provided
    if not station_id and not device_id:
        try:
            stations = await hass.async_add_executor_job(api.discover_stations)
        except Exception as err:
            _LOGGER.error("Failed to discover stations with token: %s", err)
            raise CannotConnect(f"Failed to discover stations: {err}") from err

        if not stations:
            raise CannotConnect("No stations or devices found with provided token.")

        if len(stations) == 1:
            station_id = stations[0]["station_id"]

    try:
        if device_id:
            res = await hass.async_add_executor_job(api.fetch_latest_data, device_id)
            ok = bool(res)
        elif station_id:
            ok = await hass.async_add_executor_job(api.test_connection, station_id)
        else:
            ok = bool(stations)
    except Exception as err:
        _LOGGER.error("Token validation failed: %s", err)
        ok = False

    if not ok:
        raise CannotConnect("Cannot connect with provided token.")

    title = f"Solar Station {station_id}" if station_id else "Solar Station"
    if stations and len(stations) == 1 and stations[0].get("station_name"):
        title = stations[0]["station_name"]

    return {
        "title": title,
        "station_id": station_id,
        "stations": stations,
    }


# ─── Config Flow ───────────────────────────────────────────────────────────────

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Solar of Things."""

    VERSION = 3  # bumped for new auth fields

    def __init__(self) -> None:
        self._auth_mode: str = "password"  # "password" | "token"
        self._auth_info: dict[str, Any] = {}
        self._discovered_stations: list[dict[str, Any]] = []

    # ── Step 1: choose auth mode ───────────────────────────────────────────────

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Offer choice of auth mode."""
        if user_input is not None:
            self._auth_mode = user_input.get("auth_mode", "password")
            if self._auth_mode == "token":
                return await self.async_step_token()
            return await self.async_step_password()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("auth_mode", default="password"): vol.In(
                        {"password": "User ID + Password (recommended)", "token": "IOT Token (advanced)"}
                    )
                }
            ),
            description_placeholders={
                "docs_url": "https://github.com/conexocasa/solar-of-things-ha"
            },
        )

    # ── Step 2a: user-id + password ────────────────────────────────────────────

    async def async_step_password(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """User-ID + password setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _validate_password_auth(self.hass, user_input)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception in password step")
                errors["base"] = "unknown"
            else:
                station_id = info.get("station_id")
                # If multiple stations discovered and not specified, ask user to select
                if not station_id and info.get("stations") and len(info["stations"]) > 1:
                    self._auth_info = {
                        CONF_USER_ID: user_input[CONF_USER_ID],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_DEVICE_ID: user_input.get(CONF_DEVICE_ID, ""),
                        CONF_TIME_ZONE: user_input.get(CONF_TIME_ZONE, "Asia/Manila"),
                        CONF_IOT_TOKEN: info[CONF_IOT_TOKEN],
                        CONF_REFRESH_TOKEN: info[CONF_REFRESH_TOKEN],
                        CONF_ACCESS_TOKEN_EXPIRES: info[CONF_ACCESS_TOKEN_EXPIRES],
                        CONF_REFRESH_TOKEN_EXPIRES: info[CONF_REFRESH_TOKEN_EXPIRES],
                    }
                    self._discovered_stations = info["stations"]
                    return await self.async_step_select_station()

                await self.async_set_unique_id(f"station_{station_id}")
                self._abort_if_unique_id_configured()
                entry_data = {
                    CONF_USER_ID: user_input[CONF_USER_ID],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_STATION_ID: station_id,
                    CONF_DEVICE_ID: user_input.get(CONF_DEVICE_ID, ""),
                    CONF_TIME_ZONE: user_input.get(CONF_TIME_ZONE, "Asia/Manila"),
                    CONF_IOT_TOKEN: info[CONF_IOT_TOKEN],
                    CONF_REFRESH_TOKEN: info[CONF_REFRESH_TOKEN],
                    CONF_ACCESS_TOKEN_EXPIRES: info[CONF_ACCESS_TOKEN_EXPIRES],
                    CONF_REFRESH_TOKEN_EXPIRES: info[CONF_REFRESH_TOKEN_EXPIRES],
                }
                return self.async_create_entry(title=info["title"], data=entry_data)

        return self.async_show_form(
            step_id="password",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USER_ID): cv.string,
                    vol.Required(CONF_PASSWORD): cv.string,
                    vol.Optional(CONF_STATION_ID): cv.string,
                    vol.Optional(CONF_DEVICE_ID): cv.string,
                    vol.Optional(CONF_TIME_ZONE, default="Asia/Manila"): cv.string,
                }
            ),
            errors=errors,
            description_placeholders={
                "docs_url": "https://github.com/conexocasa/solar-of-things-ha"
            },
        )

    # ── Step 2b: legacy IOT token ─────────────────────────────────────────────

    async def async_step_token(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Legacy IOT-token setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _validate_token_auth(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception in token step")
                errors["base"] = "unknown"
            else:
                station_id = info.get("station_id")
                # If multiple stations discovered and not specified, ask user to select
                if not station_id and info.get("stations") and len(info["stations"]) > 1:
                    self._auth_info = {
                        CONF_IOT_TOKEN: user_input[CONF_IOT_TOKEN],
                        CONF_DEVICE_ID: user_input.get(CONF_DEVICE_ID, ""),
                        CONF_TIME_ZONE: user_input.get(CONF_TIME_ZONE, "Asia/Manila"),
                    }
                    self._discovered_stations = info["stations"]
                    return await self.async_step_select_station()

                await self.async_set_unique_id(f"station_{station_id}")
                self._abort_if_unique_id_configured()
                entry_data = {
                    **user_input,
                    CONF_STATION_ID: station_id,
                }
                return self.async_create_entry(title=info["title"], data=entry_data)

        return self.async_show_form(
            step_id="token",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_IOT_TOKEN): cv.string,
                    vol.Optional(CONF_STATION_ID): cv.string,
                    vol.Optional(CONF_DEVICE_ID): cv.string,
                    vol.Optional(CONF_TIME_ZONE, default="Asia/Manila"): cv.string,
                }
            ),
            errors=errors,
            description_placeholders={
                "docs_url": "https://github.com/Hyllesen/solar-of-things-solar-usage"
            },
        )

    # ── Step 2c: select station (when multiple discovered) ────────────────────

    async def async_step_select_station(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step to select station when multiple stations are found."""
        errors: dict[str, str] = {}

        if user_input is not None:
            station_id = user_input[CONF_STATION_ID]
            await self.async_set_unique_id(f"station_{station_id}")
            self._abort_if_unique_id_configured()

            st_info = next(
                (s for s in self._discovered_stations if s["station_id"] == station_id),
                None,
            )
            title = st_info["station_name"] if st_info else f"Solar Station {station_id}"

            entry_data = {
                **self._auth_info,
                CONF_STATION_ID: station_id,
            }
            return self.async_create_entry(title=title, data=entry_data)

        station_options = {
            s["station_id"]: f"{s['station_name']} ({len(s['devices'])} devices) [ID: {s['station_id']}]"
            for s in self._discovered_stations
        }

        return self.async_show_form(
            step_id="select_station",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATION_ID): vol.In(station_options),
                }
            ),
            errors=errors,
        )

    # ── Re-auth flow ───────────────────────────────────────────────────────────

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Triggered by the coordinator when TokenExpiredError is raised."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask the user to re-enter credentials or a fresh token."""
        errors: dict[str, str] = {}
        existing_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        existing_user_id = (
            existing_entry.data.get(CONF_USER_ID, "") if existing_entry else ""
        )

        if user_input is not None:
            user_id = (
                user_input.get(CONF_USER_ID)
                or (existing_entry.data.get(CONF_USER_ID) if existing_entry else "")
                or ""
            ).strip()
            password = (
                user_input.get(CONF_PASSWORD)
                or (existing_entry.data.get(CONF_PASSWORD) if existing_entry else "")
                or ""
            ).strip()
            iot_token = (user_input.get(CONF_IOT_TOKEN) or "").strip()

            if iot_token:
                # Option A: User provided a fresh IOT Token
                merged = {
                    **(existing_entry.data if existing_entry else {}),
                    CONF_IOT_TOKEN: iot_token,
                }
                try:
                    await _validate_token_auth(self.hass, merged)
                except CannotConnect as err:
                    _LOGGER.warning("SolarOfThings re-auth token connection error: %s", err)
                    errors["base"] = "cannot_connect"
                except Exception as err:
                    _LOGGER.warning("SolarOfThings re-auth token validation error: %s", err)
                    errors["base"] = "invalid_auth"
                else:
                    self.hass.config_entries.async_update_entry(
                        existing_entry,
                        data={
                            **existing_entry.data,
                            CONF_IOT_TOKEN: iot_token,
                        },
                    )
                    await self.hass.config_entries.async_reload(existing_entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

            elif user_id and password:
                # Option B: Re-authenticate with User ID + Password
                merged = {
                    **(existing_entry.data if existing_entry else {}),
                    CONF_USER_ID: user_id,
                    CONF_PASSWORD: password,
                }
                try:
                    info = await _validate_password_auth(self.hass, merged)
                except InvalidAuth as err:
                    _LOGGER.warning("SolarOfThings re-auth invalid auth: %s", err)
                    errors["base"] = "invalid_auth"
                except CannotConnect as err:
                    _LOGGER.warning("SolarOfThings re-auth cannot connect: %s", err)
                    errors["base"] = "cannot_connect"
                except Exception as err:
                    _LOGGER.exception("Unexpected exception during re-auth: %s", err)
                    errors["base"] = "unknown"
                else:
                    self.hass.config_entries.async_update_entry(
                        existing_entry,
                        data={
                            **existing_entry.data,
                            CONF_USER_ID: user_id,
                            CONF_PASSWORD: password,
                            CONF_IOT_TOKEN: info[CONF_IOT_TOKEN],
                            CONF_REFRESH_TOKEN: info[CONF_REFRESH_TOKEN],
                            CONF_ACCESS_TOKEN_EXPIRES: info[CONF_ACCESS_TOKEN_EXPIRES],
                            CONF_REFRESH_TOKEN_EXPIRES: info[CONF_REFRESH_TOKEN_EXPIRES],
                        },
                    )
                    await self.hass.config_entries.async_reload(existing_entry.entry_id)
                    return self.async_abort(reason="reauth_successful")
            else:
                errors["base"] = "invalid_auth"

        schema_dict = {}
        if existing_user_id:
            schema_dict[vol.Optional(CONF_USER_ID, default=existing_user_id)] = cv.string
        else:
            schema_dict[vol.Optional(CONF_USER_ID)] = cv.string

        schema_dict[vol.Optional(CONF_PASSWORD)] = cv.string
        schema_dict[vol.Optional(CONF_IOT_TOKEN)] = cv.string

        schema = vol.Schema(schema_dict)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
        )

    # ── Options flow ───────────────────────────────────────────────────────────

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return SolarOfThingsOptionsFlowHandler(config_entry)


class SolarOfThingsOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for Solar of Things."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options, including updating credentials for automatic token renewal."""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_id = (user_input.get(CONF_USER_ID) or "").strip()
            password = (user_input.get(CONF_PASSWORD) or "").strip()
            update_interval = user_input.get("update_interval", 1)

            effective_user_id = user_id or self.config_entry.data.get(CONF_USER_ID, "")
            effective_password = password or self.config_entry.data.get(CONF_PASSWORD, "")

            # If user wants to enable/update password-based auto-refresh
            if effective_user_id and password:
                merged = {
                    **self.config_entry.data,
                    CONF_USER_ID: effective_user_id,
                    CONF_PASSWORD: password,
                }
                try:
                    info = await _validate_password_auth(self.hass, merged)
                except InvalidAuth as err:
                    _LOGGER.warning("SolarOfThings options flow invalid auth: %s", err)
                    errors["base"] = "invalid_auth"
                except CannotConnect as err:
                    _LOGGER.warning("SolarOfThings options flow cannot connect: %s", err)
                    errors["base"] = "cannot_connect"
                except Exception as err:
                    _LOGGER.exception("Unexpected error in options flow: %s", err)
                    errors["base"] = "unknown"
                else:
                    new_data = {
                        **self.config_entry.data,
                        CONF_USER_ID: effective_user_id,
                        CONF_PASSWORD: password,
                        CONF_IOT_TOKEN: info[CONF_IOT_TOKEN],
                        CONF_REFRESH_TOKEN: info[CONF_REFRESH_TOKEN],
                        CONF_ACCESS_TOKEN_EXPIRES: info[CONF_ACCESS_TOKEN_EXPIRES],
                        CONF_REFRESH_TOKEN_EXPIRES: info[CONF_REFRESH_TOKEN_EXPIRES],
                    }
                    self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
                    return self.async_create_entry(
                        title="",
                        data={"update_interval": update_interval},
                    )
            elif user_id and not effective_password:
                errors["base"] = "invalid_auth"
            elif not errors:
                return self.async_create_entry(
                    title="",
                    data={"update_interval": update_interval},
                )

        current_interval = self.config_entry.options.get("update_interval", 1)
        existing_user_id = self.config_entry.data.get(CONF_USER_ID, "")

        schema_dict: dict[Any, Any] = {
            vol.Optional(
                "update_interval",
                default=current_interval,
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        }
        if existing_user_id:
            schema_dict[vol.Optional(CONF_USER_ID, default=existing_user_id)] = cv.string
        else:
            schema_dict[vol.Optional(CONF_USER_ID)] = cv.string

        schema_dict[vol.Optional(CONF_PASSWORD)] = cv.string

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )


# ─── Custom exceptions ─────────────────────────────────────────────────────────

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid authentication credentials."""
