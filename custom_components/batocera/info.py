"""What the box tells of a game beyond the catalog, as it sends it on
request: its description, developer and publisher, the seconds it was
played in all, and the bytes it takes on the box. The page's launch dialog
asks for it.

Kept as long as a cover (covers.TTL): in memory for the most recent games,
and on disk next to the covers. An answer serves only while the game's last
played, in the catalog, stays the same: a game played since has a new total.
One kept in an older format (FORMAT) is asked again; until the box answers,
it still serves.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import json
import logging
from pathlib import Path
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .box import Box, BoxError, Game
from .covers import MEMORY, TTL

_LOGGER = logging.getLogger(__name__)

TIMEOUT = 10  # seconds the box gets to answer
# Characters kept of each text; the box cuts them shorter already.
TEXTS = {"desc": 4096, "developer": 256, "publisher": 256}
# Counts, each a positive int: the seconds played, the bytes the game takes.
COUNTS = ("gametime", "size")
# How an answer is kept on disk. 2: since the box measures the game's size;
# one kept before has none and is asked again, once, whatever the box says.
FORMAT = 2


def checked(answer: Any) -> dict[str, Any]:
    """The fields of an answer that are what they should be; the rest is
    left out."""
    if not isinstance(answer, dict):
        return {}
    info: dict[str, Any] = {}
    for key, limit in TEXTS.items():
        if isinstance(text := answer.get(key), str) and text.strip():
            info[key] = text.strip()[:limit]
    for key in COUNTS:
        count = answer.get(key)
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            info[key] = count
    return info


class InfoCache:
    def __init__(self, hass: HomeAssistant, box: Box, directory: Path) -> None:
        self.hass = hass
        self.box = box
        self.directory = directory
        self._memory: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()
        self._fetching: dict[str, asyncio.Task[dict[str, Any] | None]] = {}

    async def async_get(self, game: Game) -> dict[str, Any] | None:
        """What the box tells of the game ({} when nothing), or None when it
        cannot say now: offline, busy, or a daemon too old for the request."""
        known = self._memory.get(game.id)
        if known is not None and known[0] == game.lastplayed:
            self._memory.move_to_end(game.id)
            return known[1]
        task = self._fetching.get(game.id)
        if task is None:
            task = self.hass.async_create_task(self._load(game), eager_start=False)
            self._fetching[game.id] = task
            task.add_done_callback(lambda _: self._fetching.pop(game.id, None))
        return await asyncio.shield(task)

    async def _load(self, game: Game) -> dict[str, Any] | None:
        kept = await self.hass.async_add_executor_job(self._read, game)
        if kept is not None and kept[0] == FORMAT:
            info = kept[1]
        else:
            info = await self._fetch(game)
            if info is None:  # meanwhile, what was kept in an older format
                return kept[1] if kept is not None else None
            await self.hass.async_add_executor_job(self._write, game, info)
        self._memory[game.id] = (game.lastplayed, info)
        self._memory.move_to_end(game.id)
        while len(self._memory) > MEMORY:
            self._memory.popitem(last=False)
        return info

    async def _fetch(self, game: Game) -> dict[str, Any] | None:
        if not self.box.online:
            return None
        try:
            answer = await self.box.async_request(
                "info", timeout=TIMEOUT, system=game.system, game=game.id
            )
        except BoxError as err:  # bad_request: a daemon before 0.8
            _LOGGER.debug("%s: nothing about %s: %s", self.box.name, game.name, err)
            return None
        return checked(answer)

    # --- disk, in the executor -------------------------------------------

    def _path(self, gid: str) -> Path:
        return self.directory / f"{gid}.json"

    def _read(self, game: Game) -> tuple[Any, dict[str, Any]] | None:
        """The format and the info kept for the game, if fresh and still the
        game's (the same last played)."""
        path = self._path(game.id)
        try:
            if time.time() - path.stat().st_mtime > TTL:
                return None
            kept = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(kept, dict) or not isinstance(kept.get("info"), dict):
            return None
        if kept.get("lastplayed") != game.lastplayed:
            return None
        return kept.get("format"), checked(kept["info"])

    def _write(self, game: Game, info: dict[str, Any]) -> None:
        path = self._path(game.id)
        # Not <id>.tmp: the cover of the same game may be written meanwhile.
        tmp = path.with_name(f"{game.id}.json.tmp")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            kept = {"format": FORMAT, "lastplayed": game.lastplayed, "info": info}
            tmp.write_text(json.dumps(kept), "utf-8")
            tmp.replace(path)
        except OSError as err:
            _LOGGER.warning(
                "%s: cannot keep a game's info in %s: %s", self.box.name, self.directory, err
            )

    def prune(self) -> None:
        """Delete what is older than TTL. Blocking."""
        now = time.time()
        for path in self.directory.glob("*.json"):
            try:
                if now - path.stat().st_mtime > TTL:
                    path.unlink()
            except OSError:
                pass
