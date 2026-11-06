"""Sensors for a Batocera box."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BatoceraConfigEntry
from .box import Box
from .entity import BatoceraEntity, LiveEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BatoceraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    box = entry.runtime_data
    async_add_entities(
        [
            Sensor(box, "game", "Game", lambda b: b.game),
            Sensor(box, "system", "System", lambda b: b.system),
            SessionStart(box, "started_at", "Session start", lambda b: b.started_at),
            PlaytimeToday(box),
        ]
    )


class Sensor(LiveEntity, SensorEntity):
    def __init__(self, box: Box, key: str, name: str, value: Callable[[Box], object]) -> None:
        super().__init__(box, key, name)
        self._value = value

    @property
    def native_value(self):  # type: ignore[override]
        return self._value(self.box)


class SessionStart(Sensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        return self.box.started_at if self.box.state == "playing" else None


class PlaytimeToday(BatoceraEntity, SensorEntity):
    """Minutes played since local midnight. Computed here, so it stays
    available while the box is off."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:timer-outline"

    def __init__(self, box: Box) -> None:
        super().__init__(box, "playtime_today", "Playtime today")

    @property
    def native_value(self) -> int:
        return int(self.box.playtime() // 60)
