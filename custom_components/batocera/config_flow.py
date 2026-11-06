"""Config flow for the Batocera integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import slugify

from . import installer
from .const import CONF_CONTROL_USERS, CONF_TOPIC, DEFAULT_HOST, DEFAULT_TOPIC, DOMAIN

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
SECRETS = (CONF_PASSWORD, "mqtt_password")

INSTALL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Batocera"): str,
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PASSWORD): PASSWORD,
        # The box gets broker settings of its own: Home Assistant's may name a
        # host only Home Assistant can reach (core-mosquitto, localhost), and
        # its account is usually allowed far more than a box needs.
        vol.Required("mqtt_host"): str,
        vol.Required("mqtt_port", default=1883): cv.port,
        vol.Optional("mqtt_user"): str,
        vol.Optional("mqtt_password"): PASSWORD,
    }
)


class BatoceraConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BatoceraOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["install", "manual"])

    async def async_step_install(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Install the daemon on the box over SSH, with the root password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            device = slugify(user_input[CONF_NAME]) or "batocera"
            topic = f"batocera/{device}"
            await self.async_set_unique_id(topic)
            self._abort_if_unique_id_configured()
            settings = {"DEVICE": device} | {
                k.upper(): str(v) for k, v in user_input.items() if k.startswith("mqtt_")
            }
            try:
                data = await installer.async_install(
                    user_input[CONF_HOST], user_input[CONF_PASSWORD], settings
                )
            except installer.InstallError as err:
                errors["base"] = err.reason
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data={CONF_TOPIC: topic, **data}
                )
        return self.async_show_form(
            step_id="install",
            data_schema=self.add_suggested_values_to_schema(
                INSTALL_SCHEMA, {k: v for k, v in (user_input or {}).items() if k not in SECRETS}
            ),
            errors=errors,
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Connect to a box where the daemon was installed by hand."""
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
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pair again, trusting the host key the box has now, and reinstall
        the daemon keeping the settings on the box. This also lets Home
        Assistant manage a box that was set up by hand."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = await installer.async_install(
                    user_input[CONF_HOST], user_input[CONF_PASSWORD]
                )
            except installer.InstallError as err:
                errors["base"] = err.reason
            else:
                return self.async_update_reload_and_abort(entry, data_updates=data)
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST, DEFAULT_HOST)): str,
                vol.Required(CONF_PASSWORD): PASSWORD,
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)


class BatoceraOptionsFlow(OptionsFlow):
    """Who may launch, pause and stop games, besides admins."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        users = [
            u
            for u in await self.hass.auth.async_get_users()
            if not u.system_generated and u.is_active and not u.is_admin
        ]
        ids = {u.id for u in users}
        current = [i for i in self.config_entry.options.get(CONF_CONTROL_USERS, []) if i in ids]
        schema = vol.Schema(
            {
                vol.Optional(CONF_CONTROL_USERS, default=current): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=u.id, label=u.name or u.id)
                            for u in sorted(users, key=lambda u: (u.name or "").casefold())
                        ],
                        multiple=True,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
