"""The live screen: frames streamed to whoever watches, nothing recorded."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNKNOWN
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_mqtt_message,
    async_fire_time_changed,
)
import pytest

from custom_components.batocera import live

TOPIC = "batocera/test"  # as in conftest.py


def fire(hass, leaf, payload):
    async_fire_mqtt_message(hass, f"{TOPIC}/{leaf}", payload)


def published(mqtt_mock):
    """(topic, payload, retain) of everything the integration published."""
    return [(c.args[0], c.args[1], c.args[3]) for c in mqtt_mock.async_publish.call_args_list]


async def advance(hass, freezer, **delta):
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.fixture(autouse=True)
def quick_keepalive():
    with patch.object(live, "KEEPALIVE", 0.05):
        yield


def watches(mqtt_mock):
    return [p for t, p, r in published(mqtt_mock) if t == f"{TOPIC}/watch" and not r]


async def until(check):
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


async def next_frame(response) -> bytes:
    head = await asyncio.wait_for(response.content.readuntil(b"\r\n\r\n"), 2)
    assert head.startswith(b"--frame\r\nContent-Type: image/jpeg\r\n")
    size = int(head.split(b"Content-Length: ")[1].split(b"\r\n")[0])
    frame = await response.content.readexactly(size)
    assert await response.content.readexactly(2) == b"\r\n"
    return frame


async def test_live_frames_touch_no_entity(hass, mqtt_mock, setup):
    fire(hass, "availability", "online")
    await hass.async_block_till_done()
    changes = async_capture_events(hass, EVENT_STATE_CHANGED)
    fire(hass, "live", b"\xff\xd8 big")
    await hass.async_block_till_done()
    assert changes == []  # nothing for the recorder, the logbook or the history
    assert hass.states.get("image.arcade_screen").state == STATE_UNKNOWN


async def test_stream_while_watched(hass, mqtt_mock, setup, hass_client, freezer):
    client = await hass_client()
    response = await client.get(f"/api/batocera/{setup.entry_id}/live")
    assert response.status == 200
    assert response.headers["Content-Type"] == "multipart/x-mixed-replace;boundary=frame"
    assert response.headers["Cache-Control"] == "no-store"
    await until(lambda: watches(mqtt_mock) == ["live"])  # the daemon starts

    fire(hass, "live", b"\xff\xd8 one")
    assert await next_frame(response) == b"\xff\xd8 one"
    fire(hass, "live", b"\xff\xd8 two")
    assert await next_frame(response) == b"\xff\xd8 two"
    # A still screen sends nothing new: the last frame again, so a viewer
    # who left is noticed.
    assert await next_frame(response) == b"\xff\xd8 two"

    await advance(hass, freezer, seconds=20)
    assert watches(mqtt_mock) == ["live", "live"]  # renewed while watched

    response.close()
    await until(lambda: watches(mqtt_mock) == ["live", "live", "off"])
    await advance(hass, freezer, seconds=60)
    assert watches(mqtt_mock) == ["live", "live", "off"]  # no renewal after


async def test_first_frame_is_the_latest(hass, mqtt_mock, setup, hass_client):
    client = await hass_client()
    first = await client.get(f"/api/batocera/{setup.entry_id}/live")
    fire(hass, "live", b"\xff\xd8 now")
    assert await next_frame(first) == b"\xff\xd8 now"
    second = await client.get(f"/api/batocera/{setup.entry_id}/live")
    assert await next_frame(second) == b"\xff\xd8 now"  # no wait for a change
    assert watches(mqtt_mock) == ["live"]  # one daemon lease for both

    first.close()
    await asyncio.sleep(0.2)
    assert watches(mqtt_mock) == ["live"]  # someone still watches
    second.close()
    await until(lambda: watches(mqtt_mock) == ["live", "off"])


async def test_wakes_up_even_without_frames(hass, mqtt_mock, setup, hass_client):
    # The daemon sends nothing (screenshots off, box gone): a viewer who
    # leaves still ends the lease.
    client = await hass_client()
    response = await client.get(f"/api/batocera/{setup.entry_id}/live")
    await until(lambda: watches(mqtt_mock) == ["live"])
    response.close()
    await until(lambda: watches(mqtt_mock) == ["live", "off"])


async def test_unknown_box(hass, mqtt_mock, setup, hass_client):
    client = await hass_client()
    response = await client.get("/api/batocera/nope/live")
    assert response.status == 404
    assert watches(mqtt_mock) == []


async def test_needs_auth(hass, mqtt_mock, setup, hass_client_no_auth):
    client = await hass_client_no_auth()
    response = await client.get(f"/api/batocera/{setup.entry_id}/live")
    assert response.status == 401
    assert watches(mqtt_mock) == []


async def test_unload_stops_renewing(hass, mqtt_mock, setup, hass_client, freezer):
    client = await hass_client()
    response = await client.get(f"/api/batocera/{setup.entry_id}/live")
    await until(lambda: watches(mqtt_mock) == ["live"])
    assert await hass.config_entries.async_unload(setup.entry_id)
    await hass.async_block_till_done()
    response.close()
    await advance(hass, freezer, seconds=60)
    assert watches(mqtt_mock).count("live") == 1
    fire(hass, "live", b"\xff\xd8 late")  # nobody listens: no error
    await hass.async_block_till_done()
