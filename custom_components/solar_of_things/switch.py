"""Switch platform for Solar of Things integration."""

from __future__ import annotations

import logging
from typing import NamedTuple

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, OUTPUT_MODE_MAP, OUTPUT_MODE_REVERSE, OUTPUT_MODE_SBU
from .entity import SolarOfThingsDeviceEntity

_LOGGER = logging.getLogger(__name__)

# Switch unique_id suffixes that no longer exist; their registry entries are
# removed so they don't linger as unavailable entities.
# grid_charging: wrote acInputRangeSetting (APL/UPS), now the AC Input Range select.
_RETIRED_SWITCH_SUFFIXES = ("grid_charging",)

# Output priority restored when Backup Mode is turned off and the previous
# mode is unknown: SUB (Solar + Utility -> Battery).
_DEFAULT_MODE_AFTER_BACKUP = OUTPUT_MODE_MAP["Solar + Utility -> Battery (SUB)"]
ATTR_MODE_BEFORE_BACKUP = "output_priority_before_backup"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    station_id: str = data["station_id"]
    device_coordinators = data["device_coordinators"]

    _remove_retired_switches(hass, station_id, device_coordinators)

    entities: list[SwitchEntity] = []

    for device_id, coordinator in device_coordinators.items():
        device_meta = (
            (coordinator.data.get("device_meta") or {}) if coordinator.data else {}
        )
        device_name = device_meta.get("name", device_id)

        entities.extend(
            [
                SolarOfThingsGridFeedInSwitch(
                    api, coordinator, station_id, device_id, device_name
                ),
                SolarOfThingsBackupModeSwitch(
                    api, coordinator, station_id, device_id, device_name
                ),
            ]
        )
        entities.extend(
            SolarOfThingsToggleSwitch(
                api, coordinator, station_id, device_id, device_name, spec
            )
            for spec in TOGGLE_SWITCHES
        )

    async_add_entities(entities)


def _remove_retired_switches(
    hass: HomeAssistant, station_id: str, device_ids
) -> None:
    """Drop registry entries of switches that were replaced by other entities."""
    ent_reg = er.async_get(hass)
    for device_id in device_ids:
        for suffix in _RETIRED_SWITCH_SUFFIXES:
            unique_id = f"{DOMAIN}_{station_id}_{device_id}_{suffix}"
            if entity_id := ent_reg.async_get_entity_id("switch", DOMAIN, unique_id):
                _LOGGER.info(
                    "SolarOfThings: removing retired switch %s (replaced by the "
                    "AC Input Range select)",
                    entity_id,
                )
                ent_reg.async_remove(entity_id)


def _setting_value(coordinator_data: dict | None, key: str) -> int | None:
    """Extract the integer value for a device setting key from coordinator data."""
    settings = (coordinator_data or {}).get("settings") or {}
    entry = settings.get(key)
    if entry is None:
        return None
    raw = entry.get("value") if isinstance(entry, dict) else entry
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class _BaseSwitch(SolarOfThingsDeviceEntity, SwitchEntity):
    """Base class for Solar of Things switches."""


class SolarOfThingsGridFeedInSwitch(_BaseSwitch):
    """Switch for GRID grid switch (batteryPowerLimitingSetting).

    0 = OFF (grid switch disabled / feed-in off).
    1 = ON  (grid switch enabled / feed-in on).
    """

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_translation_key = "grid_feed_in"
        self._attr_unique_id = f"{DOMAIN}_{station_id}_{device_id}_grid_feed_in"
        self._attr_device_class = SwitchDeviceClass.SWITCH
        self._attr_icon = "mdi:transmission-tower-export"

    @property
    def is_on(self) -> bool | None:
        val = _setting_value(self.coordinator.data, "batteryPowerLimitingSetting")
        if val is None:
            return None
        return val == 1  # 1=ON

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_write_setting("batteryPowerLimitingSetting", 1)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_write_setting("batteryPowerLimitingSetting", 0)


class SolarOfThingsBackupModeSwitch(_BaseSwitch, RestoreEntity):
    """Switch that maps to Output Source Priority SBU (backup/off-grid biased).

    ON  → outputSourcePrioritySetting = 2 (SBU: Solar+Battery first, grid last).
    OFF → restores the output priority that was active before SBU (SUB if
          unknown). Turning OFF while SBU is not active writes nothing, so the
          switch never overrides a mode chosen in the Output Source Priority select.

    The last non-SBU mode is tracked from coordinator data (however it was set)
    and kept across restarts through the restored state attributes.
    """

    _setting_key = "outputSourcePrioritySetting"

    def __init__(
        self, api, coordinator, station_id: str, device_id: str, device_name: str
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._attr_translation_key = "backup_mode"
        self._attr_unique_id = f"{DOMAIN}_{station_id}_{device_id}_backup_mode"
        self._attr_device_class = SwitchDeviceClass.SWITCH
        self._attr_icon = "mdi:shield-home"
        self._mode_before_backup: int = _DEFAULT_MODE_AFTER_BACKUP
        self._track_mode_before_backup()

    def _current_mode(self) -> int | None:
        return _setting_value(self.coordinator.data, self._setting_key)

    def _track_mode_before_backup(self) -> None:
        mode = self._current_mode()
        if mode is not None and mode != OUTPUT_MODE_SBU and mode in OUTPUT_MODE_REVERSE:
            self._mode_before_backup = mode

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._current_mode() != OUTPUT_MODE_SBU:
            return  # current data already holds the right mode
        last = await self.async_get_last_state()
        label = last.attributes.get(ATTR_MODE_BEFORE_BACKUP) if last else None
        restored = OUTPUT_MODE_MAP.get(label) if isinstance(label, str) else None
        if restored is not None and restored != OUTPUT_MODE_SBU:
            self._mode_before_backup = restored

    @callback
    def _handle_coordinator_update(self) -> None:
        self._track_mode_before_backup()
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {
            ATTR_MODE_BEFORE_BACKUP: OUTPUT_MODE_REVERSE.get(self._mode_before_backup)
        }

    @property
    def is_on(self) -> bool | None:
        val = self._current_mode()
        if val is None:
            return None
        return val == OUTPUT_MODE_SBU

    async def async_turn_on(self, **kwargs) -> None:
        if self._current_mode() == OUTPUT_MODE_SBU:
            return
        self._track_mode_before_backup()
        await self._async_write_setting(self._setting_key, OUTPUT_MODE_SBU)

    async def async_turn_off(self, **kwargs) -> None:
        if self._current_mode() != OUTPUT_MODE_SBU:
            return
        await self._async_write_setting(self._setting_key, self._mode_before_backup)


# ─── Toggle switches (0=Off, 1=On pattern) ──────────────────────────────────────


class ToggleSpec(NamedTuple):
    """A device setting exposed as a plain 0/1 switch."""

    setting_key: str  # API setting key
    key: str  # unique_id suffix and translation key
    icon: str


TOGGLE_SWITCHES: tuple[ToggleSpec, ...] = (
    ToggleSpec(
        "bmsFunctionEnableSetting",
        "bms_function_enable",
        "mdi:battery-lock",
    ),
    ToggleSpec(
        "batteryEqualizationModeEnableSetting",
        "equalization_mode_enable",
        "mdi:battery-heart-outline",
    ),
    ToggleSpec(
        "backlightOn",
        "backlight_on",
        "mdi:lightbulb",
    ),
    ToggleSpec(
        "buzzerOn",
        "buzzer_on",
        "mdi:bell-alert",
    ),
    ToggleSpec(
        "displayAutomaticallyReturnsToHomepage",
        "display_auto_homepage",
        "mdi:view-dashboard",
    ),
    ToggleSpec(
        "ecoMode",
        "eco_mode",
        "mdi:leaf",
    ),
    ToggleSpec(
        "inputSourceDetectionPromptSound",
        "input_source_beep",
        "mdi:volume-source",
    ),
    ToggleSpec(
        "overTemperatureAutomaticRestart",
        "overheat_restart",
        "mdi:thermometer-alert",
    ),
    ToggleSpec(
        "overloadToBypassOperation",
        "overload_bypass",
        "mdi:arrow-right-bold-hexagon-outline",
    ),
)


class SolarOfThingsToggleSwitch(_BaseSwitch):
    """On/off switch for a 0/1 device setting (device configuration)."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        api,
        coordinator,
        station_id: str,
        device_id: str,
        device_name: str,
        spec: ToggleSpec,
    ) -> None:
        super().__init__(api, coordinator, station_id, device_id, device_name)
        self._setting_key = spec.setting_key
        self._attr_unique_id = f"{DOMAIN}_{station_id}_{device_id}_{spec.key}"
        self._attr_translation_key = spec.key
        self._attr_icon = spec.icon

    @property
    def is_on(self) -> bool | None:
        val = _setting_value(self.coordinator.data, self._setting_key)
        if val is None:
            return None
        return val == 1

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_write_setting(self._setting_key, 1)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_write_setting(self._setting_key, 0)
