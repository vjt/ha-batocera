#!/usr/bin/env python3
"""ha-batocera daemon: publish what Batocera is playing to MQTT, obey stop/lock.

Standard library only. The broker is reached through the mosquitto_pub and
mosquitto_sub binaries that Batocera ships; their options (host, port,
credentials) are read from $XDG_CONFIG_HOME/mosquitto_pub and
$XDG_CONFIG_HOME/mosquitto_sub, so nothing secret appears on a command line.

Game events come from the launch hook, one tab-separated line per event on a
FIFO: event, launcher pid, system, emulator, core, rom path.
"""

import json
import logging
import os
import selectors
import signal
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ.get("HA_BATOCERA_HOME", "/userdata/system/ha-batocera"))
FIFO = Path(os.environ.get("HA_BATOCERA_FIFO", "/var/run/ha-batocera.fifo"))
ROMS = Path("/userdata/roms")

CHECK_EVERY = 15        # seconds between launcher pid checks
STOP_GRACE = 5          # seconds the launcher gets to exit after --emukill
SUB_RETRY = 30          # seconds before restarting a dead mosquitto_sub
COVER_SIZE = 512        # cover bounding box, pixels
COVER_MAX = 500 * 1024  # never publish a bigger cover

log = logging.getLogger("ha-batocera")


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


def make_cover(image, run=subprocess.run):
    """Downscale a cover to a JPEG of at most COVER_SIZE px a side, or None."""
    scale = (f"scale='min({COVER_SIZE},iw)':'min({COVER_SIZE},ih)'"
             ":force_original_aspect_ratio=decrease")
    try:
        out = run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", image,
                   "-vf", scale, "-frames:v", "1", "-q:v", "5", "-f", "mjpeg", "-"],
                  capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as err:
        log.warning("cover %s: %s", image, err)
        return None
    if out.returncode or not out.stdout or len(out.stdout) > COVER_MAX:
        log.warning("cover %s: no usable output (rc %s, %d bytes)",
                    image, out.returncode, len(out.stdout or b""))
        return None
    return out.stdout


def starttime(pid):
    """Kernel start time of a process, or None if it is gone.

    Comparing it guards against the pid being reused by another process.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return stat.rsplit(")", 1)[1].split()[19]  # field 22


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
    def __init__(self, base, env, run=subprocess.run, now=time.time):
        self.base = base
        self.env = env
        self.run = run
        self.now = now
        self.game = None      # the session being played, or None
        self.locked = False   # retained by Home Assistant on <base>/lock
        self.stopping = False

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
            ("system", g.get("system", "")),
            ("emulator", g.get("emulator", "")),
            ("started_at", iso(g["started"]) if g else ""),
            ("cover", g.get("cover") or b""),
        ]
        state = ("state", "playing" if g else "idle")
        # Details before "playing", "idle" before clearing them.
        for leaf, payload in [*fields, state] if g else [state, *fields]:
            self.pub(leaf, payload)

    def publish_all(self):
        self.pub("availability", "online")
        self.publish_game()

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
            self.start(pid, *parts[2:6])
        elif event == "gameStop":
            if self.game:
                self.end()
        else:
            log.warning("bad hook line: %r", line)

    def start(self, pid, system, emulator, core, rom):
        if self.game:
            log.info("%s never ended", self.game["name"])
            self.end()
        name, image = game_info(rom)
        self.game = {
            "pid": pid,
            "starttime": starttime(pid),
            "name": name,
            "system": system,
            "emulator": emulator if core in ("", emulator) else f"{emulator}/{core}",
            "started": self.now(),
            "cover": make_cover(image, self.run) if image else None,
        }
        log.info("playing %s (%s)", name, system)
        self.publish_game()
        if self.locked:
            # The hook cannot refuse a launch: its exit code is ignored.
            log.info("locked: stopping %s", name)
            self.stop()

    def end(self):
        g, self.game = self.game, None
        ended = self.now()
        log.info("ended %s after %ds", g["name"], ended - g["started"])
        self.publish_game()
        self.pub("last_session", json.dumps({
            "game": g["name"],
            "system": g["system"],
            "emulator": g["emulator"],
            "started_at": iso(g["started"]),
            "ended_at": iso(ended),
            "seconds": round(ended - g["started"]),
        }))

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
             "-t", f"{self.base}/cmd", "-t", f"{self.base}/lock", "-t", avail],
            stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, env=self.env)

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
        self.publish_all()
        try:
            while True:
                if sub is None and self.now() >= sub_at:
                    sub = self.spawn_sub()
                    fd = sub.stdout.fileno()
                    sel.register(fd, selectors.EVENT_READ, self.on_mqtt)
                    bufs[fd] = b""
                for key, _ in sel.select(CHECK_EVERY):
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
                self.tick()
        finally:
            self.stopping = True
            if sub:
                sub.terminate()
                sub.wait()
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
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    log.info("starting, topics under %s/", base)
    try:
        Daemon(base, env).serve()
    except Exception:
        log.exception("crashed")
        raise


if __name__ == "__main__":
    main()
