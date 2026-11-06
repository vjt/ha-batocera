"""Keeping the daemon up to date on a box that Home Assistant installed."""

import asyncssh
import pytest

from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import async_fire_mqtt_message

from custom_components.batocera import installer
from custom_components.batocera.const import DOMAIN

from conftest import TOPIC


@pytest.fixture
def entry(managed_entry):
    return managed_entry


async def publish(hass, **leaves):
    for leaf, payload in leaves.items():
        async_fire_mqtt_message(hass, f"{TOPIC}/{leaf}", payload)
    await hass.async_block_till_done(wait_background_tasks=True)


def checks(fake_box):
    return [c for c, _ in fake_box.runs if c.startswith("cat ")]


def issue(hass, entry):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"daemon_{entry.entry_id}")


async def test_same_version_is_left_alone(hass, fake_box, setup):
    await publish(hass, availability="online", state="idle")
    assert fake_box.runs == [(f"cat {installer.BOX_DIR}/VERSION", None)]
    assert issue(hass, setup) is None


async def test_other_version_is_replaced(hass, fake_box, setup):
    fake_box.version = "0.0.1"
    await publish(hass, availability="online", state="idle")
    commands = [c for c, _ in fake_box.runs]
    assert any("tar -xf -" in c for c in commands)
    assert ("bash /tmp/ha-batocera/install.sh", asyncssh.DEVNULL) in fake_box.runs
    assert fake_box.version == installer.VERSION
    assert issue(hass, setup) is None


async def test_only_when_online_and_idle_once_per_online_period(hass, fake_box, setup):
    # Set up without reaching the box: it has said nothing yet.
    assert hass.states.get("media_player.arcade") is not None
    await publish(hass, availability="offline", state="off")
    await publish(hass, availability="online", state="playing")
    assert fake_box.runs == []  # never during a game
    await publish(hass, state="idle")
    assert len(checks(fake_box)) == 1
    await publish(hass, state="playing")
    await publish(hass, state="idle")
    assert len(checks(fake_box)) == 1
    await publish(hass, availability="offline")
    await publish(hass, availability="online")
    assert len(checks(fake_box)) == 2


async def test_host_key_mismatch(hass, fake_box, setup):
    fake_box.host_key = asyncssh.generate_private_key("ssh-ed25519")
    await publish(hass, availability="online", state="idle")
    assert fake_box.runs == []
    found = issue(hass, setup)
    assert found.translation_key == "host_key_mismatch"
    assert found.translation_placeholders["host"] == "192.0.2.10"
    assert hass.states.get("media_player.arcade").state == "idle"


async def test_failure_raises_an_issue_until_it_works(hass, fake_box, setup):
    fake_box.version = "0.0.1"
    fake_box.failing = "install.sh"
    await publish(hass, availability="online", state="idle")
    found = issue(hass, setup)
    assert found.translation_key == "update_failed"
    assert "install_failed" in found.translation_placeholders["error"]

    fake_box.failing = None
    await publish(hass, availability="offline")
    await publish(hass, availability="online")
    assert fake_box.version == installer.VERSION
    assert issue(hass, setup) is None


async def test_removing_the_entry_removes_its_issue(hass, fake_box, setup):
    fake_box.reachable = False
    await publish(hass, availability="online", state="idle")
    assert issue(hass, setup).translation_key == "update_failed"
    await hass.config_entries.async_remove(setup.entry_id)
    assert issue(hass, setup) is None


def public_web_issue(hass, entry):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"public_web_{entry.entry_id}")


async def test_public_web_access_is_a_warning_only(hass, fake_box, setup):
    await publish(hass, public_web_access="ON")
    found = public_web_issue(hass, setup)
    assert found.translation_key == "public_web_access"
    assert found.severity == ir.IssueSeverity.WARNING
    assert not found.is_fixable
    assert found.translation_placeholders == {"name": "Arcade"}
    assert not [c for c, _ in fake_box.runs if "PublicWebAccess" in c]  # nothing touched
    await publish(hass, public_web_access="")  # not known: left as it is
    assert public_web_issue(hass, setup) is not None
    await publish(hass, public_web_access="OFF")
    assert public_web_issue(hass, setup) is None
    await publish(hass, public_web_access="ON")
    await hass.config_entries.async_remove(setup.entry_id)
    assert public_web_issue(hass, setup) is None
