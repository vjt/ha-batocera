"""Config flow for the Batocera integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME

from .const import CONF_TOPIC, DEFAULT_TOPIC, DOMAIN


class BatoceraConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            topic = user_input[CONF_TOPIC].strip().strip("/")
            if not topic or "+" in topic or "#" in topic:
                errors[CONF_TOPIC] = "invalid_topic"
            else:
                await self.async_set_unique_id(topic)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data={CONF_TOPIC: topic}
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Batocera"): str,
                vol.Required(CONF_TOPIC, default=DEFAULT_TOPIC): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
