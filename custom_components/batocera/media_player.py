"""Media player for a Batocera box: what is playing, and a stop button."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BatoceraConfigEntry
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
    _attr_supported_features = MediaPlayerEntityFeature.STOP

    @property
    def state(self) -> MediaPlayerState | None:
        return STATES.get(self.box.state or "")

    @property
    def media_title(self) -> str | None:
        return self.box.game

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

    async def async_media_stop(self) -> None:
        await self.box.async_check_control(self._context)
        await self.box.async_stop()
