"""Manual lock for a Batocera box."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BatoceraConfigEntry
from .entity import BatoceraEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BatoceraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ManualLock(entry.runtime_data, "manual_lock", "Manual lock")])


class ManualLock(BatoceraEntity, SwitchEntity):
    """Locks the box regardless of the limits, until turned off."""

    _attr_icon = "mdi:lock"

    @property
    def is_on(self) -> bool:
        return self.box.manual_lock

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.box.set_option("manual_lock", True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.box.set_option("manual_lock", False)
