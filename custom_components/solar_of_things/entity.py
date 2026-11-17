"""Shared entity helpers for the Solar of Things integration."""

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


# via_device_id was added to DeviceInfo in HA 2026.8. Older versions reject
# unknown keys and refuse to add the entity, so they get the deprecated
# via_device tuple instead.
_SUPPORTS_VIA_DEVICE_ID = _device_info_supports("via_device_id")


def station_device_fields(station_id: str) -> dict[str, Any]:
    """Device registry fields for the station (parent) device."""
    return {
        "identifiers": {(DOMAIN, station_id)},
        "name": f"Solar Station {station_id}",
        "manufacturer": MANUFACTURER,
        "model": "Station",
    }


def station_device_info(station_id: str) -> DeviceInfo:
    """DeviceInfo for station-level entities."""
    return DeviceInfo(**station_device_fields(station_id))


def inverter_device_info(
    coordinator: SolarOfThingsDeviceCoordinator,
    station_id: str,
    device_id: str,
    device_name: str,
) -> DeviceInfo:
    """DeviceInfo for an inverter, linked to its station device."""
    fields: dict[str, Any] = {
        "identifiers": {(DOMAIN, station_id, device_id)},
        "name": device_name,
        "manufacturer": MANUFACTURER,
        "model": (coordinator.device_meta or {}).get("model"),
    }
    if _SUPPORTS_VIA_DEVICE_ID:
        if coordinator.station_device_id:
            fields["via_device_id"] = coordinator.station_device_id
    else:
        fields["via_device"] = (DOMAIN, station_id)
    return DeviceInfo(**fields)


class SolarOfThingsDeviceEntity(CoordinatorEntity):
    """Base for inverter entities that read and write device settings."""

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
