"""Config flow tests. The box is a fake: see FakeBox in conftest.py."""

import json
import logging
from unittest.mock import patch

import asyncssh
import pytest

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.batocera import installer
from custom_components.batocera.const import DOMAIN

from conftest import ROOT_PASSWORD, public

INSTALL = {
    "name": "Living Room",
    "host": "192.0.2.10",
    "password": ROOT_PASSWORD,
    "mqtt_host": "192.0.2.2",
    "mqtt_port": 1883,
    "mqtt_user": "box",
    "mqtt_password": "mqtt-secret",
}


async def start(hass, step):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def configure(hass, result, user_input):
    with patch("custom_components.batocera.async_setup_entry", return_value=True):
        return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def test_manual(hass):
    result = await start(hass, "manual")
    assert result["type"] is FlowResultType.FORM
    result = await configure(hass, result, {"name": "Arcade", "topic": " batocera/arcade/ "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Arcade"
    assert result["data"] == {"topic": "batocera/arcade"}


async def test_manual_rejects_wildcards_and_duplicates(hass, entry):
    result = await start(hass, "manual")
    result = await configure(hass, result, {"name": "Arcade", "topic": "batocera/#"})
    assert result["errors"] == {"topic": "invalid_topic"}
    result = await configure(hass, result, {"name": "Again", "topic": "batocera/test"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_install(hass, fake_box, caplog):
    caplog.set_level(logging.DEBUG)
    result = await start(hass, "install")
    assert result["type"] is FlowResultType.FORM
    result = await configure(hass, result, INSTALL)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room"
    data = result["data"]
    assert set(data) == {"topic", "host", "host_key", "private_key"}
    assert data["topic"] == "batocera/living_room"
    assert data["host"] == "192.0.2.10"

    # The host key is pinned; our key replaced the one of the earlier pairing.
    assert data["host_key"] == public(fake_box.host_key)
    ours = public(asyncssh.import_private_key(data["private_key"]))
    assert ours.endswith(" ha-batocera")
    assert fake_box.authorized == [fake_box.owner_key, ours]

    # The whole daemon went over, and install.sh got the settings: all in its
    # environment but the MQTT password, which went through stdin.
    files = {p.name: p.read_bytes() for p in installer.DAEMON.iterdir() if p.is_file()}
    assert fake_box.staged == files
    assert fake_box.version == installer.VERSION
    command, stdin = next((c, i) for c, i in fake_box.runs if "install.sh" in c)
    assert command == (
        "IFS= read -r MQTT_PASSWORD && export MQTT_PASSWORD DEVICE=living_room"
        " MQTT_HOST=192.0.2.2 MQTT_PORT=1883 MQTT_USER=box && bash /tmp/ha-batocera/install.sh"
    )
    assert stdin == "mqtt-secret\n"

    # Neither password is stored, logged, or put on a command line.
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    stored = json.dumps([dict(entry.data), dict(entry.options)])
    for secret in (ROOT_PASSWORD, "mqtt-secret"):
        assert secret not in stored
        assert secret not in caplog.text
        assert all(secret not in command for command, _ in fake_box.runs)


async def test_install_without_mqtt_user(hass, fake_box):
    result = await start(hass, "install")
    result = await configure(
        hass, result, {k: v for k, v in INSTALL.items() if k not in ("mqtt_user", "mqtt_password")}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    command, stdin = next((c, i) for c, i in fake_box.runs if "install.sh" in c)
    assert "MQTT_USER" not in command
    assert stdin == "\n"


def suggested(result):
    return {
        str(key): (key.description or {}).get("suggested_value")
        for key in result["data_schema"].schema
    }


@pytest.mark.parametrize(
    ("attr", "value", "error"),
    [
        ("reachable", False, "cannot_connect"),
        ("password", "something else", "invalid_auth"),
        ("failing", "install.sh", "install_failed"),
    ],
)
async def test_install_errors(hass, fake_box, attr, value, error):
    setattr(fake_box, attr, value)
    result = await start(hass, "install")
    result = await configure(hass, result, INSTALL)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    # Shown again with what was typed, except the passwords.
    values = suggested(result)
    assert values["mqtt_host"] == "192.0.2.2"
    assert values["password"] is None
    assert values["mqtt_password"] is None
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_install_rejects_duplicates(hass, fake_box, entry):
    result = await start(hass, "install")
    result = await configure(hass, result, INSTALL | {"name": "Test"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert fake_box.runs == []


async def test_reconfigure_trusts_the_new_host_key(hass, fake_box, managed_entry):
    old_key = managed_entry.data["private_key"]
    fake_box.host_key = asyncssh.generate_private_key("ssh-ed25519")  # box reinstalled
    result = await managed_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    result = await configure(hass, result, {"host": "192.0.2.11", "password": ROOT_PASSWORD})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    data = managed_entry.data
    assert data["host"] == "192.0.2.11"
    assert data["host_key"] == public(fake_box.host_key)
    assert data["private_key"] != old_key
    ours = public(asyncssh.import_private_key(data["private_key"]))
    assert fake_box.authorized == [fake_box.owner_key, ours]
    # The box keeps its settings: install.sh gets none, and no stdin to ask on.
    command, stdin = next((c, i) for c, i in fake_box.runs if "install.sh" in c)
    assert command == "bash /tmp/ha-batocera/install.sh"
    assert stdin is asyncssh.DEVNULL
    assert ROOT_PASSWORD not in json.dumps(dict(data))


async def test_reconfigure_wrong_password(hass, fake_box, managed_entry):
    data = dict(managed_entry.data)
    result = await managed_entry.start_reconfigure_flow(hass)
    result = await configure(hass, result, {"host": "192.0.2.10", "password": "nope"})
    assert result["errors"] == {"base": "invalid_auth"}
    assert dict(managed_entry.data) == data
