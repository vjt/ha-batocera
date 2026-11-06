"""Base entities for the Batocera integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .box import Box
from .const import DOMAIN


class BatoceraEntity(Entity):
    """An entity of one box. Always available: limits live in Home Assistant."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, box: Box, key: str, name: str | None) -> None:
        self.box = box
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{box.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, box.entry_id)}, name=box.name, manufacturer="Batocera"
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.box.add_listener(self.async_write_ha_state))


class LiveEntity(BatoceraEntity):
    """Mirrors the box: unavailable when it is unreachable, but not when it
    was shut down cleanly, so that it can show as off."""

    @property
    def available(self) -> bool:
        return self.box.online or self.box.state == "off"
