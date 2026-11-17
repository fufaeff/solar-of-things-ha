"""Shared device-registry helpers for Solar of Things entities."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN

MANUFACTURER = "Siseli"


def station_identifier(station_id: str) -> tuple[str, str]:
    """Return the device-registry identifier of a station."""
    return (DOMAIN, str(station_id))


def device_identifier(station_id: str, device_id: str) -> tuple[str, str]:
    """Return the device-registry identifier of an inverter/device."""
    return (DOMAIN, f"{station_id}_{device_id}")


def legacy_device_identifier(station_id: str, device_id: str) -> tuple[str, ...]:
    """Identifier used before v2.5.0 (invalid 3-tuple), kept for migration."""
    return (DOMAIN, str(station_id), str(device_id))


def station_device_info(station_id: str) -> DeviceInfo:
    """Device info of the station (parent of all inverters)."""
    return DeviceInfo(
        identifiers={station_identifier(station_id)},
        name=f"Solar Station {station_id}",
        manufacturer=MANUFACTURER,
        model="Station",
    )


def inverter_device_info(
    coordinator_data: dict[str, Any] | None,
    station_id: str,
    device_id: str,
    device_name: str,
) -> DeviceInfo:
    """Device info of a single inverter/device under a station."""
    device_meta = (coordinator_data or {}).get("device_meta") or {}
    return DeviceInfo(
        identifiers={device_identifier(station_id, device_id)},
        name=device_name,
        manufacturer=MANUFACTURER,
        model=device_meta.get("model"),
        via_device=station_identifier(station_id),
    )
