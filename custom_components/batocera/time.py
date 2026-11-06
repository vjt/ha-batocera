"""Allowed time window for a Batocera box."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BatoceraConfigEntry
from .entity import BatoceraEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BatoceraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    box = entry.runtime_data
    async_add_entities(
        [
            WindowBound(box, "allowed_from", "Allowed from"),
            WindowBound(box, "allowed_until", "Allowed until"),
        ]
    )


class WindowBound(BatoceraEntity, TimeEntity):
    """One end of the daily window in which games are allowed. With both
    ends equal there is no window; "until" before "from" spans midnight."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:clock-outline"

    @property
    def native_value(self) -> time:
        return getattr(self.box, self.key)

    async def async_set_value(self, value: time) -> None:
        self.box.set_option(self.key, value)
