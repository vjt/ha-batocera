"""The box's catalog as a media browser tree.

Content ids:
    ""                    the root: favourites, recently played, the systems
    "fav", "fav:<s>"      favourite games, of all systems or of one
    "recent", "recent:<s>"  games played last, newest first
    "system:<s>"          all games of a system
    "game:<s>:<id>"       one game: playable, never expanded
"""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.media_player import BrowseError, BrowseMedia, MediaClass, MediaType

from .box import Box, Game

RECENT = 50  # games in "recently played"
DIRECTORY = "directory"  # media_content_type of the folders


def game_content_id(game: Game) -> str:
    return f"game:{game.system}:{game.id}"


def parse_game(box: Box, content_id: str) -> Game | None:
    """The catalog's game a content id names, or None."""
    kind, _, rest = content_id.partition(":")
    system, _, gid = rest.partition(":")
    game = box.games.get(gid) if kind == "game" else None
    return game if game and game.system == system else None


def _folder(content_id: str, title: str, children: list[BrowseMedia] | None = None) -> BrowseMedia:
    return BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id=content_id,
        media_content_type=DIRECTORY,
        title=title,
        can_play=False,
        can_expand=True,
        children=children,
        children_media_class=MediaClass.GAME if children is not None else None,
    )


def browse(box: Box, content_id: str | None, image_url: Callable[[str], str]) -> BrowseMedia:
    """The folder content_id names; image_url(content id) gives a game's
    cover through Home Assistant's authenticated image proxy."""
    content_id = content_id or ""

    def item(game: Game) -> BrowseMedia:
        cid = game_content_id(game)
        return BrowseMedia(
            media_class=MediaClass.GAME,
            media_content_id=cid,
            media_content_type=MediaType.GAME,
            title=game.name,
            can_play=True,
            can_expand=False,
            thumbnail=image_url(cid) if game.has_cover else None,
        )

    names = {s["name"]: str(s.get("fullname") or s["name"]) for s in box.systems}
    if content_id == "":
        root = _folder("", box.name, [])
        root.children = [
            _folder("fav", "Favourites"),
            _folder("recent", "Recently played"),
            *(_folder(f"system:{name}", title) for name, title in names.items()),
        ]
        root.children_media_class = MediaClass.DIRECTORY
        return root

    kind, _, system = content_id.partition(":")
    if system and system not in names:
        raise BrowseError(f"No system {system} on {box.name}")
    games = box.system_games(system) if system else list(box.games.values())
    if kind == "system" and system:
        title, shown = names[system], games
    elif kind == "fav":
        title = "Favourites" + (f" · {names[system]}" if system else "")
        shown = sorted((g for g in games if g.favourite), key=lambda g: g.name.casefold())
    elif kind == "recent":
        title = "Recently played" + (f" · {names[system]}" if system else "")
        shown = sorted((g for g in games if g.lastplayed), key=lambda g: g.lastplayed, reverse=True)
        shown = shown[:RECENT]
    else:
        raise BrowseError(f"Nothing to browse at {content_id!r}")
    return _folder(content_id, title, [item(g) for g in shown])
