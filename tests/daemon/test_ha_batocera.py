"""Tests for the Batocera-side daemon and hook. Standard library only:

    python3 -m unittest discover -s tests/daemon -v
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

DAEMON = Path(__file__).resolve().parents[2] / "custom_components/batocera/daemon"
sys.path.insert(0, str(DAEMON))

import ha_batocera as hb  # noqa: E402

GAMELIST = """<?xml version="1.0"?>
<gameList>
  <game><path>./Sonic.zip</path><name>Sonic &amp; Knuckles</name>
    <image>./images/sonic-image.png</image>
    <thumbnail>./images/sonic-thumb.png</thumbnail></game>
  <game><path>./Multi/Disc.m3u</path><name>Multi Disc</name>
    <image>./images/multi.png</image></game>
  <game><path>./Bare.zip</path><name>Bare</name></game>
</gameList>
"""


class Recorder:
    """Stands in for subprocess.run and records every command."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, input=None, **kwargs):
        self.calls.append((cmd, input))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    def published(self):
        out = []
        for cmd, data in self.calls:
            if cmd[0] == "mosquitto_pub":
                topic = cmd[cmd.index("-t") + 1]
                out.append((topic, data.decode() if data and "/cover" not in topic else data))
        return out


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class TempDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)


class VersionTest(unittest.TestCase):
    def test_matches_the_integration(self):
        # Home Assistant reinstalls the daemon when the box has another one.
        manifest = json.loads((DAEMON.parent / "manifest.json").read_text())
        self.assertEqual((DAEMON / "VERSION").read_text().strip(), manifest["version"])


class ConfTest(TempDirTest):
    def test_read_conf(self):
        path = self.tmp / "conf"
        path.write_text("# comment\n\ndevice = arcade\ntopic_prefix=games\nnoise\n")
        self.assertEqual(hb.read_conf(path), {"device": "arcade", "topic_prefix": "games"})

    def test_missing_conf(self):
        self.assertEqual(hb.read_conf(self.tmp / "nope"), {})


class GameInfoTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.system = self.tmp / "roms" / "megadrive"
        (self.system / "Multi").mkdir(parents=True)
        (self.system / "gamelist.xml").write_text(GAMELIST)

    def info(self, rom):
        return hb.game_info(str(self.system / rom), roms=self.tmp / "roms")

    def test_thumbnail_preferred(self):
        self.assertEqual(self.info("Sonic.zip"),
                         ("Sonic & Knuckles", str(self.system / "images/sonic-thumb.png")))

    def test_image_fallback_and_subfolder(self):
        self.assertEqual(self.info("Multi/Disc.m3u"),
                         ("Multi Disc", str(self.system / "images/multi.png")))

    def test_no_image(self):
        self.assertEqual(self.info("Bare.zip"), ("Bare", None))

    def test_unknown_rom_uses_file_name(self):
        self.assertEqual(self.info("Streets of Rage.zip"), ("Streets of Rage", None))

    def test_broken_gamelist(self):
        (self.system / "gamelist.xml").write_text("<gameList><game>")
        with self.assertLogs(hb.log, "WARNING"):
            self.assertEqual(self.info("Sonic.zip"), ("Sonic", None))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class CoverTest(TempDirTest):
    def test_downscales_to_jpeg(self):
        src = self.tmp / "big.png"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=1200x900", "-frames:v", "1", str(src)], check=True)
        jpeg = hb.make_cover(str(src))
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))
        self.assertLessEqual(len(jpeg), hb.COVER_MAX)
        size = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                               "-of", "csv=p=0", "-"], input=jpeg, capture_output=True, check=True)
        self.assertEqual(size.stdout.decode().strip(), "512,384")

    def test_small_image_not_upscaled(self):
        src = self.tmp / "small.png"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=200x100", "-frames:v", "1", str(src)], check=True)
        size = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                               "-of", "csv=p=0", "-"], input=hb.make_cover(str(src)),
                              capture_output=True, check=True)
        self.assertEqual(size.stdout.decode().strip(), "200,100")

    def test_missing_image(self):
        with self.assertLogs(hb.log, "WARNING"):
            self.assertIsNone(hb.make_cover(str(self.tmp / "nope.png")))


class DaemonTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.run = Recorder()
        self.clock = Clock()
        self.d = hb.Daemon("batocera/test", {"XDG_CONFIG_HOME": "/x"}, run=self.run, now=self.clock)
        system = self.tmp / "roms" / "megadrive"
        system.mkdir(parents=True)
        (system / "gamelist.xml").write_text(GAMELIST)
        self.rom = str(system / "Bare.zip")
        self.pid = os.getpid()  # a launcher that is certainly alive

    def start_line(self, rom=None):
        return "\t".join(["gameStart", str(self.pid), "megadrive", "libretro",
                          "genesisplusgx", rom or self.rom])

    def test_publish_uses_stdin_and_config_env(self):
        self.d.pub("game", "Sonic")
        cmd, data = self.run.calls[0]
        self.assertEqual(cmd, ["mosquitto_pub", "-q", "1", "-t", "batocera/test/game", "-r", "-s"])
        self.assertEqual(data, b"Sonic")
        self.d.pub("game", "")
        self.assertEqual(self.run.calls[1][0][-1], "-n")  # clears the retained topic

    def test_start_and_stop_events(self):
        with self.assertLogs(hb.log, "INFO"):
            self.d.on_line(self.start_line())
            self.assertEqual(self.run.published(), [
                ("batocera/test/game", "Bare"),
                ("batocera/test/system", "megadrive"),
                ("batocera/test/emulator", "libretro/genesisplusgx"),
                ("batocera/test/started_at", "2023-11-14T22:13:20+00:00"),
                ("batocera/test/cover", None),
                ("batocera/test/state", "playing"),
            ])
            self.run.calls.clear()
            self.clock.t += 125.4
            self.d.on_line(f"gameStop\t{self.pid}\tmegadrive\tlibretro\tgenesisplusgx\t{self.rom}")
        published = self.run.published()
        self.assertEqual(published[0][0], "batocera/test/last_session")
        self.assertEqual(published[1], ("batocera/test/state", "idle"))
        self.assertEqual([t for t, p in published[2:] if p is None],
                         ["batocera/test/game", "batocera/test/system", "batocera/test/emulator",
                          "batocera/test/started_at", "batocera/test/cover"])
        self.assertEqual(json.loads(published[0][1]), {
            "game": "Bare", "system": "megadrive", "emulator": "libretro/genesisplusgx",
            "started_at": "2023-11-14T22:13:20+00:00", "ended_at": "2023-11-14T22:15:25+00:00",
            "seconds": 125})
        self.assertIsNone(self.d.game)

    def test_emulator_without_core(self):
        with self.assertLogs(hb.log, "INFO"):
            self.d.on_line(self.start_line().replace("genesisplusgx", ""))
        self.assertIn(("batocera/test/emulator", "libretro"), self.run.published())

    def test_stray_stop_and_garbage(self):
        self.d.on_line(f"gameStop\t{self.pid}\tx\ty\tz\t/r")
        self.assertEqual(self.run.calls, [])
        with self.assertLogs(hb.log, "WARNING"):
            self.d.on_line("gameStart\tnot-a-pid")
            self.d.on_line("gameStart\t12")
        self.assertEqual(self.run.calls, [])

    def test_missed_stop_closes_previous_session(self):
        with self.assertLogs(hb.log, "INFO") as logs:
            self.d.on_line(self.start_line())
            self.d.on_line(self.start_line())
        self.assertIn("Bare never ended", "\n".join(logs.output))
        self.assertEqual(sum(t.endswith("/last_session") for t, _ in self.run.published()), 1)

    def test_lost_launcher_ends_session(self):
        child = subprocess.Popen(["true"])
        child.wait()  # reaped: its pid is gone
        self.pid = child.pid
        with self.assertLogs(hb.log, "INFO") as logs:
            self.d.on_line(self.start_line())
            self.d.tick()
        self.assertIn(f"launcher {child.pid} is gone", "\n".join(logs.output))
        self.assertIsNone(self.d.game)

    def test_live_launcher_keeps_session(self):
        with self.assertLogs(hb.log, "INFO"):
            self.d.on_line(self.start_line())
        self.d.tick()
        self.assertIsNotNone(self.d.game)

    def test_stop_command(self):
        with self.assertLogs(hb.log, "INFO"):
            self.d.on_mqtt("batocera/test/cmd stop")  # nothing running: no-op
            self.assertEqual(self.run.calls, [])
            self.d.on_line(self.start_line())
            self.run.calls.clear()
            with mock.patch.object(hb, "STOP_GRACE", 0):
                self.d.game["starttime"] = None  # pretend the launcher exited
                self.d.on_mqtt("batocera/test/cmd stop")
        self.assertEqual(self.run.calls, [(["batocera-es-swissknife", "--emukill"], None)])

    def test_unknown_command(self):
        with self.assertLogs(hb.log, "WARNING"):
            self.d.on_mqtt("batocera/test/cmd reboot")
        self.assertEqual(self.run.calls, [])

    def test_launch_while_locked_is_stopped(self):
        with self.assertLogs(hb.log, "INFO") as logs, mock.patch.object(hb.Daemon, "stop") as stop:
            self.d.on_mqtt("batocera/test/lock OFF")
            self.d.on_line(self.start_line())
            stop.assert_not_called()
            self.d.on_mqtt("batocera/test/lock ON")
            stop.assert_not_called()  # a running game is Home Assistant's call
            self.d.on_line(self.start_line())
            stop.assert_called_once()
        self.assertIn("locked: stopping Bare", "\n".join(logs.output))

    def test_stop_kills_the_tree_when_emukill_fails(self):
        launcher = subprocess.Popen(["sh", "-c", "sleep 60 & sleep 60; wait"])
        self.addCleanup(launcher.kill)
        time.sleep(0.2)
        child = hb.descendants(launcher.pid)
        self.assertEqual(len(child), 2)
        self.pid = launcher.pid
        self.d.now = time.time  # stop() waits in real time
        with self.assertLogs(hb.log, "INFO") as logs, mock.patch.object(hb, "STOP_GRACE", 0.5):
            self.d.on_line(self.start_line())
            self.d.stop()
        self.assertIn(f"launcher {launcher.pid} still alive", "\n".join(logs.output))
        self.assertEqual(launcher.wait(timeout=5), -signal.SIGKILL)
        time.sleep(0.2)  # let init reap the orphans
        for pid in child:
            stat = Path(f"/proc/{pid}/stat")
            self.assertTrue(not stat.exists() or stat.read_text().rsplit(")", 1)[1].split()[0] == "Z")

    def test_availability_self_heal(self):
        with self.assertLogs(hb.log, "INFO"):
            self.d.on_mqtt("batocera/test/availability offline")
        topics = [t for t, _ in self.run.published()]
        self.assertEqual(topics[0], "batocera/test/availability")
        self.assertIn("batocera/test/state", topics)
        self.run.calls.clear()
        self.d.on_mqtt("batocera/test/availability online")
        self.d.stopping = True
        self.d.on_mqtt("batocera/test/availability offline")
        self.assertEqual(self.run.calls, [])


FAKE_PUB = """#!{python}
import json, os, sys
data = sys.stdin.buffer.read() if "-s" in sys.argv else b""
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({{"args": sys.argv[1:], "data": data.decode(errors="replace"),
                        "xdg": os.environ.get("XDG_CONFIG_HOME")}}) + "\\n")
"""

FAKE_SUB = """#!/bin/sh
echo "$@" > "$FAKE_LOG.sub"
echo "batocera/e2e/availability offline"
exec sleep 600
"""


class EndToEndTest(TempDirTest):
    """The real daemon process and hook script, with fake mosquitto clients."""

    def setUp(self):
        super().setUp()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for name, body in (("mosquitto_pub", FAKE_PUB.format(python=sys.executable)),
                           ("mosquitto_sub", FAKE_SUB)):
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
        (self.tmp / "ha-batocera.conf").write_text("device=e2e\n")
        self.log = self.tmp / "pub.log"
        self.fifo = self.tmp / "events.fifo"
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                        HA_BATOCERA_HOME=str(self.tmp), HA_BATOCERA_FIFO=str(self.fifo),
                        HA_BATOCERA_LOG=str(self.tmp / "daemon.log"), FAKE_LOG=str(self.log))

    def published(self):
        if not self.log.exists():
            return []
        out = []
        for line in self.log.read_text().splitlines():
            rec = json.loads(line)
            out.append((rec["args"][rec["args"].index("-t") + 1], rec["data"], rec["xdg"]))
        return out

    def wait_for(self, what, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if what(self.published()):
                return
            time.sleep(0.1)
        self.fail(f"timed out; published: {self.published()}")

    def hook(self, *args):
        return subprocess.run([str(DAEMON / "hook"), *args], env=self.env, timeout=5)

    def test_hook_without_daemon_returns_at_once(self):
        t = time.time()
        self.assertEqual(self.hook("gameStart", "snes", "libretro", "snes9x", "/r.sfc").returncode, 0)
        os.mkfifo(self.fifo)  # a FIFO nobody reads: the hook must give up
        self.assertEqual(self.hook("gameStart", "snes", "libretro", "snes9x", "/r.sfc").returncode, 0)
        self.assertLess(time.time() - t, 3)

    def test_daemon_lifecycle(self):
        daemon = subprocess.Popen([sys.executable, str(DAEMON / "ha_batocera.py")],
                                  env=self.env)
        self.addCleanup(daemon.kill)
        # Startup, then the stale retained "offline" makes it republish.
        self.wait_for(lambda p: [t for t, d, _ in p].count("batocera/e2e/availability") >= 2)
        self.assertTrue(self.fifo.is_fifo())
        self.assertEqual(self.published()[0], ("batocera/e2e/availability", "online", str(self.tmp)))
        sub_args = (self.tmp / "pub.log.sub").read_text().split()
        self.assertIn("--will-retain", sub_args)
        self.assertIn("batocera/e2e/cmd", sub_args)

        self.hook("gameStart", "snes", "libretro", "snes9x", "/userdata/roms/snes/Mario World.sfc")
        self.wait_for(lambda p: ("batocera/e2e/state", "playing", str(self.tmp)) in p)
        self.assertIn(("batocera/e2e/game", "Mario World", str(self.tmp)), self.published())
        self.hook("gameStop", "snes", "libretro", "snes9x", "/userdata/roms/snes/Mario World.sfc")
        self.wait_for(lambda p: any(t == "batocera/e2e/last_session" for t, _, _ in p))

        daemon.send_signal(signal.SIGTERM)
        self.assertEqual(daemon.wait(timeout=10), 0)
        self.assertEqual(self.published()[-2:], [
            ("batocera/e2e/state", "off", str(self.tmp)),
            ("batocera/e2e/availability", "offline", str(self.tmp)),
        ])
        self.assertFalse(self.fifo.exists())


if __name__ == "__main__":
    unittest.main()
