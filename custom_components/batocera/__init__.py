"""Batocera retro-gaming box over MQTT, with playtime limits."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import mqtt, websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from . import installer
from .box import Box
from .const import CONF_PRIVATE_KEY, DOMAIN
from .covers import CoverCache

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

_LOGGER = logging.getLogger(__name__)

type BatoceraConfigEntry = ConfigEntry[Box]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    from .dashboard import async_setup_dashboard

    websocket_api.async_register_command(hass, ws_access)
    await async_setup_dashboard(hass)
    return True


@websocket_api.websocket_command(
    {vol.Required("type"): "batocera/access", vol.Required("entry_id"): str}
)
@callback
def ws_access(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Whether the user may launch, pause and stop games on a box: the panel
    shows its controls only then. The services check again."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No such Batocera box")
        return
    connection.send_result(msg["id"], {"control": entry.runtime_data.may_control(connection.user)})


async def async_setup_entry(hass: HomeAssistant, entry: BatoceraConfigEntry) -> bool:
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("MQTT is not available")
    box = Box(hass, entry)
    box.covers = CoverCache(hass, box, _covers_dir(hass, entry))
    hass.async_add_executor_job(box.covers.prune)  # covers more than a week old
    if CONF_PRIVATE_KEY in entry.data:  # installed from Home Assistant
        _keep_daemon_updated(hass, entry, box)
    _warn_about_public_web(hass, entry, box)
    await box.async_setup(entry)
    entry.runtime_data = box
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = box

    from .dashboard import async_save_dashboard

    await async_save_dashboard(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BatoceraConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].pop(entry.entry_id, None)

    from .dashboard import async_save_dashboard

    await async_save_dashboard(hass)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: BatoceraConfigEntry) -> None:
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}").async_remove()
    await hass.async_add_executor_job(CoverCache.remove, _covers_dir(hass, entry))
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry))
    ir.async_delete_issue(hass, DOMAIN, _public_web_issue_id(entry))


def _covers_dir(hass: HomeAssistant, entry: ConfigEntry) -> Path:
    return Path(hass.config.cache_path(DOMAIN, entry.entry_id))


def _issue_id(entry: ConfigEntry) -> str:
    return f"daemon_{entry.entry_id}"


def _public_web_issue_id(entry: ConfigEntry) -> str:
    return f"public_web_{entry.entry_id}"


def _warn_about_public_web(hass: HomeAssistant, entry: ConfigEntry, box: Box) -> None:
    """A Repair issue while the box says EmulationStation's web API is open
    to the network. It only explains how to close it: the box's settings
    are the owner's to change."""
    shown: bool | None = None

    @callback
    def check() -> None:
        nonlocal shown
        if box.public_web is None or box.public_web == shown:
            return
        shown = box.public_web
        if shown:
            ir.async_create_issue(
                hass,
                DOMAIN,
                _public_web_issue_id(entry),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="public_web_access",
                translation_placeholders={"name": entry.title},
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, _public_web_issue_id(entry))

    entry.async_on_unload(box.add_listener(check))


def _keep_daemon_updated(hass: HomeAssistant, entry: ConfigEntry, box: Box) -> None:
    """Check the daemon's version each time the box comes online, as soon as
    it is idle: restarting the daemon during a game would lose that session."""
    checked = False

    @callback
    def check() -> None:
        nonlocal checked
        if not box.online:
            checked = False
        elif box.state == "idle" and not checked:
            checked = True
            entry.async_create_background_task(
                hass, _async_update_daemon(hass, entry), "batocera daemon update"
            )

    entry.async_on_unload(box.add_listener(check))


async def _async_update_daemon(hass: HomeAssistant, entry: ConfigEntry) -> None:
    try:
        if await installer.async_update(entry.data):
            _LOGGER.info("%s: daemon updated to %s", entry.title, installer.VERSION)
    except installer.InstallError as err:
        _LOGGER.warning("%s: cannot update the daemon: %s", entry.title, err)
        ir.async_create_issue(
            hass,
            DOMAIN,
            _issue_id(entry),
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=(
                "host_key_mismatch" if err.reason == "host_key_mismatch" else "update_failed"
            ),
            translation_placeholders={
                "name": entry.title,
                "host": entry.data[CONF_HOST],
                "error": str(err),
            },
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, _issue_id(entry))
