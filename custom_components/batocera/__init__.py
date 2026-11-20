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

from . import installer, mqtt_setup
from .box import Box, Game
from .const import CONF_MQTT_LOGIN, CONF_PRIVATE_KEY, DOMAIN
from .covers import CoverCache
from .info import InfoCache
from .live import LiveView
from .stats import compute as compute_stats

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
    websocket_api.async_register_command(hass, ws_games)
    websocket_api.async_register_command(hass, ws_info)
    websocket_api.async_register_command(hass, ws_stats)
    hass.http.register_view(LiveView())
    await async_setup_dashboard(hass)
    return True


def _ws_box(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> Box | None:
    """The loaded box msg names, or None once the error is sent."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No such Batocera box")
        return None
    return entry.runtime_data


@websocket_api.websocket_command(
    {vol.Required("type"): "batocera/access", vol.Required("entry_id"): str}
)
@callback
def ws_access(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Whether the user may launch, pause and stop games on a box: the panel
    shows its controls only then. The services check again."""
    if box := _ws_box(hass, connection, msg):
        connection.send_result(msg["id"], {"control": box.may_control(connection.user)})


def _facts(game: Game) -> dict[str, Any]:
    """What batocera/games tells of a game. Many games say nothing of their
    players, and few were played: those are left out when empty."""
    facts: dict[str, Any] = {"genre": game.genre, "year": game.year, "rating": game.rating}
    if game.players:
        facts["players"] = game.players
    if game.playcount:
        facts["playcount"] = game.playcount
    if game.lastplayed:
        facts["lastplayed"] = game.lastplayed
    return facts


@websocket_api.websocket_command(
    {
        vol.Required("type"): "batocera/games",
        vol.Required("entry_id"): str,
        vol.Optional("system"): str,
        vol.Optional("game"): str,
    }
)
@callback
def ws_games(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Genre, year, rating, players and plays of a system's games, without a
    system of every game, or of one game, by game id: the page's filters and
    its launch dialog need them, and the media browser does not carry them."""
    if box := _ws_box(hass, connection, msg):
        if "game" in msg:
            game = box.games.get(msg["game"])
            games = [game] if game and msg.get("system", game.system) == game.system else []
        else:
            games = box.system_games(msg["system"]) if "system" in msg else box.games.values()
        connection.send_result(msg["id"], {g.id: _facts(g) for g in games})


@websocket_api.websocket_command(
    {vol.Required("type"): "batocera/stats", vol.Required("entry_id"): str}
)
@callback
def ws_stats(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Time played, for the page's high scores: today, this month and the
    last 30 days from the sessions Home Assistant kept, all time from
    EmulationStation's counts in the catalog. See stats.compute."""
    if box := _ws_box(hass, connection, msg):
        connection.send_result(msg["id"], compute_stats(box))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "batocera/info",
        vol.Required("entry_id"): str,
        vol.Required("game"): str,
    }
)
@websocket_api.async_response
async def ws_info(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """What the box tells of a game beyond the catalog, for the launch dialog:
    desc, developer, publisher, gametime (seconds played in all), each left
    out when there is none. Empty when the box cannot say now: offline, busy,
    or a daemon too old to know the request."""
    if not (box := _ws_box(hass, connection, msg)):
        return
    if (game := box.games.get(msg["game"])) is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No such game")
        return
    connection.send_result(msg["id"], await box.game_info.async_get(game) or {})


async def async_setup_entry(hass: HomeAssistant, entry: BatoceraConfigEntry) -> bool:
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("MQTT is not available")
    box = Box(hass, entry)
    box.covers = CoverCache(hass, box, _covers_dir(hass, entry))
    box.game_info = InfoCache(hass, box, _covers_dir(hass, entry))  # kept with the covers
    hass.async_add_executor_job(box.covers.prune)  # covers more than a week old
    hass.async_add_executor_job(box.game_info.prune)  # and what the box told of games
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
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.sessions").async_remove()
    await hass.async_add_executor_job(CoverCache.remove, _covers_dir(hass, entry))
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry))
    ir.async_delete_issue(hass, DOMAIN, _public_web_issue_id(entry))
    if login := entry.data.get(CONF_MQTT_LOGIN):  # added to the Mosquitto add-on
        await mqtt_setup.async_remove_login(hass, login)


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
