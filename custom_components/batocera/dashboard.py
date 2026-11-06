"""Sidebar dashboard: one full-page Batocera card per box.

Same approach as Peppina's and Filippa's: register a storage-mode Lovelace
panel and rewrite its config on every load from what the entity registry
holds. It rides on Lovelace internals (LovelaceStorage), so a failure is
logged and swallowed: the entities keep working without the page.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

URL = "batocera"
TITLE = "Batocera"
ICON = "mdi:space-invaders"
WWW = Path(__file__).parent / "www"
CARD_URL = "/batocera/batocera-card.js"
CARD_FILE = WWW / "batocera-card.js"
FONTS_URL = "/batocera/fonts"  # the card loads its pixel font from here
CARD_TYPE = "custom:batocera-card"

# Card keys written here; anything else on the card is the user's.
OWN_KEYS = ("type", "name", "entry_id", "entities")
# Card role -> (entity domain, key of the unique id after the entry id).
ROLES = {
    "player": ("media_player", "media_player"),
    "locked": ("binary_sensor", "locked"),
    "playtime": ("sensor", "playtime_today"),
    "started": ("sensor", "started_at"),
    "limit": ("number", "daily_limit"),
    "manual": ("switch", "manual_lock"),
}


async def async_setup_dashboard(hass: HomeAssistant) -> None:
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(CARD_URL, str(CARD_FILE), cache_headers=False),
            StaticPathConfig(FONTS_URL, str(WWW / "fonts"), cache_headers=True),
        ]
    )
    # mtime busts the browser cache when the card changes
    frontend.add_extra_js_url(hass, f"{CARD_URL}?v={int(CARD_FILE.stat().st_mtime)}")
    frontend.async_register_built_in_panel(
        hass,
        component_name="lovelace",
        sidebar_title=TITLE,
        sidebar_icon=ICON,
        frontend_url_path=URL,
        config={"mode": "storage"},
        require_admin=False,
        update=True,
    )


async def async_save_dashboard(hass: HomeAssistant) -> None:
    """Rewrite the page from the boxes of every loaded entry."""
    try:
        await _save(hass, list(hass.data.get(DOMAIN, {}).values()))
    except (AttributeError, KeyError, TypeError, ImportError, OSError):
        _LOGGER.exception("Dashboard setup failed, Lovelace internals may have changed")


def card_entities(hass: HomeAssistant, entry_id: str) -> dict[str, str]:
    registry = er.async_get(hass)
    entities = {}
    for role, (domain, key) in ROLES.items():
        if entity_id := registry.async_get_entity_id(domain, DOMAIN, f"{entry_id}_{key}"):
            entities[role] = entity_id
    return entities


async def _added_by_hand(storage) -> dict[str, tuple[dict, dict]]:
    """What the user added to each card, by entry id: entities under roles
    of their own and card options, which a rewrite must not drop."""
    from homeassistant.components.lovelace.dashboard import ConfigNotFound

    try:
        config = await storage.async_load(False)
    except ConfigNotFound:
        return {}
    kept = {}
    for view in config.get("views", []):
        for card in view.get("cards", []):
            if card.get("type") == CARD_TYPE:
                extra = {k: v for k, v in card.get("entities", {}).items() if k not in ROLES}
                options = {k: v for k, v in card.items() if k not in OWN_KEYS}
                kept[card.get("entry_id")] = (extra, options)
    return kept


async def _save(hass: HomeAssistant, boxes: list) -> None:
    from homeassistant.components.lovelace.dashboard import LovelaceStorage

    lovelace = hass.data.get("lovelace")
    if lovelace is None:
        return
    storage = lovelace.dashboards.get(URL)
    if not isinstance(storage, LovelaceStorage):
        storage = LovelaceStorage(
            hass,
            {
                "id": URL,
                "url_path": URL,
                "title": TITLE,
                "icon": ICON,
                "show_in_sidebar": True,
                "require_admin": False,
            },
        )
        lovelace.dashboards[URL] = storage

    views = []
    kept = await _added_by_hand(storage)
    for box in sorted(boxes, key=lambda b: b.name.casefold()):
        extra, options = kept.get(box.entry_id, ({}, {}))
        views.append(
            {
                "title": box.name,
                "path": "default" if not views else box.entry_id.lower(),
                "icon": ICON,
                "type": "panel",
                "cards": [
                    {
                        **options,
                        "type": CARD_TYPE,
                        "name": box.name,
                        "entry_id": box.entry_id,
                        "entities": {**extra, **card_entities(hass, box.entry_id)},
                    }
                ],
            }
        )
    await storage.async_save({"views": views})
