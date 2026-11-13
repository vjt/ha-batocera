// The card in jsdom, against a box faked through its entities and the
// websocket answers Home Assistant would give. jsdom has no layout: how the
// page looks is not checked here.
import { JSDOM } from "jsdom";
import fs from "node:fs";
import assert from "node:assert/strict";

const here = (path) => new URL(path, import.meta.url);
const source = fs.readFileSync(here("../../custom_components/batocera/www/batocera-card.js"), "utf8");
let failures = 0;
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const ENTITIES = {
  player: "media_player.arcade", locked: "binary_sensor.arcade_locked",
  playtime: "sensor.arcade_playtime_today", started: "sensor.arcade_session_start",
  limit: "number.arcade_daily_limit", manual: "switch.arcade_manual_lock",
  screen: "image.arcade_screen",
};
const s = (state, attributes = {}) => ({ state, attributes, last_changed: new Date().toISOString() });

const idleStates = (over = {}) => ({
  "media_player.arcade": s("idle", { supported_features: 512 | 4096 | 131072, catalog: "c1" }),
  "binary_sensor.arcade_locked": s("off", { reason: null }),
  "sensor.arcade_session_start": s("unknown"),
  "sensor.arcade_playtime_today": s("12"),
  ...over,
});
const playingStates = (attrs = {}, over = {}) => idleStates({
  "media_player.arcade": s("playing", {
    supported_features: 1 | 2 | 4096 | 512 | 16384 | 131072, media_title: "Sonic the Hedgehog",
    source: "megadrive", media_content_id: "game:megadrive:9b1c4e0d2a37",
    entity_picture: "/api/media_player_proxy/media_player.arcade?token=t&cache=1",
    pausable: true, minutes_left: 42, catalog: "c1", ...attrs,
  }),
  "sensor.arcade_session_start": s(new Date(Date.now() - 125000).toISOString()),
  ...over,
});

// The media browser as Home Assistant answers it for this box.
const thumb = (id) => `/api/media_player_proxy/media_player.arcade/browse_media/game/${id}?token=t1`;
const game = (system, id, title, cover = true) => ({
  media_class: "game", media_content_id: `game:${system}:${id}`, media_content_type: "game",
  title, can_play: true, can_expand: false, thumbnail: cover ? thumb(`game:${system}:${id}`) : null,
});
const folder = (id, title, children) => ({
  media_class: "directory", media_content_id: id, media_content_type: "directory", title,
  can_play: false, can_expand: true, children, thumbnail: null,
});
const SONIC = game("megadrive", "9b1c4e0d2a37", "Sonic the Hedgehog");
const SOR = game("megadrive", "0d5e7a9b3c21", "Streets of Rage");
const BARE = game("megadrive", "1a2b3c4d5e6f", "Bare", false);
const POKEMON = game("megadrive", "2b3c4d5e6f70", "Pokémon Crystal");
const MARIO = game("snes", "5f4e3d2c1b0a", "Super Mario World");
const FOLDERS = {
  "": folder("", "Arcade", [
    folder("fav", "Favourites"), folder("recent", "Recently played"),
    folder("system:megadrive", "Sega Mega Drive"), folder("system:snes", "Super Nintendo"),
  ]),
  "system:megadrive": folder("system:megadrive", "Sega Mega Drive", [BARE, POKEMON, SONIC, SOR]),
  "fav:megadrive": folder("fav:megadrive", "Favourites · Sega Mega Drive", [SONIC]),
  "recent:megadrive": folder("recent:megadrive", "Recently played · Sega Mega Drive", [SOR, SONIC]),
  "system:snes": folder("system:snes", "Super Nintendo", [MARIO]),
  fav: folder("fav", "Favourites", [SONIC]),
  recent: folder("recent", "Recently played", [MARIO, SOR, SONIC]),
};
const browseMedia = (folders = FOLDERS) => (msg) =>
  folders[msg.media_content_id] ? Promise.resolve(structuredClone(folders[msg.media_content_id]))
    : Promise.reject(new Error("not found"));
// Home Assistant's media search over the same games: of the system the
// search starts from (sub-folders included), or of all.
const fold = (text) => text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const searchMedia = (folders = FOLDERS) => (msg) => {
  const games = new Map();
  for (const f of Object.values(folders)) {
    for (const c of f.children || []) if (c.can_play) games.set(c.media_content_id, c);
  }
  const scope = msg.media_content_id ? msg.media_content_id.split(":")[1] : null;
  const query = fold(msg.search_query.trim());
  const result = [...games.values()]
    .filter((g) => (!scope || g.media_content_id.split(":")[1] === scope) && fold(g.title).includes(query))
    .sort((a, b) => fold(a.title).localeCompare(fold(b.title)));
  return Promise.resolve({ result: structuredClone(result) });
};
const SEARCH_WAIT = 300;  // past the card's debounce
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const windows = [];
const flush = () => new Promise((r) => setTimeout(r, 0));

async function mount(states = idleStates(), { control = true, ws = {}, folders = FOLDERS, fetch } = {}) {
  const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only", url: "https://ha.example/batocera" });
  const { window } = dom;
  windows.push(window);
  window.eval(source);
  let blobs = 0;
  window.URL.createObjectURL = () => `blob:cover-${++blobs}`;
  window.URL.revokeObjectURL = () => {};
  const calls = [];
  const wsCalls = [];
  const fetched = [];
  ws = { "media_player/browse_media": browseMedia(folders), "media_player/search_media": searchMedia(folders), ...ws };
  const card = window.document.createElement("batocera-card");
  card.setConfig({ name: "Arcade", entry_id: "e1", entities: ENTITIES });
  window.document.body.appendChild(card);
  let current = states;
  let answer = () => Promise.resolve();  // what the services answer
  const hass = () => ({
    states: { ...current },
    callService: (d, sv, data) => { calls.push([d, sv, data]); return answer(); },
    callWS: (msg) => {
      wsCalls.push(msg);
      if (msg.type === "batocera/access") return Promise.resolve({ control });
      const answer = ws[msg.type];
      return answer ? answer(msg) : Promise.reject(new Error("unknown command"));
    },
    fetchWithAuth: (url) => {
      fetched.push(url);
      return fetch ? fetch(url) : Promise.resolve({ ok: !url.includes("1a2b3c"), blob: () => Promise.resolve({}) });
    },
  });
  card.hass = hass();
  await flush();
  const root = card.shadowRoot;
  const $ = (q) => root.querySelector(q);
  const $$ = (q) => [...root.querySelectorAll(q)];
  const set = async (next) => { current = next; card.hass = hass(); await flush(); };
  // jsdom objects are cross-realm: compare calls through JSON.
  const sent = () => JSON.parse(JSON.stringify(calls));
  const asked = (type) => JSON.parse(JSON.stringify(wsCalls.filter((m) => m.type === type)));
  const text = (q) => ($(q) ? $(q).textContent : undefined);
  const shown = (q) => !!$(q) && !$(q).classList.contains("hidden");
  const tiles = () => $$(".grid .tile .tn").map((n) => n.textContent);
  const open = async (title) => {
    const tile = $$(".grid .tile").find((t) => t.querySelector(".tn").textContent === title);
    assert.ok(tile, `no tile ${title} in ${tiles()}`);
    tile.click();
    await flush();
  };
  const type = async (text) => {
    $(".search").value = text;
    $(".search").dispatchEvent(new window.Event("input"));
    await flush();
  };
  // Type, then wait past the debounce for the search to be asked and answered.
  const search = async (text) => {
    await type(text);
    await wait(SEARCH_WAIT);
    await flush();
  };
  const services = (fn) => { answer = fn; };
  return { window, card, root, $, $$, calls, wsCalls, fetched, sent, asked, set, text, shown, tiles, open, type, search, services };
}

// --- the now-playing bar ------------------------------------------------

test("idle: no game, insert coin", async () => {
  const t = await mount();
  assert.equal(t.text(".title"), "NO GAME");
  assert.ok(!t.shown(".clock"));
  assert.ok(!t.shown(".ctl"));
  assert.equal(t.text(".banner .big"), "INSERT COIN");
  assert.equal(t.text(".banner .note"), "PICK A GAME");
});

test("playing: title, system, time played, time left, cover", async () => {
  const t = await mount(playingStates());
  assert.equal(t.text(".title"), "Sonic the Hedgehog");
  assert.equal(t.text(".sub"), "MEGADRIVE · 2:05");
  assert.equal(t.text(".clock .label"), "TIME LEFT");
  assert.equal(t.text(".clock .left"), "0:42");
  assert.equal(t.$(".cover img").getAttribute("src"), "/api/media_player_proxy/media_player.arcade?token=t&cache=1");
  assert.ok(!t.shown(".banner"));
  assert.ok(t.shown(".ctl"));
});

test("no limit: free play", async () => {
  const t = await mount(playingStates({ minutes_left: null }));
  assert.equal(t.text(".clock .label"), "FREE PLAY");
  assert.equal(t.text(".clock .left"), "");
});

test("the last minutes are flagged", async () => {
  const t = await mount(playingStates({ minutes_left: 4 }));
  assert.ok(t.$(".clock").classList.contains("low"));
});

test("the screen is a preview in the bar; tap opens it, escape or off closes it", async () => {
  const shot = (at) => s(at, { entity_picture: "/api/image_proxy/image.arcade_screen?token=k" });
  const t = await mount();
  assert.ok(!t.shown(".shot"));  // no screenshot yet
  assert.ok(!t.$(".screen"));  // the old full-width block is gone
  assert.ok(t.$(".bar .shot"), "the preview sits in the bar");
  await t.set(idleStates({ "image.arcade_screen": shot("2026-11-13T17:00:00+00:00") }));
  assert.ok(t.shown(".shot"));
  const src = "/api/image_proxy/image.arcade_screen?token=k&t=2026-11-13T17%3A00%3A00%2B00%3A00";
  assert.equal(t.$(".shot img").getAttribute("src"), src);
  assert.ok(!t.shown(".zoom"));
  t.$(".shot").click();
  assert.ok(t.shown(".zoom"));
  assert.equal(t.$(".zoom img").getAttribute("src"), src);
  t.$(".zoom").dispatchEvent(new t.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.ok(!t.shown(".zoom"));
  t.$(".shot").click();
  t.$(".zoom").click();  // a tap anywhere closes it too
  assert.ok(!t.shown(".zoom"));
  await t.set(playingStates({}, { "image.arcade_screen": shot("2026-11-13T17:00:15+00:00") }));
  assert.match(t.$(".shot img").getAttribute("src"), /&t=2026-11-13T17%3A00%3A15/);
  t.$(".shot").click();
  assert.match(t.$(".zoom img").getAttribute("src"), /&t=2026-11-13T17%3A00%3A15/);
  await t.set(idleStates({
    "media_player.arcade": s("off"), "image.arcade_screen": shot("2026-11-13T17:00:30+00:00"),
  }));
  assert.ok(!t.shown(".shot"));  // the last one, from before it went off: not now
  assert.ok(!t.shown(".zoom"));  // and an open zoom closes
  await t.set(idleStates({ "image.arcade_screen": s("unknown", { entity_picture: "/x?token=k" }) }));
  assert.ok(!t.shown(".shot"));
  assert.equal(t.$(".shot img").getAttribute("src"), null);
});

test("offline and locked banners", async () => {
  const t = await mount(idleStates({ "media_player.arcade": s("unavailable") }));
  assert.equal(t.text(".banner .big"), "OFFLINE");
  await t.set(idleStates({ "media_player.arcade": s("off") }));
  assert.equal(t.text(".banner .big"), "OFFLINE");
  await t.set(idleStates({ "binary_sensor.arcade_locked": s("on", { reason: "limit" }) }));
  assert.equal(t.text(".banner .big"), "GAME OVER — LOCKED");
  assert.equal(t.text(".banner .note"), "NO TIME LEFT TODAY");
  await t.set(idleStates({ "binary_sensor.arcade_locked": s("on", { reason: "window" }) }));
  assert.equal(t.text(".banner .note"), "OUTSIDE PLAY HOURS");
});

test("browse only for those who may not play", async () => {
  const t = await mount(playingStates(), { control: false });
  assert.ok(!t.shown(".ctl"));
  await t.set(idleStates());
  assert.equal(t.text(".banner .note"), "BROWSE ONLY");
  assert.deepEqual(t.asked("batocera/access"), [{ type: "batocera/access", entry_id: "e1" }]);
});

test("kill takes two taps", async () => {
  const t = await mount(playingStates());
  t.$(".kill").click();
  assert.equal(t.text(".kill"), "✖ SURE?");
  assert.deepEqual(t.sent(), []);
  t.$(".kill").click();
  assert.deepEqual(t.sent(), [["media_player", "media_stop", { entity_id: "media_player.arcade" }]]);
  assert.equal(t.text(".kill"), "✖ KILL");
});

test("pause: as far as the box knows", async () => {
  const t = await mount(playingStates());
  assert.equal(t.text(".pause"), "⏸ PAUSE");
  t.$(".pause").click();
  assert.deepEqual(t.sent().at(-1), ["media_player", "media_pause", { entity_id: "media_player.arcade" }]);
  await t.set(playingStates({ pausable: null }));
  assert.equal(t.text(".pause"), "⏸ PAUSE?");
  assert.ok(!t.$(".pause").disabled);
  await t.set(playingStates({ pausable: false }));
  assert.equal(t.text(".pause"), "NO PAUSE");
  assert.ok(t.$(".pause").disabled);
  await t.set(idleStates({ "media_player.arcade": s("paused", { ...playingStates()["media_player.arcade"].attributes }) }));
  assert.equal(t.text(".pause"), "▶ RESUME");
  assert.match(t.text(".sub"), /PAUSED/);
  t.$(".pause").click();
  assert.deepEqual(t.sent().at(-1), ["media_player", "media_play", { entity_id: "media_player.arcade" }]);
});

// --- browsing ---------------------------------------------------------

test("the top: favourites, recent, then the systems", async () => {
  const t = await mount();
  assert.equal(t.text(".where"), "Arcade");
  assert.ok(!t.shown(".back"));
  assert.ok(!t.shown(".tabs"));
  assert.deepEqual(t.tiles(), ["Favourites", "Recently played", "Sega Mega Drive", "Super Nintendo"]);
  assert.ok(t.$(".grid .tile.fav") && t.$(".grid .tile.recent"));
});

test("a system: its games, its tabs, back to the top", async () => {
  const t = await mount();
  await t.open("Sega Mega Drive");
  assert.deepEqual(t.asked("media_player/browse_media").at(-1), {
    type: "media_player/browse_media", entity_id: "media_player.arcade",
    media_content_id: "system:megadrive", media_content_type: "directory",
  });
  assert.equal(t.text(".where"), "Sega Mega Drive");
  assert.deepEqual(t.tiles(), ["Bare", "Pokémon Crystal", "Sonic the Hedgehog", "Streets of Rage"]);
  assert.ok(t.shown(".tabs") && t.shown(".back"));
  assert.equal(t.$('.tabs [data-kind="system"]').getAttribute("aria-pressed"), "true");
  t.$('.tabs [data-kind="fav"]').click();
  await flush();
  assert.deepEqual(t.tiles(), ["Sonic the Hedgehog"]);
  t.$('.tabs [data-kind="recent"]').click();
  await flush();
  assert.deepEqual(t.tiles(), ["Streets of Rage", "Sonic the Hedgehog"]);
  t.$(".back").click();
  await flush();
  assert.equal(t.tiles().length, 4);
  assert.equal(t.text(".where"), "Arcade");
});

test("search, without minding case or accents", async () => {
  const t = await mount();
  await t.open("Sega Mega Drive");
  await t.search("POKEMON");
  assert.deepEqual(t.tiles(), ["Pokémon Crystal"]);
  await t.search("zelda");
  assert.deepEqual(t.tiles(), []);
  assert.equal(t.text(".status .big"), "NO MATCH");
  t.$(".back").click();
  await flush();
  assert.equal(t.$(".search").value, "");
});

test("search from the top finds games of every system", async () => {
  const t = await mount();
  await t.type("ma");
  await t.search("mario");  // typed fast: one search, for the last text
  assert.deepEqual(t.asked("media_player/search_media"), [
    { type: "media_player/search_media", entity_id: "media_player.arcade", search_query: "mario" },
  ]);
  assert.deepEqual(t.tiles(), ["Super Mario World"]);
  await t.search("s");
  assert.deepEqual(t.tiles(), ["Pokémon Crystal", "Sonic the Hedgehog", "Streets of Rage", "Super Mario World"]);
  await t.open("Super Mario World");
  assert.ok(t.shown(".sheet"));
  assert.equal(t.text(".ssystem"), "Super Nintendo");
  t.$(".cancel").click();
  await t.type("");  // cleared: the systems again, at once
  assert.deepEqual(t.tiles(), ["Favourites", "Recently played", "Sega Mega Drive", "Super Nintendo"]);
  await wait(SEARCH_WAIT);
  assert.equal(t.asked("media_player/search_media").length, 2);
});

test("only the latest search shows", async () => {
  const pending = [];
  const t = await mount(idleStates(), {
    ws: { "media_player/search_media": (msg) => new Promise((resolve) => pending.push([msg, resolve])) },
  });
  await t.search("sonic");
  await t.search("mario");
  assert.equal(pending.length, 2);
  pending[1][1]({ result: [MARIO] });
  await flush();
  pending[0][1]({ result: [SONIC] });  // late: thrown away
  await flush();
  assert.deepEqual(t.tiles(), ["Super Mario World"]);
  await t.type("");
  assert.deepEqual(t.tiles(), ["Favourites", "Recently played", "Sega Mega Drive", "Super Nintendo"]);
});

const KI = game("mame", "0000000000a1", "Killer Instinct");
const GALAXIAN = game("mame", "0000000000a2", "Galaxian");
const MAME = {
  ...FOLDERS,
  "": folder("", "Arcade", [
    ...FOLDERS[""].children,
    { ...folder("system:mame", "MAME"), thumbnail: thumb("system:mame") },
  ]),
  "system:mame": folder("system:mame", "MAME", [folder("dir:mame:Fighter", "Fighter"), GALAXIAN]),
  "dir:mame:Fighter": folder("dir:mame:Fighter", "Fighter", [KI]),
  "fav:mame": folder("fav:mame", "Favourites · MAME", [KI]),
};

test("inside a system, search covers its sub-folders", async () => {
  const t = await mount(idleStates(), { folders: MAME });
  await t.open("MAME");
  await t.open("Fighter");
  await t.search("galax");
  assert.deepEqual(t.asked("media_player/search_media").at(-1), {
    type: "media_player/search_media", entity_id: "media_player.arcade", search_query: "galax",
    media_content_type: "directory", media_content_id: "dir:mame:Fighter",
  });
  assert.deepEqual(t.tiles(), ["Galaxian"]);
  t.$('.tabs [data-kind="fav"]').click();  // favourites: filtered here
  await flush();
  assert.deepEqual(t.tiles(), ["Killer Instinct"]);
  await t.search("killer");
  assert.deepEqual(t.tiles(), ["Killer Instinct"]);
  await t.search("galax");
  assert.deepEqual(t.tiles(), []);
  assert.equal(t.asked("media_player/search_media").length, 1);
});

test("systems show their logo, folders a folder tile", async () => {
  const t = await mount(idleStates(), { folders: MAME });
  const mame = t.$$(".grid .tile.system").find((n) => n.querySelector(".tn").textContent === "MAME");
  assert.ok(mame.classList.contains("logo"));
  assert.ok(mame.querySelector(".art img"), "the logo");
  await flush();
  assert.ok(t.fetched.includes(thumb("system:mame")), "fetched with the user's auth");
  const snes = t.$$(".grid .tile.system").find((n) => n.querySelector(".tn").textContent === "Super Nintendo");
  assert.ok(!snes.classList.contains("logo") && !snes.querySelector(".art img"));
  await t.open("MAME");
  const fighter = t.$$(".grid .tile").find((n) => n.querySelector(".tn").textContent === "Fighter");
  assert.ok(fighter.classList.contains("dir"));
  await t.open("Fighter");
  assert.equal(t.text(".where"), "Fighter");
  assert.deepEqual(t.tiles(), ["Killer Instinct"]);
  assert.ok(t.shown(".tabs"));
  t.$('.tabs [data-kind="system"]').click();  // ALL: the whole system again
  await flush();
  assert.deepEqual(t.asked("media_player/browse_media").at(-1).media_content_id, "system:mame");
  t.$(".back").click();  // from the system, not from a folder left behind
  await flush();
  assert.equal(t.text(".where"), "Arcade");
  assert.ok(!t.shown(".back"));
});

test("the running game stands out", async () => {
  const t = await mount(playingStates());
  await t.open("Sega Mega Drive");
  const now = t.$$(".grid .tile.now");
  assert.equal(now.length, 1);
  assert.equal(now[0].querySelector(".tn").textContent, "Sonic the Hedgehog");
  assert.equal(now[0].querySelector(".badge").textContent, "NOW PLAYING");
  await t.set(idleStates());
  assert.equal(t.$$(".grid .tile.now").length, 0);
});

test("covers through the proxy, with the user's auth, once each", async () => {
  const t = await mount();
  await t.open("Sega Mega Drive");
  await flush();
  assert.deepEqual(t.fetched, [thumb(POKEMON.media_content_id), thumb(SONIC.media_content_id), thumb(SOR.media_content_id)]);
  const art = (title) => t.$$(".grid .tile").find((x) => x.querySelector(".tn").textContent === title).querySelector(".art");
  assert.ok(art("Sonic the Hedgehog").classList.contains("loaded"));
  assert.match(art("Sonic the Hedgehog").querySelector("img").src, /^blob:cover-/);
  assert.ok(art("Bare").classList.contains("none"));  // no cover on the box
  assert.equal(art("Bare").textContent, "NO SIGNAL");
  // In another folder: from memory, even with a new token in the URL.
  t.$('.tabs [data-kind="fav"]').click();
  await flush();
  await flush();
  assert.equal(t.fetched.length, 3);
  assert.ok(art("Sonic the Hedgehog").classList.contains("loaded"));
});

test("covers are fetched again when the card comes back on the page", async () => {
  const t = await mount();
  await t.open("Super Nintendo");
  assert.equal(t.fetched.length, 1);
  t.card.remove();
  t.window.document.body.appendChild(t.card);
  await flush();
  await flush();
  assert.equal(t.fetched.length, 2);
  assert.ok(t.$(".grid .tile .art").classList.contains("loaded"));
});

test("covers: a few at a time", async () => {
  const pending = [];
  const many = Array.from({ length: 10 }, (_, i) => game("snes", `00000000000${i}`, `Game ${i}`));
  const folders = { ...FOLDERS, "system:snes": folder("system:snes", "Super Nintendo", many) };
  const t = await mount(idleStates(), {
    folders, fetch: () => new Promise((resolve) => pending.push(resolve)),
  });
  await t.open("Super Nintendo");
  assert.equal(t.fetched.length, 4);
  pending.shift()({ ok: true, blob: () => Promise.resolve({}) });
  await flush();
  await flush();
  assert.equal(t.fetched.length, 5);
});

test("no catalog yet, or the box answers nothing", async () => {
  const empty = { "": folder("", "Arcade", [folder("fav", "Favourites"), folder("recent", "Recently played")]) };
  const t = await mount(idleStates(), { folders: empty });
  assert.equal(t.text(".status .big"), "NO CATALOG YET");
  assert.deepEqual(t.tiles(), []);
  const u = await mount(idleStates(), { folders: {} });
  assert.equal(u.text(".status .big"), "NO CATALOG YET");
});

test("a new catalog is read again; a system gone leads back to the top", async () => {
  const folders = { ...FOLDERS };
  const t = await mount(idleStates(), { folders });
  await t.open("Super Nintendo");
  const asked = t.asked("media_player/browse_media").length;
  await t.set(idleStates());  // same catalog: nothing read
  assert.equal(t.asked("media_player/browse_media").length, asked);
  folders["system:snes"] = undefined;
  await t.set(idleStates({ "media_player.arcade": s("idle", { catalog: "c2" }) }));
  await flush();
  assert.equal(t.text(".where"), "Arcade");
  assert.equal(t.tiles().length, 4);
});

// --- the launch sheet ---------------------------------------------------

test("press start: launches, waits, and closes once it plays", async () => {
  const t = await mount();
  await t.open("Sega Mega Drive");
  await t.open("Sonic the Hedgehog");
  assert.ok(t.shown(".sheet"));
  assert.equal(t.text(".stitle"), "Sonic the Hedgehog");
  assert.equal(t.text(".ssystem"), "Sega Mega Drive");
  assert.equal(t.text(".swarn"), "");
  assert.ok(!t.$(".start").disabled);
  t.$(".start").click();
  await flush();
  assert.deepEqual(t.sent(), [["media_player", "play_media", {
    entity_id: "media_player.arcade", media_content_id: SONIC.media_content_id, media_content_type: "game",
  }]]);
  assert.equal(t.text(".start"), "LOADING…");
  assert.ok(t.$(".start").disabled);
  await t.set(playingStates());
  assert.ok(!t.shown(".sheet"));
});

test("it says what it quits, or that it already plays", async () => {
  const t = await mount(playingStates({
    media_title: "Super Mario World", media_content_id: MARIO.media_content_id, source: "snes",
  }));
  await t.open("Sega Mega Drive");
  await t.open("Streets of Rage");
  assert.equal(t.text(".swarn"), "QUITS Super Mario World");
  t.$(".cancel").click();
  assert.ok(!t.shown(".sheet"));
  await t.set(playingStates());  // Sonic runs
  await t.open("Sonic the Hedgehog");
  assert.equal(t.text(".swarn"), "ALREADY PLAYING");
  assert.ok(!t.shown(".start"));
});

test("locked, offline or not allowed: no start", async () => {
  const t = await mount(idleStates({ "binary_sensor.arcade_locked": s("on", { reason: "manual" }) }));
  await t.open("Sega Mega Drive");
  await t.open("Bare");
  assert.ok(t.$(".start").disabled);
  assert.equal(t.text(".smsg"), "LOCKED BY HAND");
  assert.ok(t.$(".sheet .art").classList.contains("none"));
  await t.set(idleStates({ "media_player.arcade": s("off") }));
  assert.equal(t.text(".smsg"), "OFFLINE");
  const u = await mount(idleStates(), { control: false });
  await u.open("Super Nintendo");
  await u.open("Super Mario World");
  assert.ok(!u.shown(".start"));
  assert.equal(u.text(".smsg"), "BROWSE ONLY");
});

test("a refused launch says why; one that never starts gives up", async () => {
  const t = await mount();
  t.services(() => Promise.reject(new Error("Arcade is locked: today's playtime is used up.")));
  await t.open("Sega Mega Drive");
  await t.open("Sonic the Hedgehog");
  t.$(".start").click();
  await flush();
  assert.equal(t.text(".smsg"), "Arcade is locked: today's playtime is used up.");
  assert.ok(!t.$(".start").disabled);

  t.services(() => Promise.resolve());
  t.$(".start").click();
  await flush();
  assert.equal(t.text(".start"), "LOADING…");
  const now = t.window.Date.now;
  t.window.Date.now = () => now() + 31000;
  await t.set(idleStates());
  t.window.Date.now = now;
  assert.equal(t.text(".smsg"), "IT DID NOT START: TRY AGAIN");
  assert.equal(t.text(".start"), "PRESS START");
});

test("the sheet closes on cancel, outside, or escape", async () => {
  const t = await mount();
  await t.open("Super Nintendo");
  await t.open("Super Mario World");
  t.$(".sheet").click();
  assert.ok(!t.shown(".sheet"));
  await t.open("Super Mario World");
  t.$(".sheet").dispatchEvent(new t.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.ok(!t.shown(".sheet"));
});

// --- the look --------------------------------------------------------------

test("the pixel font is declared once on the page", async () => {
  const t = await mount();
  const other = t.window.document.createElement("batocera-card");
  other.setConfig({ name: "Attic", entities: ENTITIES });
  t.window.document.body.appendChild(other);
  other.hass = { states: idleStates(), callService: () => Promise.resolve(), callWS: () => new Promise(() => {}) };
  const faces = t.window.document.head.querySelectorAll("#batocera-pixel-font");
  assert.equal(faces.length, 1);
  assert.match(faces[0].textContent, /url\("\/batocera\/fonts\/PressStart2P-Regular\.ttf"\)/);
});

test("motion stops for those who asked", async () => {
  const t = await mount();
  assert.match(t.$("style").textContent, /prefers-reduced-motion: reduce/);
});

for (const [name, fn] of tests) {
  try {
    await fn();
    console.log("ok  ", name);
  } catch (e) {
    failures++;
    console.log("FAIL", name, "\n     ", e.stack.split("\n").slice(0, 3).join("\n      "));
  }
}
await new Promise((r) => setTimeout(r, 20));
for (const window of windows) window.close();
if (failures) {
  console.log(`\n${failures} failed`);
  process.exit(1);
}
console.log(`\nall ${tests.length} passed`);
