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

from custom_components.batocera.box import (
    LOGO_MAX,
    BoxError,
    Game,
    contained_logo,
    logo_type,
)

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


async def test_folder_from_the_ninth_column(hass, mqtt_mock, setup):
    box = setup.runtime_data
    rows = {"megadrive": [
        [SONIC, "Sonic the Hedgehog", 1991, "Platform", "1", 3, 12, "", "Fighter"],
        [SOR, "Streets of Rage", 1991, "", "", 0, 0, ""],  # an older daemon: flat
        [BARE, "Bare", None, "", "", 0, 0, "", 42],
    ], "snes": [[MARIO, "Super Mario World", 1990, "", "", 0, 0, "", "/Extra//Demos/../"]]}
    await publish_catalog(hass, rows=rows)
    assert box.games[SONIC].folder == "Fighter"
    assert box.games[SOR].folder == ""
    assert box.games[BARE].folder == ""
    assert box.games[MARIO].folder == "Extra/Demos"  # no empty, "." or ".." parts


SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"/>'
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


async def test_logos(hass, mqtt_mock, setup):
    box = setup.runtime_data
    fire(hass, "availability", "online")  # attributes only while available
    await publish_catalog(hass)
    fire(hass, "catalog/logo/snes", SVG)
    fire(hass, "catalog/logo/megadrive", PNG)
    fire(hass, "catalog/logo/nes", SVG)  # not in the index: kept, browse ignores it
    fire(hass, "catalog/logo/garbage", b"hello")
    fire(hass, "catalog/logo/huge", b"<svg>" + b" " * LOGO_MAX)
    await hass.async_block_till_done()
    contained = contained_logo(SVG)
    assert box.logos == {"snes": contained, "megadrive": PNG, "nes": contained}
    logos = hass.states.get("media_player.arcade").attributes["logos"]
    assert logos and set(box.logo_ids) == {"snes", "megadrive", "nes"}
    fire(hass, "catalog/logo/snes", b"")  # cleared
    fire(hass, "catalog/logo/megadrive", b"hello")  # replaced by garbage
    await hass.async_block_till_done()
    assert box.logos == {"nes": contained} and set(box.logo_ids) == {"nes"}
    # The panel is told: the attribute changes with the logos.
    assert hass.states.get("media_player.arcade").attributes["logos"] not in (None, logos)
    assert len(box.games) == 4  # the catalog is untouched


def test_logo_type():
    assert logo_type(SVG) == "image/svg+xml"
    assert logo_type(b"\xef\xbb\xbf  <svg/>") == "image/svg+xml"
    assert logo_type(PNG) == "image/png"
    assert logo_type(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert logo_type(b"<?xml version='1.0'?><html/>") is None
    assert logo_type(b"<html><body>404</body></html>") is None
    assert logo_type(b"") is None
    # Comments and a DOCTYPE may come first; <svg> inside something else may not.
    assert logo_type(b"<!-- Generator: Adobe Illustrator -->\n<svg/>") == "image/svg+xml"
    assert logo_type(b'<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
                     b'"svg11.dtd"><svg width="1"/>') == "image/svg+xml"
    assert logo_type(b"<?xml version='1.0'?><html><svg/></html>") is None
    assert logo_type(b"<!-- <svg/> -->") is None
    # A DOCTYPE with its own entities, as Illustrator writes it.
    assert logo_type(b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "svg11.dtd" [\n'
                     b'  <!ENTITY ns_svg "http://www.w3.org/2000/svg">\n]>\n<svg/>') == "image/svg+xml"


def test_logo_type_is_quick_on_anything():
    # Logos arrive in Home Assistant's event loop: telling one must not take
    # longer as blanks pile up in something that is no SVG.
    for junk in (b"<!---->" + b" " * 28 + b"x", b"<!DOCTYPE html>" + b"\n" * 28 + b"<html>",
                 b"<!---->" + b" " * 4000 + b"x"):
        start = time.monotonic()
        assert logo_type(junk) is None
        assert contained_logo(junk) is None
        assert time.monotonic() - start < 0.1


EVIL = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 100" onload="alert(1)">'
        b"<script>alert(2)</script></svg>")


def test_contained_logo():
    # An SVG is served inside one of ours, drawn as an image: its scripts
    # never run, even when the logo is opened on its own.
    served = contained_logo(EVIL)
    assert served.startswith(b'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="100" '
                             b'viewBox="0 0 400 100"><image width="400" height="100" '
                             b'href="data:image/svg+xml;base64,')
    assert b"script" not in served and b"onload" not in served
    href = served.split(b'href="', 1)[1].split(b'"', 1)[0]
    assert base64.b64decode(href.removeprefix(b"data:image/svg+xml;base64,")) == EVIL
    assert logo_type(served) == "image/svg+xml"
    # The size: width and height, else the viewBox, else what browsers use.
    assert b'width="64" height="32"' in contained_logo(b'<svg width="64px" height="32"/>')
    assert b'width="400" height="100"' in contained_logo(
        b'<svg viewBox="0 0 100 100" width="400" height="100"/>')
    assert b'width="64" height="32"' in contained_logo(
        b'<!-- <svg width="1" height="1000"> --><svg width="64" height="32"/>')
    assert b'width="300" height="150"' in contained_logo(b'<svg width="100%" height="50%"/>')
    assert b'width="300" height="150"' in contained_logo(b'<svg viewBox="0 0 0 9" width="1e9"/>')
    assert b'width="300" height="150"' in contained_logo(b'<svg stroke-width="7" height="9"/>')
    # PNG and JPEG as they are; anything else is no logo.
    assert contained_logo(PNG) == PNG
    assert contained_logo(b"hello") is None


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


def gid(n):
    return f"{n:012x}"


MAME_INDEX = [{"name": "mame", "fullname": "Arcade", "manufacturer": "", "year": None,
               "count": 6, "hash": "cccccccccccc"}]
MAME_ROWS = [  # as the daemon sends them: by name
    [gid(1), "1942", 1984, "", "", 0, 0, "", "Shooter - Vertical"],
    [gid(2), "Galaxian", 1979, "", "", 0, 0, "", ""],
    [gid(3), "Killer Instinct", 1994, "", "", 1, 0, "", "Fighter"],
    [gid(4), "Pac-Land Demo", None, "", "", 0, 0, "", "Extra/Demos"],
    [gid(5), "Quiz Show", None, "", "", 0, 0, "", "Quiz: Trivia"],
    [gid(6), "Street Fighter II", 1991, "", "", 0, 0, "", "Fighter"],
]


async def publish_with_mame(hass):
    await publish_catalog(hass, index=INDEX + MAME_INDEX, rows={**ROWS, "mame": MAME_ROWS})


async def test_browse_subfolders(hass, mqtt_mock, setup, hass_ws_client):
    await publish_with_mame(hass)
    client = await hass_ws_client(hass)
    result = (await browse(hass, client, "system:mame"))["result"]
    assert result["title"] == "Arcade"
    assert children({"result": result}) == [  # folders first, then the games at the top
        ("dir:mame:Extra", "Extra"), ("dir:mame:Fighter", "Fighter"),
        ("dir:mame:Quiz: Trivia", "Quiz: Trivia"),
        ("dir:mame:Shooter - Vertical", "Shooter - Vertical"),
        (f"game:mame:{gid(2)}", "Galaxian"),
    ]
    extra = result["children"][0]
    assert (extra["can_expand"], extra["can_play"], extra["media_class"]) == (True, False, "directory")
    assert children(await browse(hass, client, "dir:mame:Extra")) == [
        ("dir:mame:Extra/Demos", "Demos")
    ]
    demos = (await browse(hass, client, "dir:mame:Extra/Demos"))["result"]
    assert demos["title"] == "Demos"
    assert children({"result": demos}) == [(f"game:mame:{gid(4)}", "Pac-Land Demo")]
    assert children(await browse(hass, client, "dir:mame:Fighter")) == [
        (f"game:mame:{gid(3)}", "Killer Instinct"), (f"game:mame:{gid(6)}", "Street Fighter II")
    ]
    assert children(await browse(hass, client, "dir:mame:Quiz: Trivia")) == [
        (f"game:mame:{gid(5)}", "Quiz Show")
    ]
    assert children(await browse(hass, client, "dir:mame:Shooter - Vertical")) == [
        (f"game:mame:{gid(1)}", "1942")
    ]
    for bad in ("dir:mame:Nope", "dir:mame:Fight", "dir:mame:", "dir:mame", "dir:nes:Fighter"):
        assert (await browse(hass, client, bad))["success"] is False, bad
    # Favourites and recently played stay flat, across folders.
    assert children(await browse(hass, client, "fav:mame")) == [
        (f"game:mame:{gid(3)}", "Killer Instinct")
    ]
    # Systems without folders are as they were.
    assert [t for _, t in children(await browse(hass, client, "system:megadrive"))] == [
        "Bare", "Sonic the Hedgehog", "Streets of Rage"
    ]


def test_search_everywhere():
    from custom_components.batocera.browse import SEARCH_MAX, search

    class FakeBox:
        name = "Arcade"

    box = FakeBox()
    games = [
        Game(gid(1), "snes", "Pokémon Crystal", None, "", "", False, True, 0, "", ""),
        Game(gid(2), "mame", "POKEMON Battle", None, "", "", False, False, 0, "", "Quiz"),
        Game(gid(3), "mame", "Pac-Man", None, "", "", False, False, 0, "", ""),
    ]
    box.games = {g.id: g for g in games}
    url = lambda cid: f"/img/{cid}"  # noqa: E731
    found = search(box, "  pokemon ", url)
    assert [(r.title, r.media_content_id) for r in found] == [  # by name, accents folded
        ("POKEMON Battle", f"game:mame:{gid(2)}"), ("Pokémon Crystal", f"game:snes:{gid(1)}")
    ]
    assert found[1].thumbnail == f"/img/game:snes:{gid(1)}" and found[0].thumbnail is None
    assert found[0].can_play and not found[0].can_expand
    assert search(box, "  ", url) == []
    assert search(box, "", url) == []
    assert [r.title for r in search(box, "P", url, system="mame")] == ["Pac-Man", "POKEMON Battle"]
    assert search(box, "pokemon", url, system="nes") == []
    box.games = {gid(n): Game(gid(n), "snes", f"Game {n:03}", None, "", "", False, False, 0, "", "")
                 for n in range(250)}
    many = search(box, "game", url)
    assert len(many) == SEARCH_MAX == 200
    assert many[0].title == "Game 000" and many[-1].title == "Game 199"


async def test_system_logo_image(hass, mqtt_mock, setup, hass_ws_client, hass_client):
    await publish_catalog(hass)
    fire(hass, "catalog/logo/snes", SVG)
    await hass.async_block_till_done()
    root = (await browse(hass, await hass_ws_client(hass)))["result"]["children"]
    megadrive, snes = root[2], root[3]
    assert megadrive["thumbnail"] is None  # no logo: no thumbnail
    assert snes["thumbnail"].startswith(
        "/api/media_player_proxy/media_player.arcade/browse_media/game/system:snes?token="
    )
    box = setup.runtime_data
    assert f"media_image_id={box.logo_ids['snes']}" in snes["thumbnail"]  # a new logo, a new URL
    client = await hass_client()
    response = await client.get(snes["thumbnail"])
    assert response.status == 200
    assert response.content_type == "image/svg+xml"
    assert await response.read() == contained_logo(SVG)
    assert (await client.get(snes["thumbnail"].replace("snes", "megadrive"))).status == 404


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
