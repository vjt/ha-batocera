"""What the box's TV shows, as an image entity."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BatoceraConfigEntry
from .box import Box
from .entity import LiveEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BatoceraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([Screen(hass, entry.runtime_data)])


class Screen(LiveEntity, ImageEntity):
    """The daemon's latest screenshot: menus too, not only games."""

    _attr_content_type = "image/jpeg"
    _attr_icon = "mdi:television"

    def __init__(self, hass: HomeAssistant, box: Box) -> None:
        LiveEntity.__init__(self, box, "screen", "Screen")
        ImageEntity.__init__(self, hass)
        self._attr_image_last_updated = box.screen_at

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.box.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self._attr_image_last_updated = self.box.screen_at
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        return self.box.screen
