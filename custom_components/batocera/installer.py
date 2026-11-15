"""Install and update the daemon on a box, over SSH.

Batocera runs dropbear without SFTP, so files go over as a tar stream on the
standard input of a remote command. Root's home is /userdata/system, and the
host keys live in /userdata/system/ssh: both survive Batocera upgrades.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
import io
from pathlib import Path
import shlex
import tarfile
from typing import Any

import asyncssh

from homeassistant.const import CONF_HOST

from .const import CONF_HOST_KEY, CONF_PRIVATE_KEY

DAEMON = Path(__file__).parent / "daemon"
BOX_DIR = "/userdata/system/ha-batocera"  # where install.sh puts the daemon
STAGING = "/tmp/ha-batocera"
AUTHORIZED_KEYS = "/userdata/system/.ssh/authorized_keys"
KEY_COMMENT = "ha-batocera"
CONNECT_TIMEOUT = 15
RUN_TIMEOUT = 120
MQTT_PORT = 1883  # install.sh's default
MQTT_TIMEOUT = 10  # seconds the box waits for a broker to answer
# How a mosquitto_pub stopped by timeout(1) ends: GNU's timeout exits with
# 124; busybox's kills it with SIGTERM, which the shell reports as 143, or
# dropbear as the signal itself when the shell exec'd it.
TIMED_OUT = (124, 143, -15)

# Adds our key, replacing the one of an earlier pairing.
AUTHORIZE = (
    f"umask 077 && mkdir -p {Path(AUTHORIZED_KEYS).parent} && touch {AUTHORIZED_KEYS}"
    f" && sed -i '/ {KEY_COMMENT}$/d' {AUTHORIZED_KEYS}"
    f" && IFS= read -r key && printf '%s\\n' \"$key\" >> {AUTHORIZED_KEYS}"
)
# Settings not installed yet go, through stdin, to a mosquitto_pub file of
# their own (mode 600, in a private directory) that is removed afterwards.
PROBE = (
    "dir=$(mktemp -d) && trap 'rm -rf \"$dir\"' EXIT && umask 077"
    ' && cat > "$dir/mosquitto_pub" && XDG_CONFIG_HOME=$dir '
)
# The settings install.sh wrote, in the files the daemon reads.
CHECK = f"XDG_CONFIG_HOME={BOX_DIR} "


def _bundle() -> tuple[str, bytes]:
    """The daemon's version, and its files as a tar archive."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path in sorted(DAEMON.iterdir()):
            if path.is_file():
                tar.add(path, arcname=path.name)
    return (DAEMON / "VERSION").read_text().strip(), buf.getvalue()


# Read at import, which Home Assistant does outside the event loop.
VERSION, BUNDLE = _bundle()


@dataclass(frozen=True)
class Broker:
    """The broker as the box reaches it, and the box's account on it."""

    host: str
    port: int = MQTT_PORT
    user: str = ""
    password: str = field(default="", repr=False)

    def __str__(self) -> str:
        return f"[{self.host}]:{self.port}" if ":" in self.host else f"{self.host}:{self.port}"

    def config(self) -> str:
        """mosquitto client options, one per line, as install.sh writes them."""
        lines = [f"-h {self.host}", f"-p {self.port}"]
        if self.user:
            lines += [f"-u {self.user}", f"-P {self.password}"]
        return "".join(f"{line}\n" for line in lines)

    def settings(self, device: str) -> dict[str, str]:
        """install.sh's environment: no user means no authentication."""
        env = {"DEVICE": device, "MQTT_HOST": self.host, "MQTT_PORT": str(self.port)}
        if self.user:
            env |= {"MQTT_USER": self.user, "MQTT_PASSWORD": self.password}
        return env


class InstallError(Exception):
    """Failure; reason is cannot_connect, invalid_auth, host_key_mismatch or
    install_failed."""

    def __init__(self, reason: str, detail: object) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


class MqttError(InstallError):
    """The box cannot use a broker; reason is mqtt_refused (connection
    refused, host not found), mqtt_timeout, mqtt_auth (user name or
    password), or mqtt_tls (TLS, which is not detected)."""

    def __init__(self, reason: str, broker: Broker, detail: object = "") -> None:
        super().__init__(reason, f"{broker}: {detail}" if detail else broker)
        self.broker = broker


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value or "")


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except asyncssh.HostKeyNotVerifiable as err:
        raise InstallError("host_key_mismatch", err) from err
    except asyncssh.PermissionDenied as err:
        raise InstallError("invalid_auth", err) from err
    except asyncssh.ProcessError as err:
        stderr = _text(err.stderr).strip()[-300:]
        detail = (err.reason or "timed out") + (f": {stderr}" if stderr else "")
        raise InstallError("install_failed", detail) from err
    except (OSError, asyncssh.Error) as err:  # TimeoutError is an OSError
        raise InstallError("cannot_connect", str(err) or type(err).__name__) from err


def _connect(host: str, **kwargs: Any):
    # No ~/.ssh/config, agent or default keys of the Home Assistant host.
    return asyncssh.connect(
        host, username="root", config=None, agent_path=None,
        connect_timeout=CONNECT_TIMEOUT, **kwargs,
    )


def _connect_with_key(data: Mapping[str, Any]):
    return _connect(
        data[CONF_HOST],
        client_keys=[asyncssh.import_private_key(data[CONF_PRIVATE_KEY])],
        known_hosts=([asyncssh.import_public_key(data[CONF_HOST_KEY])], [], []),
    )


async def _version(conn: asyncssh.SSHClientConnection) -> str:
    result = await conn.run(f"cat {BOX_DIR}/VERSION", timeout=RUN_TIMEOUT)
    return str(result.stdout).strip() if result.exit_status == 0 else ""


async def _push(
    conn: asyncssh.SSHClientConnection, settings: Mapping[str, str] | None = None
) -> None:
    """Copy the daemon to the box and run install.sh there. Settings go to
    its environment, the MQTT password through stdin: never on a command line."""
    await conn.run(
        f"rm -rf {STAGING} && mkdir -p {STAGING} && tar -xf - -C {STAGING}",
        input=BUNDLE, encoding=None, check=True, timeout=RUN_TIMEOUT,
    )
    command = f"bash {STAGING}/install.sh"
    if not settings:  # keep the box's settings
        await conn.run(command, stdin=asyncssh.DEVNULL, check=True, timeout=RUN_TIMEOUT)
        return
    env = {k: v for k, v in settings.items() if k != "MQTT_PASSWORD"}
    assign = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    await conn.run(
        f"IFS= read -r MQTT_PASSWORD && export MQTT_PASSWORD {assign} && {command}",
        input=settings.get("MQTT_PASSWORD", "") + "\n", check=True, timeout=RUN_TIMEOUT,
    )


async def _mqtt(
    conn: asyncssh.SSHClientConnection, files: str, broker: Broker, topic: str,
    config: str | None = None,
) -> None:
    """Connect to the broker from the box as the daemon does: mosquitto_pub
    with the options in the mosquitto_pub file under $XDG_CONFIG_HOME
    (files), publishing an empty message, not retained, on a topic nothing
    reads. Raises MqttError unless it works."""
    command = (
        f"{files}timeout {MQTT_TIMEOUT} mosquitto_pub -q 1 -n"
        f" -t {shlex.quote(topic + '/check')}"
    )
    stdin: dict[str, Any] = {"stdin": asyncssh.DEVNULL} if config is None else {"input": config}
    result = await conn.run(command, timeout=RUN_TIMEOUT, **stdin)
    if result.returncode == 0:
        return
    stderr = _text(result.stderr).strip()[-300:]
    # mosquitto's CONNACK texts: "Connection Refused: bad user name or
    # password." and "Connection Refused: not authorised." (MQTT 3.1.1).
    if "bad user name or password" in stderr.lower() or "not authori" in stderr.lower():
        reason = "mqtt_auth"
    elif result.returncode in TIMED_OUT:
        reason = "mqtt_timeout"
    else:
        reason = "mqtt_refused"
    raise MqttError(reason, broker, stderr or f"exit status {result.returncode}")


class Pairing:
    """A box reached with its root password, where nothing has changed yet."""

    def __init__(self, conn: asyncssh.SSHClientConnection, host: str) -> None:
        self._conn = conn
        self._host = host

    async def async_ha_address(self) -> str | None:
        """Home Assistant's address as the box sees it: the first field of
        $SSH_CLIENT. Not the local address of our socket, which behind
        Docker's NAT is the container's, not one the box can reach."""
        result = await self._conn.run(
            'echo "$SSH_CLIENT"', stdin=asyncssh.DEVNULL, timeout=RUN_TIMEOUT
        )
        fields = _text(result.stdout).split()
        return fields[0] if fields else None

    async def async_probe(self, brokers: Iterable[Broker], topic: str) -> Broker:
        """The first of the brokers the box connects to. When none works,
        raises the MqttError of the first one that answered (the account was
        refused), or else of the first one tried."""
        failures: list[MqttError] = []
        for broker in brokers:
            try:
                await _mqtt(self._conn, PROBE, broker, topic, broker.config())
            except MqttError as err:
                failures.append(err)
            else:
                return broker
        raise next((err for err in failures if err.reason == "mqtt_auth"), failures[0])

    async def async_install(self, settings: Mapping[str, str] | None = None) -> dict[str, str]:
        """Put a new key pair of ours in root's authorized_keys, replacing
        any earlier one, and install the daemon. Settings are install.sh's
        environment variables; without them the box keeps its own. Returns
        the config entry data."""
        key = asyncssh.generate_private_key("ssh-ed25519", comment=KEY_COMMENT)
        host_key = self._conn.get_server_host_key()
        await self._conn.run(
            AUTHORIZE, input=key.export_public_key().decode(), check=True,
            timeout=RUN_TIMEOUT,
        )
        await _push(self._conn, settings)
        return {
            CONF_HOST: self._host,
            CONF_HOST_KEY: host_key.export_public_key().decode().strip(),
            CONF_PRIVATE_KEY: key.export_private_key().decode(),
        }


@asynccontextmanager
async def async_pair(host: str, password: str) -> AsyncIterator[Pairing]:
    """Connect to a box with its root password, which is used for this
    connection only. The host key seen now is trusted from then on."""
    with _errors():
        # Trust on first use. Ask for the ed25519 host key, which Batocera's
        # dropbear has: left to choose, it may generate an RSA one on the
        # spot, which takes long on small boards.
        async with _connect(
            host, password=password, client_keys=None, known_hosts=None,
            server_host_key_algs=["ssh-ed25519"],
        ) as conn:
            yield Pairing(conn, host)


async def async_verify(
    data: Mapping[str, Any], broker: Broker | None = None, topic: str = ""
) -> None:
    """Connect with our key, which proves that it works, and check the
    daemon's version; with a broker, also that the box connects to it with
    the settings installed for the daemon."""
    with _errors():
        async with _connect_with_key(data) as conn:
            if (found := await _version(conn)) != VERSION:
                raise InstallError("install_failed", f"version {found!r} after install")
            if broker is not None:
                await _mqtt(conn, CHECK, broker, topic)


async def async_install(host: str, password: str) -> dict[str, str]:
    """Pair with a box using its root password, then install the daemon
    keeping the settings on the box. Returns the config entry data."""
    async with async_pair(host, password) as box:
        data = await box.async_install()
    await async_verify(data)
    return data


async def async_update(data: Mapping[str, Any]) -> bool:
    """Install the bundled daemon if the box runs another version. Returns
    whether it did."""
    with _errors():
        async with _connect_with_key(data) as conn:
            if await _version(conn) == VERSION:
                return False
            await _push(conn)
    return True
