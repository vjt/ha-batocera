"""Covers of the catalog's games, as the box sends them on request.

They are small JPEGs (256 px), kept in memory for the most recent ones
and on disk, in Home Assistant's cache folder, for a week. The box is
asked for at most a few at a time, and each one only once while it is
being fetched.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections import OrderedDict
import logging
from pathlib import Path
import shutil
import time

from homeassistant.core import HomeAssistant

from .box import Box, BoxError, Game

_LOGGER = logging.getLogger(__name__)

MEMORY = 300          # covers kept in memory
TTL = 7 * 24 * 3600   # seconds a cover stays on disk
CONCURRENT = 4        # covers asked of the box at once
TIMEOUT = 10          # seconds the box gets to send one


class CoverCache:
    def __init__(self, hass: HomeAssistant, box: Box, directory: Path) -> None:
        self.hass = hass
        self.box = box
        self.directory = directory
        self._memory: OrderedDict[str, bytes] = OrderedDict()
        self._fetching: dict[str, asyncio.Task[bytes | None]] = {}
        self._slots = asyncio.Semaphore(CONCURRENT)

    async def async_get(self, game: Game) -> bytes | None:
        """The game's cover as JPEG, or None if there is none or the box
        cannot send it now."""
        if not game.has_cover:
            return None
        if (jpeg := self._memory.get(game.id)) is not None:
            self._memory.move_to_end(game.id)
            return jpeg
        task = self._fetching.get(game.id)
        if task is None:
            task = self.hass.async_create_task(self._load(game), eager_start=False)
            self._fetching[game.id] = task
            task.add_done_callback(lambda _: self._fetching.pop(game.id, None))
        return await asyncio.shield(task)

    def _remember(self, gid: str, jpeg: bytes) -> None:
        self._memory[gid] = jpeg
        self._memory.move_to_end(gid)
        while len(self._memory) > MEMORY:
            self._memory.popitem(last=False)

    async def _load(self, game: Game) -> bytes | None:
        jpeg = await self.hass.async_add_executor_job(self._read, game.id)
        if jpeg is None:
            jpeg = await self._fetch(game)
            if jpeg is None:
                return None
            await self.hass.async_add_executor_job(self._write, game.id, jpeg)
        self._remember(game.id, jpeg)
        return jpeg

    async def _fetch(self, game: Game) -> bytes | None:
        if not self.box.online:
            return None
        async with self._slots:
            try:
                answer = await self.box.async_request(
                    "cover", timeout=TIMEOUT, system=game.system, game=game.id
                )
            except BoxError as err:
                _LOGGER.debug("%s: no cover for %s: %s", self.box.name, game.name, err)
                return None
        try:
            jpeg = base64.b64decode(str(answer.get("jpeg", "")), validate=True)
        except (binascii.Error, ValueError):
            jpeg = b""
        if not jpeg.startswith(b"\xff\xd8"):
            _LOGGER.debug("%s: the cover of %s is not a JPEG", self.box.name, game.name)
            return None
        return jpeg

    # --- disk, in the executor -------------------------------------------

    def _path(self, gid: str) -> Path:
        return self.directory / f"{gid}.jpg"

    def _read(self, gid: str) -> bytes | None:
        path = self._path(gid)
        try:
            if time.time() - path.stat().st_mtime > TTL:
                return None
            return path.read_bytes()
        except OSError:
            return None

    def _write(self, gid: str, jpeg: bytes) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = self._path(gid).with_suffix(".tmp")
            tmp.write_bytes(jpeg)
            tmp.replace(self._path(gid))
        except OSError as err:
            _LOGGER.warning("%s: cannot keep a cover in %s: %s", self.box.name, self.directory, err)

    def prune(self) -> None:
        """Delete the covers older than TTL. Blocking."""
        now = time.time()
        for path in self.directory.glob("*.jpg"):
            try:
                if now - path.stat().st_mtime > TTL:
                    path.unlink()
            except OSError:
                pass

    @staticmethod
    def remove(directory: Path) -> None:
        """Delete all covers of a box. Blocking."""
        shutil.rmtree(directory, ignore_errors=True)
