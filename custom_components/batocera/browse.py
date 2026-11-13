"""The box's catalog as a media browser tree.

Content ids:
    ""                    the root: favourites, recently played, the systems
    "fav", "fav:<s>"      favourite games, of all systems or of one
    "recent", "recent:<s>"  games played last, newest first
    "system:<s>"          a system: its sub-folders, then its games at the top
    "dir:<s>:<folder>"    a sub-folder of a system ("Fighter", "Extra/Demos";
                          everything after the second colon, colons included)
    "game:<s>:<id>"       one game: playable, never expanded
"""

from __future__ import annotations

from collections.abc import Callable
import unicodedata

from homeassistant.components.media_player import BrowseError, BrowseMedia, MediaClass, MediaType

from .box import Box, Game

RECENT = 50  # games in "recently played"
SEARCH_MAX = 200  # games a search answers with
DIRECTORY = "directory"  # media_content_type of the folders


def game_content_id(game: Game) -> str:
    return f"game:{game.system}:{game.id}"


def parse_game(box: Box, content_id: str) -> Game | None:
    """The catalog's game a content id names, or None."""
    kind, _, rest = content_id.partition(":")
    system, _, gid = rest.partition(":")
    game = box.games.get(gid) if kind == "game" else None
    return game if game and game.system == system else None


def fold(text: str) -> str:
    """Text as a search compares it: no accents, no case."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _folder(
    content_id: str,
    title: str,
    children: list[BrowseMedia] | None = None,
    thumbnail: str | None = None,
) -> BrowseMedia:
    return BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id=content_id,
        media_content_type=DIRECTORY,
        title=title,
        can_play=False,
        can_expand=True,
        children=children,
        children_media_class=MediaClass.GAME if children is not None else None,
        thumbnail=thumbnail,
    )


def _item(game: Game, image_url: Callable[[str], str]) -> BrowseMedia:
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


def _in_folder(
    system: str, title: str, folder: str, games: list[Game], image_url: Callable[[str], str]
) -> BrowseMedia:
    """A system's folder ("" for the top): the sub-folders right under it,
    then its own games."""
    prefix = f"{folder}/" if folder else ""
    subs = {
        g.folder[len(prefix):].split("/", 1)[0]
        for g in games
        if g.folder.startswith(prefix) and g.folder != folder
    }
    content_id = f"dir:{system}:{folder}" if folder else f"system:{system}"
    return _folder(
        content_id,
        title,
        [
            *(_folder(f"dir:{system}:{prefix}{sub}", sub) for sub in sorted(subs, key=str.casefold)),
            *(_item(g, image_url) for g in games if g.folder == folder),
        ],
    )


def search(
    box: Box, query: str, image_url: Callable[[str], str], system: str | None = None
) -> list[BrowseMedia]:
    """Games whose name holds the query, accents and case aside, of one
    system or all, by name: at most SEARCH_MAX."""
    wanted = fold(query.strip())
    if not wanted:
        return []
    found = sorted(
        (
            (key, g)
            for g in box.games.values()
            if (system is None or g.system == system) and wanted in (key := fold(g.name))
        ),
        key=lambda pair: (pair[0], pair[1].id),
    )
    return [_item(g, image_url) for _, g in found[:SEARCH_MAX]]


def browse(box: Box, content_id: str | None, image_url: Callable[[str], str]) -> BrowseMedia:
    """The folder content_id names; image_url(content id) gives a game's
    cover, or a system's logo, through Home Assistant's authenticated image
    proxy."""
    content_id = content_id or ""

    names = {s["name"]: str(s.get("fullname") or s["name"]) for s in box.systems}
    if content_id == "":
        root = _folder("", box.name, [])
        root.children = [
            _folder("fav", "Favourites"),
            _folder("recent", "Recently played"),
            *(
                _folder(
                    f"system:{name}",
                    title,
                    thumbnail=image_url(f"system:{name}") if name in box.logos else None,
                )
                for name, title in names.items()
            ),
        ]
        root.children_media_class = MediaClass.DIRECTORY
        return root

    kind, _, rest = content_id.partition(":")
    system, _, folder = rest.partition(":") if kind == "dir" else (rest, "", "")
    if system and system not in names:
        raise BrowseError(f"No system {system} on {box.name}")
    games = box.system_games(system) if system else list(box.games.values())
    if kind == "system" and system:
        return _in_folder(system, names[system], "", games, image_url)
    if kind == "dir" and system and folder:
        if not any(g.folder == folder or g.folder.startswith(f"{folder}/") for g in games):
            raise BrowseError(f"No folder {folder} in {system} on {box.name}")
        return _in_folder(system, folder.rsplit("/", 1)[-1], folder, games, image_url)
    if kind == "fav":
        title = "Favourites" + (f" · {names[system]}" if system else "")
        shown = sorted((g for g in games if g.favourite), key=lambda g: g.name.casefold())
    elif kind == "recent":
        title = "Recently played" + (f" · {names[system]}" if system else "")
        shown = sorted((g for g in games if g.lastplayed), key=lambda g: g.lastplayed, reverse=True)
        shown = shown[:RECENT]
    else:
        raise BrowseError(f"Nothing to browse at {content_id!r}")
    return _folder(content_id, title, [_item(g, image_url) for g in shown])


def search_scope(content_id: str | None) -> str | None:
    """The system a search from content_id covers, or None for all."""
    kind, _, rest = (content_id or "").partition(":")
    if kind in ("system", "dir", "fav", "recent"):
        return rest.partition(":")[0] or None
    return None
