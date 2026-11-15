"""Setting up a box's MQTT without asking: the broker found from the box, a
login of its own on the Mosquitto add-on, and the check from the box. The
box and the add-on are fakes: see FakeBox and FakeMosquitto in conftest.py."""

import json
import logging
from unittest.mock import patch

import pytest

from homeassistant.components.hassio import AddonState
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.batocera import installer, mqtt_setup
from custom_components.batocera.const import DOMAIN

from conftest import (
    HA_ADDRESS,
    ROOT_PASSWORD,
    TOPIC,
    advanced,
    configure,
    mqtt_entry,
    start,
    suggested,
)

BOX = {"name": "Living Room", "host": "192.0.2.10", "password": ROOT_PASSWORD, "advanced": {}}
LOGIN = "batocera-living_room"
GIVEN = {
    "mqtt_host": "192.0.2.2", "mqtt_port": 1883, "mqtt_user": "box", "mqtt_password": "box-secret"
}
OTHER_OPTIONS = {
    "require_certificate": False,
    "certfile": "fullchain.pem",
    "keyfile": "privkey.pem",
    "customize": {"active": False, "folder": "mosquitto"},
}


def install_command(fake_box):
    return next(((c, i) for c, i in fake_box.runs if "install.sh" in c), (None, None))


def touched(fake_box):
    """Whether anything changed on the box: our key, or the daemon."""
    return any(c == installer.AUTHORIZE or "install.sh" in c for c, _ in fake_box.runs)


def tried(fake_box):
    return [(host, port) for host, port, _ in fake_box.mqtt_tries]


async def install(hass, user_input=BOX):
    result = await start(hass, "install")
    return await configure(hass, result, user_input)


# --- the Mosquitto add-on ---------------------------------------------------


async def test_mosquitto_login(hass, fake_box, mosquitto, caplog):
    caplog.set_level(logging.DEBUG)
    result = await start(hass, "install")
    assert result["step_id"] == "install_mosquitto"  # says the add-on restarts
    assert result["errors"] == {}
    assert advanced(result).options["collapsed"]
    assert mosquitto.restarts == 0  # nothing happens before the form is sent
    result = await configure(hass, result, BOX)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["mqtt_login"] == LOGIN

    # A login more on the add-on, everything else as it was; then a restart.
    password = mosquitto.logins()[LOGIN]
    assert len(password) >= 32
    assert mosquitto.logins() == {"zigbee": "zigbee-secret", LOGIN: password}
    assert {k: v for k, v in mosquitto.options.items() if k != "logins"} == OTHER_OPTIONS
    assert mosquitto.restarts == 1

    # core-mosquitto means nothing to the box: Home Assistant's address, as
    # the box sees it, works once the add-on is back with the new login
    # (FakeMosquitto changes its accounts only then). Found, then checked.
    assert fake_box.mqtt_tries == [(HA_ADDRESS, 1883, LOGIN)] * 2
    probe = next(i for c, i in fake_box.runs if c.startswith(installer.PROBE))
    assert probe == f"-h {HA_ADDRESS}\n-p 1883\n-u {LOGIN}\n-P {password}\n"
    command, stdin = install_command(fake_box)
    assert f" MQTT_HOST={HA_ADDRESS} MQTT_PORT=1883 MQTT_USER={LOGIN} " in command
    assert stdin == password + "\n"
    assert fake_box.runs[-1][0].startswith(installer.CHECK)

    # The password went to the box through stdin only, and is not stored.
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert password not in json.dumps([dict(entry.data), dict(entry.options)])
    assert password not in caplog.text
    assert all(password not in command for command, _ in fake_box.runs)


async def test_mosquitto_login_replaces_the_old_one(hass, fake_box, mosquitto):
    mosquitto.options["logins"].append({"username": LOGIN, "password": "old"})
    result = await install(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    logins = mosquitto.options["logins"]
    assert [x["username"] for x in logins] == ["zigbee", LOGIN]
    assert logins[1]["password"] != "old"


async def test_mosquitto_login_is_kept_across_tries(hass, fake_box, mosquitto):
    accounts = fake_box.brokers.pop((HA_ADDRESS, 1883))  # the box cannot reach it
    result = await install(hass)
    assert result["errors"] == {"base": "mqtt_refused"}
    assert result["description_placeholders"]["broker"] == f"{HA_ADDRESS}:1883"
    assert result["step_id"] == "install_mosquitto"
    assert not advanced(result).options["collapsed"]
    assert suggested(result)["mqtt_host"] is None  # no broker answered
    assert not touched(fake_box)

    fake_box.brokers[(HA_ADDRESS, 1883)] = accounts
    result = await configure(hass, result, BOX)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mosquitto.restarts == 1  # not again


async def test_mosquitto_not_back(hass, fake_box, mosquitto, caplog):
    mosquitto.comes_back = False
    with patch.object(mqtt_setup, "RESTART_TIMEOUT", 0.01):
        result = await install(hass)
    assert "not back after" in caplog.text
    # Still the old logins: the broker refuses the box's.
    assert result["errors"] == {"base": "mqtt_auth"}
    assert result["description_placeholders"]["broker"] == f"{HA_ADDRESS}:1883"
    assert not touched(fake_box)


@pytest.mark.parametrize("method", ["async_set_addon_options", "async_restart_addon"])
async def test_mosquitto_failure(hass, fake_box, mosquitto, method):
    mosquitto.failing = method
    result = await install(hass)
    assert result["errors"] == {"base": "mqtt_addon_failed"}
    assert not advanced(result).options["collapsed"]
    assert fake_box.mqtt_tries == []
    assert not touched(fake_box)


@pytest.mark.parametrize("case", ["stopped", "not_installed", "other_broker", "no_info"])
async def test_mosquitto_not_used(hass, fake_box, mosquitto, case):
    """Without the running add-on behind Home Assistant's MQTT entry, no
    login is added: the box needs an account typed under Advanced."""
    if case == "stopped":
        mosquitto.state = AddonState.NOT_RUNNING
    elif case == "not_installed":
        mosquitto.state, mosquitto.hostname = AddonState.NOT_INSTALLED, None
    elif case == "other_broker":
        entry = hass.config_entries.async_entries("mqtt")[0]
        hass.config_entries.async_update_entry(entry, data={**entry.data, "broker": "192.0.2.2"})
    else:
        mosquitto.failing = "async_get_addon_info"
    options = json.dumps(mosquitto.options)
    result = await start(hass, "install")
    assert result["step_id"] == "install"
    result = await configure(hass, result, BOX)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"].startswith("mqtt_")
    assert json.dumps(mosquitto.options) == options
    assert mosquitto.restarts == 0
    assert all(user == "" for _, _, user in fake_box.mqtt_tries)


async def test_given_broker_adds_no_login(hass, fake_box, mosquitto):
    fake_box.brokers[("192.0.2.2", 1883)] = {"box": "box-secret"}
    result = await install(hass, BOX | {"advanced": GIVEN})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "mqtt_login" not in result["data"]
    assert mosquitto.restarts == 0
    assert tried(fake_box) == [("192.0.2.2", 1883)]  # the check only


# --- removing the entry -------------------------------------------------------


def managed(hass, login="batocera-test"):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Arcade", data={"topic": TOPIC, "mqtt_login": login}, unique_id=TOPIC
    )
    entry.add_to_hass(hass)
    return entry


async def test_removing_the_entry_removes_the_login(hass, mosquitto):
    mosquitto.options["logins"].append({"username": "batocera-test", "password": "x"})
    await hass.config_entries.async_remove(managed(hass).entry_id)
    assert mosquitto.logins() == {"zigbee": "zigbee-secret"}
    assert {k: v for k, v in mosquitto.options.items() if k != "logins"} == OTHER_OPTIONS
    assert mosquitto.restarts == 1


async def test_removing_the_entry_does_not_start_a_stopped_addon(hass, mosquitto):
    mosquitto.options["logins"].append({"username": "batocera-test", "password": "x"})
    mosquitto.state = AddonState.NOT_RUNNING
    await hass.config_entries.async_remove(managed(hass).entry_id)
    assert mosquitto.logins() == {"zigbee": "zigbee-secret"}
    assert mosquitto.restarts == 0


async def test_removing_the_entry_without_its_login(hass, mosquitto):
    await hass.config_entries.async_remove(managed(hass).entry_id)
    assert mosquitto.logins() == {"zigbee": "zigbee-secret"}
    assert mosquitto.restarts == 0


async def test_removing_the_entry_despite_the_addon(hass, mosquitto, caplog):
    mosquitto.options["logins"].append({"username": "batocera-test", "password": "x"})
    mosquitto.failing = "async_set_addon_options"
    entry = managed(hass)
    await hass.config_entries.async_remove(entry.entry_id)
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert "Cannot remove MQTT user batocera-test" in caplog.text


async def test_removing_the_entry_without_supervisor(hass):
    with patch("custom_components.batocera.mqtt_setup.get_addon_manager") as manager:
        await hass.config_entries.async_remove(managed(hass).entry_id)
    manager.assert_not_called()


# --- no Supervisor: no add-on ----------------------------------------------------


async def test_anonymous_broker_is_found(hass, fake_box):
    mqtt_entry(hass, broker="192.0.2.2", username="homeassistant", password="ha-secret")
    fake_box.brokers[("192.0.2.2", 1883)] = None
    result = await start(hass, "install")
    assert result["step_id"] == "install"
    assert result["description_placeholders"]["acls"].endswith("docs/internals.md#mqtt-topics")
    result = await configure(hass, result, BOX)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "mqtt_login" not in result["data"]
    command, stdin = install_command(fake_box)
    assert " MQTT_HOST=192.0.2.2 MQTT_PORT=1883 && bash " in command  # no user
    assert stdin == "\n"  # never Home Assistant's account
    assert fake_box.mqtt_tries == [("192.0.2.2", 1883, "")] * 2


async def test_broker_that_wants_an_account(hass, fake_box):
    mqtt_entry(hass, broker="mqtt.example.lan")
    fake_box.brokers[("mqtt.example.lan", 1883)] = {"box": "box-secret"}
    result = await install(hass)
    assert result["errors"] == {"base": "mqtt_auth"}
    assert result["description_placeholders"]["broker"] == "mqtt.example.lan:1883"
    assert tried(fake_box) == [
        ("mqtt.example.lan", 1883), (HA_ADDRESS, 1883), ("homeassistant.local", 1883)
    ]
    assert not touched(fake_box)
    # Shown again, open on Advanced, with the broker that answered filled in.
    assert not advanced(result).options["collapsed"]
    values = suggested(result)
    assert (values["mqtt_host"], values["mqtt_port"]) == ("mqtt.example.lan", 1883)
    assert values["name"] == "Living Room"

    account = {"mqtt_host": "mqtt.example.lan", "mqtt_port": 1883, "mqtt_user": "box",
               "mqtt_password": "box-secret"}
    result = await configure(hass, result, BOX | {"advanced": account})
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("host", ["core-mosquitto", "localhost", "127.0.0.1", "127.0.1.1", "::1"])
async def test_hosts_only_home_assistant_knows_are_skipped(hass, fake_box, host):
    mqtt_entry(hass, broker=host, port=1884)
    result = await install(hass)
    assert result["errors"] == {"base": "mqtt_refused"}
    assert result["description_placeholders"]["broker"] == f"{HA_ADDRESS}:1884"  # the first
    assert tried(fake_box) == [(HA_ADDRESS, 1884), ("homeassistant.local", 1884)]


async def test_candidates_in_order(hass, fake_box):
    mqtt_entry(hass, broker="192.0.2.9")
    fake_box.brokers[("homeassistant.local", 1883)] = None
    result = await install(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert tried(fake_box) == [
        ("192.0.2.9", 1883), (HA_ADDRESS, 1883), ("homeassistant.local", 1883),
        ("homeassistant.local", 1883),  # the check
    ]


async def test_candidates_without_an_mqtt_entry(hass, fake_box):
    fake_box.ssh_client = "2001:db8::1 50522 22"
    result = await install(hass)
    assert result["errors"] == {"base": "mqtt_refused"}
    assert result["description_placeholders"]["broker"] == "[2001:db8::1]:1883"
    assert tried(fake_box) == [("2001:db8::1", 1883), ("homeassistant.local", 1883)]


async def test_home_assistant_address_tried_once(hass, fake_box):
    mqtt_entry(hass, broker=HA_ADDRESS)
    await install(hass)
    assert tried(fake_box) == [(HA_ADDRESS, 1883), ("homeassistant.local", 1883)]


async def test_tls_is_not_detected(hass, fake_box, mosquitto):
    entry = hass.config_entries.async_entries("mqtt")[0]
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "port": 8883, "certificate": "auto"}
    )
    result = await start(hass, "install")
    assert result["step_id"] == "install"  # no login on the add-on
    assert result["errors"] == {"base": "mqtt_tls"}
    assert result["description_placeholders"]["broker"] == "core-mosquitto:8883"
    assert not advanced(result).options["collapsed"]

    result = await configure(hass, result, BOX)
    assert result["errors"] == {"base": "mqtt_tls"}
    assert fake_box.runs == []
    assert mosquitto.restarts == 0

    fake_box.brokers[("192.0.2.2", 1883)] = {"box": "box-secret"}
    result = await configure(hass, result, BOX | {"advanced": GIVEN})
    assert result["type"] is FlowResultType.CREATE_ENTRY


# --- the check from the box, after installing -----------------------------------------


def nothing_there(box):
    pass


def silent(status):
    def setup(box):
        box.brokers[("192.0.2.2", 1883)] = {"box": "box-secret"}
        box.silent.add(("192.0.2.2", 1883))
        box.timed_out = status

    return setup


def other_password(box):
    box.brokers[("192.0.2.2", 1883)] = {"box": "something else"}


@pytest.mark.parametrize(
    ("setup", "error"),
    [
        (nothing_there, "mqtt_refused"),
        (silent(124), "mqtt_timeout"),  # GNU timeout
        (silent(143), "mqtt_timeout"),  # busybox timeout, through the shell
        (silent(-15), "mqtt_timeout"),  # busybox timeout, exec'd by the shell
        (other_password, "mqtt_auth"),
    ],
)
async def test_check_from_the_box(hass, fake_box, setup, error):
    setup(fake_box)
    result = await install(hass, BOX | {"advanced": GIVEN})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert result["description_placeholders"]["broker"] == "192.0.2.2:1883"
    assert not advanced(result).options["collapsed"]
    values = suggested(result)
    assert (values["mqtt_host"], values["mqtt_port"], values["mqtt_user"]) == (
        "192.0.2.2", 1883, "box"
    )
    assert values["mqtt_password"] is None
    assert hass.config_entries.async_entries(DOMAIN) == []
    # Checked with the daemon's own files, once installed: installing again
    # overwrites them.
    assert install_command(fake_box)[0] is not None
    assert fake_box.runs[-1][0].startswith(installer.CHECK)
    assert tried(fake_box) == [("192.0.2.2", 1883)]

    fake_box.silent.clear()
    fake_box.brokers[("192.0.2.2", 1883)] = {"box": "box-secret"}
    result = await configure(hass, result, BOX | {"advanced": GIVEN})
    assert result["type"] is FlowResultType.CREATE_ENTRY
