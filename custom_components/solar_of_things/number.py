"""Number platform for Solar of Things integration."""

from __future__ import annotations

import logging

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, NUMBER_SETTING_DEFINITIONS
from .entity import SolarOfThingsDeviceEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities (controls) for each device."""

    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    station_id: str = data["station_id"]
    device_coordinators = data["device_coordinators"]

    entities: list[NumberEntity] = []

    for device_id, coordinator in device_coordinators.items():
        device_meta = (
            (coordinator.data.get("device_meta") or {}) if coordinator.data else {}
        )
        device_name = device_meta.get("name", device_id)

        entities.extend(
            [
                # Existing number entities
                SolarOfThingsMaximumTotalChargingCurrentNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsMaxUtilityChargeCurrentNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                # New number entities from NUMBER_SETTING_DEFINITIONS
                SolarOfThingsBmsLockMachineBatteryCapacityNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsBatteryConstantChargingVoltageNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsBatteryEqualizationIntervalNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsBatteryEqualizationTimeNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsBatteryEqualizationTimeoutNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsBatteryEqualizationVoltageNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsBatteryFloatChargingVoltageNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsBatteryRechargeVoltageNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsBatteryRedischargeVoltageNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsCtZeroPowerNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsDischargeCurrentLimitNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsInverterStartupBatteryCapacityNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsLowBatteryAlarmVoltageNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsRestoreBatteryDischargingCapacityNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsRestoreMainsChargingCapacityNumber(
                    api, coordinator, station_id, device_id, device_name
                ),
            ]
        )

    async_add_entities(entities)


def _get_setting_value(coordinator_data: dict | None, key: str) -> float | None:
    """Extract the numeric value for a device setting key from coordinator data."""
    settings = (coordinator_data or {}).get("settings") or {}
    entry = settings.get(key)
    if entry is None:
        return None
    raw = entry.get("value") if isinstance(entry, dict) else entry
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class _BaseNumber(SolarOfThingsDeviceEntity, NumberEntity):
    _setting_key: str
    # None -> write as int; N -> write as float rounded to N decimals
    _value_decimals: int | None = None

    async def async_set_native_value(self, value: float) -> None:
        if self._value_decimals is None:
            raw: float | int = int(value)
        else:
            raw = round(value, self._value_decimals)
        await self._async_write_setting(self._setting_key, raw)

    @property
    def native_value(self):
        return _get_setting_value(self.coordinator.data, self._setting_key)


class SolarOfThingsMaximumTotalChargingCurrentNumber(_BaseNumber):
    _setting_key = "setMaxChargingCurrent"

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} Maximum Total Charging Current"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_maximum_total_charging_current"
        )
        self._attr_native_min_value = 10
        self._attr_native_max_value = 160
        self._attr_native_step = 10
        self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
        self._attr_mode = NumberMode.SLIDER
        self._attr_device_class = NumberDeviceClass.CURRENT
        self._attr_icon = "mdi:battery-arrow-up"


class SolarOfThingsMaxUtilityChargeCurrentNumber(_BaseNumber):
    _setting_key = "setUtilityMaxChargingCurrent"

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} Max Utility Charge Current"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_max_utility_charge_current"
        )
        self._attr_native_min_value = 10
        self._attr_native_max_value = 140
        self._attr_native_step = 10
        self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
        self._attr_mode = NumberMode.SLIDER
        self._attr_device_class = NumberDeviceClass.CURRENT
        self._attr_icon = "mdi:transmission-tower-import"


class SolarOfThingsBmsLockMachineBatteryCapacityNumber(_BaseNumber):
    _setting_key = "bmsLockMachineBatteryCapacity"

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} BMS Inverter Cutoff"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_bms_lock_machine_battery_capacity"
        )
        self._attr_native_min_value = 5
        self._attr_native_max_value = 95
        self._attr_native_step = 5
        self._attr_native_unit_of_measurement = "%"
        self._attr_mode = NumberMode.BOX
        self._attr_icon = "mdi:battery-arrow-down"


class SolarOfThingsBatteryConstantChargingVoltageNumber(_BaseNumber):
    _setting_key = "setBatteryCVChargeVoltage"
    _value_decimals = 1

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} Bulk Voltage"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_battery_constant_charging_voltage"
        )
        self._attr_native_min_value = 48
        self._attr_native_max_value = 61
        self._attr_native_step = 0.1
        self._attr_native_unit_of_measurement = "V"
        self._attr_mode = NumberMode.BOX
        self._attr_device_class = NumberDeviceClass.VOLTAGE
        self._attr_icon = "mdi:battery-charging-high"


class SolarOfThingsBatteryEqualizationIntervalNumber(_BaseNumber):
    _setting_key = "batteryEqualizationIntervalSetting"

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} Equalization Interval"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_battery_equalization_interval"
        )
        self._attr_native_min_value = 0
        self._attr_native_max_value = 90
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = UnitOfTime.DAYS
        self._attr_mode = NumberMode.BOX
        self._attr_icon = "mdi:calendar-repeat"


class SolarOfThingsBatteryEqualizationTimeNumber(_BaseNumber):
    _setting_key = "batteryEqualizationTimeSetting"

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} Equalization Time"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_battery_equalization_time"
        )
        self._attr_native_min_value = 5
        self._attr_native_max_value = 900
        self._attr_native_step = 5
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        self._attr_mode = NumberMode.BOX
        self._attr_icon = "mdi:clock-outline"


class SolarOfThingsBatteryEqualizationTimeoutNumber(_BaseNumber):
    _setting_key = "batteryEqualizationTimeoutSetting"

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} Equalization Timeout"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_battery_equalization_timeout"
        )
        self._attr_native_min_value = 5
        self._attr_native_max_value = 900
        self._attr_native_step = 5
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        self._attr_mode = NumberMode.BOX
        self._attr_icon = "mdi:timer-outline"


class SolarOfThingsBatteryEqualizationVoltageNumber(_BaseNumber):
    _setting_key = "batteryEqualizationVoltageSetting"
    _value_decimals = 1

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} Equalization Voltage"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_battery_equalization_voltage"
        )
        self._attr_native_min_value = 48
        self._attr_native_max_value = 62
        self._attr_native_step = 0.1
        self._attr_native_unit_of_measurement = "V"
        self._attr_mode = NumberMode.BOX
        self._attr_device_class = NumberDeviceClass.VOLTAGE
        self._attr_icon = "mdi:battery-charging-high"


class SolarOfThingsBatteryFloatChargingVoltageNumber(_BaseNumber):
    _setting_key = "setBatteryFloatChargingVoltage"
    _value_decimals = 1

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} Float Voltage"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_battery_float_charging_voltage"
        )
        self._attr_native_min_value = 48
        self._attr_native_max_value = 61
        self._attr_native_step = 0.1
        self._attr_native_unit_of_measurement = "V"
        self._attr_mode = NumberMode.BOX
        self._attr_device_class = NumberDeviceClass.VOLTAGE
        self._attr_icon = "mdi:battery-heart"


class SolarOfThingsBatteryRechargeVoltageNumber(_BaseNumber):
    _setting_key = "batteryRechargeVoltageSetting"
    _value_decimals = 1

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} SBU Utility Takeover"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_battery_recharge_voltage"
        )
        self._attr_native_min_value = 44
        self._attr_native_max_value = 54
        self._attr_native_step = 0.5
        self._attr_native_unit_of_measurement = "V"
        self._attr_mode = NumberMode.BOX
        self._attr_device_class = NumberDeviceClass.VOLTAGE
        self._attr_icon = "mdi:transmission-tower-import"


class SolarOfThingsBatteryRedischargeVoltageNumber(_BaseNumber):
    _setting_key = "batteryRedischargeVoltageSetting"
    _value_decimals = 1

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} SBU Battery Takeover"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_battery_redischarge_voltage"
        )
        self._attr_native_min_value = 48
        self._attr_native_max_value = 58
        self._attr_native_step = 0.5
        self._attr_native_unit_of_measurement = "V"
        self._attr_mode = NumberMode.BOX
        self._attr_device_class = NumberDeviceClass.VOLTAGE
        self._attr_icon = "mdi:battery-check"


class SolarOfThingsCtZeroPowerNumber(_BaseNumber):
    _setting_key = "ctZeroPower"
    _value_decimals = 2

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} Main Input Power"
        self._attr_unique_id = f"{DOMAIN}_{station_id}_{device_id}_ct_zero_power"
        self._attr_native_min_value = 0.01
        self._attr_native_max_value = 0.5
        self._attr_native_step = 0.01
        self._attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
        self._attr_mode = NumberMode.BOX
        self._attr_icon = "mdi:home-lightning-bolt"


class SolarOfThingsDischargeCurrentLimitNumber(_BaseNumber):
    _setting_key = "dischargeCurrentLimit"

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} Maximum Battery Discharge Current"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_discharge_current_limit"
        )
        self._attr_native_min_value = 20
        self._attr_native_max_value = 200
        self._attr_native_step = 10
        self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
        self._attr_mode = NumberMode.BOX
        self._attr_device_class = NumberDeviceClass.CURRENT
        self._attr_icon = "mdi:battery-arrow-down"


class SolarOfThingsInverterStartupBatteryCapacityNumber(_BaseNumber):
    _setting_key = "inverterStartupBatteryCapacity"

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} Battery Startup SOC"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_inverter_startup_battery_capacity"
        )
        self._attr_native_min_value = 5
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "%"
        self._attr_mode = NumberMode.BOX
        self._attr_icon = "mdi:battery-check"


class SolarOfThingsLowBatteryAlarmVoltageNumber(_BaseNumber):
    _setting_key = "lowBatteryAlarmVoltageSetting"
    _value_decimals = 1

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} Battery Low Alarm"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_low_battery_alarm_voltage"
        )
        self._attr_native_min_value = 40
        self._attr_native_max_value = 54
        self._attr_native_step = 0.1
        self._attr_native_unit_of_measurement = "V"
        self._attr_mode = NumberMode.BOX
        self._attr_device_class = NumberDeviceClass.VOLTAGE
        self._attr_icon = "mdi:battery-alert"


class SolarOfThingsRestoreBatteryDischargingCapacityNumber(_BaseNumber):
    _setting_key = "restoreBatteryDischargingBatteryCapacity"

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} Battery DC Takeover"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_restore_battery_discharging_capacity"
        )
        self._attr_native_min_value = 5
        self._attr_native_max_value = 100
        self._attr_native_step = 5
        self._attr_native_unit_of_measurement = "%"
        self._attr_mode = NumberMode.BOX
        self._attr_icon = "mdi:battery-charging-high"


class SolarOfThingsRestoreMainsChargingCapacityNumber(_BaseNumber):
    _setting_key = "restoreMainsChargingBatteryCapacity"

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_name = f"{device_name} Battery Mains Charging Takeover"
        self._attr_unique_id = (
            f"{DOMAIN}_{station_id}_{device_id}_restore_mains_charging_capacity"
        )
        self._attr_native_min_value = 5
        self._attr_native_max_value = 95
        self._attr_native_step = 5
        self._attr_native_unit_of_measurement = "%"
        self._attr_mode = NumberMode.BOX
        self._attr_icon = "mdi:battery-charging-high"
