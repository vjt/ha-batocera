"""Who may launch, pause and stop games: admins and the users in the options."""

import pytest

from homeassistant.core import Context
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import Unauthorized

from conftest import TOPIC
from test_entities import call, play, published


@pytest.fixture
async def kid(hass):
    """A plain user: not an admin, but allowed to control entities."""
    return await hass.auth.async_create_user("Kid", group_ids=["system-users"])


async def stop_as(hass, user, service=("button", "press", "button.arcade_stop_game")):
    domain, name, entity_id = service
    await hass.services.async_call(
        domain, name, {"entity_id": entity_id}, blocking=True,
        context=Context(user_id=user.id if user else None),
    )


@pytest.mark.parametrize(
    "service",
    [("button", "press", "button.arcade_stop_game"),
     ("media_player", "media_stop", "media_player.arcade")],
)
async def test_stop_is_for_admins_and_chosen_users(
    hass, mqtt_mock, setup, hass_admin_user, kid, service
):
    await play(hass)
    mqtt_mock.async_publish.reset_mock()
    with pytest.raises(Unauthorized):
        await stop_as(hass, kid, service)
    assert published(mqtt_mock) == []

    await stop_as(hass, hass_admin_user, service)
    await stop_as(hass, None, service)  # an automation
    assert published(mqtt_mock) == [(f"{TOPIC}/cmd", "stop", False)] * 2

    hass.config_entries.async_update_entry(
        setup, options={"control_users": [kid.id]}
    )
    await stop_as(hass, kid, service)
    assert len(published(mqtt_mock)) == 3


async def test_inactive_user_may_not(hass, mqtt_mock, setup, kid):
    await play(hass)
    hass.config_entries.async_update_entry(
        setup, options={"control_users": [kid.id]}
    )
    kid.is_active = False
    with pytest.raises(Unauthorized):
        await stop_as(hass, kid)


async def test_options_flow_lists_non_admin_users(
    hass, mqtt_mock, setup, hass_admin_user, kid
):
    await hass.auth.async_create_user("Gone", group_ids=["system-users"])
    gone = [u for u in await hass.auth.async_get_users() if u.name == "Gone"][0]
    await hass.auth.async_deactivate_user(gone)
    result = await hass.config_entries.options.async_init(setup.entry_id)
    assert result["type"] is FlowResultType.FORM
    [key] = result["data_schema"].schema
    options = result["data_schema"].schema[key].config["options"]
    assert options == [{"value": kid.id, "label": "Kid"}]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"control_users": [kid.id]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup.options == {"control_users": [kid.id]}
