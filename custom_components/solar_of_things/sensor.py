"""Sensor platform for Solar of Things integration."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SENSOR_DEFINITIONS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solar of Things sensors."""

    data = hass.data[DOMAIN][entry.entry_id]
    station_id: str = data["station_id"]
    device_coordinators = data["device_coordinators"]
    station_coordinator = data["station_coordinator"]

    entities: list[SensorEntity] = []

    # Per-device sensors
    for device_id, coordinator in device_coordinators.items():
        device_name = (coordinator.device_meta or {}).get("name") or device_id
        latest_state = (coordinator.data or {}).get("latest_state") or {}

        # Create sensors for both predefined keys and discovered keys from the API response
        all_keys = set(SENSOR_DEFINITIONS.keys()) | set(latest_state.keys())

        for key in all_keys:
            if key.startswith("monthly_"):
                continue

            if key in SENSOR_DEFINITIONS:
                definition = SENSOR_DEFINITIONS[key]
            else:
                # For discovered keys, use information from the latest API response
                info = latest_state.get(key, {})
                definition = {
                    "name": info.get("nameDisplay", key.replace("_", " ").title()),
                    "unit": info.get("unit", ""),
                    "icon": "mdi:sensor",
                }

            entities.append(
                SolarOfThingsDeviceSensor(
                    coordinator=coordinator,
                    station_id=station_id,
                    device_id=device_id,
                    device_name=device_name,
                    sensor_key=key,
                    sensor_definition=definition,
                )
            )

    # Station-level monthly sensors
    if station_coordinator:
        for key, definition in SENSOR_DEFINITIONS.items():
            if not key.startswith("monthly_"):
                continue

            entities.append(
                SolarOfThingsStationMonthlySensor(
                    coordinator=station_coordinator,
                    station_id=station_id,
                    sensor_key=key,
                    sensor_definition=definition,
                )
            )

    async_add_entities(entities)


class SolarOfThingsDeviceSensor(CoordinatorEntity, SensorEntity):
    """Per-device telemetry sensor."""

    def __init__(
        self,
        coordinator,
        station_id: str,
        device_id: str,
        device_name: str,
        sensor_key: str,
        sensor_definition: dict,
    ) -> None:
        super().__init__(coordinator)

        self._station_id = station_id
        self._device_id = device_id
        self._device_name = device_name
        self._sensor_key = sensor_key
        self._sensor_definition = sensor_definition

        self._attr_name = f"{device_name} {sensor_definition['name']}"
        self._attr_unique_id = f"{DOMAIN}_{station_id}_{device_id}_{sensor_key}"
        self._attr_icon = sensor_definition.get("icon")

        unit_raw = str(sensor_definition.get("unit") or "").strip()
        unit_lower = unit_raw.lower()
        key_lower = sensor_key.lower()

        # Energy (kWh, Wh, MWh, or keys indicating energy)
        if unit_lower in ("kwh", "kilowatt-hour", "kilowatt_hour"):
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        elif unit_lower in ("wh", "watt-hour", "watt_hour") or (
            "energy" in key_lower and unit_lower not in ("w", "kw", "mw")
        ):
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = (
                UnitOfEnergy.KILO_WATT_HOUR
                if unit_lower == "kwh"
                else UnitOfEnergy.WATT_HOUR
            )
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        elif unit_lower in ("mwh",):
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.MEGA_WATT_HOUR
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        # Power (W, kW)
        elif unit_lower in ("w", "watt"):
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_native_unit_of_measurement = UnitOfPower.WATT
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif unit_lower in ("kw", "kilowatt"):
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
            self._attr_state_class = SensorStateClass.MEASUREMENT
        # Current (A)
        elif unit_lower in ("a", "amp", "ampere"):
            self._attr_device_class = SensorDeviceClass.CURRENT
            self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
            self._attr_state_class = SensorStateClass.MEASUREMENT
        # Voltage (V)
        elif unit_lower in ("v", "volt"):
            self._attr_device_class = SensorDeviceClass.VOLTAGE
            self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
            self._attr_state_class = SensorStateClass.MEASUREMENT
        # Percentage (%)
        elif unit_lower in ("%", "percent"):
            if "battery" in key_lower or "soc" in key_lower:
                self._attr_device_class = SensorDeviceClass.BATTERY
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_state_class = SensorStateClass.MEASUREMENT
        # Frequency (Hz)
        elif unit_lower in ("hz", "hertz"):
            self._attr_device_class = SensorDeviceClass.FREQUENCY
            self._attr_native_unit_of_measurement = UnitOfFrequency.HERTZ
            self._attr_state_class = SensorStateClass.MEASUREMENT
        # Temperature (°C, C, ℃)
        elif unit_lower in ("°c", "c", "℃"):
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif unit_raw:
            self._attr_native_unit_of_measurement = unit_raw

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._station_id, self._device_id)},
            "name": self._device_name,
            "manufacturer": "Siseli",
            "model": (self.coordinator.data.get("device_meta") or {}).get("model")
            if self.coordinator.data
            else None,
            "via_device": (DOMAIN, self._station_id),
        }

    @property
    def native_value(self):
        latest = (self.coordinator.data or {}).get("latest_state") or {}
        info = latest.get(self._sensor_key)
        if info is not None and info.get("value") is not None:
            try:
                return round(float(info["value"]), 2)
            except (ValueError, TypeError):
                return info["value"]

        # Fallback to time_series for calculated values (batteryPower, gridPower, loadPower)
        ts = (self.coordinator.data or {}).get("time_series") or {}
        val = ts.get(self._sensor_key)
        if val is not None:
            try:
                return round(float(val), 2)
            except (ValueError, TypeError):
                return val

        return None


class SolarOfThingsStationMonthlySensor(CoordinatorEntity, SensorEntity):
    """Station-level monthly summary sensor."""

    def __init__(
        self,
        coordinator,
        station_id: str,
        sensor_key: str,
        sensor_definition: dict,
    ) -> None:
        super().__init__(coordinator)

        self._station_id = station_id
        self._sensor_key = sensor_key
        self._sensor_definition = sensor_definition

        self._attr_name = f"Station {station_id} {sensor_definition['name']}"
        self._attr_unique_id = f"{DOMAIN}_{station_id}_{sensor_key}"
        self._attr_icon = sensor_definition.get("icon")

        unit = sensor_definition.get("unit")
        if unit == "kWh":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_state_class = SensorStateClass.TOTAL
        elif unit == "%":
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._station_id)},
            "name": f"Solar Station {self._station_id}",
            "manufacturer": "Siseli",
            "model": "Station",
        }

    @property
    def native_value(self):
        monthly = (self.coordinator.data or {}).get("monthly") or {}
        val = monthly.get(self._sensor_key)
        if val is None:
            return None
        try:
            return round(float(val), 2)
        except (ValueError, TypeError):
            return val
