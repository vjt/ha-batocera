"""Shut down and Power on: one while the box is on, the other while it is off."""

import json
import socket
from unittest.mock import patch

import pytest

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, Unauthorized

from custom_components.batocera.wake import mac_address, magic_packet, send_magic_packet

from conftest import MARIO, advanced, publish_catalog
from test_entities import advance, fire

SHUT_DOWN, POWER_ON = "button.arcade_shut_down", "button.arcade_power_on"
WAKE_ON_LAN = "binary_sensor.arcade_wake_on_lan"
MAC = "38:05:25:0a:0b:0c"
# What the daemon reports (see docs/internals.md, "MQTT topics").
NETWORK = {"interface": "eth0", "mac": MAC, "broadcast": "192.0.2.255", "wake_on": "g",
           "supports_wake_on": "pumbg"}


async def on(hass):
    fire(hass, "availability", "online")
    fire(hass, "state", "idle")
    await hass.async_block_till_done()


async def off(hass):
    """A clean shutdown: the daemon's last words."""
    fire(hass, "state", "off")
    fire(hass, "availability", "offline")
    await hass.async_block_till_done()


async def press(hass, entity_id, user=None):
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True,
        context=Context(user_id=user.id if user else None),
    )
    await hass.async_block_till_done()


def state(hass, entity_id):
    return hass.states.get(entity_id).state


@pytest.fixture
def magic():
    """Power on's packets: (MAC, broadcast address), never sent."""
    with patch("custom_components.batocera.button.send_magic_packet") as send:
        yield send


async def test_one_button_at_a_time(hass, mqtt_mock, setup, daemon, magic):
    await on(hass)
    fire(hass, "network", json.dumps(NETWORK))
    await hass.async_block_till_done()
    assert state(hass, SHUT_DOWN) == STATE_UNKNOWN  # pressable
    assert state(hass, POWER_ON) == STATE_UNAVAILABLE
    await press(hass, POWER_ON)
    assert magic.call_count == 0

    await off(hass)
    assert state(hass, SHUT_DOWN) == STATE_UNAVAILABLE
    assert state(hass, POWER_ON) == STATE_UNKNOWN
    await press(hass, SHUT_DOWN)
    assert daemon.requests == []

    # Unreachable, not shut down: off as far as anyone can tell.
    await on(hass)
    fire(hass, "availability", "offline")
    await hass.async_block_till_done()
    assert state(hass, SHUT_DOWN) == STATE_UNAVAILABLE
    assert state(hass, POWER_ON) == STATE_UNKNOWN


async def test_shut_down(hass, mqtt_mock, setup, daemon):
    await on(hass)
    daemon.answers["shutdown"] = {"ok": True}
    await press(hass, SHUT_DOWN)
    [req] = daemon.requests
    assert req["op"] == "shutdown"
    assert set(req) == {"id", "op"}


@pytest.mark.parametrize(
    ("error", "key"),
    [
        ("bad_request", "shutdown_bad_request"),  # a daemon from before shutdowns
        ("busy", "shutdown_busy"),
        ("es_unreachable", "shutdown_es_unreachable"),
        ("shutting_down", "shutdown_shutting_down"),
        ("weird", "shutdown_failed"),
    ],
)
async def test_shut_down_refused(hass, mqtt_mock, setup, daemon, error, key):
    await on(hass)
    daemon.answers["shutdown"] = {"ok": False, "error": error}
    with pytest.raises(HomeAssistantError) as err:
        await press(hass, SHUT_DOWN)
    assert err.value.translation_key == key
    assert err.value.translation_placeholders == {"name": "Arcade", "reason": error}


async def test_power_needs_control(hass, mqtt_mock, setup, daemon, magic, kid):
    await on(hass)
    fire(hass, "network", json.dumps(NETWORK))
    daemon.answers["shutdown"] = {"ok": True}
    with pytest.raises(Unauthorized):
        await press(hass, SHUT_DOWN, kid)
    await off(hass)
    with pytest.raises(Unauthorized):
        await press(hass, POWER_ON, kid)
    assert (daemon.requests, magic.call_count) == ([], 0)
    hass.config_entries.async_update_entry(setup, options={"control_users": [kid.id]})
    await press(hass, POWER_ON, kid)
    assert magic.call_count == 1


async def test_power_on_with_what_the_box_said(hass, mqtt_mock, setup, magic, freezer, hass_storage):
    await off(hass)
    assert state(hass, POWER_ON) == STATE_UNAVAILABLE  # no MAC yet
    assert state(hass, WAKE_ON_LAN) == STATE_UNKNOWN

    await on(hass)
    fire(hass, "network", json.dumps({**NETWORK, "wake_on": "d"}))
    await hass.async_block_till_done()
    wol = hass.states.get(WAKE_ON_LAN)
    assert wol.state == STATE_OFF  # this box would not wake
    assert wol.attributes["mac_address"] == MAC
    assert wol.attributes["broadcast_address"] == "192.0.2.255"
    assert wol.attributes["interface"] == "eth0"
    fire(hass, "network", json.dumps(NETWORK))
    await hass.async_block_till_done()
    assert state(hass, WAKE_ON_LAN) == STATE_ON

    # Garbage, or the topic cleared: the last report stays.
    fire(hass, "network", "")
    fire(hass, "network", json.dumps({**NETWORK, "mac": "nope"}))
    await off(hass)
    await press(hass, POWER_ON)
    magic.assert_called_once_with(MAC, "192.0.2.255")

    # Kept for when the box is off at Home Assistant's next start.
    await advance(hass, freezer, seconds=5)
    stored = hass_storage[f"batocera.{setup.entry_id}"]["data"]["network"]
    assert stored == NETWORK


async def test_power_on_after_a_restart(hass, mqtt_mock, entry, magic, freezer, hass_storage):
    freezer.move_to("2026-03-02T10:00:00-08:00")
    hass_storage[f"batocera.{entry.entry_id}"] = {
        "version": 1, "minor_version": 1, "key": f"batocera.{entry.entry_id}",
        "data": {
            "daily_limit": 0, "allowed_from": "00:00:00", "allowed_until": "00:00:00",
            "manual_lock": False, "day": "2026-03-02", "played": 0.0, "counted_start": None,
            "network": {"mac": MAC, "broadcast": "192.0.2.255"},
        },
    }
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert state(hass, POWER_ON) == STATE_UNKNOWN  # not online: off
    await press(hass, POWER_ON)
    magic.assert_called_once_with(MAC, "192.0.2.255")


async def test_power_on_from_the_options(hass, mqtt_mock, setup, magic):
    await off(hass)
    result = await hass.config_entries.options.async_init(setup.entry_id)
    assert result["data_schema"]({})["advanced"] == {}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"advanced": {"wake_mac": "38-05-25-0A-0B-0C"}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup.options == {"control_users": [], "wake_mac": MAC}
    await hass.async_block_till_done()
    # No subnet known: the limited broadcast, as a last resort.
    assert state(hass, POWER_ON) == STATE_UNKNOWN
    await press(hass, POWER_ON)
    assert magic.call_args.args == (MAC, "255.255.255.255")

    fire(hass, "network", json.dumps({**NETWORK, "mac": "02:00:00:00:00:01"}))
    await hass.async_block_till_done()
    await press(hass, POWER_ON)
    assert magic.call_args.args == (MAC, "192.0.2.255")  # the option wins


@pytest.mark.parametrize(
    ("advanced", "error"),
    [({"wake_mac": "38:05:25:0a:0b"}, "invalid_mac"),
     ({"wake_mac": MAC, "wake_broadcast": "192.0.2"}, "invalid_broadcast")],
)
async def test_options_refuse_what_is_not_an_address(hass, mqtt_mock, setup, advanced, error):
    result = await hass.config_entries.options.async_init(setup.entry_id)
    assert advanced(result).options["collapsed"]  # nothing set
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"advanced": advanced}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert not advanced(result).options["collapsed"]
    assert setup.options == {}


async def test_wake_failed(hass, mqtt_mock, setup, magic):
    fire(hass, "network", json.dumps(NETWORK))
    await off(hass)
    magic.side_effect = OSError("Network is unreachable")
    with pytest.raises(HomeAssistantError) as err:
        await press(hass, POWER_ON)
    assert err.value.translation_key == "wake_failed"


def test_magic_packet():
    sent = []

    class Socket:
        def __init__(self, family, kind):
            assert (family, kind) == (socket.AF_INET, socket.SOCK_DGRAM)
            self.broadcast = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def setsockopt(self, level, name, value):
            self.broadcast = (level, name, value) == (socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        def sendto(self, data, address):
            sent.append((data, address, self.broadcast))

    with patch("custom_components.batocera.wake.socket.socket", Socket):
        send_magic_packet(MAC, "192.0.2.255")
    assert sent == [(magic_packet(MAC), ("192.0.2.255", 9), True)]
    assert magic_packet(MAC) == b"\xff" * 6 + bytes.fromhex("3805250a0b0c") * 16


def test_mac_address():
    for text in (MAC, "38:05:25:0A:0B:0C", "38-05-25-0a-0b-0c", " 3805250a0b0c "):
        assert mac_address(text) == MAC, text
    for text in ("38:05:25:0a:0b", "38:05-25:0a:0b:0c", "zz:05:25:0a:0b:0c", "", None, 3):
        assert mac_address(text) is None, text


async def test_launch_while_shutting_down(hass, mqtt_mock, setup, daemon):
    await publish_catalog(hass)
    await on(hass)
    daemon.answers["launch"] = {"ok": False, "error": "shutting_down"}
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "media_player", "play_media",
            {"entity_id": "media_player.arcade", "media_content_id": f"game:snes:{MARIO}",
             "media_content_type": "game"},
            blocking=True,
        )
    assert err.value.translation_key == "launch_shutting_down"
