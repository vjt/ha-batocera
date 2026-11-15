"""MQTT for a new box, without asking: where the box may reach Home
Assistant's broker, and a login of its own on the Mosquitto add-on.

Home Assistant users are never created for this: there is no public API for
it. The add-on's documented "logins" option is, through the MQTT
integration's own AddonManager (one instance per add-on, so never another).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import logging
import secrets

from homeassistant.components import mqtt
from homeassistant.components.hassio import AddonError, AddonManager, AddonState
from homeassistant.components.mqtt.addon import get_addon_manager
from homeassistant.components.mqtt.const import (
    CONF_BROKER,
    CONF_CERTIFICATE,
    DEFAULT_PORT,
)
from homeassistant.const import CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.hassio import is_hassio

_LOGGER = logging.getLogger(__name__)

# Broker hosts that mean something only inside Home Assistant.
LOCAL_HOSTS = ("core-mosquitto", "localhost")
FALLBACK_HOST = "homeassistant.local"
RESTART_TIMEOUT = 60  # seconds for the add-on's broker to be back


@dataclass(frozen=True)
class HaBroker:
    """The broker of Home Assistant's MQTT entry, as Home Assistant reaches it."""

    host: str
    port: int
    tls: bool


def ha_broker(hass: HomeAssistant) -> HaBroker | None:
    """None without an MQTT entry."""
    for entry in hass.config_entries.async_entries(mqtt.DOMAIN):
        return HaBroker(
            str(entry.data.get(CONF_BROKER, "")),
            int(entry.data.get(CONF_PORT, DEFAULT_PORT)),
            # Home Assistant's client uses TLS when a CA is set ("auto" or a
            # certificate of its own).
            bool(entry.data.get(CONF_CERTIFICATE)),
        )
    return None


def is_local(host: str) -> bool:
    """Whether the host only means something inside Home Assistant."""
    if host.lower() in LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def candidates(ha: HaBroker | None, ha_address: str | None) -> list[tuple[str, int]]:
    """Where the box may reach the broker, in the order to try: the host of
    Home Assistant's MQTT entry, unless only Home Assistant can resolve it;
    Home Assistant's own address as the box sees it; its mDNS name. All on
    the port of Home Assistant's entry."""
    port = ha.port if ha else DEFAULT_PORT
    hosts = (ha.host if ha and not is_local(ha.host) else "", ha_address or "", FALLBACK_HOST)
    return list(dict.fromkeys((host, port) for host in hosts if host))


async def async_mosquitto(hass: HomeAssistant, ha: HaBroker | None) -> AddonManager | None:
    """The Mosquitto add-on's manager, when it runs and Home Assistant's MQTT
    entry uses it: the one broker where Home Assistant can add a login for
    the box. Never with TLS, which is not detected."""
    if ha is None or ha.tls or not is_hassio(hass):
        return None
    manager = get_addon_manager(hass)
    try:
        info = await manager.async_get_addon_info()
    except AddonError as err:
        _LOGGER.debug("No Mosquitto add-on: %s", err)
        return None
    if info.state is AddonState.RUNNING and info.hostname == ha.host:
        return manager
    return None


def login_name(device: str) -> str:
    return f"batocera-{device}"


async def async_add_login(hass: HomeAssistant, manager: AddonManager, username: str) -> str:
    """Add the login to the add-on's options, replacing one of the same name
    and keeping everything else, then restart the add-on, which reads its
    logins as it starts. Returns the login's new, random password."""
    password = secrets.token_urlsafe(24)
    options = (await manager.async_get_addon_info()).options
    logins = [login for login in options.get("logins", []) if login.get("username") != username]
    logins.append({"username": username, "password": password})
    await manager.async_set_addon_options({**options, "logins": logins})
    await _async_restart(hass, manager)
    return password


async def async_remove_login(hass: HomeAssistant, username: str) -> None:
    """Remove the login from the add-on, restarting it if it runs. A failure
    is only logged: the entry goes anyway."""
    if not is_hassio(hass):
        return
    manager = get_addon_manager(hass)
    try:
        info = await manager.async_get_addon_info()
        logins = info.options.get("logins", [])
        kept = [login for login in logins if login.get("username") != username]
        if kept == logins:
            return
        await manager.async_set_addon_options({**info.options, "logins": kept})
        if info.state is AddonState.RUNNING:
            await manager.async_restart_addon()
    except AddonError as err:
        _LOGGER.warning("Cannot remove MQTT user %s from the Mosquitto add-on: %s", username, err)


async def _async_restart(hass: HomeAssistant, manager: AddonManager) -> None:
    """Restart the add-on and wait until it runs again, that is until Home
    Assistant's own MQTT client, which uses it, is connected again: the
    add-on counts as started a few seconds before its broker listens."""
    back = asyncio.Event()

    @callback
    def on_connection(connected: bool) -> None:
        if connected:
            back.set()

    unsubscribe = mqtt.async_subscribe_connection_status(hass, on_connection)
    try:
        await manager.async_restart_addon()
        async with asyncio.timeout(RESTART_TIMEOUT):
            await back.wait()
    except TimeoutError:
        _LOGGER.warning("The Mosquitto add-on is not back after %d seconds", RESTART_TIMEOUT)
    finally:
        unsubscribe()
