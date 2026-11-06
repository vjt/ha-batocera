"""Launching games from Home Assistant: play_media on the media player."""

from datetime import timedelta

import pytest

from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized
from homeassistant.util import dt as dt_util

from conftest import MARIO, SONIC, publish_catalog
from test_catalog import fire


@pytest.fixture
async def ready(hass, mqtt_mock, setup, daemon):
    await publish_catalog(hass)
    fire(hass, "availability", "online")
    fire(hass, "state", "idle")
    await hass.async_block_till_done()
    daemon.answers["launch"] = {"ok": True}
    return setup


async def launch(hass, content_id, user=None):
    await hass.services.async_call(
        "media_player", "play_media",
        {"entity_id": "media_player.arcade", "media_content_id": content_id,
         "media_content_type": "game"},
        blocking=True, context=Context(user_id=user.id if user else None),
    )


async def test_launch(hass, ready, daemon, hass_admin_user):
    features = hass.states.get("media_player.arcade").attributes["supported_features"]
    assert features & 512  # PLAY_MEDIA
    await launch(hass, f"game:snes:{MARIO}", hass_admin_user)
    [req] = daemon.requests
    assert (req["op"], req["system"], req["game"]) == ("launch", "snes", MARIO)
    await launch(hass, f"game:megadrive:{SONIC}")  # an automation
    assert len(daemon.requests) == 2


async def test_launch_needs_control(hass, ready, daemon, kid):
    with pytest.raises(Unauthorized):
        await launch(hass, f"game:snes:{MARIO}", kid)
    hass.config_entries.async_update_entry(ready, options={"control_users": [kid.id]})
    await launch(hass, f"game:snes:{MARIO}", kid)
    assert len(daemon.requests) == 1


async def test_launch_refused_by_home_assistant(hass, ready, daemon):
    with pytest.raises(ServiceValidationError) as err:
        await launch(hass, f"game:megadrive:{MARIO}")  # wrong system
    assert err.value.translation_key == "unknown_game"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.arcade_manual_lock"}, blocking=True
    )
    with pytest.raises(ServiceValidationError) as err:
        await launch(hass, f"game:snes:{MARIO}")
    assert err.value.translation_key == "locked_manual"
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.arcade_manual_lock"}, blocking=True
    )

    # Turned off: the player is there, showing "off".
    fire(hass, "state", "off")
    fire(hass, "availability", "offline")
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as err:
        await launch(hass, f"game:snes:{MARIO}")
    assert err.value.translation_key == "offline"
    # Unreachable: the player is unavailable, Home Assistant calls nothing.
    fire(hass, "state", "idle")
    await hass.async_block_till_done()
    await launch(hass, f"game:snes:{MARIO}")
    assert daemon.requests == []


@pytest.mark.parametrize(
    ("error", "key"),
    [("busy", "launch_busy"), ("locked", "launch_locked"), ("weird", "launch_failed")],
)
async def test_launch_refused_by_the_box(hass, ready, daemon, error, key):
    daemon.answers["launch"] = {"ok": False, "error": error}
    with pytest.raises(HomeAssistantError) as err:
        await launch(hass, f"game:snes:{MARIO}")
    assert err.value.translation_key == key
    assert err.value.translation_placeholders["game"] == "Super Mario World"


async def playing(hass, pausable):
    for leaf, payload in (("game", "Super Mario World"), ("system", "snes"), ("game_id", MARIO),
                          ("pausable", pausable), ("paused", "OFF"), ("state", "playing")):
        fire(hass, leaf, payload)
    await hass.async_block_till_done()
    return hass.states.get("media_player.arcade")


PAUSE = 1 | 16384  # PAUSE | PLAY


async def test_pause_follows_the_emulator(hass, ready):
    assert not hass.states.get("media_player.arcade").attributes["supported_features"] & PAUSE
    player = await playing(hass, "ON")
    assert player.attributes["supported_features"] & PAUSE == PAUSE
    assert player.attributes["pausable"] is True
    player = await playing(hass, "")  # not known: offered, the box will tell
    assert player.attributes["supported_features"] & PAUSE == PAUSE
    assert player.attributes["pausable"] is None
    player = await playing(hass, "OFF")
    assert not player.attributes["supported_features"] & PAUSE
    assert player.attributes["pausable"] is False


async def test_pause_and_resume(hass, ready, daemon, kid):
    await playing(hass, "ON")
    daemon.answers["pause"] = lambda req: {"ok": True, "paused": req["paused"]}
    await hass.services.async_call(
        "media_player", "media_pause", {"entity_id": "media_player.arcade"}, blocking=True
    )
    assert daemon.requests[-1]["op"] == "pause"
    assert daemon.requests[-1]["paused"] is True
    fire(hass, "paused", "ON")  # the box's belief, retained
    await hass.async_block_till_done()
    assert hass.states.get("media_player.arcade").state == "paused"
    await hass.services.async_call(
        "media_player", "media_play", {"entity_id": "media_player.arcade"}, blocking=True
    )
    assert daemon.requests[-1]["paused"] is False

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            "media_player", "media_play", {"entity_id": "media_player.arcade"},
            blocking=True, context=Context(user_id=kid.id),
        )
    daemon.answers["pause"] = {"ok": False, "error": "not_pausable"}
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "media_player", "media_pause", {"entity_id": "media_player.arcade"}, blocking=True
        )
    assert err.value.translation_key == "pause_not_pausable"


async def test_minutes_left(hass, ready, freezer):
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.arcade_daily_limit", "value": 40},
        blocking=True,
    )
    assert hass.states.get("media_player.arcade").attributes["minutes_left"] is None  # idle
    fire(hass, "started_at", (dt_util.now() - timedelta(minutes=30)).isoformat())
    player = await playing(hass, "ON")
    assert player.attributes["minutes_left"] == 10
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.arcade_daily_limit", "value": 0},
        blocking=True,
    )
    assert hass.states.get("media_player.arcade").attributes["minutes_left"] is None  # free play
