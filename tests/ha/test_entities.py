"""Entity, playtime and limit tests."""

from datetime import timedelta
import json

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_mqtt_message,
    async_fire_time_changed,
)

TOPIC = "batocera/test"  # as in conftest.py


def fire(hass, leaf, payload):
    async_fire_mqtt_message(hass, f"{TOPIC}/{leaf}", payload)


def published(mqtt_mock):
    """(topic, payload, retain) of everything the integration published."""
    return [(c.args[0], c.args[1], c.args[3]) for c in mqtt_mock.async_publish.call_args_list]


async def play(hass, minutes_ago=0, game="Sonic"):
    started = dt_util.now() - timedelta(minutes=minutes_ago)
    for leaf, payload in (
        ("availability", "online"),
        ("game", game),
        ("system", "megadrive"),
        ("emulator", "libretro/genesisplusgx"),
        ("started_at", started.isoformat()),
        ("state", "playing"),
    ):
        fire(hass, leaf, payload)
    await hass.async_block_till_done()
    return started


async def end(hass, started):
    """In the daemon's order: last_session, then idle and the cleared details."""
    fire(hass, "last_session", session(started, dt_util.now()))
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.arcade_playing").state == STATE_ON
    fire(hass, "state", "idle")
    for leaf in ("game", "system", "emulator", "started_at"):
        fire(hass, leaf, "")
    await hass.async_block_till_done()


def session(started, ended):
    return json.dumps(
        {
            "game": "Sonic",
            "system": "megadrive",
            "emulator": "libretro",
            "started_at": started.isoformat(),
            "ended_at": ended.isoformat(),
            "seconds": round((ended - started).total_seconds()),
        }
    )


async def advance(hass, freezer, **delta):
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def call(hass, domain, service, entity_id, **data):
    await hass.services.async_call(
        domain, service, {"entity_id": entity_id, **data}, blocking=True
    )
    await hass.async_block_till_done()


def playtime(hass):
    return int(hass.states.get("sensor.arcade_playtime_today").state)


async def test_entities_follow_the_box(hass, mqtt_mock, setup, hass_client):
    assert hass.states.get("media_player.arcade").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.arcade_playtime_today").state == "0"

    started = await play(hass)
    player = hass.states.get("media_player.arcade")
    assert player.state == "playing"
    assert player.attributes["media_title"] == "Sonic"
    assert player.attributes["source"] == "megadrive"
    assert player.attributes["app_name"] == "libretro/genesisplusgx"
    assert player.attributes["media_content_type"] == "game"
    assert "entity_picture" not in player.attributes
    assert hass.states.get("binary_sensor.arcade_playing").state == STATE_ON
    assert hass.states.get("sensor.arcade_game").state == "Sonic"
    assert hass.states.get("sensor.arcade_system").state == "megadrive"
    assert dt_util.parse_datetime(hass.states.get("sensor.arcade_session_start").state) == started

    fire(hass, "cover", b"\xff\xd8 not really a jpeg")
    await hass.async_block_till_done()
    picture = hass.states.get("media_player.arcade").attributes["entity_picture"]
    assert picture.startswith("/api/media_player_proxy/media_player.arcade?token=")
    client = await hass_client()
    response = await client.get(picture)
    assert response.status == 200
    assert response.content_type == "image/jpeg"
    assert await response.read() == b"\xff\xd8 not really a jpeg"

    await end(hass, started)
    fire(hass, "cover", b"")
    await hass.async_block_till_done()
    player = hass.states.get("media_player.arcade")
    assert player.state == "idle"
    assert "media_title" not in player.attributes
    assert "entity_picture" not in player.attributes
    assert hass.states.get("binary_sensor.arcade_playing").state == STATE_OFF
    assert hass.states.get("sensor.arcade_game").state == STATE_UNKNOWN
    assert hass.states.get("sensor.arcade_session_start").state == STATE_UNKNOWN

    # Lost connection: the will says offline, the last state is stale.
    fire(hass, "availability", "offline")
    await hass.async_block_till_done()
    assert hass.states.get("media_player.arcade").state == STATE_UNAVAILABLE
    assert hass.states.get("button.arcade_stop_game").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.arcade_playtime_today").state == "0"
    assert hass.states.get("number.arcade_daily_limit").state == "0"

    # Clean shutdown: "off", then offline.
    fire(hass, "state", "off")
    await hass.async_block_till_done()
    assert hass.states.get("media_player.arcade").state == STATE_OFF
    assert hass.states.get("binary_sensor.arcade_playing").state == STATE_OFF


async def test_stop(hass, mqtt_mock, setup):
    await play(hass)
    mqtt_mock.async_publish.reset_mock()
    await call(hass, "button", "press", "button.arcade_stop_game")
    await call(hass, "media_player", "media_stop", "media_player.arcade")
    assert published(mqtt_mock) == [(f"{TOPIC}/cmd", "stop", False)] * 2


async def test_playtime_counts_each_session_once(hass, mqtt_mock, setup, freezer):
    started = await play(hass, minutes_ago=30)
    assert playtime(hass) == 30
    await advance(hass, freezer, minutes=10)
    assert playtime(hass) == 40
    await end(hass, started)
    assert playtime(hass) == 40
    fire(hass, "last_session", session(started, dt_util.now()))  # seen again
    await hass.async_block_till_done()
    assert playtime(hass) == 40

    await advance(hass, freezer, minutes=20)
    started = await play(hass, minutes_ago=5)
    assert playtime(hass) == 45
    fire(hass, "last_session", session(started, dt_util.now()))
    await hass.async_block_till_done()
    assert playtime(hass) == 45  # still "playing", but not counted twice
    await end(hass, started)
    assert playtime(hass) == 45

    await advance(hass, freezer, days=1)
    assert playtime(hass) == 0


async def test_session_across_midnight_counts_today_only(hass, mqtt_mock, setup):
    midnight = dt_util.start_of_local_day()
    minutes = lambda m: midnight + timedelta(minutes=m)  # noqa: E731
    fire(hass, "last_session", session(minutes(-60), minutes(-10)))
    await hass.async_block_till_done()
    assert playtime(hass) == 0
    fire(hass, "last_session", session(minutes(-5), minutes(20)))
    await hass.async_block_till_done()
    assert playtime(hass) == 20


async def test_daily_limit_warns_then_stops(hass, mqtt_mock, setup, freezer):
    warnings = async_capture_events(hass, "batocera_warning")
    await call(hass, "number", "set_value", "number.arcade_daily_limit", value=40)
    started = await play(hass, minutes_ago=30)
    assert (f"{TOPIC}/lock", "OFF", True) in published(mqtt_mock)
    assert warnings == []

    await advance(hass, freezer, minutes=6)
    assert [e.data for e in warnings] == [
        {"entry_id": setup.entry_id, "name": "Arcade", "reason": "limit", "minutes_left": 4}
    ]
    await advance(hass, freezer, minutes=1)
    assert len(warnings) == 1

    mqtt_mock.async_publish.reset_mock()
    await advance(hass, freezer, minutes=3)
    assert published(mqtt_mock) == [(f"{TOPIC}/lock", "ON", True), (f"{TOPIC}/cmd", "stop", False)]
    locked = hass.states.get("binary_sensor.arcade_locked")
    assert locked.state == STATE_ON
    assert locked.attributes["reason"] == "limit"

    mqtt_mock.async_publish.reset_mock()
    await advance(hass, freezer, seconds=30)
    assert published(mqtt_mock) == []  # stop is not repeated too often
    await advance(hass, freezer, seconds=30)
    assert published(mqtt_mock) == [(f"{TOPIC}/cmd", "stop", False)]

    mqtt_mock.async_publish.reset_mock()
    await end(hass, started)
    assert published(mqtt_mock) == []
    assert hass.states.get("binary_sensor.arcade_locked").state == STATE_ON

    await call(hass, "number", "set_value", "number.arcade_daily_limit", value=0)
    assert published(mqtt_mock) == [(f"{TOPIC}/lock", "OFF", True)]
    assert hass.states.get("binary_sensor.arcade_locked").state == STATE_OFF


async def test_time_window(hass, mqtt_mock, setup, freezer):
    warnings = async_capture_events(hass, "batocera_warning")
    # It is 10:00.
    await call(hass, "time", "set_value", "time.arcade_allowed_from", time="12:00:00")
    await call(hass, "time", "set_value", "time.arcade_allowed_until", time="20:00:00")
    await play(hass)
    locked = hass.states.get("binary_sensor.arcade_locked")
    assert (locked.state, locked.attributes["reason"]) == (STATE_ON, "window")
    assert (f"{TOPIC}/cmd", "stop", False) in published(mqtt_mock)

    await call(hass, "time", "set_value", "time.arcade_allowed_from", time="08:00:00")
    await call(hass, "time", "set_value", "time.arcade_allowed_until", time="10:03:00")
    assert hass.states.get("binary_sensor.arcade_locked").state == STATE_OFF
    assert [(e.data["reason"], e.data["minutes_left"]) for e in warnings] == [("window", 3)]

    # Overnight window, 20:00 to 11:00: 10:00 is inside, 11:00 is not.
    await call(hass, "time", "set_value", "time.arcade_allowed_from", time="20:00:00")
    await call(hass, "time", "set_value", "time.arcade_allowed_until", time="11:00:00")
    assert hass.states.get("binary_sensor.arcade_locked").state == STATE_OFF
    await advance(hass, freezer, hours=1)
    assert hass.states.get("binary_sensor.arcade_locked").state == STATE_ON

    # Equal ends: no window.
    await call(hass, "time", "set_value", "time.arcade_allowed_until", time="20:00:00")
    assert hass.states.get("binary_sensor.arcade_locked").state == STATE_OFF


async def test_manual_lock(hass, mqtt_mock, setup):
    await call(hass, "switch", "turn_on", "switch.arcade_manual_lock")
    locked = hass.states.get("binary_sensor.arcade_locked")
    assert (locked.state, locked.attributes["reason"]) == (STATE_ON, "manual")
    assert published(mqtt_mock)[-1] == (f"{TOPIC}/lock", "ON", True)
    await call(hass, "switch", "turn_off", "switch.arcade_manual_lock")
    assert published(mqtt_mock)[-1] == (f"{TOPIC}/lock", "OFF", True)


async def test_retained_lock_is_not_republished(hass, mqtt_mock, setup):
    fire(hass, "lock", "OFF")
    await play(hass)
    assert not [p for p in published(mqtt_mock) if p[0].endswith("/lock")]


async def test_settings_and_playtime_survive_restart(hass, mqtt_mock, entry, hass_storage, freezer):
    freezer.move_to("2026-03-02T10:00:00-08:00")
    hass_storage[f"batocera.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"batocera.{entry.entry_id}",
        "data": {
            "daily_limit": 90,
            "allowed_from": "08:00:00",
            "allowed_until": "21:30:00",
            "manual_lock": False,
            "day": "2026-03-02",
            "played": 1500.0,
            "counted_start": "2026-03-02T08:00:00-08:00",
        },
    }
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("number.arcade_daily_limit").state == "90"
    assert hass.states.get("time.arcade_allowed_until").state == "21:30:00"
    assert playtime(hass) == 25

    await call(hass, "number", "set_value", "number.arcade_daily_limit", value=60)
    await advance(hass, freezer, seconds=5)
    assert hass_storage[f"batocera.{entry.entry_id}"]["data"]["daily_limit"] == 60
