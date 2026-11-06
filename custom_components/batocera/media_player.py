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
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BatoceraConfigEntry
from .browse import browse, parse_game
from .entity import LiveEntity

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
        return MediaPlayerEntityFeature.STOP | MediaPlayerEntityFeature.BROWSE_MEDIA

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

    async def async_media_stop(self) -> None:
        await self.box.async_check_control(self._context)
        await self.box.async_stop()
