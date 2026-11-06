"""Binary sensors for a Batocera box."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BatoceraConfigEntry
from .entity import BatoceraEntity, LiveEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BatoceraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    box = entry.runtime_data
    async_add_entities([Playing(box, "playing", "Playing"), Locked(box, "locked", "Locked")])


class Playing(LiveEntity, BinarySensorEntity):
    _attr_icon = "mdi:gamepad-variant"

    @property
    def is_on(self) -> bool:
        return self.box.state == "playing"


class Locked(BatoceraEntity, BinarySensorEntity):
    """On while games are not allowed: manual lock, outside the window, or
    over the daily limit (the "reason" attribute)."""

    _attr_icon = "mdi:lock-clock"

    @property
    def is_on(self) -> bool:
        return self.box.lock_reason is not None

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"reason": self.box.lock_reason}
