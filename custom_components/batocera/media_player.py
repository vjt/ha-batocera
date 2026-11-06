"""Media player for a Batocera box: what is playing, a stop button, and
the box's games to browse."""

from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BatoceraConfigEntry
from .box import BoxError
from .browse import browse, parse_game
from .const import DOMAIN
from .entity import LiveEntity

# Seconds a launch may take before the box answers: it quits the running
# game first (up to 15 s) and waits for EmulationStation (up to 10 s).
LAUNCH_TIMEOUT = 40
# The daemon's refusals that have a message of their own.
LAUNCH_ERRORS = ("busy", "es_unreachable", "locked", "timeout", "unknown_game")

STATES = {
    "playing": MediaPlayerState.PLAYING,
    "idle": MediaPlayerState.IDLE,
    "off": MediaPlayerState.OFF,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BatoceraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([BatoceraMediaPlayer(entry.runtime_data, "media_player", None)])


class BatoceraMediaPlayer(LiveEntity, MediaPlayerEntity):
    _attr_media_content_type = MediaType.GAME

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        return (
            MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.BROWSE_MEDIA
            | MediaPlayerEntityFeature.PLAY_MEDIA
        )

    @property
    def state(self) -> MediaPlayerState | None:
        return STATES.get(self.box.state or "")

    @property
    def media_title(self) -> str | None:
        return self.box.game

    @property
    def media_content_id(self) -> str | None:
        """The running game, as the media browser names it."""
        if self.box.state == "playing" and self.box.game_id and self.box.system:
            return f"game:{self.box.system}:{self.box.game_id}"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # Changes when the catalog does: the panel then reads it again.
        return {"catalog": self.box.catalog_hash}

    @property
    def source(self) -> str | None:
        return self.box.system

    @property
    def app_name(self) -> str | None:
        return self.box.emulator

    @property
    def media_image_hash(self) -> str | None:
        return self.box.cover_hash

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        if self.box.cover:
            return self.box.cover, "image/jpeg"
        return None, None

    async def async_browse_media(
        self, media_content_type: str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        return browse(
            self.box,
            media_content_id,
            lambda cid: self.get_browse_image_url(MediaType.GAME, cid),
        )

    async def async_get_browse_image(
        self, media_content_type: str, media_content_id: str, media_image_id: str | None = None
    ) -> tuple[bytes | None, str | None]:
        game = parse_game(self.box, media_content_id)
        jpeg = await self.box.covers.async_get(game) if game else None
        return (jpeg, "image/jpeg") if jpeg else (None, None)

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        """Launch a game of the catalog, quitting the running one."""
        await self.box.async_check_control(self._context)
        name = {"name": self.box.name}
        game = parse_game(self.box, media_id)
        if game is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_game",
                translation_placeholders={**name, "media_id": media_id},
            )
        if self.box.lock_reason:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=f"locked_{self.box.lock_reason}",
                translation_placeholders=name,
            )
        if not self.box.online or self.box.state == "off":
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="offline", translation_placeholders=name
            )
        try:
            await self.box.async_request(
                "launch", timeout=LAUNCH_TIMEOUT, system=game.system, game=game.id
            )
        except BoxError as err:
            reason = err.reason if err.reason in LAUNCH_ERRORS else "failed"
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=f"launch_{reason}",
                translation_placeholders={**name, "game": game.name, "reason": err.reason},
            ) from err

    async def async_media_stop(self) -> None:
        await self.box.async_check_control(self._context)
        await self.box.async_stop()
