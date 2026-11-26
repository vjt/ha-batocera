"""The Batocera sidebar page: one panel view and card per box."""

import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.batocera import dashboard
from custom_components.batocera.const import CONF_TOPIC, DOMAIN
from custom_components.batocera.dashboard import (
    CARD_FILE,
    CARD_URL,
    _save,
    async_setup_dashboard,  # before conftest's no_dashboard replaces it
)


async def test_one_card_per_box(hass, mqtt_mock, setup):
    from homeassistant.components.lovelace.dashboard import LovelaceStorage

    other = MockConfigEntry(domain=DOMAIN, title="Attic", data={CONF_TOPIC: "batocera/attic"},
                            unique_id="batocera/attic")
    other.add_to_hass(hass)
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()
    assert set(hass.data[DOMAIN]) == {setup.entry_id, other.entry_id}

    hass.data["lovelace"] = SimpleNamespace(dashboards={})
    await _save(hass, list(hass.data[DOMAIN].values()))
    storage = hass.data["lovelace"].dashboards["batocera"]
    assert isinstance(storage, LovelaceStorage)
    config = await storage.async_load(False)
    assert [(v["title"], v["path"], v["type"]) for v in config["views"]] == [
        ("Arcade", "default", "panel"), ("Attic", other.entry_id.lower(), "panel")
    ]
    card = config["views"][0]["cards"][0]
    assert card == {
        "type": "custom:batocera-card",
        "name": "Arcade",
        "entry_id": setup.entry_id,
        "entities": {
            "player": "media_player.arcade",
            "locked": "binary_sensor.arcade_locked",
            "playtime": "sensor.arcade_playtime_today",
            "started": "sensor.arcade_session_start",
            "limit": "number.arcade_daily_limit",
            "manual": "switch.arcade_manual_lock",
            "screen": "image.arcade_screen",
            "shutdown": "button.arcade_shut_down",
            "power_on": "button.arcade_power_on",
        },
    }

    # What the user added stays.
    card["entities"]["extra"] = "automation.bedtime"
    card["theme"] = "dark"
    await storage.async_save(config)
    await _save(hass, list(hass.data[DOMAIN].values()))
    card = (await storage.async_load(False))["views"][0]["cards"][0]
    assert card["entities"]["extra"] == "automation.bedtime"
    assert card["theme"] == "dark"
    assert card["entities"]["player"] == "media_player.arcade"

    # An unloaded box leaves the page.
    assert await hass.config_entries.async_unload(other.entry_id)
    assert set(hass.data[DOMAIN]) == {setup.entry_id}


async def test_the_card_url_changes_with_the_card(hass):
    """The card's URL carries its mtime, read in the executor: a stat in
    the event loop blocks it."""
    real, ran = dashboard._card_version, []

    def version():
        ran.append((threading.current_thread(), real()))
        return ran[-1][1]

    hass.http = Mock(async_register_static_paths=AsyncMock())
    with patch.object(dashboard, "_card_version", version), patch(
        "homeassistant.components.frontend.add_extra_js_url"
    ) as add_js, patch("homeassistant.components.frontend.async_register_built_in_panel"):
        await async_setup_dashboard(hass)
    [(thread, mtime)] = ran
    assert thread is not threading.current_thread()
    assert mtime == int((await hass.async_add_executor_job(CARD_FILE.stat)).st_mtime)
    add_js.assert_called_once_with(hass, f"{CARD_URL}?v={mtime}")


def test_the_card_and_its_font_ship_with_the_integration():
    assert CARD_FILE.is_file()
    fonts = CARD_FILE.parent / "fonts"
    assert (fonts / "PressStart2P-Regular.ttf").is_file()
    assert "SIL Open Font License" in (fonts / "OFL.txt").read_text()
