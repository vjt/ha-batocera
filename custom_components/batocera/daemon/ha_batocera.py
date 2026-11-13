#!/usr/bin/env python3
"""ha-batocera daemon: publish what Batocera is playing to MQTT, obey stop/lock.

Standard library only. The broker is reached through the mosquitto_pub and
mosquitto_sub binaries that Batocera ships; their options (host, port,
credentials) are read from $XDG_CONFIG_HOME/mosquitto_pub and
$XDG_CONFIG_HOME/mosquitto_sub, so nothing secret appears on a command line.

Game events come from the launch hook, one tab-separated line per event on a
FIFO: event, launcher pid, system, emulator, core, rom path.
"""

import base64
import gzip
import hashlib
import json
import logging
import os
import re
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

HOME = Path(os.environ.get("HA_BATOCERA_HOME", "/userdata/system/ha-batocera"))
FIFO = Path(os.environ.get("HA_BATOCERA_FIFO", "/var/run/ha-batocera.fifo"))
ROMS = Path("/userdata/roms")
ES_SETTINGS = Path("/userdata/system/configs/emulationstation/es_settings.cfg")
# Batocera's hotkeygen (41 and later) keeps the hotkeys of the running
# emulator here; 43 writes JSON. Later releases may move it to D-Bus.
HOTKEYS = Path("/var/run/hotkeygen.context")

CHECK_EVERY = 15        # seconds between launcher pid checks
STOP_GRACE = 5          # seconds the launcher gets to exit after --emukill
SUB_RETRY = 30          # seconds before restarting a dead mosquitto_sub
COVER_SIZE = 512        # cover bounding box, pixels
COVER_MAX = 500 * 1024  # never publish a bigger cover
THUMB_SIZE = 256        # catalog covers, pixels
REQ_MAX = 4096          # bigger requests are dropped
REQ_ID = re.compile(r"^[0-9a-f]{8,32}$")
# EmulationStation's web API, by address: "localhost" may resolve to ::1,
# which it refuses. Only ever used on the box itself.
ES_URL = "http://127.0.0.1:1234"
ES_TIMEOUT = 10         # seconds for one call to EmulationStation
ES_MAX = 32 * 1024 * 1024  # never read a bigger answer
LOGO_MAX = 256 * 1024   # never publish a bigger system logo
LOGO_UNKNOWN = "?"  # a logo an earlier run may have left on the broker
CATALOG_EVERY = 30 * 60  # seconds between catalog reads while no game runs
CATALOG_RETRY = 30      # seconds before asking EmulationStation again
INDEX_WAIT = 3          # seconds the first catalog read waits for an earlier run's index
CATALOG_AFTER_GAME = 10  # seconds after a game, for its play count to be saved
LAUNCH_IDLE_WAIT = 10   # seconds for EmulationStation to be idle before a launch
LAUNCH_POLL = 1         # seconds between asks whether it is
LAUNCH_START_WAIT = 30  # seconds for a launched game to start
PAUSABLE_AFTER = 5      # seconds after a start: the launcher sets the hotkeys after the hook
SCREEN_EVERY = 15       # seconds between screenshots; 0 in the settings turns them off
SCREEN_WIDTH = 640      # screenshot width, pixels
LIVE_EVERY = 1          # seconds between frames while someone watches the live screen
LIVE_WIDTH = 1280       # live frame width, pixels
LIVE_LEASE = 60         # seconds of live screen a "watch live" buys; Home Assistant renews it
# Where Batocera's Wayland compositor listens, for grim. The service does not
# inherit the session's environment, so these are the defaults.
WAYLAND = {"WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": "/var/run"}

log = logging.getLogger("ha-batocera")


def read_version():
    try:
        return Path(__file__).with_name("VERSION").read_text().strip()
    except OSError:
        return ""


VERSION = read_version()


def read_conf(path):
    """Parse key=value lines, ignoring blanks and # comments."""
    conf = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return conf
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            conf[key.strip()] = value.strip()
    return conf


def game_id(path):
    """A game's id: the first 12 hex digits of the MD5 of its ROM path.

    The catalog names games by it, and game_id tells which one is running,
    so both must hash the path the same way.
    """
    path = os.path.normpath(path)
    return hashlib.md5(path.encode(), usedforsecurity=False).hexdigest()[:12]


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def game_info(rom, roms=ROMS):
    """Return (name, cover image path or None) for a ROM, from its gamelist.xml."""
    rom = os.path.normpath(rom)
    for folder in Path(rom).parents:
        gamelist = folder / "gamelist.xml"
        if gamelist.is_file():
            try:
                games = ET.parse(gamelist).getroot().iter("game")
            except (ET.ParseError, OSError) as err:
                log.warning("cannot read %s: %s", gamelist, err)
                games = ()
            for game in games:
                path = game.findtext("path")
                if path and os.path.normpath(folder / path) == rom:
                    image = game.findtext("thumbnail") or game.findtext("image")
                    name = game.findtext("name") or Path(rom).stem
                    return name, os.path.normpath(folder / image) if image else None
        if folder == roms:
            break
    return Path(rom).stem, None


def make_cover(image, run=subprocess.run, size=COVER_SIZE, nice=False):
    """Downscale a cover to a JPEG of at most size px a side, or None.

    nice: at the lowest priority, not to slow down a game being played.
    """
    scale = (f"scale='min({size},iw)':'min({size},ih)'"
             ":force_original_aspect_ratio=decrease")
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", image,
           "-vf", scale, "-frames:v", "1", "-q:v", "5", "-f", "mjpeg", "-"]
    try:
        out = run(["nice", "-n", "19", *cmd] if nice else cmd, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as err:
        log.warning("cover %s: %s", image, err)
        return None
    if out.returncode or not out.stdout or len(out.stdout) > COVER_MAX:
        log.warning("cover %s: no usable output (rc %s, %d bytes)",
                    image, out.returncode, len(out.stdout or b""))
        return None
    return out.stdout


class ScreenError(Exception):
    """No screenshot: no compositor to ask, or grim or ffmpeg failed."""


def grab_screen(run=subprocess.run, env=None, width=SCREEN_WIDTH):
    """A JPEG of what the TV shows, width px wide.

    grim asks the Wayland compositor (Batocera 40 and later) for an
    uncompressed PNG, ffmpeg scales it down. Both at the lowest priority, not
    to slow down a game being played.
    """
    env = {**WAYLAND, **(os.environ if env is None else env)}
    scale = f"scale='min({width},iw)':-2"
    steps = [
        ["nice", "-n", "19", "grim", "-t", "png", "-l", "0", "-"],
        ["nice", "-n", "19", "ffmpeg", "-nostdin", "-loglevel", "error",
         "-f", "png_pipe", "-i", "-", "-vf", scale, "-frames:v", "1",
         "-q:v", "5", "-f", "mjpeg", "-"],
    ]
    data = None  # grim reads nothing, ffmpeg reads grim's PNG
    for cmd in steps:
        try:
            out = run(cmd, input=data, env=env, capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as err:
            raise ScreenError(f"{cmd[3]}: {err}") from err
        if out.returncode or not out.stdout:
            err = (out.stderr or b"").decode(errors="replace").strip()
            raise ScreenError(f"{cmd[3]}: rc {out.returncode} {err}".strip())
        data = out.stdout
    if len(data) > COVER_MAX:
        raise ScreenError(f"{len(data)} bytes")
    return data


def pausable_from(text):
    """Whether hotkeygen's context maps a pause hotkey; None if unreadable.

    The context is read as JSON, {"name": ..., "keys": {action: key...}},
    or a bare {action: key...}. Not checked against a box yet.
    """
    try:
        context = json.loads(text)
    except ValueError:
        return None
    if not isinstance(context, dict):
        return None
    keys = context.get("keys", context)
    return "pause" in keys if isinstance(keys, dict) else None


def emulator_from(text):
    """The emulator hotkeygen's context is named after, "" if unreadable.

    It is the emulator's own name: "retroarch" for every libretro core.
    """
    try:
        name = json.loads(text).get("name")
    except (ValueError, AttributeError):
        return ""
    return name if isinstance(name, str) else ""


def on_off(value):
    """ON, OFF or empty (not known) for a retained flag."""
    return {True: "ON", False: "OFF"}.get(value, "")


def es_settings(path=ES_SETTINGS):
    """EmulationStation's settings, name -> value; {} if unreadable.

    Lines look like <bool name="PublicWebAccess" value="true" />.
    """
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return {}
    return dict(re.findall(r'<\w+\s+name="([^"]*)"\s+value="([^"]*)"', text))


# --- the catalog ------------------------------------------------------------
#
# What EmulationStation's /systems and /systems/<name>/games answer is read
# here only. Booleans come as the strings "true" and "false".

FAVOURITE, HAS_COVER = 1, 2     # row flags
COVER_KEYS = ("thumbnail", "image")  # media for the cover, the first there is
SYSTEM_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")  # usable as a topic level


class Rom(NamedTuple):
    """What the daemon keeps to itself about a game: Home Assistant only
    ever names a game by its id."""
    system: str
    path: str
    es_id: str
    media: "str | None"  # the media key of its cover


class SystemCatalog(NamedTuple):
    entry: dict         # for catalog/index
    data: bytes         # the rows, as compact JSON
    roms: dict          # id -> Rom


def truthy(value):
    return value is True or str(value).lower() == "true"


def es_year(value):
    """1991 from EmulationStation's "19910623T000000" or "1991"."""
    match = re.match(r"(\d{4})", str(value or ""))
    return int(match[1]) if match and int(match[1]) else None


def es_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def rom_folder(path, roms=ROMS):
    """The folder of a ROM under its system's ROM folder: "Fighter" for
    /userdata/roms/mame/Fighter/kinst.zip, "" for a ROM at the top or
    outside the ROM folders. Never more of the path than that."""
    parts = Path(os.path.normpath(path)).parts
    top = roms.parts
    if parts[:len(top)] != top or len(parts) < len(top) + 3:
        return ""
    return "/".join(parts[len(top) + 1:-1])


# What may come before <svg>: blanks, an XML declaration, comments, a DOCTYPE
# (with its own entities, as Illustrator writes it). Skipped one piece at a
# time: one pattern repeating them all backtracks for ages on what is no SVG.
SVG_PROLOGUE = re.compile(rb"\s+|<\?xml[^>]*>|<!--.*?-->|<!DOCTYPE[^>\[]*(?:\[[^\]]*\][^>]*)?>",
                          re.S | re.I)
SVG_TAG = re.compile(rb"<svg[\s/>]", re.I)


def image_type(data):
    """The media type of a logo's bytes: SVG, PNG or JPEG, else None."""
    head = data[:4096].removeprefix(b"\xef\xbb\xbf")
    pos = 0
    while found := SVG_PROLOGUE.match(head, pos):
        pos = found.end()
    if SVG_TAG.match(head, pos):
        return "image/svg+xml"
    head = head.lstrip()
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    return None


def catalog_system(system, games, kid_mode):
    """One system's catalog, or None when no game of it is shown."""
    name = system["name"]
    rows, roms = {}, {}
    for game in games:
        path = game.get("path")
        if not isinstance(path, str) or not path or truthy(game.get("hidden")):
            continue
        if kid_mode and not truthy(game.get("kidgame")):
            continue
        gid = game_id(path)
        media = next((key for key in COVER_KEYS if game.get(key)), None)
        flags = (FAVOURITE if truthy(game.get("favorite")) else 0) | (HAS_COVER if media else 0)
        rows[gid] = [gid, str(game.get("name") or Path(path).stem),
                     es_year(game.get("releasedate")), str(game.get("genre") or ""),
                     str(game.get("players") or ""), flags, es_int(game.get("playcount")),
                     str(game.get("lastplayed") or ""), rom_folder(path)]
        roms[gid] = Rom(name, os.path.normpath(path), str(game.get("id", "")), media)
    if not rows:
        return None
    ordered = sorted(rows.values(), key=lambda row: (row[1].casefold(), row[0]))
    data = json.dumps(ordered, separators=(",", ":"), ensure_ascii=False).encode()
    entry = {
        "name": name,
        "fullname": str(system.get("fullname") or name),
        "manufacturer": str(system.get("manufacturer") or ""),
        "year": es_year(system.get("releaseYear")),
        "count": len(ordered),
        "hash": hashlib.sha256(data).hexdigest()[:12],
    }
    return SystemCatalog(entry, data, roms)


def catalog_systems(systems):
    """The systems shown: visible game systems, no collections."""
    return [s for s in systems
            if isinstance(s, dict) and SYSTEM_NAME.match(str(s.get("name", "")))
            and truthy(s.get("visible")) and truthy(s.get("gamesystem"))
            and not truthy(s.get("collection"))]


def build_catalog(systems, games, kid_mode=False):
    """name -> SystemCatalog, from /systems and {name: /games} (systems
    whose games are missing are left out). In Kid mode, kid games only."""
    catalog = {}
    for system in catalog_systems(systems):
        if system["name"] in games:
            found = catalog_system(system, games[system["name"]], kid_mode)
            if found:
                catalog[system["name"]] = found
    return catalog


class EsError(Exception):
    """EmulationStation did not answer, or not as expected."""


class EsApi:
    """EmulationStation's web API, on the box's loopback address only.

    What the answers look like comes from EmulationStation's source
    (HttpApi.cpp), not from a box: keep their reading in this class.
    """

    def __init__(self, base=ES_URL, opener=None):
        self.base = base
        # No proxy from the environment: this is the box talking to itself.
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.bad_logos = set()  # systems whose logo was warned about

    def request(self, path, data=None):
        """(HTTP status, body); EsError if nothing answers."""
        req = urllib.request.Request(self.base + path, data=data,
                                     method="GET" if data is None else "POST")
        try:
            with self.opener.open(req, timeout=ES_TIMEOUT) as resp:
                return resp.status, resp.read(ES_MAX)
        except urllib.error.HTTPError as err:
            err.close()
            return err.code, b""
        except (OSError, ValueError) as err:  # URLError and timeouts are OSErrors
            raise EsError(f"{path}: {err}") from err

    def json(self, path):
        status, body = self.request(path)
        if status != 200:
            raise EsError(f"{path}: HTTP {status}")
        try:
            return json.loads(body)
        except ValueError as err:
            raise EsError(f"{path}: {err}") from err

    def systems(self):
        return self.json("/systems")

    def games(self, system):
        return self.json(f"/systems/{quote(system, safe='')}/games")

    def media(self, system, es_id, key):
        """A game's media file (key: thumbnail, image...), or None."""
        path = f"/systems/{quote(system, safe='')}/games/{quote(es_id, safe='')}/media/{key}"
        status, body = self.request(path)
        return body if status == 200 and body else None

    def logo(self, system):
        """The system's logo from the theme (SVG, PNG or JPEG, at most
        LOGO_MAX bytes), or None. A body that is not one is warned about
        once per system: a theme or an EmulationStation that changed."""
        status, body = self.request(f"/systems/{quote(system, safe='')}/logo")
        if status != 200 or not body:
            return None
        if len(body) <= LOGO_MAX and image_type(body):
            return body
        if system not in self.bad_logos:
            self.bad_logos.add(system)
            log.warning("logo of %s: %d bytes, not an image or too big", system, len(body))
        return None

    def running(self):
        """Whether a game runs: 200 with the game, 201 when there is none."""
        status, _ = self.request("/runningGame")
        if status not in (200, 201):
            raise EsError(f"/runningGame: HTTP {status}")
        return status == 200

    def launch(self, path):
        """Ask EmulationStation to launch a ROM, as if picked in its menus."""
        status, _ = self.request("/launch", path.encode())
        if not 200 <= status < 300:
            raise EsError(f"/launch: HTTP {status}")


def starttime(pid):
    """Kernel start time of a process, or None if it is gone.

    Comparing it guards against the pid being reused by another process.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return stat.rsplit(")", 1)[1].split()[19]  # field 22


def started_at(pid):
    """When a process started, in seconds since the epoch; None if it is gone."""
    ticks = starttime(pid)
    if ticks is None:
        return None
    for line in Path("/proc/stat").read_text().splitlines():
        if line.startswith("btime "):
            return int(line.split()[1]) + int(ticks) / os.sysconf("SC_CLK_TCK")
    return None


def launcher_args(pid):
    """(system, rom) from the command line of launcher pid, or None.

    Read from emulatorlauncher's command line, "python emulatorlauncher
    -system <system> -rom <rom> ...", not from a log that may be a launch old.
    The ROM is the path ES launched, even for a .squashfs game, which the
    launcher mounts and hands its scripts by the mount.
    """
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")
    except OSError:
        return None
    if not any(Path(arg).name == "emulatorlauncher" for arg in argv[:2]):
        return None
    opts = dict(zip(argv, argv[1:]))  # each argument -> the one after it
    if opts.get("-system") and opts.get("-rom"):
        return opts["-system"], opts["-rom"]
    return None


def running_launcher():
    """The launcher of a game that is running: (pid, system, rom), or None."""
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit() and (args := launcher_args(entry.name)):
            return int(entry.name), *args
    return None


def descendants(pid):
    """All descendants of pid, deepest last."""
    children = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                ppid = int(entry.joinpath("stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, IndexError, ValueError):
                continue
            children.setdefault(ppid, []).append(int(entry.name))
    found, queue = [], [pid]
    while queue:
        kids = children.get(queue.pop(0), [])
        found += kids
        queue += kids
    return found


class Daemon:
    def __init__(self, base, env, run=subprocess.run, now=time.time, es=None,
                 settings=es_settings, hotkeys=HOTKEYS, screen_every=SCREEN_EVERY):
        self.base = base
        self.env = env
        self.run = run
        self.now = now
        self.es = es or EsApi()
        self.settings = settings
        self.hotkeys = hotkeys
        self.screen_every = screen_every
        self.screen_sent = None  # hash of the screenshot on the broker
        self.screen_error = None  # the last screenshot failure, logged once
        self.live_until = 0.0  # live frames until then: someone watches
        self.live_sent = None  # hash of the last live frame sent
        self.game = None      # the session being played, or None
        self.locked = False   # retained by Home Assistant on <base>/lock
        self.stopping = False
        self.timers = {}      # name -> (when, function)
        self.catalog = {}     # system name -> SystemCatalog, as last read
        self.catalog_read = False  # whether all of it was read once
        self.roms = {}        # game id -> Rom, from the catalog
        self.published = {}   # system name -> hash, as on the broker
        self.index_sent = None  # the catalog/index payload on the broker
        self.logos_sent = {}  # system name -> hash of its logo on the broker
        self.launching = None   # the launch in progress: {rid, rom, until}
        self.public_web = None  # what public_web_access says on the broker

    # --- timers -----------------------------------------------------------

    def at(self, name, delay, function):
        """Run function in delay seconds, replacing the timer of that name."""
        self.timers[name] = (self.now() + delay, function)

    def run_timers(self):
        for name, timer in sorted(self.timers.items(), key=lambda item: item[1][0]):
            # A timer that ran may have replaced or cancelled this one.
            if timer[0] <= self.now() and self.timers.get(name) == timer:
                del self.timers[name]
                timer[1]()

    def timeout(self):
        """How long the main loop may wait for input."""
        first = min((when for when, _ in self.timers.values()), default=None)
        if first is None:
            return CHECK_EVERY
        return max(0.0, min(CHECK_EVERY, first - self.now()))

    # --- MQTT out ---------------------------------------------------------

    def pub(self, leaf, payload, retain=True):
        """Publish one message; an empty payload clears a retained topic."""
        cmd = ["mosquitto_pub", "-q", "1", "-t", f"{self.base}/{leaf}"]
        if retain:
            cmd.append("-r")
        data = payload.encode() if isinstance(payload, str) else payload
        cmd.append("-s" if data else "-n")  # -s: payload from stdin, -n: null
        try:
            out = self.run(cmd, input=data or None, env=self.env,
                           capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as err:
            log.warning("publish %s: %s", leaf, err)
            return
        if out.returncode:
            log.warning("publish %s: rc %d %s", leaf, out.returncode,
                        out.stderr.decode(errors="replace").strip())

    def publish_game(self):
        g = self.game or {}
        fields = [
            ("game", g.get("name", "")),
            ("game_id", g.get("id", "")),
            ("system", g.get("system", "")),
            ("emulator", g.get("emulator", "")),
            ("started_at", iso(g["started"]) if g else ""),
            ("cover", g.get("cover") or b""),
            ("pausable", on_off(g.get("pausable"))),
            ("paused", on_off(g.get("paused")) if g else ""),
        ]
        state = ("state", "playing" if g else "idle")
        # Details before "playing", "idle" before clearing them.
        for leaf, payload in [*fields, state] if g else [state, *fields]:
            self.pub(leaf, payload)

    def publish_catalog(self):
        """Publish the systems that changed, then the index, then clear the
        systems that are gone."""
        for name, system in self.catalog.items():
            if self.published.get(name) != system.entry["hash"]:
                self.pub(f"catalog/system/{name}", gzip.compress(system.data, mtime=0))
        index = json.dumps([s.entry for s in self.catalog.values()],
                           separators=(",", ":"), ensure_ascii=False)
        if index != self.index_sent:
            self.pub("catalog/index", index)
            self.index_sent = index
        for name in sorted(set(self.published) - set(self.catalog)):
            self.pub(f"catalog/system/{name}", "")
        self.published = {name: s.entry["hash"] for name, s in self.catalog.items()}

    def publish_logos(self):
        """Each system's logo, retained, when it changed; cleared for a
        system without one or gone from the catalog. What is on the broker
        stays when EmulationStation stops answering."""
        for name in self.catalog:
            try:
                data = self.es.logo(name)
            except EsError as err:
                log.warning("logos: %s", err)
                return
            digest = hashlib.sha256(data).hexdigest()[:12] if data else None
            if digest != self.logos_sent.get(name):
                self.pub(f"catalog/logo/{name}", data or "")
                if digest:
                    self.logos_sent[name] = digest
                else:
                    del self.logos_sent[name]
        for name in sorted(set(self.logos_sent) - set(self.catalog)):
            self.pub(f"catalog/logo/{name}", "")
            del self.logos_sent[name]

    def publish_all(self):
        self.pub("availability", "online")
        self.pub("version", VERSION)
        self.publish_game()

    def screen(self):
        """Publish a screenshot if the screen changed, every screen_every s.

        Menus and paused games stay still, so most of these publish nothing.
        A failure clears the screenshot, not to show a stale one."""
        self.at("screen", self.screen_every, self.screen)
        try:
            jpeg = grab_screen(self.run, self.env)
        except ScreenError as err:
            if str(err) != self.screen_error:
                log.warning("screenshot: %s", err)
                self.screen_error = str(err)
            if self.screen_sent is not None:
                self.pub("screen", "")
                self.screen_sent = None
            return
        if self.screen_error is not None:
            log.info("screenshots work again")
            self.screen_error = None
        digest = hashlib.sha256(jpeg).digest()
        if digest != self.screen_sent:
            self.pub("screen", jpeg)
            self.screen_sent = digest

    def watch(self, payload):
        """Home Assistant says someone watches the live screen ("live", again
        every so often) or nobody does any more ("off")."""
        if payload == "off":
            self.live_until = 0.0
            self.timers.pop("live", None)
            return
        if payload != "live":
            log.warning("unknown watch %r", payload)
            return
        if self.screen_every <= 0:  # screenshots are off in the settings
            return
        watched = self.now() < self.live_until
        self.live_until = self.now() + LIVE_LEASE
        if not watched:
            self.live_sent = None  # a new viewer gets the first frame
            self.at("live", 0, self.live)

    def live(self):
        """While watched, a big frame every LIVE_EVERY s when the screen
        changed. Not retained: it is for whoever watches now. Failures are
        screen()'s to report."""
        if self.now() >= self.live_until:
            return
        self.at("live", LIVE_EVERY, self.live)
        try:
            jpeg = grab_screen(self.run, self.env, LIVE_WIDTH)
        except ScreenError:
            return
        digest = hashlib.sha256(jpeg).digest()
        if digest != self.live_sent:
            self.pub("live", jpeg, retain=False)
            self.live_sent = digest

    # --- requests -----------------------------------------------------------

    def on_request(self, payload):
        """One request from Home Assistant on <base>/req."""
        if len(payload.encode()) > REQ_MAX:
            log.warning("request of %d bytes dropped", len(payload.encode()))
            return
        try:
            req = json.loads(payload)
        except ValueError:
            req = None
        rid = req.get("id") if isinstance(req, dict) else None
        if not isinstance(rid, str) or not REQ_ID.match(rid):
            log.warning("request without a valid id dropped: %.80r", payload)
            return
        handler = {
            "cover": self.req_cover,
            "launch": self.req_launch,
            "pause": self.req_pause,
            "refresh": self.req_refresh,
        }.get(req.get("op"))
        if handler is None:
            log.warning("unknown request %.80r", payload)
            self.respond(rid, "bad_request")
        else:
            handler(rid, req)

    def respond(self, rid, error=None, **fields):
        answer = {"id": rid, "ok": error is None, **fields}
        if error:
            answer["error"] = error
        self.pub(f"resp/{rid}", json.dumps(answer), retain=False)

    def find_rom(self, req):
        """The catalog's game a request names, or None. Only ids are taken:
        never a path from the broker."""
        rom = self.roms.get(req.get("game")) if isinstance(req.get("game"), str) else None
        if rom and req.get("system") not in (None, rom.system):
            return None
        return rom

    def req_cover(self, rid, req):
        rom = self.find_rom(req)
        if rom is None:
            return self.respond(rid, "unknown_game")
        if rom.media is None:
            return self.respond(rid, "no_cover")
        try:
            image = self.es.media(rom.system, rom.es_id, rom.media)
        except EsError as err:
            log.warning("cover of %s: %s", rom.path, err)
            return self.respond(rid, "es_unreachable")
        if not image:
            return self.respond(rid, "no_cover")
        with tempfile.NamedTemporaryFile(prefix="ha-batocera-") as src:
            src.write(image)
            src.flush()
            jpeg = make_cover(src.name, self.run, size=THUMB_SIZE, nice=True)
        if jpeg is None:
            return self.respond(rid, "failed")
        self.respond(rid, jpeg=base64.b64encode(jpeg).decode())

    def req_launch(self, rid, req):
        """Quit the running game, wait for EmulationStation to be idle, then
        have it launch the game, so that it and the hook see a normal launch."""
        rom = self.find_rom(req)
        if rom is None:
            return self.respond(rid, "unknown_game")
        if self.locked:
            log.info("locked: not launching %s", rom.path)
            return self.respond(rid, "locked")
        if self.launching:
            return self.respond(rid, "busy")
        log.info("launch of %s requested", rom.path)
        if self.game:
            self.stop()
        self.launching = {"rid": rid, "rom": rom, "until": self.now() + LAUNCH_IDLE_WAIT}
        self.launch_step()

    def launch_step(self):
        job = self.launching
        if self.locked:  # the lock came while waiting
            self.launching = None
            return self.respond(job["rid"], "locked")
        try:
            running = self.es.running()
        except EsError as err:
            log.warning("launch: %s", err)
            running = None
        if running is False:
            try:
                self.es.launch(job["rom"].path)
            except EsError as err:
                log.warning("launch of %s: %s", job["rom"].path, err)
                self.launching = None
                return self.respond(job["rid"], "es_unreachable")
            self.respond(job["rid"])
            job.update(rid=None, until=self.now() + LAUNCH_START_WAIT)
            self.at("launch", LAUNCH_START_WAIT, self.launch_timeout)
        elif self.now() >= job["until"]:
            log.warning("launch of %s: EmulationStation %s", job["rom"].path,
                        "is still busy" if running else "does not answer")
            self.launching = None
            self.respond(job["rid"], "busy" if running else "es_unreachable")
        else:
            self.at("launch", LAUNCH_POLL, self.launch_step)

    def launch_timeout(self):
        log.warning("%s did not start within %ds", self.launching["rom"].path, LAUNCH_START_WAIT)
        self.launching = None

    def req_pause(self, rid, req):
        """Pause or resume through the emulator's pause hotkey. It toggles and
        says nothing back: "paused" is what the daemon believes."""
        want = req.get("paused")
        if not isinstance(want, bool):
            return self.respond(rid, "bad_request")
        if not self.game:
            return self.respond(rid, "not_playing")
        if self.game["pausable"] is False:
            return self.respond(rid, "not_pausable")
        if self.game["paused"] != want:
            try:
                out = self.run(["hotkeygen", "--send", "pause"], capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError) as err:
                log.warning("pause: %s", err)
                return self.respond(rid, "failed")
            if out.returncode:
                log.warning("pause: hotkeygen rc %d %s", out.returncode,
                            out.stderr.decode(errors="replace").strip())
                return self.respond(rid, "failed")
            log.info("%s %s", "paused" if want else "resumed", self.game["name"])
            self.game["paused"] = want
            self.pub("paused", on_off(want))
        self.respond(rid, paused=self.game["paused"])

    def check_pausable(self):
        """Read whether the running emulator has a pause hotkey."""
        if not self.game:
            return
        try:
            pausable = pausable_from(self.hotkeys.read_text())
        except OSError:
            pausable = None
        self.game["pausable"] = pausable
        self.pub("pausable", on_off(pausable))

    def req_refresh(self, rid, req):
        log.info("catalog refresh requested")
        self.respond(rid, None if self.refresh_catalog() else "es_unreachable")

    # --- the catalog --------------------------------------------------------

    def refresh_catalog(self, only=None):
        """Read the catalog from EmulationStation, all of it or one system,
        and publish what changed. Returns whether EmulationStation answered."""
        settings = self.settings()
        self.publish_public_web(settings)
        kid_mode = settings.get("UIMode") == "Kid"
        if not self.catalog_read:
            only = None  # one system alone would make an index of one
        try:
            systems = self.es.systems()
        except EsError as err:
            log.warning("catalog: %s, trying again in %ds", err, CATALOG_RETRY)
            if only is None:
                self.at("catalog", CATALOG_RETRY, self.scheduled_catalog)
            return False
        names = [s["name"] for s in catalog_systems(systems)]
        games = {}
        for name in names:
            if only in (None, name):
                try:
                    games[name] = self.es.games(name)
                except EsError as err:
                    log.warning("catalog of %s: %s", name, err)
        fresh = build_catalog(systems, games, kid_mode)
        catalog = {}
        for name in names:  # a system that was not read keeps what it had
            found = fresh.get(name) if name in games else self.catalog.get(name)
            if found:
                catalog[name] = found
        self.catalog = catalog
        self.roms = {gid: rom for s in catalog.values() for gid, rom in s.roms.items()}
        log.info("catalog: %d games in %d systems", len(self.roms), len(catalog))
        self.publish_catalog()
        if only is None:  # logos come with the theme, not with a game
            self.publish_logos()
            self.catalog_read = True
            self.at("catalog", CATALOG_EVERY, self.scheduled_catalog)
        return True

    def publish_public_web(self, settings):
        """Whether EmulationStation's web API is open to the network: Home
        Assistant warns about it. Empty when the settings cannot be read."""
        value = on_off(settings.get("PublicWebAccess") == "true") if settings else ""
        if value != self.public_web:
            self.public_web = value
            self.pub("public_web_access", value)

    def scheduled_catalog(self):
        # Leave EmulationStation alone during a game, unless there is no
        # catalog yet (started during a game): without it, every cover and
        # launch request names an unknown game.
        if self.game and self.catalog:
            self.at("catalog", CATALOG_EVERY, self.scheduled_catalog)
        else:
            self.refresh_catalog()

    # --- events -----------------------------------------------------------

    def on_line(self, line):
        """One line from the launch hook."""
        parts = line.split("\t")
        try:
            event, pid = parts[0], int(parts[1])
        except (IndexError, ValueError):
            log.warning("bad hook line: %r", line)
            return
        if event == "gameStart" and len(parts) >= 6:
            # The hook gets a .squashfs game by its mount under /var/run/squashfs:
            # the launcher's -rom is the path ES and the catalog know.
            args = launcher_args(pid)
            self.start(pid, *parts[2:5], args[1] if args else parts[5])
        elif event == "gameStop":
            if self.game:
                self.end()
        else:
            log.warning("bad hook line: %r", line)

    def start(self, pid, system, emulator, core, rom, started=None):
        if self.game:
            log.info("%s never ended", self.game["name"])
            self.end()
        if self.launching and self.launching["rid"] is None:  # launched: done
            self.launching = None
            self.timers.pop("launch", None)
        name, image = game_info(rom)
        self.game = {
            "pid": pid,
            "starttime": starttime(pid),
            "name": name,
            "id": game_id(rom),
            "system": system,
            "emulator": emulator if core in ("", emulator) else f"{emulator}/{core}",
            "started": self.now() if started is None else started,
            "cover": make_cover(image, self.run) if image else None,
            "pausable": None,  # not known until the launcher sets the hotkeys
            "paused": False,
        }
        log.info("playing %s (%s)", name, system)
        self.publish_game()
        self.at("pausable", PAUSABLE_AFTER, self.check_pausable)
        if self.locked:
            # The hook cannot refuse a launch: its exit code is ignored.
            log.info("locked: stopping %s", name)
            self.stop()

    def adopt(self):
        """Report a game launched before the daemon started, as after an
        upgrade mid-game: its hook went to the previous daemon, or nowhere.

        The session counts from the launcher's start: Home Assistant counts a
        running session only while it is reported, and its end only from
        last_session, so the time before the restart was not counted yet.
        """
        found = running_launcher()
        if not found:
            return
        pid, system, rom = found
        try:
            emulator = emulator_from(self.hotkeys.read_text())
        except OSError:
            emulator = ""
        log.info("launcher %d was already running", pid)
        self.start(pid, system, emulator, "", rom, started=started_at(pid))

    def end(self):
        g, self.game = self.game, None
        ended = self.now()
        self.timers.pop("pausable", None)
        # EmulationStation saves the play count once the launcher returns.
        self.at("catalog_game", CATALOG_AFTER_GAME,
                lambda: self.refresh_catalog(only=g["system"]))
        log.info("ended %s after %ds", g["name"], ended - g["started"])
        # Before "idle": Home Assistant counts the session from this, and
        # stops counting the running one when it sees the same started_at.
        self.pub("last_session", json.dumps({
            "game": g["name"],
            "system": g["system"],
            "emulator": g["emulator"],
            "started_at": iso(g["started"]),
            "ended_at": iso(ended),
            "seconds": round(ended - g["started"]),
        }))
        self.publish_game()

    def launcher_alive(self):
        st = self.game["starttime"]
        return st is not None and starttime(self.game["pid"]) == st

    def stop(self):
        """Quit the running game the way the hotkey does, then force it."""
        if not self.game:
            return
        try:
            # Sends the emulator a clean exit (saves are written), waits 5 s,
            # then SIGKILLs the launcher's children.
            self.run(["batocera-es-swissknife", "--emukill"],
                     capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as err:
            log.warning("emukill: %s", err)
        deadline = self.now() + STOP_GRACE
        while self.launcher_alive() and self.now() < deadline:
            time.sleep(0.5)
        if self.launcher_alive():
            pid = self.game["pid"]
            log.warning("launcher %d still alive, killing its tree", pid)
            for victim in [*reversed(descendants(pid)), pid]:
                try:
                    os.kill(victim, signal.SIGKILL)
                except OSError:
                    pass

    def on_mqtt(self, line):
        """One "topic payload" line from mosquitto_sub -v."""
        topic, _, payload = line.partition(" ")
        leaf = topic.removeprefix(self.base + "/")
        if leaf == "cmd":
            if payload == "stop":
                log.info("stop requested")
                self.stop()
            else:
                log.warning("unknown command %r", payload)
        elif leaf == "lock":
            self.locked = payload.upper() == "ON"
            log.info("lock %s", "on" if self.locked else "off")
        elif leaf == "req":
            self.on_request(payload)
        elif leaf == "watch":
            self.watch(payload)
        elif leaf == "catalog/index":
            if self.index_sent is None and payload:  # left by an earlier run
                try:
                    self.published = {e["name"]: e["hash"] for e in json.loads(payload)}
                    # Its logos are not known: the first read publishes or
                    # clears each one, so none is left behind.
                    self.logos_sent = {name: LOGO_UNKNOWN for name in self.published}
                except (ValueError, TypeError, KeyError) as err:
                    log.warning("retained catalog index: %s", err)
                else:
                    self.index_sent = payload
                    self.at("catalog", 0, self.scheduled_catalog)  # no need to wait more
        elif leaf == "availability" and payload != "online" and not self.stopping:
            # Our will fired (connection dropped) or a stale "offline" was
            # retained: we are alive, say so again.
            log.info("availability was %r, republishing", payload)
            self.publish_all()

    def tick(self):
        if self.game and not self.launcher_alive():
            log.info("launcher %d is gone", self.game["pid"])
            self.end()

    # --- main loop --------------------------------------------------------

    def spawn_sub(self):
        avail = f"{self.base}/availability"
        return subprocess.Popen(
            ["mosquitto_sub", "-v", "-q", "1",
             "--will-topic", avail, "--will-payload", "offline",
             "--will-qos", "1", "--will-retain",
             "-t", f"{self.base}/cmd", "-t", f"{self.base}/lock", "-t", avail,
             "-t", f"{self.base}/req", "-t", f"{self.base}/watch",
             "-t", f"{self.base}/catalog/index"],
            stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, env=self.env)

    def boot(self):
        """What serve() does first. The catalog waits a moment: mosquitto_sub
        hands back the index an earlier run left, and with it what is on the
        broker, so the first read leaves nothing behind (see on_mqtt)."""
        self.adopt()  # first: never say "idle" over a running game
        self.publish_all()
        self.at("catalog", INDEX_WAIT, self.scheduled_catalog)
        if self.screen_every > 0:
            self.at("screen", 0, self.screen)

    def serve(self, fifo=FIFO):
        if fifo.exists() or fifo.is_symlink():
            fifo.unlink()
        os.mkfifo(fifo, 0o600)
        # Read-write: the open never sees EOF and writers never block on it.
        fifo_fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
        sel = selectors.DefaultSelector()
        sel.register(fifo_fd, selectors.EVENT_READ, self.on_line)
        bufs = {fifo_fd: b""}
        sub, sub_at = None, 0.0
        self.boot()
        try:
            while True:
                if sub is None and self.now() >= sub_at:
                    sub = self.spawn_sub()
                    fd = sub.stdout.fileno()
                    sel.register(fd, selectors.EVENT_READ, self.on_mqtt)
                    bufs[fd] = b""
                for key, _ in sel.select(self.timeout()):
                    data = os.read(key.fd, 65536)
                    if not data:  # only mosquitto_sub can hit EOF
                        sel.unregister(key.fd)
                        log.warning("mosquitto_sub exited (rc %s), retrying in %ds",
                                    sub.wait(), SUB_RETRY)
                        sub, sub_at = None, self.now() + SUB_RETRY
                        continue
                    *lines, bufs[key.fd] = (bufs[key.fd] + data).split(b"\n")
                    for line in lines:
                        key.data(line.decode(errors="replace"))
                self.run_timers()
                self.tick()
        finally:
            self.stopping = True
            if sub:
                sub.terminate()
                sub.wait()
            self.pub("screen", "")  # the box is going away: no stale screen
            self.pub("state", "off")
            self.pub("availability", "offline")
            fifo.unlink(missing_ok=True)


def main():
    logging.basicConfig(level=logging.INFO, filename=os.environ.get("HA_BATOCERA_LOG"),
                        format="%(asctime)s %(levelname)s %(message)s")
    conf = read_conf(HOME / "ha-batocera.conf")
    device = conf.get("device") or socket.gethostname().lower()
    base = f"{conf.get('topic_prefix', 'batocera')}/{device}"
    env = dict(os.environ, XDG_CONFIG_HOME=str(HOME))
    es = EsApi(os.environ.get("HA_BATOCERA_ES_URL", ES_URL))  # tests use another
    try:
        screen_every = float(conf.get("screen_every", SCREEN_EVERY))
    except ValueError:
        log.warning("screen_every=%r is not a number, using %d",
                    conf["screen_every"], SCREEN_EVERY)
        screen_every = SCREEN_EVERY
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    log.info("starting %s, topics under %s/", VERSION, base)
    try:
        Daemon(base, env, es=es, screen_every=screen_every).serve()
    except Exception:
        log.exception("crashed")
        raise


if __name__ == "__main__":
    main()
