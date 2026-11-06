"""Stop button for a Batocera box."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BatoceraConfigEntry
from .entity import LiveEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BatoceraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([StopButton(entry.runtime_data, "stop", "Stop game")])


class StopButton(LiveEntity, ButtonEntity):
    _attr_icon = "mdi:stop"

    async def async_press(self) -> None:
        await self.box.async_stop()
