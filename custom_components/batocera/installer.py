"""Install and update the daemon on a box, over SSH.

Batocera runs dropbear without SFTP, so files go over as a tar stream on the
standard input of a remote command. Root's home is /userdata/system, and the
host keys live in /userdata/system/ssh: both survive Batocera upgrades.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
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

# Adds our key, replacing the one of an earlier pairing.
AUTHORIZE = (
    f"umask 077 && mkdir -p {Path(AUTHORIZED_KEYS).parent} && touch {AUTHORIZED_KEYS}"
    f" && sed -i '/ {KEY_COMMENT}$/d' {AUTHORIZED_KEYS}"
    f" && IFS= read -r key && printf '%s\\n' \"$key\" >> {AUTHORIZED_KEYS}"
)


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


class InstallError(Exception):
    """Failure; reason is cannot_connect, invalid_auth, host_key_mismatch or
    install_failed."""

    def __init__(self, reason: str, detail: object) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except asyncssh.HostKeyNotVerifiable as err:
        raise InstallError("host_key_mismatch", err) from err
    except asyncssh.PermissionDenied as err:
        raise InstallError("invalid_auth", err) from err
    except asyncssh.ProcessError as err:
        stderr = err.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        stderr = stderr.strip()[-300:]
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


async def async_install(
    host: str, password: str, settings: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Pair with a box using its root password, then install the daemon.

    The host key seen now is trusted from then on, and a new key pair of ours
    replaces any earlier one in root's authorized_keys. The password is used
    for this connection only. Settings are install.sh's environment variables;
    without them the box keeps its own. Returns the config entry data.
    """
    key = asyncssh.generate_private_key("ssh-ed25519", comment=KEY_COMMENT)
    with _errors():
        # Trust on first use. Ask for the ed25519 host key, which Batocera's
        # dropbear has: left to choose, it may generate an RSA one on the
        # spot, which takes long on small boards.
        async with _connect(
            host, password=password, client_keys=None, known_hosts=None,
            server_host_key_algs=["ssh-ed25519"],
        ) as conn:
            host_key = conn.get_server_host_key()
            await conn.run(
                AUTHORIZE, input=key.export_public_key().decode(), check=True,
                timeout=RUN_TIMEOUT,
            )
            await _push(conn, settings)
    data = {
        CONF_HOST: host,
        CONF_HOST_KEY: host_key.export_public_key().decode().strip(),
        CONF_PRIVATE_KEY: key.export_private_key().decode(),
    }
    with _errors():
        async with _connect_with_key(data) as conn:  # proves that the key works
            if (found := await _version(conn)) != VERSION:
                raise InstallError("install_failed", f"version {found!r} after install")
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
