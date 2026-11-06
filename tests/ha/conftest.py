"""Fixtures for the Home Assistant integration tests."""

from contextlib import asynccontextmanager
import io
import tarfile
from types import SimpleNamespace
from unittest.mock import patch

import asyncssh
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.batocera import installer
from custom_components.batocera.const import CONF_TOPIC, DOMAIN

TOPIC = "batocera/test"
NOW = "2026-03-02T10:00:00-08:00"  # tests run in US/Pacific
ROOT_PASSWORD = "root-secret"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="Arcade", data={CONF_TOPIC: TOPIC}, unique_id=TOPIC)
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup(hass, mqtt_mock, entry, freezer):
    freezer.move_to(NOW)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def public(key: asyncssh.SSHKey) -> str:
    return key.export_public_key().decode().strip()


class FakeBox:
    """A Batocera box as asyncssh would see it: dropbear with a root password
    and authorized_keys, running the commands the installer sends."""

    def __init__(self) -> None:
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.password = ROOT_PASSWORD
        self.owner_key = public(asyncssh.generate_private_key("ssh-ed25519", comment="owner@laptop"))
        old = public(asyncssh.generate_private_key("ssh-ed25519", comment=installer.KEY_COMMENT))
        self.authorized = [self.owner_key, old]  # old: from an earlier pairing
        self.version = ""  # not installed
        self.staged: dict[str, bytes] = {}
        self.runs: list[tuple[str, object]] = []  # (command, stdin)
        self.reachable = True
        self.failing: str | None = None  # commands containing this fail

    def trusts(self, keys) -> bool:
        authorized = {asyncssh.import_public_key(k).public_data for k in self.authorized}
        return any(k.public_data in authorized for k in keys or ())

    @asynccontextmanager
    async def connect(self, host, *, username, password=None, client_keys=None,
                      known_hosts=None, **kwargs):
        assert username == "root"
        assert kwargs["config"] is None and kwargs["agent_path"] is None
        if not self.reachable:
            raise OSError("No route to host")
        if known_hosts is not None and self.host_key.public_data not in [
            k.public_data for k in known_hosts[0]
        ]:
            raise asyncssh.HostKeyNotVerifiable("Host key is not trusted")
        if password != self.password and not self.trusts(client_keys):
            raise asyncssh.PermissionDenied("Permission denied")
        yield SimpleNamespace(get_server_host_key=lambda: self.host_key, run=self.run)

    async def run(self, command, *, input=None, stdin=None, check=False, **kwargs):
        self.runs.append((command, input if input is not None else stdin))
        status, out = 0, ""
        if self.failing and self.failing in command:
            status = 1
        elif command.startswith("cat "):
            status, out = (0, self.version + "\n") if self.version else (1, "")
        elif command == installer.AUTHORIZE:
            self.authorized = [k for k in self.authorized if not k.endswith(" ha-batocera")]
            self.authorized.append(input.strip())
        elif "tar -xf -" in command:
            with tarfile.open(fileobj=io.BytesIO(input)) as tar:
                self.staged = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
        elif "install.sh" in command:
            self.version = self.staged["VERSION"].decode().strip()
        if check and status:
            raise asyncssh.ProcessError(None, command, None, status, None, status, "", "it broke")
        return SimpleNamespace(exit_status=status, stdout=out, stderr="")


@pytest.fixture
def fake_box():
    box = FakeBox()
    with patch("custom_components.batocera.installer.asyncssh.connect", box.connect):
        yield box


@pytest.fixture
async def managed_entry(hass, fake_box):
    """The entry of a box that Home Assistant installed."""
    data = await installer.async_install("192.0.2.10", ROOT_PASSWORD)
    fake_box.runs.clear()
    entry = MockConfigEntry(
        domain=DOMAIN, title="Arcade", data={CONF_TOPIC: TOPIC, **data}, unique_id=TOPIC
    )
    entry.add_to_hass(hass)
    return entry
