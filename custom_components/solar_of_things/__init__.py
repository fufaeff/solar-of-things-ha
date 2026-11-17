"""The Solar of Things integration.

Authentication notes
────────────────────
The integration supports two modes:

  Email + Password (MODE_PASSWORD):
    • api.login() is called at setup → obtains accessToken + refreshToken.
    • _ensure_token_valid() is called before every API request; it proactively
      refreshes the token 5 minutes before expiry using the refresh-token
      endpoint, and falls back to re-login with stored credentials if the
      refresh token is also expired.
    • on_token_refreshed callback updates the config entry with fresh tokens so
      the state survives HA restarts without having to re-login every time.

  Legacy IOT-Token (MODE_TOKEN):
    • The stored token is used as-is.
    • On 401 / TokenExpiredError, the coordinator raises UpdateFailed and also
      calls entry.async_start_reauth() so the user is prompted for a fresh token.

Version history
────────────────
  v2.3.0 – user-id/password auth (replacing email), working IOT-Open signing
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SolarOfThingsAPI, TokenExpiredError
from .const import (
    CONF_ACCESS_TOKEN_EXPIRES,
    CONF_DEVICE_ID,
    CONF_IOT_TOKEN,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_REFRESH_TOKEN_EXPIRES,
    CONF_STATION_ID,
    CONF_TIME_ZONE,
    CONF_USER_ID,
    DOMAIN,
    MAX_DEVICE_STALE_UPDATES,
    MAX_STATION_STALE_UPDATES,
)
from .entity import (
    device_identifier,
    legacy_device_identifier,
    station_device_info,
)
from .entity import station_device_fields

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]

DEVICE_UPDATE_INTERVAL = timedelta(minutes=1)
STATION_UPDATE_INTERVAL = timedelta(minutes=30)

# How long a value we just wrote is shown instead of the (cached, possibly stale)
# value reported by the cloud settings endpoint.
PENDING_SETTING_TTL = timedelta(minutes=5)


def _setting_raw(entry: Any) -> Any:
    """Return the raw value of a settings entry ({"value": x, ...} or bare x)."""
    return entry.get("value") if isinstance(entry, dict) else entry


def _setting_values_equal(left: Any, right: Any) -> bool:
    """Compare setting values that may arrive as int, float or numeric string."""
    try:
        return abs(float(left) - float(right)) < 1e-6
    except (TypeError, ValueError):
        return str(left) == str(right)


# ──────────────────────────────────────────────────────────────────────────────
# Entry setup / teardown
# ──────────────────────────────────────────────────────────────────────────────


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Solar of Things from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    time_zone = entry.data.get(CONF_TIME_ZONE) or entry.options.get(CONF_TIME_ZONE)
    user_id = entry.data.get(CONF_USER_ID)
    password = entry.data.get(CONF_PASSWORD)

    # Build the token-refreshed callback *before* constructing the API so the
    # callback reference is captured.
    def _on_token_refreshed(
        access_token: str,
        refresh_token: str,
        access_expires: str,
        refresh_expires: str,
    ) -> None:
        """Persist refreshed token back to the config entry (thread-safe)."""
        new_data = {
            **entry.data,
            CONF_IOT_TOKEN: access_token,
            CONF_REFRESH_TOKEN: refresh_token,
            CONF_ACCESS_TOKEN_EXPIRES: access_expires,
            CONF_REFRESH_TOKEN_EXPIRES: refresh_expires,
        }

        def _update_entry() -> None:
            hass.config_entries.async_update_entry(entry, data=new_data)
            _LOGGER.debug(
                "SolarOfThings [%s]: access token refreshed; entry data updated",
                entry.entry_id,
            )

        hass.loop.call_soon_threadsafe(_update_entry)

    # Instantiate API in the appropriate auth mode
    if user_id and password:
        api = SolarOfThingsAPI(
            user_id=user_id,
            password=password,
            iot_token=entry.data.get(
                CONF_IOT_TOKEN
            ),  # cached token (avoids login on every restart)
            refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
            access_token_expires=entry.data.get(CONF_ACCESS_TOKEN_EXPIRES),
            refresh_token_expires=entry.data.get(CONF_REFRESH_TOKEN_EXPIRES),
            time_zone=time_zone,
            on_token_refreshed=_on_token_refreshed,
        )
        # If the cached token is already expired (or absent), login immediately.
        if not api.access_token:
            await hass.async_add_executor_job(api.login)
    else:
        # Legacy IOT-token mode
        api = SolarOfThingsAPI(
            iot_token=entry.data[CONF_IOT_TOKEN],
            time_zone=time_zone,
            on_token_refreshed=_on_token_refreshed,
        )

    station_id = entry.data[CONF_STATION_ID]
    configured_device_id = (entry.data.get(CONF_DEVICE_ID) or "").strip()

    # Register the station (parent) device first so per-device entities can
    # link to it through via_device_id.
    station_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        **station_device_fields(station_id),
    )

    # ── Station coordinator (device list + monthly) ────────────────────────────
    station_coordinator = SolarOfThingsStationCoordinator(
        hass=hass,
        api=api,
        station_id=station_id,
        entry=entry,
    )
    await station_coordinator.async_config_entry_first_refresh()

    # ── Per-device coordinators ────────────────────────────────────────────────
    devices: list[dict[str, Any]] = (
        station_coordinator.data.get("devices", []) if station_coordinator.data else []
    )

    if configured_device_id:
        filtered = [d for d in devices if str(d.get("id")) == configured_device_id]
        devices = (
            filtered
            if filtered
            else [{"id": configured_device_id, "name": configured_device_id}]
        )

    device_coordinators: dict[str, SolarOfThingsDeviceCoordinator] = {}

    for dev in devices:
        device_id = str(dev.get("id") or "")
        if not device_id:
            continue
        c = SolarOfThingsDeviceCoordinator(
            hass=hass,
            api=api,
            station_id=station_id,
            device=device_id,
            device_meta=dev,
            entry=entry,
            station_device_id=station_device.id,
        )
        await c.async_config_entry_first_refresh()
        device_coordinators[device_id] = c

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "station_id": station_id,
        "station_device_id": station_device.id,
        "station_coordinator": station_coordinator,
        "device_coordinators": device_coordinators,
        "devices": devices,
        "reload_snapshot": _reload_snapshot(entry),
    }

    _async_setup_devices(hass, entry, station_id, device_coordinators)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


# Entry-data keys that change on every token refresh.  Updating them must not
# reload the integration — the running API client already holds the new tokens.
_TOKEN_STATE_KEYS = frozenset(
    {
        CONF_IOT_TOKEN,
        CONF_REFRESH_TOKEN,
        CONF_ACCESS_TOKEN_EXPIRES,
        CONF_REFRESH_TOKEN_EXPIRES,
    }
)


def _reload_snapshot(entry: ConfigEntry) -> dict[str, Any]:
    """Return the part of the entry whose change requires a reload."""
    return {
        "data": {k: v for k, v in entry.data.items() if k not in _TOKEN_STATE_KEYS},
        "options": dict(entry.options),
    }


@callback
def _async_setup_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    station_id: str,
    device_ids: Iterable[str],
) -> None:
    """Register the station device and migrate legacy device identifiers.

    The station must exist before inverter entities reference it through
    ``via_device``.  Before v2.5.0 inverters used an invalid 3-tuple
    identifier; it is rewritten in place so history and entity customisation
    stay attached to the same device.
    """
    dev_reg = dr.async_get(hass)
    dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id, **station_device_info(station_id)
    )

    for device_id in device_ids:
        legacy = dev_reg.async_get_device(
            identifiers={legacy_device_identifier(station_id, device_id)}
        )
        if legacy is None:
            continue
        new_identifier = device_identifier(station_id, device_id)
        if dev_reg.async_get_device(identifiers={new_identifier}) is not None:
            continue
        _LOGGER.debug(
            "SolarOfThings: migrating device identifier for %s to %s",
            device_id,
            new_identifier,
        )
        dev_reg.async_update_device(legacy.id, new_identifiers={new_identifier})


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options or credentials change (not on token refresh)."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is not None and runtime.get("reload_snapshot") == _reload_snapshot(
        entry
    ):
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


# ──────────────────────────────────────────────────────────────────────────────
# Coordinators
# ──────────────────────────────────────────────────────────────────────────────


class SolarOfThingsStationCoordinator(DataUpdateCoordinator):
    """Fetch station-level data (device list + monthly stats)."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: SolarOfThingsAPI,
        station_id: str,
        entry: ConfigEntry,
    ) -> None:
        self.api = api
        self.station_id = station_id
        self._entry = entry
        self._stale_updates = 0
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_station_{station_id}",
            update_interval=STATION_UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            devices = await self.hass.async_add_executor_job(
                self.api.list_devices, self.station_id
            )
            monthly = await self.hass.async_add_executor_job(
                self.api.fetch_monthly_summary, self.station_id
            )
            self._stale_updates = 0
            return {"devices": devices, "monthly": monthly}
        except TokenExpiredError as err:
            _LOGGER.error(
                "SolarOfThings station %s: token expired — triggering re-auth: %s",
                self.station_id,
                err,
            )
            self._entry.async_start_reauth(self.hass)
            raise UpdateFailed(f"Token expired: {err}") from err
        except Exception as err:
            self._stale_updates += 1
            if self.data and self._stale_updates <= MAX_STATION_STALE_UPDATES:
                _LOGGER.warning(
                    "SolarOfThings station %s: update failed (%s), retaining "
                    "previous data (%d/%d)",
                    self.station_id,
                    err,
                    self._stale_updates,
                    MAX_STATION_STALE_UPDATES,
                )
                return self.data
            raise UpdateFailed(f"Station update failed: {err}") from err


class SolarOfThingsDeviceCoordinator(DataUpdateCoordinator):
    """Fetch device-level telemetry + settings."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: SolarOfThingsAPI,
        station_id: str,
        device: str,
        device_meta: dict[str, Any],
        entry: ConfigEntry,
        station_device_id: str | None = None,
    ) -> None:
        self.api = api
        self.station_id = station_id
        self.device_id = device
        self.device_meta = device_meta
        self.station_device_id = station_device_id
        self._entry = entry
        self._is_first_refresh = True
        self._stale_updates = 0
        interval_min = entry.options.get("update_interval", 1)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_device_{device}",
            update_interval=timedelta(minutes=interval_min),
        )

    @callback
    def start_reauth(self) -> None:
        """Ask the user to re-authenticate the config entry."""
        self._entry.async_start_reauth(self.hass)

    @callback
    def async_set_pending_setting(self, key: str, value: Any) -> None:
        """Show a just-written setting value until the cloud reports it.

        The settings endpoint is a cache that lags behind the inverter, so
        without this the entity would flip back to the old value on the next
        refresh. The pending value wins until the cloud returns the same value
        or PENDING_SETTING_TTL expires.
        """
        self._pending_settings[key] = (value, dt_util.utcnow() + PENDING_SETTING_TTL)
        if self.data is not None:
            self.async_set_updated_data(
                {
                    **self.data,
                    "settings": self._apply_pending_settings(
                        self.data.get("settings") or {}, confirmed=False
                    ),
                }
            )

    def _apply_pending_settings(
        self, settings: dict[str, Any], *, confirmed: bool
    ) -> dict[str, Any]:
        """Overlay pending writes on settings.

        confirmed=True means settings came fresh from the cloud, so a pending
        value that now matches is considered applied and dropped.
        """
        if not self._pending_settings:
            return settings
        now = dt_util.utcnow()
        result = dict(settings)
        for key, (value, expires) in list(self._pending_settings.items()):
            if expires <= now or (
                confirmed and _setting_values_equal(_setting_raw(settings.get(key)), value)
            ):
                del self._pending_settings[key]
                continue
            current = result.get(key)
            result[key] = (
                {**current, "value": value}
                if isinstance(current, dict)
                else {"value": value}
            )
        return result

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._fetch_device_data()
        except TokenExpiredError as err:
            _LOGGER.error(
                "SolarOfThings device %s: token expired — triggering re-auth: %s",
                self.device_id,
                err,
            )
            self._entry.async_start_reauth(self.hass)
            raise UpdateFailed(f"Token expired: {err}") from err
        except Exception as err:
            self._stale_updates += 1
            if self.data and self._stale_updates <= MAX_DEVICE_STALE_UPDATES:
                _LOGGER.warning(
                    "SolarOfThings device %s: update failed (%s), retaining previous "
                    "data (%d/%d)",
                    self.device_id,
                    err,
                    self._stale_updates,
                    MAX_DEVICE_STALE_UPDATES,
                )
                return {
                    **self.data,
                    "latest_state_fresh": False,
                    "time_series_fresh": False,
                }
            raise UpdateFailed(
                f"Device update failed for {self.device_id}: {err}"
            ) from err

    async def _fetch_device_data(self) -> dict[str, Any]:
        """Fetch telemetry + settings.

        The returned dict carries ``latest_state_fresh`` / ``time_series_fresh``
        flags so that entities (e.g. the energy integrators) can tell freshly
        fetched values from values carried over from a previous update.
        """
        time_series: dict[str, Any] = {}
        try:
            time_series = await self.hass.async_add_executor_job(
                self.api.fetch_latest_data, self.device_id
            )
        except TokenExpiredError:
            raise
        except Exception as ts_err:
            _LOGGER.debug(
                "SolarOfThings device %s: timeseries fetch unavailable (%s), "
                "falling back to latest state",
                self.device_id,
                ts_err,
            )
        time_series_fresh = bool(time_series)

        latest_state: dict[str, Any] = {}
        latest_state_fresh = False
        try:
            latest_state = await self.hass.async_add_executor_job(
                self.api.fetch_latest_state, self.device_id
            )
            latest_state_fresh = True
        except TokenExpiredError:
            raise
        except Exception as ls_err:
            if not time_series_fresh:
                # Nothing fresh at all — let the caller apply the stale policy.
                raise
            self._stale_updates += 1
            _LOGGER.warning(
                "SolarOfThings device %s: latest state fetch failed (%s) (%d/%d)",
                self.device_id,
                ls_err,
                self._stale_updates,
                MAX_DEVICE_STALE_UPDATES,
            )
            if (
                self._stale_updates <= MAX_DEVICE_STALE_UPDATES
                and self.data
                and self.data.get("latest_state")
            ):
                latest_state = self.data["latest_state"]

        if latest_state_fresh:
            self._stale_updates = 0

        # If timeseries failed or returned empty data, derive power metrics
        # from a freshly fetched latest_state.
        if not time_series and latest_state_fresh:
            time_series = _derive_time_series(latest_state)
            time_series_fresh = bool(time_series)

        # Retrieve settings from fast cached cloud endpoint.  Settings change
        # rarely, so carrying over the previous values is harmless.
        settings: dict[str, Any] = {}
        try:
            settings = await self.hass.async_add_executor_job(
                self.api.fetch_settings, self.device_id
            )
        except TokenExpiredError:
            raise
        except Exception as set_err:
            _LOGGER.debug(
                "SolarOfThings device %s: settings fetch error (%s)",
                self.device_id,
                set_err,
            )
            if self.data and self.data.get("settings"):
                settings = self.data["settings"]

        # If cached settings are completely empty on first run, attempt batch read fallback
        if not settings and self._is_first_refresh:
            _LOGGER.debug(
                "SolarOfThings device %s: cached settings empty, attempting batch read",
                self.device_id,
            )
            try:
                settings = await self.hass.async_add_executor_job(
                    self.api.fetch_settings_batch, self.device_id
                )
            except TokenExpiredError:
                raise
            except Exception as batch_err:
                _LOGGER.debug(
                    "SolarOfThings device %s: batch read fallback failed: %s",
                    self.device_id,
                    batch_err,
                )

        self._is_first_refresh = False

        return {
            "time_series": time_series,
            "latest_state": latest_state,
            "settings": settings,
            "device": self.device_id,
            "station_id": self.station_id,
            "device_meta": self.device_meta,
            "time_series_fresh": time_series_fresh,
            "latest_state_fresh": latest_state_fresh,
        }


def _state_value(latest_state: dict[str, Any], key: str) -> float:
    """Return a latest_state field as float (0.0 when missing)."""
    return float((latest_state.get(key) or {}).get("value") or 0)


def _derive_time_series(latest_state: dict[str, Any]) -> dict[str, Any]:
    """Compute the derived power metrics from latest_state fields."""
    try:
        voltage = _state_value(latest_state, "batteryVoltage")
        discharge = _state_value(latest_state, "batteryDischargeCurrent")
        charge = _state_value(latest_state, "batteryChargingCurrent")
        battery_power = (discharge - charge) * voltage

        pv_power = _state_value(latest_state, "pvInputPower")
        pv2_power = _state_value(latest_state, "pv2InputPower")
        total_pv_power = round(pv_power + pv2_power, 2)

        ac_output = _state_value(latest_state, "acOutputActivePower")
        ac_unit = (latest_state.get("acOutputActivePower") or {}).get("unit") or ""
        if ac_unit.lower() == "kw":
            ac_output *= 1000.0

        feed_in = _state_value(latest_state, "feedInPower")
    except (TypeError, ValueError) as calc_err:
        _LOGGER.debug(
            "SolarOfThings: could not derive power values from latest_state: %s",
            calc_err,
        )
        return {}

    return {
        "batteryVoltage": voltage,
        "batteryDischargeCurrent": discharge,
        "batteryChargingCurrent": charge,
        "batteryPower": battery_power,
        "batteryChargingPower": round(charge * voltage, 2),
        "batteryDischargePower": round(discharge * voltage, 2),
        "pvInputPower": pv_power,
        "pv2InputPower": pv2_power,
        "totalPvPower": total_pv_power,
        "acOutputActivePower": ac_output,
        "feedInPower": feed_in,
        "gridPower": max(0.0, ac_output - total_pv_power + battery_power + feed_in),
        "loadPower": ac_output,
    }
