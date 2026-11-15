"""Buttons for a Batocera box: stop the game, shut the box down, power it on."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BatoceraConfigEntry
from .box import BoxError
from .const import DOMAIN
from .entity import BatoceraEntity, LiveEntity
from .wake import send_magic_packet

# Seconds a shutdown may take before the box answers: it quits the running
# game first (up to 15 s) and waits for EmulationStation (up to 10 s).
SHUTDOWN_TIMEOUT = 40
# The daemon's refusals that have a message of their own; bad_request is a
# daemon from before shutdowns.
SHUTDOWN_ERRORS = ("bad_request", "busy", "es_unreachable", "shutting_down", "timeout")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BatoceraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    box = entry.runtime_data
    async_add_entities(
        [
            StopButton(box, "stop", "Stop game"),
            ShutdownButton(box, "shutdown", "Shut down"),
            PowerOnButton(box, "power_on", "Power on"),
        ]
    )


class StopButton(LiveEntity, ButtonEntity):
    _attr_icon = "mdi:stop"

    async def async_press(self) -> None:
        await self.box.async_check_control(self._context)
        await self.box.async_stop()


class ShutdownButton(BatoceraEntity, ButtonEntity):
    """Shuts the box down the way EmulationStation's menu does, so the
    gamelists are saved. Only while the box is on: Power on is for when it
    is off, never both."""

    _attr_icon = "mdi:power-standby"

    @property
    def available(self) -> bool:
        return self.box.online

    async def async_press(self) -> None:
        await self.box.async_check_control(self._context)
        try:
            await self.box.async_request("shutdown", timeout=SHUTDOWN_TIMEOUT)
        except BoxError as err:
            reason = err.reason if err.reason in SHUTDOWN_ERRORS else "failed"
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=f"shutdown_{reason}",
                translation_placeholders={"name": self.box.name, "reason": err.reason},
            ) from err


class PowerOnButton(BatoceraEntity, ButtonEntity):
    """Wakes the box with Wake-on-LAN, sent by Home Assistant itself (see
    wake.py). Only while the box is off, and once its MAC address is known:
    the box reports it while it is on, or the options give it."""

    _attr_icon = "mdi:power"

    @property
    def available(self) -> bool:
        return not self.box.online and self.box.wake_target() is not None

    async def async_press(self) -> None:
        await self.box.async_check_control(self._context)
        if (target := self.box.wake_target()) is None:
            return  # unavailable: never called
        try:
            await self.hass.async_add_executor_job(send_magic_packet, *target)
        except OSError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="wake_failed",
                translation_placeholders={"name": self.box.name, "reason": str(err)},
            ) from err
