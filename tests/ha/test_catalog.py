"""The catalog, covers, launch and pause, with the box faked over MQTT."""

import asyncio
import base64
import gzip
import json
import os
from pathlib import Path
import time

import pytest
from pytest_homeassistant_custom_component.common import async_fire_mqtt_message

from custom_components.batocera.box import BoxError

from conftest import BARE, INDEX, MARIO, ROWS, SONIC, SOR, TOPIC, publish_catalog


def fire(hass, leaf, payload):
    async_fire_mqtt_message(hass, f"{TOPIC}/{leaf}", payload)


async def test_catalog_is_parsed(hass, mqtt_mock, setup):
    box = setup.runtime_data
    await publish_catalog(hass)
    assert [s["name"] for s in box.systems] == ["megadrive", "snes"]
    assert [g.name for g in box.system_games("megadrive")] == [
        "Bare", "Sonic the Hedgehog", "Streets of Rage"
    ]
    sonic = box.games[SONIC]
    assert (sonic.system, sonic.year, sonic.genre, sonic.players) == (
        "megadrive", 1991, "Platform", "1"
    )
    assert (sonic.favourite, sonic.has_cover, sonic.playcount) == (True, True, 12)
    assert not box.games[BARE].has_cover
    assert box.games[SOR].lastplayed == "20261104T200000"
    assert box.catalog_hash


async def test_catalog_follows_the_index(hass, mqtt_mock, setup):
    box = setup.runtime_data
    # Systems may come before or after the index that lists them.
    fire(hass, "catalog/system/snes", gzip.compress(json.dumps(ROWS["snes"]).encode()))
    await hass.async_block_till_done()
    assert box.games == {}
    await publish_catalog(hass)
    assert MARIO in box.games
    # A system no longer listed is gone, and so is a cleared one.
    fire(hass, "catalog/index", json.dumps(INDEX[:1]))
    await hass.async_block_till_done()
    assert MARIO not in box.games
    assert box.system_games("snes") == []
    fire(hass, "catalog/system/megadrive", b"")
    await hass.async_block_till_done()
    assert box.games == {}


async def test_bad_catalog_messages_are_ignored(hass, mqtt_mock, setup, caplog):
    box = setup.runtime_data
    await publish_catalog(hass)
    fire(hass, "catalog/system/megadrive", b"\x1f\x8b not gzip")
    fire(hass, "catalog/system/snes", json.dumps([["short"], ["NOT-AN-ID", "x", 1, "", "", 0, 0, ""]]))
    fire(hass, "catalog/index", json.dumps(INDEX + [{"name": "bad/name"}, "junk"]))
    await hass.async_block_till_done()
    assert "bad catalog message" in caplog.text
    assert [s["name"] for s in box.systems] == ["megadrive", "snes"]
    assert len(box.system_games("megadrive")) == 3  # kept
    assert box.system_games("snes") == []


async def test_new_leaves(hass, mqtt_mock, setup):
    box = setup.runtime_data
    for leaf, payload in (("version", "0.3.0"), ("game_id", SONIC), ("pausable", "ON"),
                          ("paused", "ON")):
        fire(hass, leaf, payload)
    await hass.async_block_till_done()
    assert (box.version, box.game_id, box.pausable, box.paused) == ("0.3.0", SONIC, True, True)
    for leaf, payload in (("game_id", ""), ("pausable", ""), ("paused", "")):
        fire(hass, leaf, payload)
    await hass.async_block_till_done()
    assert (box.game_id, box.pausable, box.paused) == (None, None, False)
    fire(hass, "pausable", "OFF")
    await hass.async_block_till_done()
    assert box.pausable is False


async def test_request_and_answer(hass, setup, daemon):
    box = setup.runtime_data
    daemon.answers["refresh"] = {"ok": True}
    assert (await box.async_request("refresh"))["ok"] is True
    [req] = daemon.requests
    assert req["op"] == "refresh"
    assert len(req["id"]) == 16 and int(req["id"], 16) >= 0

    daemon.answers["cover"] = lambda req: {"ok": False, "error": "no_cover"}
    with pytest.raises(BoxError) as err:
        await box.async_request("cover", system="megadrive", game=BARE)
    assert err.value.reason == "no_cover"
    assert daemon.requests[-1] == {"id": daemon.requests[-1]["id"], "op": "cover",
                                   "system": "megadrive", "game": BARE}


async def test_unanswered_request_times_out(hass, setup, daemon, freezer):
    box = setup.runtime_data
    task = hass.async_create_task(box.async_request("refresh", timeout=5))
    for _ in range(5):
        await asyncio.sleep(0)
    freezer.tick(6)  # time is frozen in these tests: move it past the timeout
    with pytest.raises(BoxError) as err:
        await task
    assert err.value.reason == "timeout"
    assert box._requests == {}
    # A late answer, or one to someone else, is ignored.
    fire(hass, f"resp/{daemon.requests[0]['id']}", json.dumps({"ok": True}))
    fire(hass, "resp/0123abcd", "not json")
    await hass.async_block_till_done()


async def browse(hass, client, content_id=""):
    await client.send_json_auto_id(
        {"type": "media_player/browse_media", "entity_id": "media_player.arcade",
         "media_content_id": content_id, "media_content_type": "directory"}
    )
    return await client.receive_json()


def children(result):
    return [(c["media_content_id"], c["title"]) for c in result["result"]["children"]]


async def test_browse(hass, mqtt_mock, setup, hass_ws_client):
    await publish_catalog(hass)
    client = await hass_ws_client(hass)
    assert children(await browse(hass, client)) == [
        ("fav", "Favourites"), ("recent", "Recently played"),
        ("system:megadrive", "Sega Mega Drive"), ("system:snes", "Super Nintendo"),
    ]
    result = (await browse(hass, client, "system:megadrive"))["result"]
    assert result["title"] == "Sega Mega Drive"
    assert [c["title"] for c in result["children"]] == [
        "Bare", "Sonic the Hedgehog", "Streets of Rage"
    ]
    bare, sonic, _ = result["children"]
    assert sonic["media_content_id"] == f"game:megadrive:{SONIC}"
    assert (sonic["can_play"], sonic["can_expand"], sonic["media_class"]) == (True, False, "game")
    assert sonic["thumbnail"].startswith(
        f"/api/media_player_proxy/media_player.arcade/browse_media/game/game:megadrive:{SONIC}?token="
    )
    assert bare["thumbnail"] is None  # no cover on the box

    assert children(await browse(hass, client, "fav")) == [
        (f"game:megadrive:{SONIC}", "Sonic the Hedgehog")
    ]
    assert children(await browse(hass, client, "recent")) == [
        (f"game:snes:{MARIO}", "Super Mario World"),
        (f"game:megadrive:{SOR}", "Streets of Rage"),
        (f"game:megadrive:{SONIC}", "Sonic the Hedgehog"),
    ]
    assert children(await browse(hass, client, "recent:snes")) == [
        (f"game:snes:{MARIO}", "Super Mario World")
    ]
    assert children(await browse(hass, client, "fav:snes")) == []
    for bad in ("system:nes", "system:", "game:snes:" + MARIO, "whatever"):
        assert (await browse(hass, client, bad))["success"] is False


async def test_running_game_has_a_content_id(hass, mqtt_mock, setup):
    for leaf, payload in (("availability", "online"), ("system", "megadrive"),
                          ("game_id", SONIC), ("state", "playing")):
        fire(hass, leaf, payload)
    await hass.async_block_till_done()
    player = hass.states.get("media_player.arcade")
    assert player.attributes["media_content_id"] == f"game:megadrive:{SONIC}"
    await publish_catalog(hass)
    assert hass.states.get("media_player.arcade").attributes["catalog"]


JPEG = b"\xff\xd8 a cover"


async def test_covers(hass, mqtt_mock, setup, daemon, hass_client, hass_ws_client):
    await publish_catalog(hass)
    fire(hass, "availability", "online")
    await hass.async_block_till_done()
    daemon.answers["cover"] = {"ok": True, "jpeg": base64.b64encode(JPEG).decode()}
    games = (await browse(hass, await hass_ws_client(hass), "system:megadrive"))["result"]
    url = games["children"][1]["thumbnail"]  # Sonic's, with the proxy's token
    client = await hass_client()
    response = await client.get(url)
    assert response.status == 200
    assert response.content_type == "image/jpeg"
    assert await response.read() == JPEG
    [req] = daemon.requests
    assert (req["op"], req["system"], req["game"]) == ("cover", "megadrive", SONIC)

    # Again: from memory. After a restart: from disk. Never asked twice.
    assert await (await client.get(url)).read() == JPEG
    box = setup.runtime_data
    box.covers._memory.clear()
    assert await (await client.get(url)).read() == JPEG
    assert len(daemon.requests) == 1
    path = Path(hass.config.cache_path("batocera", setup.entry_id)) / f"{SONIC}.jpg"
    assert path.read_bytes() == JPEG

    # A week later the disk copy is stale: asked again.
    box.covers._memory.clear()
    old = time.time() - 8 * 24 * 3600
    os.utime(path, (old, old))
    assert await (await client.get(url)).read() == JPEG
    assert len(daemon.requests) == 2

    # No cover on the box, a game it does not have: 404 without asking.
    bare = url.replace(SONIC, BARE)
    assert (await client.get(bare)).status == 404
    assert (await client.get(url.replace(SONIC, "0" * 12))).status == 404
    assert len(daemon.requests) == 2

    # Removing the box removes its covers.
    await hass.config_entries.async_remove(setup.entry_id)
    await hass.async_block_till_done()
    assert not path.parent.exists()


async def test_cover_fetched_once_while_asked_many_times(hass, mqtt_mock, setup, daemon):
    await publish_catalog(hass)
    fire(hass, "availability", "online")
    await hass.async_block_till_done()
    daemon.answers["cover"] = {"ok": True, "jpeg": base64.b64encode(JPEG).decode()}
    box = setup.runtime_data
    game = box.games[SOR]
    results = await asyncio.gather(*(box.covers.async_get(game) for _ in range(5)))
    assert results == [JPEG] * 5
    assert len(daemon.requests) == 1


async def test_cover_errors(hass, mqtt_mock, setup, daemon):
    await publish_catalog(hass)
    box = setup.runtime_data
    sonic = box.games[SONIC]
    assert await box.covers.async_get(sonic) is None  # offline: not even asked
    assert daemon.requests == []
    fire(hass, "availability", "online")
    await hass.async_block_till_done()
    daemon.answers["cover"] = {"ok": False, "error": "es_unreachable"}
    assert await box.covers.async_get(sonic) is None
    daemon.answers["cover"] = {"ok": True, "jpeg": "!!! not base64"}
    assert await box.covers.async_get(sonic) is None
    daemon.answers["cover"] = {"ok": True, "jpeg": base64.b64encode(b"GIF89a").decode()}
    assert await box.covers.async_get(sonic) is None
    assert len(daemon.requests) == 3
