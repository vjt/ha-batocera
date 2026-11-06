"""Fixtures for the Home Assistant integration tests."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.batocera.const import CONF_TOPIC, DOMAIN

TOPIC = "batocera/test"
NOW = "2026-03-02T10:00:00-08:00"  # tests run in US/Pacific


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="Arcade", data={CONF_TOPIC: TOPIC}, unique_id=TOPIC)
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup(hass, mqtt_mock, entry, freezer):
    freezer.move_to(NOW)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
