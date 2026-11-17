"""Tests for entry setup, coordinators and the energy integrator."""

from __future__ import annotations

from collections.abc import Generator
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_of_things.api import SolarOfThingsAPI, TokenExpiredError
from custom_components.solar_of_things.const import (
    DOMAIN,
    MAX_DEVICE_STALE_UPDATES,
)

STATION_ID = "st1"
DEVICE_ID = "dev1"
API_PATH = "custom_components.solar_of_things.api.SolarOfThingsAPI"


class FakeCloud:
    """Mutable stand-in for the Siseli cloud used by the patched API methods."""

    def __init__(self) -> None:
        self.pv_power = 1000.0
        self.fail: Exception | None = None

    def list_devices(self, station_id=None, page_size=50):
        return [{"id": DEVICE_ID, "name": "Inverter", "stationId": STATION_ID}]

    def fetch_monthly_summary(self, station_id):
        return {"monthly_pv_generated": 10.0}

    def fetch_latest_data(self, device_id):
        if self.fail:
            raise self.fail
        return {}

    def fetch_latest_state(self, device_id):
        if self.fail:
            raise self.fail
        return {
            "pvInputPower": {"value": self.pv_power, "unit": "W"},
            "pv2InputPower": {"value": 0, "unit": "W"},
            "batterySOC": {"value": 80, "unit": "%"},
        }

    def fetch_settings(self, device_id):
        return {"ecoMode": {"value": 0}}


@pytest.fixture
def cloud() -> Generator[FakeCloud]:
    fake = FakeCloud()
    with (
        patch(f"{API_PATH}.list_devices", side_effect=fake.list_devices),
        patch(f"{API_PATH}.fetch_monthly_summary", side_effect=fake.fetch_monthly_summary),
        patch(f"{API_PATH}.fetch_latest_data", side_effect=fake.fetch_latest_data),
        patch(f"{API_PATH}.fetch_latest_state", side_effect=fake.fetch_latest_state),
        patch(f"{API_PATH}.fetch_settings", side_effect=fake.fetch_settings),
    ):
        yield fake


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        unique_id=f"station_{STATION_ID}",
        data={"iot_token": "token", "station_id": STATION_ID, "time_zone": "UTC"},
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _device_coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    return hass.data[DOMAIN][entry.entry_id]["device_coordinators"][DEVICE_ID]


async def test_setup_registers_station_and_device(
    hass: HomeAssistant, entry: MockConfigEntry, cloud: FakeCloud
) -> None:
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    dev_reg = dr.async_get(hass)
    station = dev_reg.async_get_device(identifiers={(DOMAIN, STATION_ID)})
    inverter = dev_reg.async_get_device(identifiers={(DOMAIN, f"{STATION_ID}_{DEVICE_ID}")})
    assert station is not None
    assert inverter is not None
    assert inverter.via_device_id == station.id


async def test_legacy_device_identifier_is_migrated(
    hass: HomeAssistant, entry: MockConfigEntry, cloud: FakeCloud
) -> None:
    dev_reg = dr.async_get(hass)
    legacy = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, STATION_ID, DEVICE_ID)},
        name="Inverter",
    )

    await _setup(hass, entry)

    migrated = dev_reg.async_get_device(identifiers={(DOMAIN, f"{STATION_ID}_{DEVICE_ID}")})
    assert migrated is not None
    assert migrated.id == legacy.id


async def test_device_data_is_kept_only_for_limited_failures(
    hass: HomeAssistant, entry: MockConfigEntry, cloud: FakeCloud
) -> None:
    await _setup(hass, entry)
    coordinator = _device_coordinator(hass, entry)
    cloud.fail = RuntimeError("cloud error 20000")

    for _ in range(MAX_DEVICE_STALE_UPDATES):
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert coordinator.data["latest_state_fresh"] is False

    await coordinator.async_refresh()
    assert not coordinator.last_update_success

    cloud.fail = None
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data["latest_state_fresh"] is True


async def test_token_expired_starts_reauth(
    hass: HomeAssistant, entry: MockConfigEntry, cloud: FakeCloud
) -> None:
    await _setup(hass, entry)
    cloud.fail = TokenExpiredError("expired")

    await _device_coordinator(hass, entry).async_refresh()
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_token_refresh_does_not_reload_entry(
    hass: HomeAssistant, entry: MockConfigEntry, cloud: FakeCloud
) -> None:
    await _setup(hass, entry)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, "iot_token": "new", "refresh_token": "r"},
        )
        await hass.async_block_till_done()
        reload.assert_not_called()

        hass.config_entries.async_update_entry(entry, options={"update_interval": 5})
        await hass.async_block_till_done()
        reload.assert_called_once_with(entry.entry_id)


async def test_energy_is_not_integrated_from_stale_data(
    hass: HomeAssistant, entry: MockConfigEntry, cloud: FakeCloud, freezer
) -> None:
    await _setup(hass, entry)
    coordinator = _device_coordinator(hass, entry)
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_{STATION_ID}_{DEVICE_ID}_totalPvEnergy"
    )
    assert entity_id is not None

    async def refresh_after(seconds: int) -> float:
        freezer.tick(timedelta(seconds=seconds))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        return float(hass.states.get(entity_id).state)

    await refresh_after(60)  # first fresh sample
    after_one_minute = await refresh_after(60)  # 1000 W for 60 s
    assert after_one_minute == pytest.approx(1000 * 60 / 3_600_000, abs=1e-3)

    cloud.fail = RuntimeError("cloud error")
    assert await refresh_after(600) == after_one_minute
    assert await refresh_after(600) == after_one_minute

    cloud.fail = None
    # First fresh sample after the outage only restarts integration.
    assert await refresh_after(60) == after_one_minute
    assert await refresh_after(60) == pytest.approx(2 * 1000 * 60 / 3_600_000, abs=1e-3)


async def test_unload_entry(hass: HomeAssistant, entry: MockConfigEntry, cloud: FakeCloud) -> None:
    await _setup(hass, entry)

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert entry.entry_id not in hass.data[DOMAIN]


def test_api_class_is_patched_target() -> None:
    """Guard: the patch target used above must stay the real API class."""
    assert SolarOfThingsAPI.__module__ == "custom_components.solar_of_things.api"
