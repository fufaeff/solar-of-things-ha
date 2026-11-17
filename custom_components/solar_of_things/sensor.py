"""Sensor platform for Solar of Things integration."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SENSOR_DEFINITIONS
from .entity import inverter_device_info, station_device_info

_LOGGER = logging.getLogger(__name__)

ACCUMULATED_ENERGY_CONFIG: dict[str, str] = {
    "batteryChargingEnergy": "batteryChargingPower",
    "batteryDischargeEnergy": "batteryDischargePower",
    "acOutputEnergy": "acOutputActivePower",
    "totalPvEnergy": "totalPvPower",
}


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

            if key in ACCUMULATED_ENERGY_CONFIG:
                entities.append(
                    SolarOfThingsAccumulatedEnergySensor(
                        coordinator=coordinator,
                        station_id=station_id,
                        device_id=device_id,
                        device_name=device_name,
                        sensor_key=key,
                        sensor_definition=definition,
                        source_power_key=ACCUMULATED_ENERGY_CONFIG[key],
                    )
                )
            else:
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
    def device_info(self) -> DeviceInfo:
        return inverter_device_info(
            self.coordinator.data, self._station_id, self._device_id, self._device_name
        )

    @property
    def native_value(self):
        latest = (self.coordinator.data or {}).get("latest_state") or {}
        info = latest.get(self._sensor_key)
        if info is not None and info.get("value") is not None:
            try:
                val = float(info["value"])
                # Normalize kW to W if sensor is configured as Watts
                unit = str(info.get("unit") or "").strip().lower()
                if self.native_unit_of_measurement == UnitOfPower.WATT and unit == "kw":
                    val *= 1000.0
                elif self.native_unit_of_measurement == UnitOfPower.KILO_WATT and unit == "w":
                    val /= 1000.0
                return round(val, 2)
            except (ValueError, TypeError):
                return info["value"]

        # Fallback to time_series for calculated values (batteryPower, batteryChargingPower, batteryDischargePower, gridPower, loadPower)
        ts = (self.coordinator.data or {}).get("time_series") or {}
        val = ts.get(self._sensor_key)
        if val is not None:
            try:
                return round(float(val), 2)
            except (ValueError, TypeError):
                return val

        # Dynamic fallback calculation from latest_state if not present in time_series
        if latest:
            try:
                v_info = latest.get("batteryVoltage")
                if v_info and v_info.get("value") is not None:
                    v = float(v_info["value"])
                    if self._sensor_key == "batteryChargingPower":
                        c_info = latest.get("batteryChargingCurrent")
                        if c_info and c_info.get("value") is not None:
                            return round(v * float(c_info["value"]), 2)
                    elif self._sensor_key == "batteryDischargePower":
                        d_info = latest.get("batteryDischargeCurrent")
                        if d_info and d_info.get("value") is not None:
                            return round(v * float(d_info["value"]), 2)
                    elif self._sensor_key == "batteryPower":
                        c_val = float((latest.get("batteryChargingCurrent") or {}).get("value") or 0)
                        d_val = float((latest.get("batteryDischargeCurrent") or {}).get("value") or 0)
                        return round((d_val - c_val) * v, 2)
                    elif self._sensor_key == "totalPvPower":
                        p1 = float((latest.get("pvInputPower") or {}).get("value") or 0)
                        p2 = float((latest.get("pv2InputPower") or {}).get("value") or 0)
                        return round(p1 + p2, 2)
            except (ValueError, TypeError):
                pass

        if self._sensor_key == "totalPvPower" and latest:
            try:
                p1 = float((latest.get("pvInputPower") or {}).get("value") or 0)
                p2 = float((latest.get("pv2InputPower") or {}).get("value") or 0)
                return round(p1 + p2, 2)
            except (ValueError, TypeError):
                pass

        return None


class SolarOfThingsAccumulatedEnergySensor(SolarOfThingsDeviceSensor, RestoreEntity):
    """Energy sensor that accumulates kWh from power (W) over time with state persistence."""

    def __init__(
        self,
        coordinator,
        station_id: str,
        device_id: str,
        device_name: str,
        sensor_key: str,
        sensor_definition: dict,
        source_power_key: str,
    ) -> None:
        super().__init__(
            coordinator,
            station_id,
            device_id,
            device_name,
            sensor_key,
            sensor_definition,
        )
        self._source_power_key = source_power_key
        self._accumulated_kwh: float = 0.0
        self._last_power: float | None = None
        self._last_update_time: datetime | None = None

        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    async def async_added_to_hass(self) -> None:
        """Restore last accumulated energy state on Home Assistant startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if (
            last_state
            and last_state.state is not None
            and last_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        ):
            try:
                self._accumulated_kwh = float(last_state.state)
                _LOGGER.debug(
                    "SolarOfThings: restored accumulated energy for %s: %s kWh",
                    self.entity_id,
                    self._accumulated_kwh,
                )
            except (ValueError, TypeError):
                pass

    def _get_current_power(self) -> float | None:
        """Return the current source power in Watts, or None if not fresh.

        Only values fetched in the latest coordinator update are used; data
        carried over from a previous update must not be integrated again.
        """
        data = self.coordinator.data or {}

        if data.get("latest_state_fresh"):
            power = self._power_from_latest_state(data.get("latest_state") or {})
            if power is not None:
                return power

        if data.get("time_series_fresh"):
            val = (data.get("time_series") or {}).get(self._source_power_key)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass

        return None

    def _power_from_latest_state(self, latest: dict) -> float | None:
        """Extract the source power in Watts from latest_state fields."""

        def _value(key: str) -> float | None:
            info = latest.get(key)
            if not info or info.get("value") is None:
                return None
            try:
                return float(info["value"])
            except (ValueError, TypeError):
                return None

        if self._source_power_key == "totalPvPower":
            pv1 = _value("pvInputPower")
            pv2 = _value("pv2InputPower")
            if pv1 is None and pv2 is None:
                return None
            return (pv1 or 0.0) + (pv2 or 0.0)

        if self._source_power_key in ("batteryChargingPower", "batteryDischargePower"):
            voltage = _value("batteryVoltage")
            current = _value(
                "batteryChargingCurrent"
                if self._source_power_key == "batteryChargingPower"
                else "batteryDischargeCurrent"
            )
            if voltage is None or current is None:
                return None
            return voltage * current

        val = _value(self._source_power_key)
        if val is None:
            return None
        unit = str((latest.get(self._source_power_key) or {}).get("unit") or "")
        if unit.strip().lower() == "kw":
            val *= 1000.0
        return val

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator and integrate power into energy."""
        current_power = (
            self._get_current_power() if self.coordinator.last_update_success else None
        )
        now = dt_util.utcnow()

        if current_power is None or current_power < 0:
            # No fresh sample: forget the last one so the gap is not integrated
            # with outdated power once fresh data arrives again.
            self._last_power = None
            self._last_update_time = None
        else:
            if self._last_update_time is not None and self._last_power is not None:
                dt_seconds = (now - self._last_update_time).total_seconds()
                # Integrate if update interval is positive and within 2 hours
                if 0 < dt_seconds <= 7200:
                    avg_p = (self._last_power + current_power) / 2.0
                    self._accumulated_kwh += (avg_p * dt_seconds) / (3600.0 * 1000.0)

            self._last_power = current_power
            self._last_update_time = now

        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        return round(self._accumulated_kwh, 3)


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
            # Monthly counters drop to 0 at the start of each month; with
            # TOTAL_INCREASING the recorder treats that drop as a meter reset.
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        elif unit == "%":
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def device_info(self) -> DeviceInfo:
        return station_device_info(self._station_id)

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
