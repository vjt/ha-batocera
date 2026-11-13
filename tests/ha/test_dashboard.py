"""The Batocera sidebar page: one panel view and card per box."""

from types import SimpleNamespace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.batocera.const import CONF_TOPIC, DOMAIN
from custom_components.batocera.dashboard import CARD_FILE, _save


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


def test_the_card_and_its_font_ship_with_the_integration():
    assert CARD_FILE.is_file()
    fonts = CARD_FILE.parent / "fonts"
    assert (fonts / "PressStart2P-Regular.ttf").is_file()
    assert "SIL Open Font License" in (fonts / "OFL.txt").read_text()
