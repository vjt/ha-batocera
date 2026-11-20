"""Play statistics: the session log and batocera/stats."""

from datetime import datetime, timedelta
import json

from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    async_fire_mqtt_message,
    async_fire_time_changed,
)

from conftest import INDEX, MARIO, ROWS, SONIC, SOR, TOPIC, publish_catalog

MINUTE = timedelta(minutes=1)


def fire(hass, leaf, payload):
    async_fire_mqtt_message(hass, f"{TOPIC}/{leaf}", payload)


def at(text: str) -> datetime:
    """A local time, the tests' zone being US/Pacific."""
    return dt_util.parse_datetime(text)  # type: ignore[return-value]


async def ended(hass, start, end, game="Sonic the Hedgehog", system="megadrive", gid=SONIC):
    """last_session, as the daemon sends it when a game ends."""
    session = {"game": game, "system": system, "emulator": "libretro",
               "started_at": start.isoformat(), "ended_at": end.isoformat(),
               "seconds": round((end - start).total_seconds())}
    if gid:
        session["game_id"] = gid
    fire(hass, "last_session", json.dumps(session))
    await hass.async_block_till_done()


async def play(hass, start, game="Sonic the Hedgehog", system="megadrive", gid=SONIC):
    for leaf, payload in (("availability", "online"), ("game", game), ("game_id", gid),
                          ("system", system), ("started_at", start.isoformat()),
                          ("state", "playing")):
        fire(hass, leaf, payload)
    await hass.async_block_till_done()


async def idle(hass):
    fire(hass, "state", "idle")
    for leaf in ("game", "game_id", "system", "started_at"):
        fire(hass, leaf, "")
    await hass.async_block_till_done()


async def advance(hass, freezer, delta):
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def stats(hass, hass_ws_client, entry):
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "batocera/stats", "entry_id": entry.entry_id})
    msg = await client.receive_json()
    assert msg["success"], msg
    return msg["result"]


def board(result, range_):
    """(name, seconds, count) of a range's games, in order."""
    return [(g["name"], g["seconds"], g["count"]) for g in result[range_]["games"]]


async def test_sessions_make_today_s_board(hass, mqtt_mock, setup, hass_ws_client):
    await publish_catalog(hass)
    now = dt_util.now()  # 10:00
    await ended(hass, now - 120 * MINUTE, now - 90 * MINUTE)
    await ended(hass, now - 80 * MINUTE, now - 70 * MINUTE, "Super Mario World", "snes", MARIO)
    await ended(hass, now - 60 * MINUTE, now - 45 * MINUTE)
    result = await stats(hass, hass_ws_client, setup)
    assert result["date"] == "2026-03-02"
    assert result["log_since"] == "2026-03-02"
    assert result["names"] == {"megadrive": "Sega Mega Drive", "snes": "Super Nintendo"}
    assert board(result, "today") == [("Sonic the Hedgehog", 2700, 2), ("Super Mario World", 600, 1)]
    assert result["today"]["games"][0]["id"] == SONIC
    assert "playing" not in result["today"]["games"][0]
    assert result["today"]["seconds"] == 3300
    assert result["today"]["systems"] == [
        {"system": "megadrive", "seconds": 2700, "games": 1, "top": "Sonic the Hedgehog"},
        {"system": "snes", "seconds": 600, "games": 1, "top": "Super Mario World"},
    ]
    assert result["month"] == result["today"]  # the 2nd: nothing on the 1st
    assert len(result["days"]) == 30
    assert result["days"][0] == {"date": "2026-02-01", "seconds": {}}
    assert result["days"][-1] == {"date": "2026-03-02",
                                  "seconds": {"megadrive": 2700, "snes": 600}}


async def test_the_board_ranks_ties_by_name_and_keeps_the_top(hass, mqtt_mock, setup, hass_ws_client):
    now = dt_util.now()
    for i in range(12):  # twelve games of ten minutes, the earliest first
        start = now - (12 - i) * 15 * MINUTE
        await ended(hass, start, start + 10 * MINUTE, f"Game {i:02}", "nes", None)
    result = await stats(hass, hass_ws_client, setup)
    names = [g["name"] for g in result["today"]["games"]]
    assert names == [f"Game {i:02}" for i in range(10)]
    assert result["today"]["games"][0]["id"] is None  # a daemon before game_id
    assert len(result["month"]["games"]) == 12
    assert result["names"] == {"nes": "nes"}  # not in the catalog
    assert result["today"]["systems"] == [
        {"system": "nes", "seconds": 7200, "games": 12, "top": "Game 00"}]


async def test_a_session_across_midnight_splits_into_its_days(hass, mqtt_mock, setup, hass_ws_client):
    await ended(hass, at("2026-03-01T23:30:00-08:00"), at("2026-03-02T00:20:00-08:00"))
    result = await stats(hass, hass_ws_client, setup)
    assert result["days"][-2] == {"date": "2026-03-01", "seconds": {"megadrive": 1800}}
    assert result["days"][-1] == {"date": "2026-03-02", "seconds": {"megadrive": 1200}}
    assert board(result, "today") == [("Sonic the Hedgehog", 1200, 1)]
    assert board(result, "month") == [("Sonic the Hedgehog", 3000, 1)]
    assert result["log_since"] == "2026-03-01"


async def test_the_month_is_the_calendar_s(hass, mqtt_mock, setup, hass_ws_client):
    await ended(hass, at("2026-01-10T18:00:00-08:00"), at("2026-01-10T19:00:00-08:00"))
    await ended(hass, at("2026-02-20T18:00:00-08:00"), at("2026-02-20T18:30:00-08:00"),
                "Streets of Rage", "megadrive", SOR)
    await ended(hass, at("2026-03-01T18:00:00-08:00"), at("2026-03-01T18:05:00-08:00"))
    result = await stats(hass, hass_ws_client, setup)
    assert board(result, "today") == []
    assert result["today"] == {"seconds": 0, "games": [], "systems": []}
    assert board(result, "month") == [("Sonic the Hedgehog", 300, 1)]
    days = {d["date"]: d["seconds"] for d in result["days"]}
    assert days["2026-02-20"] == {"megadrive": 1800}  # in the 30 days, not in March
    assert "2026-01-10" not in days
    assert result["log_since"] == "2026-01-10"


async def test_the_running_game_counts_as_it_goes(hass, mqtt_mock, setup, hass_ws_client, freezer):
    await play(hass, dt_util.now() - 20 * MINUTE)
    result = await stats(hass, hass_ws_client, setup)
    assert result["today"]["games"] == [
        {"id": SONIC, "name": "Sonic the Hedgehog", "system": "megadrive", "seconds": 1200,
         "count": 1, "playing": True}]
    assert result["days"][-1]["seconds"] == {"megadrive": 1200}
    assert result["log_since"] == "2026-03-02"
    await advance(hass, freezer, 5 * MINUTE)
    result = await stats(hass, hass_ws_client, setup)
    assert board(result, "today") == [("Sonic the Hedgehog", 1500, 1)]


async def test_a_power_cut_logs_the_game_until_the_box_went_away(
    hass, mqtt_mock, setup, hass_ws_client, freezer
):
    await play(hass, dt_util.now() - 30 * MINUTE)
    fire(hass, "availability", "offline")
    await advance(hass, freezer, timedelta(minutes=20))
    result = await stats(hass, hass_ws_client, setup)
    assert result["today"]["games"] == [  # no longer playing, as far as we know
        {"id": SONIC, "name": "Sonic the Hedgehog", "system": "megadrive", "seconds": 1800,
         "count": 1}]
    fire(hass, "availability", "online")
    await idle(hass)
    result = await stats(hass, hass_ws_client, setup)
    assert board(result, "today") == [("Sonic the Hedgehog", 1800, 1)]


async def test_a_game_the_box_reports_again_replaces_its_cut(
    hass, mqtt_mock, setup, hass_ws_client, freezer
):
    # The network dropped, the game went on: one session, the time away included.
    start = dt_util.now() - 30 * MINUTE
    await play(hass, start)
    fire(hass, "availability", "offline")
    await advance(hass, freezer, 5 * MINUTE)
    await play(hass, start)
    result = await stats(hass, hass_ws_client, setup)
    assert board(result, "today") == [("Sonic the Hedgehog", 2100, 1)]
    assert result["today"]["games"][0]["playing"] is True
    await advance(hass, freezer, 10 * MINUTE)
    await ended(hass, start, dt_util.now())
    await idle(hass)
    result = await stats(hass, hass_ws_client, setup)
    assert board(result, "today") == [("Sonic the Hedgehog", 2700, 1)]


async def test_a_session_seen_again_is_logged_once(hass, mqtt_mock, setup, hass_ws_client):
    now = dt_util.now()
    await ended(hass, now - 30 * MINUTE, now - 10 * MINUTE)
    await ended(hass, now - 30 * MINUTE, now - 10 * MINUTE)  # retained, seen on restart
    result = await stats(hass, hass_ws_client, setup)
    assert board(result, "today") == [("Sonic the Hedgehog", 1200, 1)]


async def test_all_time_comes_from_the_catalog(hass, mqtt_mock, setup, hass_ws_client):
    await publish_catalog(hass)  # rows without the time played: a daemon before it
    result = await stats(hass, hass_ws_client, setup)
    assert result["all"] is None

    rows = {
        "megadrive": [
            [*ROWS["megadrive"][0], "", None, 0],
            [*ROWS["megadrive"][1], "", 0.75, 4321],
            [*ROWS["megadrive"][2], "", None, 600],
        ],
        "snes": [[*ROWS["snes"][0], "", None, 7200]],
    }
    await publish_catalog(hass, INDEX, rows)
    result = await stats(hass, hass_ws_client, setup)
    assert result["all"] == {
        "seconds": 12121,
        "played": 3,
        "owned": 4,
        "games": [
            {"id": MARIO, "name": "Super Mario World", "system": "snes", "seconds": 7200, "count": 3},
            {"id": SONIC, "name": "Sonic the Hedgehog", "system": "megadrive", "seconds": 4321,
             "count": 12},
            {"id": SOR, "name": "Streets of Rage", "system": "megadrive", "seconds": 600, "count": 1},
        ],
        "systems": [
            {"system": "snes", "seconds": 7200, "games": 1, "owned": 1, "top": "Super Mario World"},
            {"system": "megadrive", "seconds": 4921, "games": 2, "owned": 3,
             "top": "Sonic the Hedgehog"},
        ],
    }
    assert result["names"] == {"megadrive": "Sega Mega Drive", "snes": "Super Nintendo"}


@pytest.fixture
def kept(entry, hass_storage):
    """A session log from before the restart."""
    key = f"batocera.{entry.entry_id}.sessions"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {"sessions": [
            ["2025-01-05T18:00:00-08:00", "2025-01-05T19:00:00-08:00", "snes", MARIO,
             "Super Mario World", False],  # more than 400 days ago
            ["2026-02-28T18:00:00-08:00", "2026-02-28T18:20:00-08:00", "megadrive", SONIC,
             "Sonic the Hedgehog", True],
        ]},
    }
    return key


async def test_the_log_survives_restarts_and_forgets_old_sessions(
    hass, mqtt_mock, kept, setup, hass_storage, hass_ws_client, freezer
):
    key, entry = kept, setup
    result = await stats(hass, hass_ws_client, entry)
    assert result["log_since"] == "2026-02-28"
    assert result["days"][-3] == {"date": "2026-02-28", "seconds": {"megadrive": 1200}}

    now = dt_util.now()
    await ended(hass, now - 10 * MINUTE, now)
    await advance(hass, freezer, timedelta(seconds=5))
    assert hass_storage[key]["data"]["sessions"] == [
        ["2026-02-28T18:00:00-08:00", "2026-02-28T18:20:00-08:00", "megadrive", SONIC,
         "Sonic the Hedgehog", True],
        [(now - 10 * MINUTE).isoformat(), now.isoformat(), "megadrive", SONIC,
         "Sonic the Hedgehog", False],
    ]


async def test_no_such_box(hass, mqtt_mock, setup, hass_ws_client):
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "batocera/stats", "entry_id": "nope"})
    msg = await client.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == "not_found"


async def test_removing_the_box_removes_its_log(
    hass, mqtt_mock, setup, hass_storage, freezer
):
    now = dt_util.now()
    await ended(hass, now - 10 * MINUTE, now)
    await advance(hass, freezer, timedelta(seconds=5))
    key = f"batocera.{setup.entry_id}.sessions"
    assert hass_storage[key]["data"]["sessions"]
    await hass.config_entries.async_remove(setup.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
