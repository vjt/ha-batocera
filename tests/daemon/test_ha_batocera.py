"""Tests for the Batocera-side daemon and hook. Standard library only:

    python3 -m unittest discover -s tests/daemon -v
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


# EmulationStation's answers, made up from its source: see the file's _note.
SYNTHETIC = json.loads(Path(__file__).with_name("es_synthetic.json").read_text())


class FakeEs:
    """EmulationStation's web API on a loopback port, answering from
    es_synthetic.json."""

    def __init__(self):
        self.running = None   # the running game's JSON, or None
        self.launched = []    # bodies of POST /launch
        self.media = {}       # path -> bytes
        self.requests = []    # (method, path)
        self.raw = []         # paths as sent, quoted
        self.systems = SYNTHETIC["systems"]
        self.games = SYNTHETIC["games"]
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fake.handle(self, None)

            def do_POST(self):
                fake.handle(self, self.rfile.read(int(self.headers.get("Content-Length", 0))))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def handle(self, handler, body):
        path = urllib.parse.unquote(handler.path)
        self.requests.append((handler.command, path))
        self.raw.append(handler.path)
        status, out = self.answer(handler.command, path, body)
        handler.send_response(status)
        handler.send_header("Content-Length", str(len(out)))
        handler.end_headers()
        handler.wfile.write(out)

    def answer(self, method, path, body):
        if method == "POST":
            if path == "/launch":
                self.launched.append(body.decode())
                return 200, b""
            return 404, b""
        if path == "/systems":
            return 200, json.dumps(self.systems).encode()
        if path == "/runningGame":
            if self.running:
                return 200, json.dumps(self.running).encode()
            return 201, b'{"msg":"NO GAME RUNNING"}'
        match = re.fullmatch(r"/systems/(.+)/games", path)
        if match and match[1] in self.games:
            return 200, json.dumps(self.games[match[1]]).encode()
        if path in self.media:
            return 200, self.media[path]
        return 404, b""


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
    def test_daemon_knows_its_version(self):
        self.assertEqual(hb.VERSION, (DAEMON / "VERSION").read_text().strip())

    def test_matches_the_integration(self):
        # Home Assistant reinstalls the daemon when the box has another one.
        manifest = json.loads((DAEMON.parent / "manifest.json").read_text())
        self.assertEqual((DAEMON / "VERSION").read_text().strip(), manifest["version"])

    def test_get_script_fetches_this_version(self):
        tag = "v" + (DAEMON / "VERSION").read_text().strip()
        repo = DAEMON.parents[2]
        self.assertIn(f"\nTAG={tag}\n", (repo / "get.sh").read_text())
        self.assertIn(f"/ha-batocera/{tag}/get.sh", (repo / "README.md").read_text())


class GameIdTest(unittest.TestCase):
    def test_md5_of_the_path(self):
        # The same id names the game in the catalog and while it runs.
        self.assertEqual(hb.game_id("/userdata/roms/snes/Mario World.sfc"),
                         "636f37e368fb")
        self.assertEqual(hb.game_id("/userdata/roms/snes/./Mario World.sfc"),
                         hb.game_id("/userdata/roms/snes/Mario World.sfc"))


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


class CatalogTest(TempDirTest):
    def build(self, kid=False, games=None):
        return hb.build_catalog(SYNTHETIC["systems"], games or SYNTHETIC["games"], kid)

    def test_only_visible_game_systems(self):
        # Not collections, not the screenshot viewer, not hidden systems, and
        # never a name that would not make a plain MQTT topic.
        self.assertEqual(list(self.build()), ["megadrive", "snes"])

    def test_index_entry(self):
        entry = self.build()["megadrive"].entry
        self.assertEqual({k: v for k, v in entry.items() if k != "hash"}, {
            "name": "megadrive", "fullname": "Sega Mega Drive", "manufacturer": "Sega",
            "year": 1988, "count": 4})
        self.assertRegex(entry["hash"], r"^[0-9a-f]{12}$")

    def test_rows(self):
        rows = json.loads(self.build()["megadrive"].data)
        sonic = hb.game_id("/userdata/roms/megadrive/Sonic.zip")
        sor = hb.game_id("/userdata/roms/megadrive/Streets of Rage.zip")
        self.assertEqual(rows, [  # by name; the hidden game is left out
            [hb.game_id("/userdata/roms/megadrive/Bare.zip"), "Bare", None, "", "", 0, 0, ""],
            [hb.game_id("/userdata/roms/megadrive/Comix Zone.zip"), "Comix Zone", None, "", "", 0, 0, ""],
            [sonic, "Sonic the Hedgehog", 1991, "Platform", "1", 3, 12, "20261102T183000"],
            [sor, "Streets of Rage", 1991, "Beat'em up", "1-2", 2, 0, ""],
        ])

    def test_roms_stay_on_the_box(self):
        roms = self.build()["megadrive"].roms
        sonic = roms[hb.game_id("/userdata/roms/megadrive/Sonic.zip")]
        self.assertEqual(sonic, hb.Rom("megadrive", "/userdata/roms/megadrive/Sonic.zip", "a0", "thumbnail"))
        sor = roms[hb.game_id("/userdata/roms/megadrive/Streets of Rage.zip")]
        self.assertEqual(sor.media, "image")  # no thumbnail: the image
        self.assertIsNone(roms[hb.game_id("/userdata/roms/megadrive/Bare.zip")].media)

    def test_kid_mode(self):
        catalog = self.build(kid=True)
        self.assertEqual(list(catalog), ["megadrive"])  # snes has no kid game
        names = [r[1] for r in json.loads(catalog["megadrive"].data)]
        self.assertEqual(names, ["Comix Zone", "Sonic the Hedgehog"])

    def test_hash_follows_the_games(self):
        games = json.loads(json.dumps(SYNTHETIC["games"]))
        before = self.build(games=games)["snes"].entry["hash"]
        self.assertEqual(self.build(games=games)["snes"].entry["hash"], before)
        games["snes"][0]["playcount"] = "1"
        self.assertNotEqual(self.build(games=games)["snes"].entry["hash"], before)

    def test_missing_games_are_skipped(self):
        games = {"megadrive": SYNTHETIC["games"]["megadrive"]}  # snes not fetched
        self.assertEqual(list(self.build(games=games)), ["megadrive"])

    def test_es_settings(self):
        path = self.tmp / "es_settings.cfg"
        path.write_text('<?xml version="1.0"?>\n<config>\n'
                        '\t<bool name="PublicWebAccess" value="true" />\n'
                        '\t<string name="UIMode" value="Kid" />\n</config>\n')
        self.assertEqual(hb.es_settings(path), {"PublicWebAccess": "true", "UIMode": "Kid"})
        self.assertEqual(hb.es_settings(self.tmp / "nope"), {})


class EsApiTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeEs()
        self.addCleanup(self.fake.close)
        self.es = hb.EsApi(self.fake.url)

    def test_default_is_loopback_by_address(self):
        # "localhost" may resolve to ::1, which EmulationStation refuses.
        self.assertEqual(hb.EsApi().base, "http://127.0.0.1:1234")

    def test_systems_and_games(self):
        self.assertEqual([s["name"] for s in self.es.systems()][:2], ["megadrive", "snes"])
        self.assertEqual(self.es.games("snes")[0]["name"], "Super Mario World")
        with self.assertRaises(hb.EsError):
            self.es.games("nope")  # 404

    def test_running(self):
        self.assertIs(self.es.running(), False)  # 201, NO GAME RUNNING
        self.fake.running = SYNTHETIC["games"]["snes"][0]
        self.assertIs(self.es.running(), True)

    def test_launch_posts_the_path(self):
        self.es.launch("/userdata/roms/snes/Mario World.sfc")
        self.assertEqual(self.fake.launched, ["/userdata/roms/snes/Mario World.sfc"])

    def test_media(self):
        self.fake.media["/systems/snes/games/b0/media/thumbnail"] = b"PNG"
        self.assertEqual(self.es.media("snes", "b0", "thumbnail"), b"PNG")
        self.assertIsNone(self.es.media("snes", "b0", "image"))
        self.assertEqual(self.fake.requests[-1], ("GET", "/systems/snes/games/b0/media/image"))

    def test_path_parts_are_quoted(self):
        self.assertIsNone(self.es.media("a b", "x/../y", "image"))
        self.assertEqual(self.fake.raw[-1], "/systems/a%20b/games/x%2F..%2Fy/media/image")

    def test_proxies_are_ignored(self):
        with mock.patch.dict(os.environ, {"http_proxy": "http://proxy.invalid:9", "no_proxy": ""}):
            self.assertEqual(len(hb.EsApi(self.fake.url).systems()), len(SYNTHETIC["systems"]))

    def test_down(self):
        self.fake.close()
        with self.assertRaises(hb.EsError):
            self.es.systems()
        with self.assertRaises(hb.EsError):
            self.es.running()


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
                ("batocera/test/game_id", hb.game_id(self.rom)),
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
                         ["batocera/test/game", "batocera/test/game_id", "batocera/test/system",
                          "batocera/test/emulator", "batocera/test/started_at",
                          "batocera/test/cover"])
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
        published = self.run.published()
        topics = [t for t, _ in published]
        self.assertEqual(topics[0], "batocera/test/availability")
        self.assertIn(("batocera/test/version", hb.VERSION), published)
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
