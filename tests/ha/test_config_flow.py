"""Config flow tests."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.batocera.const import DOMAIN


async def test_create_entry(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.batocera.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"name": "Arcade", "topic": " batocera/arcade/ "}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Arcade"
    assert result["data"] == {"topic": "batocera/arcade"}


async def test_rejects_wildcards_and_duplicates(hass, entry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Arcade", "topic": "batocera/#"}
    )
    assert result["errors"] == {"topic": "invalid_topic"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Again", "topic": "batocera/test"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
