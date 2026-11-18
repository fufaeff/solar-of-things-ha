"""Shared device-registry helpers and base entity for Solar of Things."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import requests
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import SettingsWriteError, TokenExpiredError
from .const import DOMAIN

if TYPE_CHECKING:
    from . import SolarOfThingsDeviceCoordinator
    from .api import SolarOfThingsAPI

MANUFACTURER = "Siseli"


def _device_info_supports(field: str) -> bool:
    """Return True if this HA version's DeviceInfo accepts the given field."""
    # HA >= 2026.11: DeviceInfo is a dataclass
    dataclass_fields = getattr(DeviceInfo, "__dataclass_fields__", None)
    if dataclass_fields is not None:
        return field in dataclass_fields
    # Older HA: DeviceInfo is a TypedDict
    return field in getattr(DeviceInfo, "__annotations__", {})


# via_device_id was added to DeviceInfo in HA 2026.8, where via_device became
# deprecated (removed in 2027.8). Older versions reject unknown keys and refuse
# to add the entity, so they keep using the via_device tuple.
_SUPPORTS_VIA_DEVICE_ID = _device_info_supports("via_device_id")


def station_identifier(station_id: str) -> tuple[str, str]:
    """Return the device-registry identifier of a station."""
    return (DOMAIN, str(station_id))


def device_identifier(station_id: str, device_id: str) -> tuple[str, str]:
    """Return the device-registry identifier of an inverter/device."""
    return (DOMAIN, f"{station_id}_{device_id}")


def legacy_device_identifier(station_id: str, device_id: str) -> tuple[str, ...]:
    """Identifier used before v2.5.0 (invalid 3-tuple), kept for migration."""
    return (DOMAIN, str(station_id), str(device_id))


def station_device_fields(station_id: str) -> dict[str, Any]:
    """Device registry fields of the station (parent of all inverters).

    Plain dict for dev_reg.async_get_or_create(**fields); unpacking a DeviceInfo
    is deprecated since HA 2026.11.
    """
    return {
        "identifiers": {station_identifier(station_id)},
        "name": f"Solar Station {station_id}",
        "manufacturer": MANUFACTURER,
        "model": "Station",
    }


def station_device_info(station_id: str) -> DeviceInfo:
    """Device info of the station (parent of all inverters)."""
    return DeviceInfo(**station_device_fields(station_id))


def inverter_device_info(
    coordinator: SolarOfThingsDeviceCoordinator,
    station_id: str,
    device_id: str,
    device_name: str,
) -> DeviceInfo:
    """Device info of a single inverter/device under a station."""
    fields: dict[str, Any] = {
        "identifiers": {device_identifier(station_id, device_id)},
        "name": device_name,
        "manufacturer": MANUFACTURER,
        "model": (coordinator.device_meta or {}).get("model"),
    }
    if _SUPPORTS_VIA_DEVICE_ID:
        if coordinator.station_device_id:
            fields["via_device_id"] = coordinator.station_device_id
    else:
        fields["via_device"] = station_identifier(station_id)
    return DeviceInfo(**fields)


class SolarOfThingsDeviceEntity(CoordinatorEntity):
    """Base for inverter entities that read and write device settings.

    Names come from translations (entity.<platform>.<translation_key>.name);
    HA prefixes them with the device name.
    """

    _attr_has_entity_name = True
    coordinator: SolarOfThingsDeviceCoordinator

    def __init__(
        self,
        api: SolarOfThingsAPI,
        coordinator: SolarOfThingsDeviceCoordinator,
        station_id: str,
        device_id: str,
        device_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._station_id = station_id
        self._device_id = device_id
        self._device_name = device_name
        self._attr_device_info = inverter_device_info(
            coordinator, station_id, device_id, device_name
        )

    async def _async_write_setting(self, key: str, value: Any) -> None:
        """Write a setting, surface failures as HomeAssistantError, update state."""
        try:
            await self.hass.async_add_executor_job(
                self._api.write_setting, self._device_id, key, value
            )
        except TokenExpiredError as err:
            self.coordinator.start_reauth()
            raise HomeAssistantError(
                f"Solar of Things authentication expired while writing {key}; "
                "re-authenticate the integration and try again"
            ) from err
        except SettingsWriteError as err:
            raise HomeAssistantError(
                f"Inverter rejected {key}={value}: {err}"
            ) from err
        except requests.RequestException as err:
            raise HomeAssistantError(
                f"Could not reach the Solar of Things cloud to write {key}: {err}"
            ) from err

        self.coordinator.async_set_pending_setting(key, value)
        await self.coordinator.async_request_refresh()
