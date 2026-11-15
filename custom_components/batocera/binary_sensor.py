"""Binary sensors for a Batocera box."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory
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
    async_add_entities(
        [
            Playing(box, "playing", "Playing"),
            Locked(box, "locked", "Locked"),
            WakeOnLan(box, "wake_on_lan", "Wake-on-LAN"),
        ]
    )


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


class WakeOnLan(BatoceraEntity, BinarySensorEntity):
    """Whether the box wakes on the magic packet, as it last said (ethtool's
    Wake-on holds "g"): off, Power on will not wake it. Unknown until a
    daemon that tells has run. The attributes say where Power on sends."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:lan-pending"

    @property
    def is_on(self) -> bool | None:
        wake_on = self.box.network.get("wake_on")
        return "g" in wake_on if wake_on else None

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        mac, broadcast = self.box.wake_target() or (None, None)
        return {
            "mac_address": mac,
            "broadcast_address": broadcast,
            "interface": self.box.network.get("interface"),
            "wake_on": self.box.network.get("wake_on"),
            "supports_wake_on": self.box.network.get("supports_wake_on"),
        }
