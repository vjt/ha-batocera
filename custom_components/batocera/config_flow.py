"""Config flow for the Batocera integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.hassio import AddonError, AddonManager
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
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

from . import installer, mqtt_setup
from .const import (
    CONF_CONTROL_USERS,
    CONF_MQTT_LOGIN,
    CONF_TOPIC,
    DEFAULT_HOST,
    DEFAULT_TOPIC,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
SECRETS = (CONF_PASSWORD, "mqtt_password")
ADVANCED = "advanced"
# What a broker account for the box needs, if the broker uses ACLs.
ACLS_URL = "https://github.com/vjt/ha-batocera/blob/main/docs/internals.md#mqtt-topics"

INSTALL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Batocera"): str,
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PASSWORD): PASSWORD,
    }
)
# The box's own broker settings, used as given when any is filled in. The
# box never gets Home Assistant's: they may name a host only Home Assistant
# can reach (core-mosquitto, localhost), with an account allowed far more
# than a box needs.
BROKER_SCHEMA = vol.Schema(
    {
        vol.Optional("mqtt_host"): str,
        vol.Optional("mqtt_port"): cv.port,
        vol.Optional("mqtt_user"): str,
        vol.Optional("mqtt_password"): PASSWORD,
    }
)


def _install_schema(collapsed: bool) -> vol.Schema:
    return INSTALL_SCHEMA.extend(
        {vol.Required(ADVANCED): section(BROKER_SCHEMA, {"collapsed": collapsed})}
    )


def _given_broker(fields: Mapping[str, Any]) -> installer.Broker | None:
    """The broker typed under Advanced; None when nothing is, and Home
    Assistant finds one."""
    if not any(fields.values()):
        return None
    if not (host := str(fields.get("mqtt_host", "")).strip()):
        raise installer.InstallError("mqtt_host_required", "no broker host under Advanced")
    return installer.Broker(
        host,
        fields.get("mqtt_port") or installer.MQTT_PORT,
        fields.get("mqtt_user", ""),
        fields.get("mqtt_password", ""),
    )


class BatoceraConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    # The Mosquitto add-on login added by this flow, (user, password): kept
    # for the next try, so that each try does not restart the add-on.
    _login: tuple[str, str] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BatoceraOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["install", "manual"])

    async def async_step_install(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Install the daemon on the box over SSH, with the root password, and
        give it broker settings: those typed under Advanced, as given, or else
        a broker found from the box, with a login of its own when Home
        Assistant uses the Mosquitto add-on. Then the form is
        install_mosquitto, which says so beforehand."""
        ha = mqtt_setup.ha_broker(self.hass)
        mosquitto = await mqtt_setup.async_mosquitto(self.hass, ha)
        errors: dict[str, str] = {}
        tried = ""  # the broker an MQTT error is about, as host:port
        suggested: dict[str, Any] = {}
        if user_input is not None:
            device = slugify(user_input[CONF_NAME]) or "batocera"
            topic = f"batocera/{device}"
            await self.async_set_unique_id(topic)
            self._abort_if_unique_id_configured()
            suggested = {k: v for k, v in user_input.items() if k not in SECRETS}
            suggested[ADVANCED] = {
                k: v for k, v in user_input[ADVANCED].items() if k not in SECRETS
            }
            try:
                data = await self._async_install(user_input, device, ha, mosquitto)
            except installer.InstallError as err:
                _LOGGER.warning("Cannot install on %s: %s", user_input[CONF_HOST], err)
                errors["base"] = err.reason
                if isinstance(err, installer.MqttError):
                    tried = str(err.broker)
                    if err.reason == "mqtt_auth" and not any(user_input[ADVANCED].values()):
                        # Found: a broker answered there, and wants an account.
                        suggested[ADVANCED] = {
                            "mqtt_host": err.broker.host, "mqtt_port": err.broker.port,
                        }
            except AddonError as err:
                _LOGGER.warning("Cannot add the box's MQTT user: %s", err)
                errors["base"] = "mqtt_addon_failed"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data={CONF_TOPIC: topic, **data}
                )
        elif ha is not None and ha.tls:
            errors["base"] = "mqtt_tls"
            tried = str(installer.Broker(ha.host, ha.port))
        return self.async_show_form(
            step_id="install_mosquitto" if mosquitto else "install",
            data_schema=self.add_suggested_values_to_schema(
                # Open on the broker fields when they are the way out.
                _install_schema(collapsed=not errors.get("base", "").startswith("mqtt_")),
                suggested,
            ),
            errors=errors,
            description_placeholders={"broker": tried, "acls": ACLS_URL},
        )

    # The same step, its form saying that the add-on gets a user and restarts.
    async_step_install_mosquitto = async_step_install

    async def _async_install(
        self,
        user_input: dict[str, Any],
        device: str,
        ha: mqtt_setup.HaBroker | None,
        mosquitto: AddonManager | None,
    ) -> dict[str, Any]:
        """Pair, settle the broker, install, and check from the box that the
        daemon connects. Returns the config entry data."""
        topic = f"batocera/{device}"
        broker = _given_broker(user_input[ADVANCED])
        if broker is None and ha is not None and ha.tls:
            raise installer.MqttError("mqtt_tls", installer.Broker(ha.host, ha.port))
        login = None
        async with installer.async_pair(user_input[CONF_HOST], user_input[CONF_PASSWORD]) as box:
            if broker is None:
                user = password = ""
                if mosquitto is not None:
                    user, password = await self._async_login(mosquitto, device)
                    login = user
                found = mqtt_setup.candidates(ha, await box.async_ha_address())
                broker = await box.async_probe(
                    [installer.Broker(host, port, user, password) for host, port in found], topic
                )
            data: dict[str, Any] = await box.async_install(broker.settings(device))
        await installer.async_verify(data, broker, topic)
        if login is not None:
            data[CONF_MQTT_LOGIN] = login
        return data

    async def _async_login(self, manager: AddonManager, device: str) -> tuple[str, str]:
        """The box's login on the Mosquitto add-on, added unless an earlier
        try of this flow did."""
        username = mqtt_setup.login_name(device)
        if self._login is None or self._login[0] != username:
            password = await mqtt_setup.async_add_login(self.hass, manager, username)
            self._login = (username, password)
        return self._login

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
