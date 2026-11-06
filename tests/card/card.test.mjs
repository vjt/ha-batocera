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

const windows = [];
const flush = () => new Promise((r) => setTimeout(r, 0));

async function mount(states = idleStates(), { control = true, ws = {} } = {}) {
  const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only", url: "https://ha.example/batocera" });
  const { window } = dom;
  windows.push(window);
  window.eval(source);
  const calls = [];
  const wsCalls = [];
  const card = window.document.createElement("batocera-card");
  card.setConfig({ name: "Arcade", entry_id: "e1", entities: ENTITIES });
  window.document.body.appendChild(card);
  let current = states;
  const hass = () => ({
    states: { ...current },
    callService: (d, sv, data) => { calls.push([d, sv, data]); return Promise.resolve(); },
    callWS: (msg) => {
      wsCalls.push(msg);
      if (msg.type === "batocera/access") return Promise.resolve({ control });
      const answer = ws[msg.type];
      return answer ? answer(msg) : Promise.reject(new Error("unknown command"));
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
  return { window, card, root, $, $$, calls, wsCalls, sent, asked, set, text, shown };
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
