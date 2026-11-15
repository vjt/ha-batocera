"""Fixtures for the Home Assistant integration tests."""

from contextlib import asynccontextmanager
import copy
import gzip
import io
import json
import re
import shlex
import tarfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncssh
import pytest
from homeassistant import config_entries
from homeassistant.components import mqtt
from homeassistant.components.hassio import AddonError, AddonInfo, AddonState
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResultType, section
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message

from custom_components.batocera import installer
from custom_components.batocera.const import CONF_TOPIC, DOMAIN

TOPIC = "batocera/test"
NOW = "2026-03-02T10:00:00-08:00"  # tests run in US/Pacific
ROOT_PASSWORD = "root-secret"
HA_ADDRESS = "192.0.2.1"  # Home Assistant, as the box sees it
REFUSED = "Error: Connection refused"
NOT_AUTHORISED = "Connection error: Connection Refused: not authorised."


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_dashboard(hass):
    """The frontend is taken as set up (the real one needs its build), and
    the page, which needs Lovelace and the http server, is tested on its
    own in test_dashboard.py."""
    hass.config.components.add("frontend")
    with patch("custom_components.batocera.dashboard.async_setup_dashboard", AsyncMock()), patch(
        "custom_components.batocera.dashboard.async_save_dashboard", AsyncMock()
    ):
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
    and authorized_keys, running the commands the installer sends, and the
    brokers it can reach through mosquitto_pub."""

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
        self.ssh_client = f"{HA_ADDRESS} 50522 22"
        # (host, port) -> accounts {user: password}, or None for anonymous.
        self.brokers: dict[tuple[str, int], dict[str, str] | None] = {}
        self.silent: set[tuple[str, int]] = set()  # brokers that never answer
        self.timed_out = 124  # how timeout(1) ends mosquitto_pub: GNU's
        self.mqtt_tries: list[tuple[str, int, str]] = []  # (host, port, user)
        self.mqtt_config: str | None = None  # the daemon's mosquitto_pub file

    def mqtt(self, config: str) -> tuple[int, str]:
        """mosquitto_pub with these options: exit status and stderr."""
        options = dict(line.split(" ", 1) for line in config.splitlines())
        host, port = options["-h"], int(options["-p"])
        user, password = options.get("-u", ""), options.get("-P", "")
        self.mqtt_tries.append((host, port, user))
        if (host, port) in self.silent:
            return self.timed_out, ""
        if (host, port) not in self.brokers:
            return 1, REFUSED
        accounts = self.brokers[host, port]
        if accounts is not None and (not user or accounts.get(user) != password):
            return 5, NOT_AUTHORISED
        return 0, ""

    def install(self, command: str, stdin: str) -> None:
        """install.sh: writes the daemon's mosquitto_pub file when given a host."""
        env = dict(t.split("=", 1) for t in shlex.split(command) if re.match(r"[A-Z_]+=", t))
        if env.get("MQTT_HOST"):
            config = f"-h {env['MQTT_HOST']}\n-p {env.get('MQTT_PORT') or 1883}\n"
            if env.get("MQTT_USER"):
                config += f"-u {env['MQTT_USER']}\n-P {stdin.removesuffix(chr(10))}\n"
            self.mqtt_config = config
        self.version = self.staged["VERSION"].decode().strip()

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
        status, out, err = 0, "", ""
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
            self.install(command, input or "")
        elif command == 'echo "$SSH_CLIENT"':
            out = self.ssh_client + "\n"
        elif "mosquitto_pub" in command:
            # Settings to try come on stdin; the daemon's are in its files.
            probe = command.startswith(installer.PROBE)
            status, err = self.mqtt(input if probe else self.mqtt_config)
        if check and status:
            raise asyncssh.ProcessError(None, command, None, status, None, status, "", "it broke")
        return SimpleNamespace(exit_status=status, returncode=status, stdout=out, stderr=err)


@pytest.fixture
async def kid(hass, hass_admin_user):
    """A plain user: not an admin, but allowed to control entities. (The
    admin is there first: the first user would become the owner.)"""
    return await hass.auth.async_create_user("Kid", group_ids=["system-users"])


@pytest.fixture
def fake_box():
    box = FakeBox()
    with patch("custom_components.batocera.installer.asyncssh.connect", box.connect):
        yield box


class FakeMosquitto:
    """The Mosquitto add-on, as the MQTT integration's AddonManager reports
    it. Its broker is the one the box reaches at accounts: logins change
    there when the add-on has restarted, as Home Assistant's own MQTT client
    connects again."""

    def __init__(self, hass, accounts: dict[str, str]) -> None:
        self.hass = hass
        self.accounts = accounts
        self.state = AddonState.RUNNING
        self.hostname = "core-mosquitto"
        self.options = {
            "logins": [{"username": "zigbee", "password": "zigbee-secret"}],
            "require_certificate": False,
            "certfile": "fullchain.pem",
            "keyfile": "privkey.pem",
            "customize": {"active": False, "folder": "mosquitto"},
        }
        self.restarts = 0
        self.comes_back = True
        self.failing: str | None = None  # the name of a method that fails
        self.accounts.update(self.logins())

    def fail(self, method: str) -> None:
        if self.failing == method:
            raise AddonError(f"{method}: the Supervisor said no")

    async def async_get_addon_info(self) -> AddonInfo:
        self.fail("async_get_addon_info")
        return AddonInfo(
            available=True, hostname=self.hostname, options=copy.deepcopy(self.options),
            state=self.state, update_available=False, version="6.5.2",
        )

    async def async_set_addon_options(self, config: dict) -> None:
        self.fail("async_set_addon_options")
        self.options = copy.deepcopy(config)

    async def async_restart_addon(self) -> None:
        self.fail("async_restart_addon")
        self.restarts += 1
        async_dispatcher_send(self.hass, mqtt.MQTT_CONNECTION_STATE, False)
        if self.comes_back:
            self.hass.loop.call_soon(self.started)

    @callback
    def started(self) -> None:
        self.accounts.clear()
        self.accounts.update(self.logins())
        async_dispatcher_send(self.hass, mqtt.MQTT_CONNECTION_STATE, True)

    def logins(self) -> dict[str, str]:
        return {x["username"]: x["password"] for x in self.options["logins"]}


def mqtt_entry(hass, **data):
    """Home Assistant's MQTT entry. Taken as loaded: never set up for real."""
    entry = MockConfigEntry(
        domain=mqtt.DOMAIN, title="MQTT", data={"broker": "core-mosquitto", "port": 1883} | data,
        state=config_entries.ConfigEntryState.LOADED,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def supervisor(hass):
    """Home Assistant OS or Supervised: is_hassio is true."""
    with patch("custom_components.batocera.mqtt_setup.is_hassio", return_value=True):
        yield


@pytest.fixture
def mosquitto(hass, fake_box, supervisor):
    """The Mosquitto add-on, running, used by Home Assistant's MQTT entry,
    and reached by the box on Home Assistant's address."""
    mqtt_entry(hass, username="homeassistant", password="ha-secret")
    addon = FakeMosquitto(hass, fake_box.brokers.setdefault((HA_ADDRESS, 1883), {}))
    with patch("custom_components.batocera.mqtt_setup.get_addon_manager", return_value=addon):
        yield addon


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


def advanced(result) -> section:
    return next(v for v in result["data_schema"].schema.values() if isinstance(v, section))


def suggested(result):
    """What the form shows filled in, the Advanced fields included."""
    keys = [k for k in result["data_schema"].schema if k != "advanced"]
    keys += list(advanced(result).schema.schema)
    return {str(key): (key.description or {}).get("suggested_value") for key in keys}


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


# A catalog as the daemon publishes it (see docs/internals.md, "The catalog").
SONIC, SOR, BARE, MARIO = "9b1c4e0d2a37", "0d5e7a9b3c21", "1a2b3c4d5e6f", "5f4e3d2c1b0a"
INDEX = [
    {"name": "megadrive", "fullname": "Sega Mega Drive", "manufacturer": "Sega", "year": 1988,
     "count": 3, "hash": "aaaaaaaaaaaa"},
    {"name": "snes", "fullname": "Super Nintendo", "manufacturer": "Nintendo", "year": 1990,
     "count": 1, "hash": "bbbbbbbbbbbb"},
]
ROWS = {
    "megadrive": [
        [BARE, "Bare", None, "", "", 0, 0, ""],
        [SONIC, "Sonic the Hedgehog", 1991, "Platform", "1", 3, 12, "20261102T183000"],
        [SOR, "Streets of Rage", 1991, "Beat'em up", "1-2", 2, 1, "20261104T200000"],
    ],
    "snes": [[MARIO, "Super Mario World", 1990, "Platform", "1-2", 2, 3, "20261105T090000"]],
}


async def publish_catalog(hass, index=INDEX, rows=ROWS):
    for name, games in rows.items():
        async_fire_mqtt_message(
            hass, f"{TOPIC}/catalog/system/{name}", gzip.compress(json.dumps(games).encode())
        )
    async_fire_mqtt_message(hass, f"{TOPIC}/catalog/index", json.dumps(index))
    await hass.async_block_till_done()


class FakeDaemon:
    """Answers the integration's requests on req as the daemon would.

    answers maps an op to the answer's fields (ok, error, ...), or to a
    function of the request returning them; None leaves it unanswered.
    """

    def __init__(self, hass) -> None:
        self.hass = hass
        self.requests: list[dict] = []
        self.answers: dict = {}

    @callback
    def on_request(self, msg) -> None:
        req = json.loads(msg.payload)
        self.requests.append(req)
        answer = self.answers.get(req["op"])
        if callable(answer):
            answer = answer(req)
        if answer is not None:
            self.hass.loop.call_soon(
                async_fire_mqtt_message,
                self.hass,
                f"{TOPIC}/resp/{req['id']}",
                json.dumps({"id": req["id"], **answer}),
            )


@pytest.fixture
async def daemon(hass, mqtt_mock):
    fake = FakeDaemon(hass)
    unsubscribe = await mqtt.async_subscribe(hass, f"{TOPIC}/req", fake.on_request)
    yield fake
    unsubscribe()
