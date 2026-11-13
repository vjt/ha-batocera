"""Tests for the Batocera-side daemon and hook. Standard library only:

    python3 -m unittest discover -s tests/daemon -v
"""

import base64
import gzip
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
                binary = any(t in topic for t in ("/cover", "/screen", "/catalog/system/",
                                                  "/catalog/logo/"))
                out.append((topic, data.decode() if data and not binary else data))
        return out


class FfmpegRecorder(Recorder):
    """Records like Recorder, but ffmpeg (nice or not) writes a fake JPEG."""

    def __call__(self, cmd, input=None, **kwargs):
        out = super().__call__(cmd, input, **kwargs)
        if "ffmpeg" in cmd[:4]:
            return subprocess.CompletedProcess(cmd, 0, b"\xff\xd8fake", b"")
        return out


class RealFfmpeg(Recorder):
    """Runs ffmpeg for real, records everything else."""

    def __call__(self, cmd, input=None, **kwargs):
        if "ffmpeg" in cmd[:4]:
            return subprocess.run(cmd, input=input, **kwargs)
        return super().__call__(cmd, input, **kwargs)


class ScreenRecorder(Recorder):
    """Records like Recorder, plus each command's environment. grim writes a
    fake PNG of self.frame, ffmpeg a fake JPEG of the PNG it reads; fail names
    the one that fails."""

    def __init__(self, frame=b"menu", fail=None):
        super().__init__()
        self.frame = frame
        self.fail = fail
        self.envs = []

    def __call__(self, cmd, input=None, **kwargs):
        out = super().__call__(cmd, input, **kwargs)
        self.envs.append(kwargs.get("env"))
        if self.fail in cmd[:4]:
            return subprocess.CompletedProcess(cmd, 1, b"", b"failed to create display")
        if "grim" in cmd[:4]:
            return subprocess.CompletedProcess(cmd, 0, b"\x89PNG" + self.frame, b"")
        if "ffmpeg" in cmd[:4]:
            return subprocess.CompletedProcess(cmd, 0, b"\xff\xd8" + input[4:], b"")
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


class ScreenTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def daemon(self, run):
        return hb.Daemon("batocera/test", {"XDG_CONFIG_HOME": "/x"}, run=run, now=self.clock)

    def screens(self, run):
        return [p for t, p in run.published() if t == "batocera/test/screen"]

    def test_grim_into_ffmpeg(self):
        run = ScreenRecorder()
        self.assertEqual(hb.grab_screen(run, {"XDG_CONFIG_HOME": "/x"}), b"\xff\xd8menu")
        (grim, _), (ffmpeg, png) = run.calls
        self.assertEqual(grim[:3], ["nice", "-n", "19"])
        self.assertEqual(grim[3], "grim")
        self.assertEqual(ffmpeg[3], "ffmpeg")
        self.assertEqual(png, b"\x89PNGmenu")
        # The compositor's defaults, and the rest of the environment.
        self.assertEqual(run.envs[0], {"WAYLAND_DISPLAY": "wayland-0",
                                       "XDG_RUNTIME_DIR": "/var/run", "XDG_CONFIG_HOME": "/x"})
        hb.grab_screen(run, {"WAYLAND_DISPLAY": "wayland-1"})
        self.assertEqual(run.envs[-1]["WAYLAND_DISPLAY"], "wayland-1")

    def test_grab_errors(self):
        for fail in ("grim", "ffmpeg"):
            with self.assertRaisesRegex(hb.ScreenError, f"^{fail}: rc 1 failed to create display"):
                hb.grab_screen(ScreenRecorder(fail=fail), {})

        def missing(cmd, **kwargs):
            raise FileNotFoundError(cmd[0])
        with self.assertRaisesRegex(hb.ScreenError, "^grim: "):
            hb.grab_screen(missing, {})
        with self.assertRaisesRegex(hb.ScreenError, "bytes"):
            hb.grab_screen(ScreenRecorder(frame=b"x" * hb.COVER_MAX), {})

    def test_publishes_only_changes(self):
        run = ScreenRecorder()
        d = self.daemon(run)
        d.screen()
        self.assertEqual(self.screens(run), [b"\xff\xd8menu"])
        self.assertEqual(d.timers["screen"][0], self.clock.t + hb.SCREEN_EVERY)
        d.screen()  # the same menu: nothing to send
        self.assertEqual(len(self.screens(run)), 1)
        run.frame = b"game"
        d.screen()
        self.assertEqual(self.screens(run), [b"\xff\xd8menu", b"\xff\xd8game"])
        self.assertIn("-r", run.calls[-1][0])  # retained

    def test_failure_clears_and_warns_once(self):
        run = ScreenRecorder()
        d = self.daemon(run)
        d.screen()
        run.fail = "grim"
        with self.assertLogs(hb.log, "INFO") as logs:
            d.screen()
            d.screen()
            run.fail = None
            d.screen()
        self.assertEqual(self.screens(run), [b"\xff\xd8menu", None, b"\xff\xd8menu"])
        self.assertEqual(logs.output, [
            "WARNING:ha-batocera:screenshot: grim: rc 1 failed to create display",
            "INFO:ha-batocera:screenshots work again",
        ])
        self.assertIn("screen", d.timers)  # it keeps trying

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
    def test_real_ffmpeg(self):
        tv = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                             "testsrc=size=1920x1080", "-frames:v", "1", "-f", "image2pipe",
                             "-c:v", "png", "-"], capture_output=True, check=True).stdout

        def grim_then_ffmpeg(cmd, input=None, **kwargs):
            if "grim" in cmd:
                return subprocess.CompletedProcess(cmd, 0, tv, b"")
            return subprocess.run(cmd, input=input, **kwargs)
        jpeg = hb.grab_screen(grim_then_ffmpeg)
        size = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                               "-of", "csv=p=0", "-"], input=jpeg, capture_output=True, check=True)
        self.assertEqual(size.stdout.decode().strip(), "640,360")


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
            [hb.game_id("/userdata/roms/megadrive/Bare.zip"), "Bare", None, "", "", 0, 0, "", ""],
            [hb.game_id("/userdata/roms/megadrive/Comix Zone.zip"), "Comix Zone", None, "", "", 0, 0, "", ""],
            [sonic, "Sonic the Hedgehog", 1991, "Platform", "1", 3, 12, "20261102T183000", ""],
            [sor, "Streets of Rage", 1991, "Beat'em up", "1-2", 2, 0, "", ""],
        ])

    def test_rom_folder(self):
        # Only the folder under the system's ROM folder ever leaves the box.
        self.assertEqual(hb.rom_folder("/userdata/roms/mame/Fighter/kinst.zip"), "Fighter")
        self.assertEqual(hb.rom_folder("/userdata/roms/amiga1200/Extra/Demos/x.lha"), "Extra/Demos")
        self.assertEqual(hb.rom_folder("/userdata/roms/snes/a.sfc"), "")
        self.assertEqual(hb.rom_folder("/userdata/roms/a.sfc"), "")
        self.assertEqual(hb.rom_folder("/media/usb/x.zip"), "")
        self.assertEqual(hb.rom_folder("/userdata/roms/mame/../../etc/x.zip"), "")
        self.assertEqual(hb.rom_folder("/userdata/roms/mame/Shooter - Vertical/1942.zip"),
                         "Shooter - Vertical")

    def test_catalog_rows_carry_the_folder(self):
        games = json.loads(json.dumps(SYNTHETIC["games"]))
        games["megadrive"].append({"path": "/userdata/roms/megadrive/Platform/Sonic 2.zip",
                                   "name": "Sonic 2", "id": "a9"})
        rows = {r[1]: r for r in json.loads(self.build(games=games)["megadrive"].data)}
        self.assertEqual(rows["Sonic 2"][8], "Platform")
        self.assertEqual(rows["Bare"][8], "")

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


class CatalogDaemonTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeEs()
        self.addCleanup(self.fake.close)
        self.fake.games = json.loads(json.dumps(SYNTHETIC["games"]))
        self.run = Recorder()
        self.clock = Clock()
        self.settings = {}
        self.d = hb.Daemon("batocera/test", {}, run=self.run, now=self.clock,
                           es=hb.EsApi(self.fake.url), settings=lambda: self.settings)

    def published(self):
        out = [(t, p) for t, p in self.run.published() if not t.endswith("/public_web_access")]
        self.run.calls.clear()
        return out

    def refresh(self, **kwargs):
        with self.assertLogs(hb.log, "INFO"):
            return self.d.refresh_catalog(**kwargs)

    def test_systems_then_index(self):
        self.assertTrue(self.refresh())
        published = self.published()
        self.assertEqual([t for t, _ in published], [
            "batocera/test/catalog/system/megadrive", "batocera/test/catalog/system/snes",
            "batocera/test/catalog/index"])
        rows = json.loads(gzip.decompress(published[1][1]))
        self.assertEqual(rows[0][1], "Super Mario World")
        index = json.loads(published[2][1])
        self.assertEqual([(e["name"], e["count"]) for e in index], [("megadrive", 4), ("snes", 1)])
        self.assertEqual(self.d.roms[rows[0][0]].path, "/userdata/roms/snes/Mario World.sfc")
        # Nothing changed, nothing published.
        self.refresh()
        self.assertEqual(self.published(), [])

    def test_only_what_changed(self):
        self.refresh()
        self.published()
        self.fake.games["snes"][0]["playcount"] = "1"
        self.refresh()
        self.assertEqual([t for t, _ in self.published()], [
            "batocera/test/catalog/system/snes", "batocera/test/catalog/index"])

    def test_gone_systems_are_cleared_after_the_index(self):
        self.refresh()
        self.published()
        self.fake.systems = [s for s in SYNTHETIC["systems"] if s["name"] != "snes"]
        self.refresh()
        self.assertEqual(self.published(), [
            ("batocera/test/catalog/index", json.dumps(
                [self.d.catalog["megadrive"].entry], separators=(",", ":"))),
            ("batocera/test/catalog/system/snes", None),
        ])
        self.assertNotIn(hb.game_id("/userdata/roms/snes/Mario World.sfc"), self.d.roms)

    def test_starts_from_the_retained_index(self):
        catalog = hb.build_catalog(SYNTHETIC["systems"], SYNTHETIC["games"])
        index = [catalog["megadrive"].entry, dict(catalog["snes"].entry, hash="0" * 12),
                 {"name": "gone", "hash": "1" * 12}]
        self.d.on_mqtt("batocera/test/catalog/index " + json.dumps(index))
        self.refresh()
        self.assertEqual([(t, p is None) for t, p in self.published()], [
            ("batocera/test/catalog/system/snes", False),
            ("batocera/test/catalog/index", False),
            ("batocera/test/catalog/system/gone", True),
            # No logos now: whatever the earlier run left is cleared.
            ("batocera/test/catalog/logo/megadrive", True),
            ("batocera/test/catalog/logo/snes", True),
            ("batocera/test/catalog/logo/gone", True),
        ])
        # Our own index, seen again, changes nothing.
        self.d.on_mqtt("batocera/test/catalog/index " + json.dumps(index))
        self.refresh()
        self.assertEqual(self.published(), [])

    def test_kid_mode(self):
        self.settings = {"UIMode": "Kid"}
        self.refresh()
        self.assertEqual(list(self.d.catalog), ["megadrive"])

    def test_public_web_access(self):
        def flags():
            out = [p for t, p in self.run.published() if t.endswith("/public_web_access")]
            self.run.calls.clear()
            return out

        self.settings = {"PublicWebAccess": "true", "UIMode": "Full"}
        self.refresh()
        self.assertEqual(flags(), ["ON"])
        self.refresh()
        self.assertEqual(flags(), [])  # only when it changes
        self.settings = {"UIMode": "Full"}  # ES's default: off
        self.refresh()
        self.assertEqual(flags(), ["OFF"])
        self.settings = {}  # unreadable: not known
        self.fake.close()  # and told even when EmulationStation is down
        with self.assertLogs(hb.log, "WARNING"):
            self.d.refresh_catalog()
        self.assertEqual(flags(), [None])

    def test_es_down_retries(self):
        self.fake.close()
        with self.assertLogs(hb.log, "WARNING"):
            self.assertFalse(self.d.refresh_catalog())
        self.assertEqual(self.published(), [])
        self.assertEqual(self.d.timers["catalog"][0], self.clock.t + hb.CATALOG_RETRY)

    def test_a_system_that_fails_keeps_what_it_had(self):
        self.refresh()
        self.published()
        del self.fake.games["snes"]  # 404
        with self.assertLogs(hb.log, "WARNING"):
            self.d.refresh_catalog()
        self.assertEqual(list(self.d.catalog), ["megadrive", "snes"])
        self.assertEqual(self.published(), [])

    def test_schedule(self):
        self.d.at("catalog", 0, self.d.scheduled_catalog)
        self.assertEqual(self.d.timeout(), 0)
        with self.assertLogs(hb.log, "INFO"):
            self.d.run_timers()
        self.assertEqual(self.d.timers["catalog"][0], self.clock.t + hb.CATALOG_EVERY)
        self.assertEqual(self.d.timeout(), hb.CHECK_EVERY)
        self.clock.t += hb.CATALOG_EVERY
        self.d.game = {"system": "snes"}  # during a game: later
        self.fake.requests.clear()
        self.d.run_timers()
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(self.d.timers["catalog"][0], self.clock.t + hb.CATALOG_EVERY)

    def test_first_catalog_is_read_during_a_game(self):
        # Started during a game (adopted): without a catalog every cover
        # and launch request is an unknown game until the game ends.
        self.d.game = {"system": "snes"}
        self.d.at("catalog", 0, self.d.scheduled_catalog)
        with self.assertLogs(hb.log, "INFO"):
            self.d.run_timers()
        self.assertEqual(list(self.d.catalog), ["megadrive", "snes"])
        self.assertTrue(self.d.roms)

    def test_cleared_retained_index_is_no_news(self):
        self.d.on_mqtt("batocera/test/catalog/index ")  # no warning
        self.assertIsNone(self.d.index_sent)

    def test_one_system_before_the_whole_catalog_reads_it_all(self):
        # EmulationStation was not up at start, a game ends: everything.
        with self.assertLogs(hb.log, "INFO"):
            self.d.refresh_catalog(only="snes")
        self.assertEqual(list(self.d.catalog), ["megadrive", "snes"])
        self.assertIn("catalog", self.d.timers)

    SVG = b'\xef\xbb\xbf <?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"/>'
    PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32

    def retained(self, leaf):
        """Payloads published, retained, on <base>/<leaf>."""
        topic = f"batocera/test/{leaf}"
        return [data for cmd, data in self.run.calls
                if cmd[0] == "mosquitto_pub" and cmd[cmd.index("-t") + 1] == topic and "-r" in cmd]

    def test_logos_published_once_and_cleared(self):
        self.fake.media["/systems/megadrive/logo"] = self.SVG
        self.fake.media["/systems/snes/logo"] = self.PNG
        self.refresh()
        self.assertEqual(self.retained("catalog/logo/megadrive"), [self.SVG])
        self.assertEqual(self.retained("catalog/logo/snes"), [self.PNG])
        self.assertEqual([t for t, _ in self.published()], [  # after the catalog
            "batocera/test/catalog/system/megadrive", "batocera/test/catalog/system/snes",
            "batocera/test/catalog/index",
            "batocera/test/catalog/logo/megadrive", "batocera/test/catalog/logo/snes"])
        self.refresh()  # the same logos: nothing
        self.assertEqual(self.published(), [])
        self.fake.media["/systems/snes/logo"] = self.PNG + b"x"  # a new theme
        self.refresh()
        self.assertEqual(self.published(), [("batocera/test/catalog/logo/snes", self.PNG + b"x")])
        del self.fake.media["/systems/snes/logo"]  # a theme without it
        self.refresh()
        self.assertEqual(self.published(), [("batocera/test/catalog/logo/snes", None)])
        self.fake.systems = [s for s in SYNTHETIC["systems"] if s["name"] != "megadrive"]
        self.refresh()  # a system that leaves the catalog loses its logo
        self.assertEqual(self.retained("catalog/logo/megadrive"), [None])
        self.assertEqual(self.published()[-1], ("batocera/test/catalog/logo/megadrive", None))

    def test_logos_only_on_a_full_read(self):
        self.refresh()
        self.fake.requests.clear()
        with self.assertLogs(hb.log, "INFO"):
            self.d.refresh_catalog(only="snes")
        self.assertNotIn(("GET", "/systems/snes/logo"), self.fake.requests)

    def test_bad_logo_is_skipped(self):
        # snes: 404, no logo and no warning. megadrive: an error page, then
        # an SVG too big: never published, one warning.
        self.fake.media["/systems/megadrive/logo"] = b"<html><body>oops</body></html>"
        with self.assertLogs(hb.log, "INFO") as logs:
            self.assertTrue(self.d.refresh_catalog())
        warnings = [r.getMessage() for r in logs.records if r.levelname == "WARNING"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("megadrive", warnings[0])
        self.fake.media["/systems/megadrive/logo"] = b"<svg>" + b" " * hb.LOGO_MAX
        with self.assertLogs(hb.log, "INFO") as logs:
            self.d.refresh_catalog()
        self.assertEqual([r for r in logs.records if r.levelname == "WARNING"], [])
        topics = [t for t, _ in self.published()]
        self.assertIn("batocera/test/catalog/index", topics)
        self.assertFalse([t for t in topics if "/catalog/logo/" in t])

    def test_es_down_while_reading_logos(self):
        with mock.patch.object(self.d.es, "logo", side_effect=hb.EsError("down")) as logo:
            with self.assertLogs(hb.log, "INFO") as logs:
                self.assertTrue(self.d.refresh_catalog())
        self.assertEqual(logo.call_count, 1)  # not once per system
        self.assertTrue([r for r in logs.records if r.levelname == "WARNING"])
        topics = [t for t, _ in self.published()]
        self.assertIn("batocera/test/catalog/index", topics)
        self.assertFalse([t for t in topics if "/catalog/logo/" in t])

    def test_logos_after_a_restart(self):
        # An earlier run left its index, and maybe logos, on the broker: the
        # first read publishes every logo again and clears the others, so
        # none of the old ones stays behind.
        catalog = hb.build_catalog(SYNTHETIC["systems"], SYNTHETIC["games"])
        index = [catalog["megadrive"].entry, catalog["snes"].entry,
                 {"name": "gone", "hash": "1" * 12}]
        self.d.on_mqtt("batocera/test/catalog/index " + json.dumps(index))
        self.fake.media["/systems/snes/logo"] = self.PNG
        self.refresh()
        self.assertEqual(self.retained("catalog/logo/snes"), [self.PNG])
        self.assertEqual(self.retained("catalog/logo/megadrive"), [None])  # lost its logo
        self.assertEqual(self.retained("catalog/logo/gone"), [None])  # left the catalog
        self.published()
        self.refresh()  # then only changes
        self.assertEqual(self.published(), [])

    def test_svg_after_a_comment_or_doctype(self):
        for head in (b"<!-- Generator: Adobe Illustrator 24 -->\n",
                     b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
                     b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n',
                     b'<?xml version="1.0"?>\n<!-- a -->\n<!DOCTYPE svg>\n'):
            self.assertEqual(hb.image_type(head + b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
                             "image/svg+xml")
        self.assertIsNone(hb.image_type(b"<?xml version='1.0'?><html><svg/></html>"))
        self.assertIsNone(hb.image_type(b"<!-- <svg/> -->"))
        # A DOCTYPE with its own entities, as Illustrator writes it.
        self.assertEqual(hb.image_type(b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "x.dtd" [\n'
                                       b'  <!ENTITY ns_svg "http://www.w3.org/2000/svg">\n]>\n<svg/>'),
                         "image/svg+xml")

    def test_image_type_is_quick_on_anything(self):
        for junk in (b"<!---->" + b" " * 28 + b"x", b"<!DOCTYPE html>" + b"\n" * 28 + b"<html>",
                     b"<!---->" + b" " * 4000 + b"x"):
            start = time.monotonic()
            self.assertIsNone(hb.image_type(junk))
            self.assertLess(time.monotonic() - start, 0.1)

    def test_first_read_waits_for_the_index_left_on_the_broker(self):
        # mosquitto_sub hands back an earlier run's index a moment after the
        # start: the first read waits for it, so no logo is left behind.
        catalog = hb.build_catalog(SYNTHETIC["systems"], SYNTHETIC["games"])
        index = [catalog["megadrive"].entry, {"name": "gone", "hash": "1" * 12}]
        self.d.screen_every = 0
        with mock.patch.object(hb, "running_launcher", return_value=None):
            self.d.boot()
        self.d.run_timers()
        self.assertFalse(self.retained("catalog/index"), "not read before the index came")
        self.d.on_mqtt("batocera/test/catalog/index " + json.dumps(index))
        with self.assertLogs(hb.log, "INFO"):
            self.d.run_timers()
        self.assertEqual(self.retained("catalog/logo/gone"), [None])  # left the catalog
        self.assertEqual(self.retained("catalog/logo/megadrive"), [None])  # has no logo

    def test_first_read_without_an_index_left(self):
        self.d.screen_every = 0
        with mock.patch.object(hb, "running_launcher", return_value=None):
            self.d.boot()
        self.d.run_timers()
        self.assertFalse(self.retained("catalog/index"))
        self.clock.t += hb.INDEX_WAIT
        with self.assertLogs(hb.log, "INFO"):
            self.d.run_timers()
        self.assertTrue(self.retained("catalog/index"))

    def test_after_a_game_its_system_is_read_again(self):
        self.refresh()
        self.d.game = {"name": "Super Mario World", "system": "snes", "emulator": "libretro",
                       "started": self.clock.t - 60, "id": "x"}
        with self.assertLogs(hb.log, "INFO"):
            self.d.end()
        self.clock.t += hb.CATALOG_AFTER_GAME
        self.fake.requests.clear()
        with self.assertLogs(hb.log, "INFO"):
            self.d.run_timers()
        self.assertEqual(self.fake.requests, [("GET", "/systems"), ("GET", "/systems/snes/games")])
        self.assertEqual(list(self.d.catalog), ["megadrive", "snes"])


class RequestTest(unittest.TestCase):
    SONIC = hb.game_id("/userdata/roms/megadrive/Sonic.zip")
    BARE = hb.game_id("/userdata/roms/megadrive/Bare.zip")

    def setUp(self):
        self.fake = FakeEs()
        self.addCleanup(self.fake.close)
        self.run = FfmpegRecorder()
        self.clock = Clock()
        self.d = hb.Daemon("batocera/test", {}, run=self.run, now=self.clock,
                           es=hb.EsApi(self.fake.url), settings=dict)
        with self.assertLogs(hb.log, "INFO"):
            self.d.refresh_catalog()
        self.run.calls.clear()
        self.fake.media["/systems/megadrive/games/a0/media/thumbnail"] = b"PNG"

    def request(self, **req):
        req.setdefault("id", "0123abcd")
        self.d.on_mqtt("batocera/test/req " + json.dumps(req))
        answers = [(t, json.loads(p)) for t, p in self.run.published() if "/resp/" in t]
        self.calls = list(self.run.calls)
        self.run.calls.clear()
        return answers

    def answer(self, **req):
        [(topic, answer)] = self.request(**req)
        self.assertEqual(topic, "batocera/test/resp/" + req.get("id", "0123abcd"))
        return answer

    def test_dropped(self):
        with self.assertLogs(hb.log, "WARNING"):
            for line in ('{"op": "refresh", "id": "0123ABCD"}', '{"op": "refresh", "id": "0123"}',
                         '{"op": "refresh"}', "not json", "[1, 2]",
                         json.dumps({"op": "cover", "id": "0123abcd", "pad": "x" * 4096})):
                self.d.on_mqtt("batocera/test/req " + line)
        self.assertEqual(self.run.calls, [])

    def test_unknown_op(self):
        with self.assertLogs(hb.log, "WARNING"):
            self.assertEqual(self.answer(op="reboot"),
                             {"id": "0123abcd", "ok": False, "error": "bad_request"})

    def test_answers_are_not_retained(self):
        self.request(op="cover", game="nope")
        [(cmd, _)] = self.calls
        self.assertEqual(cmd, ["mosquitto_pub", "-q", "1", "-t", "batocera/test/resp/0123abcd", "-s"])

    def test_cover(self):
        answer = self.answer(op="cover", system="megadrive", game=self.SONIC)
        self.assertEqual(base64.b64decode(answer.pop("jpeg")), b"\xff\xd8fake")
        self.assertEqual(answer, {"id": "0123abcd", "ok": True})
        ffmpeg = next(c for c, _ in self.calls if "ffmpeg" in c)
        self.assertEqual(ffmpeg[:4], ["nice", "-n", "19", "ffmpeg"])
        self.assertIn("scale='min(256,iw)':'min(256,ih)':force_original_aspect_ratio=decrease", ffmpeg)

    def test_cover_errors(self):
        self.assertEqual(self.answer(op="cover", game="0" * 12)["error"], "unknown_game")
        self.assertEqual(self.answer(op="cover", system="snes", game=self.SONIC)["error"],
                         "unknown_game")
        self.assertEqual(self.answer(op="cover", game=self.BARE)["error"], "no_cover")
        sor = hb.game_id("/userdata/roms/megadrive/Streets of Rage.zip")
        self.assertEqual(self.answer(op="cover", game=sor)["error"], "no_cover")  # 404
        self.run = Recorder()  # ffmpeg writes nothing
        self.d.run = self.run
        with self.assertLogs(hb.log, "WARNING"):
            self.assertEqual(self.answer(op="cover", game=self.SONIC)["error"], "failed")
        self.fake.close()
        with self.assertLogs(hb.log, "WARNING"):
            self.assertEqual(self.answer(op="cover", game=self.SONIC)["error"], "es_unreachable")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
    def test_cover_for_real(self):
        png = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                              "testsrc=size=1200x900", "-frames:v", "1", "-f", "image2pipe",
                              "-c:v", "png", "-"], capture_output=True, check=True).stdout
        self.fake.media["/systems/megadrive/games/a0/media/thumbnail"] = png
        self.d.run = self.run = RealFfmpeg()
        jpeg = base64.b64decode(self.answer(op="cover", game=self.SONIC)["jpeg"])
        size = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                               "-of", "csv=p=0", "-"], input=jpeg, capture_output=True, check=True)
        self.assertEqual(size.stdout.decode().strip(), "256,192")

    def start_line(self, rom="/userdata/roms/megadrive/Sonic.zip"):
        return "\t".join(["gameStart", str(os.getpid()), "megadrive", "libretro",
                          "genesisplusgx", rom])

    def test_launch_when_idle(self):
        with self.assertLogs(hb.log, "INFO"):
            self.assertEqual(self.answer(op="launch", system="megadrive", game=self.SONIC),
                             {"id": "0123abcd", "ok": True})
        self.assertEqual(self.fake.launched, ["/userdata/roms/megadrive/Sonic.zip"])
        self.assertIn("launch", self.d.timers)
        with self.assertLogs(hb.log, "INFO"):
            self.d.on_line(self.start_line())  # the hook sees a normal launch
        self.assertIsNone(self.d.launching)
        self.assertNotIn("launch", self.d.timers)

    def test_launch_refused(self):
        self.assertEqual(self.answer(op="launch", game="/userdata/roms/x.zip")["error"],
                         "unknown_game")
        with self.assertLogs(hb.log, "INFO"):
            self.d.on_mqtt("batocera/test/lock ON")
            self.assertEqual(self.answer(op="launch", game=self.SONIC)["error"], "locked")
        self.assertEqual(self.fake.launched, [])

    def test_launch_quits_the_running_game_first(self):
        with self.assertLogs(hb.log, "INFO"):
            self.d.on_line(self.start_line(rom="/userdata/roms/megadrive/Bare.zip"))
        self.fake.running = SYNTHETIC["games"]["megadrive"][3]
        with mock.patch.object(hb.Daemon, "stop") as stop, self.assertLogs(hb.log, "INFO"):
            self.assertEqual(self.request(op="launch", game=self.SONIC), [])
            stop.assert_called_once()
        # Not yet: EmulationStation still says a game runs.
        self.assertEqual(self.fake.launched, [])
        self.assertEqual(self.request(op="launch", game=self.BARE)[0][1]["error"], "busy")
        self.clock.t += hb.LAUNCH_POLL
        self.d.run_timers()
        self.assertEqual(self.fake.launched, [])
        self.fake.running = None
        self.clock.t += hb.LAUNCH_POLL
        self.d.run_timers()
        self.assertEqual(self.fake.launched, ["/userdata/roms/megadrive/Sonic.zip"])
        self.assertEqual(self.run.published()[-1][0], "batocera/test/resp/0123abcd")

    def test_launch_gives_up_when_es_stays_busy(self):
        self.fake.running = SYNTHETIC["games"]["megadrive"][3]
        self.request(op="launch", game=self.SONIC)
        with self.assertLogs(hb.log, "WARNING"):
            for _ in range(hb.LAUNCH_IDLE_WAIT):
                self.clock.t += hb.LAUNCH_POLL
                self.d.run_timers()
        self.assertEqual([json.loads(p)["error"] for t, p in self.run.published()], ["busy"])
        self.assertEqual(self.fake.launched, [])
        self.assertIsNone(self.d.launching)

    def test_lock_while_waiting(self):
        self.fake.running = SYNTHETIC["games"]["megadrive"][3]
        self.request(op="launch", game=self.SONIC)
        with self.assertLogs(hb.log, "INFO"):
            self.d.on_mqtt("batocera/test/lock ON")
        self.fake.running = None
        self.clock.t += hb.LAUNCH_POLL
        self.d.run_timers()
        self.assertEqual([json.loads(p)["error"] for t, p in self.run.published()], ["locked"])
        self.assertEqual(self.fake.launched, [])

    def test_launched_game_that_never_starts(self):
        with self.assertLogs(hb.log, "INFO"):
            self.request(op="launch", game=self.SONIC)
        self.clock.t += hb.LAUNCH_START_WAIT
        with self.assertLogs(hb.log, "WARNING") as logs:
            self.d.run_timers()
        self.assertIn("did not start", "\n".join(logs.output))
        self.assertIsNone(self.d.launching)

    def test_launch_with_es_down(self):
        self.fake.close()
        with self.assertLogs(hb.log, "WARNING"):
            self.request(op="launch", game=self.SONIC)
            self.clock.t += hb.LAUNCH_IDLE_WAIT
            self.d.run_timers()
        self.assertEqual([json.loads(p)["error"] for t, p in self.run.published()],
                         ["es_unreachable"])

    def play(self, hotkeys):
        self.d.hotkeys = Path(self.tmp.name) / "hotkeygen.context"
        if hotkeys is not None:
            self.d.hotkeys.write_text(hotkeys)
        with self.assertLogs(hb.log, "INFO"):
            self.d.on_line(self.start_line())
        self.clock.t += hb.PAUSABLE_AFTER
        self.run.calls.clear()
        self.d.run_timers()
        return self.run.published()

    def test_pausable(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for hotkeys, flag in [
            ('{"name": "retroarch", "keys": {"exit": ["KEY_LEFTCTRL", "KEY_C"], "pause": "KEY_P"}}', "ON"),
            ('{"name": "dolphin", "keys": {"exit": ["KEY_LEFTALT", "KEY_F4"]}}', "OFF"),
            ('{"exit": "KEY_ESC", "pause": "KEY_P"}', "ON"),
            ("not json", None),
            (None, None),  # no context file
        ]:
            with self.subTest(hotkeys=hotkeys):
                self.assertEqual(self.play(hotkeys), [("batocera/test/pausable", flag)])

    def test_pause_and_resume(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.play('{"name": "mame", "keys": {"pause": "KEY_P"}}')
        self.run.calls.clear()
        with self.assertLogs(hb.log, "INFO"):
            self.assertEqual(self.answer(op="pause", paused=True),
                             {"id": "0123abcd", "ok": True, "paused": True})
        self.assertIn((["hotkeygen", "--send", "pause"], None), self.calls)
        self.assertIn(("batocera/test/paused", "ON"), [
            (c[c.index("-t") + 1], i.decode()) for c, i in self.calls if c[0] == "mosquitto_pub"])
        # Already paused: no hotkey, it would resume.
        self.assertEqual(self.answer(op="pause", paused=True)["paused"], True)
        self.assertNotIn((["hotkeygen", "--send", "pause"], None), self.calls)
        with self.assertLogs(hb.log, "INFO"):
            self.assertEqual(self.answer(op="pause", paused=False)["paused"], False)
        self.assertIn((["hotkeygen", "--send", "pause"], None), self.calls)

    def test_pause_refused(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.assertEqual(self.answer(op="pause", paused=True)["error"], "not_playing")
        self.play('{"name": "dolphin", "keys": {"exit": "KEY_ESC"}}')
        self.assertEqual(self.answer(op="pause", paused="yes")["error"], "bad_request")
        self.assertEqual(self.answer(op="pause", paused=True)["error"], "not_pausable")
        self.assertNotIn("hotkeygen", [c[0] for c, _ in self.calls])

    def test_pause_when_not_known_is_tried(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.play(None)

        class NoHotkeygen(Recorder):
            def __call__(self, cmd, input=None, **kwargs):
                super().__call__(cmd, input, **kwargs)
                if cmd[0] == "hotkeygen":
                    raise FileNotFoundError("hotkeygen")
                return subprocess.CompletedProcess(cmd, 0, b"", b"")

        self.d.run = self.run = NoHotkeygen()
        with self.assertLogs(hb.log, "WARNING"):
            self.assertEqual(self.answer(op="pause", paused=True)["error"], "failed")
        self.assertIn((["hotkeygen", "--send", "pause"], None), self.calls)
        self.assertFalse(self.d.game["paused"])

    def test_refresh(self):
        self.fake.games = dict(SYNTHETIC["games"], snes=[])
        with self.assertLogs(hb.log, "INFO"):
            answers = self.request(op="refresh")
        self.assertEqual(answers, [("batocera/test/resp/0123abcd", {"id": "0123abcd", "ok": True})])
        self.assertEqual(list(self.d.catalog), ["megadrive"])
        self.fake.close()
        with self.assertLogs(hb.log, "INFO"):
            self.assertEqual(self.answer(op="refresh")["error"], "es_unreachable")


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
                ("batocera/test/pausable", None),  # not known yet
                ("batocera/test/paused", "OFF"),
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
                          "batocera/test/cover", "batocera/test/pausable",
                          "batocera/test/paused"])
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

    def fake_launcher(self, *args):
        """A process with emulatorlauncher's command line, as Batocera runs it."""
        script = self.tmp / "emulatorlauncher"
        script.write_text("import time\ntime.sleep(60)\n")
        launcher = subprocess.Popen([sys.executable, str(script), *args])
        self.addCleanup(launcher.wait)
        self.addCleanup(launcher.kill)
        return launcher

    def test_adopts_a_game_already_running(self):
        launcher = self.fake_launcher("-p1name", "DualSense Wireless Controller",
                                      "-system", "megadrive", "-rom", self.rom,
                                      "-gameinfoxml", "/tmp/game.xml")
        self.d.hotkeys = self.tmp / "hotkeygen.context"
        self.d.hotkeys.write_text('{"name": "retroarch", "keys": {"exit": "KEY_F4"}}')
        with self.assertLogs(hb.log, "INFO") as logs:
            self.d.adopt()
        self.assertIn(f"launcher {launcher.pid} was already running", "\n".join(logs.output))
        published = self.run.published()
        self.assertIn(("batocera/test/game", "Bare"), published)
        self.assertIn(("batocera/test/system", "megadrive"), published)
        self.assertIn(("batocera/test/emulator", "retroarch"), published)
        self.assertEqual(published[-1], ("batocera/test/state", "playing"))
        self.assertEqual(self.d.game["pid"], launcher.pid)
        # From the launcher's start, in real time: not from the fake clock.
        self.assertLess(abs(self.d.game["started"] - time.time()), 5)
        self.d.tick()  # the launcher is alive: the session stays
        self.assertIsNotNone(self.d.game)

    def test_squashfs_game_named_by_the_launchers_rom(self):
        # The launcher mounts a .squashfs game and hands the hook the mount;
        # its -rom is the path ES and the catalog know.
        Path(self.rom).with_name("gamelist.xml").write_text(
            GAMELIST.replace("./Sonic.zip", "./Sonic.squashfs"))
        rom = str(Path(self.rom).with_name("Sonic.squashfs"))
        launcher = self.fake_launcher("-system", "megadrive", "-rom", rom)
        line = "\t".join(["gameStart", str(launcher.pid), "megadrive", "libretro",
                          "genesisplusgx", "/var/run/squashfs/Sonic/Sonic.md"])
        with self.assertLogs(hb.log, "INFO"):
            self.d.on_line(line)
        published = self.run.published()
        self.assertIn(("batocera/test/game", "Sonic & Knuckles"), published)
        self.assertIn(("batocera/test/game_id", hb.game_id(rom)), published)

    def test_adopt_without_hotkeys_context(self):
        self.fake_launcher("-system", "megadrive", "-rom", self.rom)
        self.d.hotkeys = self.tmp / "missing"
        with self.assertLogs(hb.log, "INFO"):
            self.d.adopt()
        self.assertIn(("batocera/test/emulator", None), self.run.published())

    def test_nothing_to_adopt(self):
        self.fake_launcher("-system", "megadrive")  # no ROM: not a game
        self.d.adopt()
        self.assertEqual(self.run.calls, [])
        self.assertIsNone(self.d.game)

    def test_emulator_from(self):
        self.assertEqual(hb.emulator_from('{"name": "cemu", "keys": {}}'), "cemu")
        for text in ("not json", "[1]", '{"keys": {}}', '{"name": 3}'):
            self.assertEqual(hb.emulator_from(text), "", text)

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
        # No screenshots: grim would capture the screen of whoever runs this.
        (self.tmp / "ha-batocera.conf").write_text("device=e2e\nscreen_every=0\n")
        self.log = self.tmp / "pub.log"
        self.fifo = self.tmp / "events.fifo"
        self.es = FakeEs()
        self.addCleanup(self.es.close)
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                        HA_BATOCERA_HOME=str(self.tmp), HA_BATOCERA_FIFO=str(self.fifo),
                        HA_BATOCERA_LOG=str(self.tmp / "daemon.log"), FAKE_LOG=str(self.log),
                        HA_BATOCERA_ES_URL=self.es.url)

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
        self.assertIn("batocera/e2e/catalog/index", sub_args)
        self.wait_for(lambda p: any(t == "batocera/e2e/catalog/index" for t, _, _ in p))

        self.hook("gameStart", "snes", "libretro", "snes9x", "/userdata/roms/snes/Mario World.sfc")
        self.wait_for(lambda p: ("batocera/e2e/state", "playing", str(self.tmp)) in p)
        self.assertIn(("batocera/e2e/game", "Mario World", str(self.tmp)), self.published())
        self.hook("gameStop", "snes", "libretro", "snes9x", "/userdata/roms/snes/Mario World.sfc")
        self.wait_for(lambda p: any(t == "batocera/e2e/last_session" for t, _, _ in p))

        daemon.send_signal(signal.SIGTERM)
        self.assertEqual(daemon.wait(timeout=10), 0)
        self.assertEqual(self.published()[-3:], [
            ("batocera/e2e/screen", "", str(self.tmp)),
            ("batocera/e2e/state", "off", str(self.tmp)),
            ("batocera/e2e/availability", "offline", str(self.tmp)),
        ])
        self.assertFalse(self.fifo.exists())
        self.assertNotIn("screenshot", (self.tmp / "daemon.log").read_text())  # turned off

    def test_daemon_started_during_a_game(self):
        script = self.tmp / "emulatorlauncher"
        script.write_text("import time\ntime.sleep(60)\n")
        launcher = subprocess.Popen([sys.executable, str(script), "-system", "snes",
                                     "-rom", "/userdata/roms/snes/Mario World.sfc"])
        self.addCleanup(launcher.wait)
        self.addCleanup(launcher.kill)
        daemon = subprocess.Popen([sys.executable, str(DAEMON / "ha_batocera.py")],
                                  env=self.env)
        self.addCleanup(daemon.kill)
        self.wait_for(lambda p: ("batocera/e2e/state", "playing", str(self.tmp)) in p)
        published = self.published()
        self.assertIn(("batocera/e2e/game", "Mario World", str(self.tmp)), published)
        # Never "idle" first: that would end a session that is still going.
        self.assertNotIn(("batocera/e2e/state", "idle", str(self.tmp)), published)


if __name__ == "__main__":
    unittest.main()
