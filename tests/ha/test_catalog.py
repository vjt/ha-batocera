"""The catalog, covers, launch and pause, with the box faked over MQTT."""

import asyncio
import gzip
import json

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
