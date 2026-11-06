// Batocera card: the box's games, full page, and what it is playing.
//
// Served by the integration (see dashboard.py) and dropped into the Batocera
// dashboard, one card per box:
//   type: custom:batocera-card
//   name: Arcade
//   entry_id: <the box's config entry>
//   entities: {player: media_player.arcade, locked: binary_sensor.arcade_locked,
//              started: sensor.arcade_session_start, ...}
// What it shows comes from those entities; what it does goes through the
// media player's services, which check the user's rights and the limits
// again. Missing roles are simply not shown.

// Every text the card shows, in one table.
const STRINGS = {
  noGame: "NO GAME",
  freePlay: "FREE PLAY",
  timeLeft: "TIME LEFT",
  pause: "⏸ PAUSE",
  pauseUnsure: "⏸ PAUSE?",
  pauseUnsureTitle: "The box cannot tell whether this game can pause: try",
  noPause: "NO PAUSE",
  noPauseTitle: "This emulator has no pause",
  resume: "▶ RESUME",
  kill: "✖ KILL",
  killSure: "✖ SURE?",
  offline: "OFFLINE",
  offlineNote: "NO SIGNAL FROM THE BOX",
  locked: "GAME OVER — LOCKED",
  lockReasons: {
    manual: "LOCKED BY HAND",
    window: "OUTSIDE PLAY HOURS",
    limit: "NO TIME LEFT TODAY",
  },
  insertCoin: "INSERT COIN",
  insertCoinNote: "PICK A GAME",
  paused: "PAUSED",
  browseOnly: "BROWSE ONLY",
  back: "◀ BACK",
  all: "ALL",
  favourites: "★",
  recent: "RECENT",
  favouritesTitle: "★ FAVOURITES",
  recentTitle: "RECENT",
  search: "SEARCH",
  loading: "LOADING…",
  noCatalog: "NO CATALOG YET",
  noCatalogNote: "THE BOX SENDS ITS GAMES ONCE EMULATIONSTATION IS UP",
  noGames: "NO GAMES HERE",
  noMatch: "NO MATCH",
  nowPlaying: "NOW PLAYING",
  noSignal: "NO SIGNAL",
  pressStart: "PRESS START",
  cancel: "CANCEL",
  quits: (title) => `QUITS ${title}`,
  alreadyPlaying: "ALREADY PLAYING",
  didNotStart: "IT DID NOT START: TRY AGAIN",
};

// How long the kill button waits for its second tap.
const SURE_MS = 4000;
// Covers fetched at once: the box downscales them one by one.
const COVERS_AT_ONCE = 4;
// How long a launch may take before the card stops waiting for it.
const START_MS = 30000;
// The folders of a system: all its games, its favourites, played last.
const TABS = [["system", "all"], ["fav", "favourites"], ["recent", "recent"]];

const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
const pad = (n) => String(n).padStart(2, "0");
// 1:02:03 or 2:03, for the time played.
const clock = (seconds) => {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
};
// 1:05 for 65 minutes, for the time left.
const hm = (minutes) => `${Math.floor(minutes / 60)}:${pad(minutes % 60)}`;
// For search: lower case, without accents.
const fold = (text) => (text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
// "system:snes", "fav:snes" -> ["system", "snes"]; "fav" -> ["fav", ""].
const split = (id) => {
  const at = id.indexOf(":");
  return at < 0 ? [id, ""] : [id.slice(0, at), id.slice(at + 1)];
};

// The pixel font, bundled with the integration (OFL, see fonts/OFL.txt). It
// is declared on the page: a @font-face inside a shadow root is ignored.
const FONT = "Batocera Pixel";
const FONT_URL = "/batocera/fonts/PressStart2P-Regular.ttf";
const loadFont = () => {
  if (document.getElementById("batocera-pixel-font")) return;
  const face = document.createElement("style");
  face.id = "batocera-pixel-font";
  face.textContent = `@font-face { font-family: "${FONT}"; src: url("${FONT_URL}") format("truetype");
    font-display: swap; }`;
  document.head.appendChild(face);
};

// A dark CRT: neon on near black, scanlines, a little glow. The pixel font
// for headings and labels only; names and notes in the system's font.
const STYLE = `
  :host {
    display: block; height: 100%;
    --bt-bg: #07060d; --bt-panel: #110f1f; --bt-edge: #2c2852;
    --bt-fg: #ecebff; --bt-dim: #a9a6cc;
    --bt-cyan: #3ef4ff; --bt-pink: #ff52da; --bt-yellow: #ffe94d;
    --bt-green: #4dff95; --bt-red: #ff5a6e;
    --bt-pixel: "${FONT}", ui-monospace, monospace;
  }
  .wrap {
    position: relative; min-height: calc(100vh - var(--header-height, 56px)); box-sizing: border-box;
    background: radial-gradient(ellipse at 50% 0%, #17133a 0%, var(--bt-bg) 60%) fixed, var(--bt-bg);
    color: var(--bt-fg); font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  .wrap::after {  /* scanlines */
    content: ""; position: absolute; inset: 0; pointer-events: none; z-index: 4;
    background: repeating-linear-gradient(to bottom, transparent 0 2px, rgba(0, 0, 0, 0.08) 2px 3px);
  }
  .title, .clock .label, .clock .left, .banner .big, .where, button, .badge,
  .nosignal, .stitle, .status .big, .dir .tn {
    font-family: var(--bt-pixel); text-transform: uppercase; letter-spacing: 0.04em;
  }

  .bar {
    position: sticky; top: 0; z-index: 3; display: flex; flex-wrap: wrap; align-items: center;
    gap: 12px; padding: 10px 16px; background: rgba(7, 6, 13, 0.94);
    border-bottom: 2px solid var(--bt-edge); box-shadow: 0 2px 12px rgba(62, 244, 255, 0.12);
  }
  .playing .bar { border-bottom-color: var(--bt-cyan); }
  .cover { width: 56px; height: 56px; flex: none; border: 2px solid var(--bt-edge); background: #000; }
  .cover img { width: 100%; height: 100%; object-fit: contain; display: block; }
  .now { flex: 1 1 160px; min-width: 0; }
  .title { font-size: 12px; line-height: 1.6; color: var(--bt-cyan); text-shadow: 0 0 6px rgba(62, 244, 255, 0.6);
           overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sub { font-size: 13px; color: var(--bt-dim); margin-top: 4px; }
  .clock { text-align: right; }
  .clock .label { font-size: 9px; color: var(--bt-dim); }
  .clock .left { font-size: 16px; color: var(--bt-yellow); text-shadow: 0 0 6px rgba(255, 233, 77, 0.6); margin-top: 6px; }
  .clock.low .left { color: var(--bt-red); text-shadow: 0 0 8px rgba(255, 90, 110, 0.8); animation: blink 1s steps(2) infinite; }
  .ctl { display: flex; gap: 8px; }

  button {
    min-height: 44px; min-width: 44px; padding: 10px 14px; cursor: pointer; font-size: 10px;
    color: var(--bt-cyan); background: transparent; border: 2px solid currentColor; border-radius: 0;
    box-shadow: 0 0 6px rgba(62, 244, 255, 0.25), inset 0 0 6px rgba(62, 244, 255, 0.15);
    transition: transform 0.08s ease, background 0.15s ease;
  }
  button:hover:not(:disabled) { background: rgba(62, 244, 255, 0.1); }
  button:active:not(:disabled) { transform: translateY(1px); }
  button:focus-visible, .search:focus-visible { outline: 2px solid var(--bt-yellow); outline-offset: 2px; }
  button:disabled { cursor: default; opacity: 0.45; box-shadow: none; }
  .kill { color: var(--bt-red); box-shadow: 0 0 6px rgba(255, 90, 110, 0.3); }
  .kill.sure { background: var(--bt-red); color: var(--bt-bg); }

  .banner { text-align: center; padding: 28px 16px 12px; }
  .banner .big { font-size: clamp(16px, 4.5vw, 28px); line-height: 1.4; }
  .banner .note { margin-top: 12px; font-size: 14px; color: var(--bt-dim); letter-spacing: 0.08em; }
  .banner.coin .big { color: var(--bt-yellow); text-shadow: 0 0 10px rgba(255, 233, 77, 0.7); animation: blink 1.2s steps(2) infinite; }
  .banner.locked .big { color: var(--bt-red); text-shadow: 0 0 10px rgba(255, 90, 110, 0.7); }
  .banner.offline .big { color: var(--bt-dim); text-shadow: 0 0 8px rgba(169, 166, 204, 0.5); }
  .banner:empty, .hidden { display: none !important; }

  .browse { padding: 0 16px 32px; }
  .nav { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 16px 0 8px; }
  .where { flex: 1 1 auto; min-width: 0; font-size: 13px; line-height: 1.5; color: var(--bt-pink);
           text-shadow: 0 0 6px rgba(255, 82, 218, 0.6); }
  .tabs { display: flex; gap: 8px; }
  .tabs button[aria-pressed="true"] { background: var(--bt-cyan); color: var(--bt-bg); }
  .search {
    min-height: 44px; box-sizing: border-box; width: 100%; margin: 4px 0 16px; padding: 10px 12px;
    font: inherit; font-size: 16px; color: var(--bt-fg); background: var(--bt-panel);
    border: 2px solid var(--bt-edge); border-radius: 0;
  }
  .search::placeholder { color: var(--bt-dim); font-family: var(--bt-pixel); font-size: 10px; }
  .status { text-align: center; padding: 32px 8px; }
  .status .big { font-size: 13px; color: var(--bt-dim); }
  .status .note { margin-top: 10px; font-size: 13px; color: var(--bt-dim); }

  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)); gap: 14px; }
  .tile {
    display: flex; flex-direction: column; gap: 8px; text-align: left; padding: 8px; font-size: 13px;
    color: var(--bt-fg); background: var(--bt-panel); border: 2px solid var(--bt-edge);
    box-shadow: none; content-visibility: auto; contain-intrinsic-size: 132px 220px;
  }
  .tile:hover:not(:disabled) { border-color: var(--bt-cyan); background: var(--bt-panel);
                               box-shadow: 0 0 10px rgba(62, 244, 255, 0.35); }
  .tile.now { border-color: var(--bt-pink); box-shadow: 0 0 12px rgba(255, 82, 218, 0.5); }
  .tn { font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; text-transform: none;
        letter-spacing: 0; line-height: 1.3; overflow: hidden; text-overflow: ellipsis; display: -webkit-box;
        -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  .dir .tn { font-size: 10px; line-height: 1.6; color: var(--bt-cyan); }
  .badge { font-size: 8px; color: var(--bt-pink); animation: blink 1.2s steps(2) infinite; }
  .art {
    position: relative; aspect-ratio: 3 / 4; display: flex; align-items: center; justify-content: center;
    overflow: hidden; background: #000;
  }
  .art img { width: 100%; height: 100%; object-fit: contain; }
  .art:not(.loaded) img { display: none; }
  .art.loaded .nosignal { display: none; }
  .art:not(.loaded) {  /* static, until the cover comes */
    background:
      repeating-linear-gradient(0deg, rgba(255, 255, 255, 0.05) 0 1px, transparent 1px 3px),
      repeating-linear-gradient(90deg, rgba(255, 255, 255, 0.03) 0 1px, transparent 1px 4px), #0b0a14;
  }
  .nosignal { font-size: 8px; color: var(--bt-dim); opacity: 0; }
  .art.none .nosignal { opacity: 1; }
  .dir .art { aspect-ratio: auto; min-height: 72px; font-size: 28px; color: var(--bt-yellow);
              text-shadow: 0 0 8px rgba(255, 233, 77, 0.6); }
  .dir.system .art { display: none; }
  .dir.system { justify-content: center; min-height: 96px; }

  .sheet {
    position: fixed; inset: 0; z-index: 5; display: flex; align-items: center; justify-content: center;
    padding: 16px; background: rgba(0, 0, 0, 0.78);
  }
  .sheet .panel {
    width: min(100%, 420px); max-height: 100%; overflow: auto; box-sizing: border-box;
    display: flex; flex-direction: column; gap: 12px; padding: 20px; text-align: center;
    background: var(--bt-panel); border: 2px solid var(--bt-cyan);
    box-shadow: 0 0 24px rgba(62, 244, 255, 0.35);
  }
  .sheet .art { width: min(60vw, 200px); margin: 0 auto; border: 2px solid var(--bt-edge); }
  .stitle { font-size: 13px; line-height: 1.6; color: var(--bt-cyan); text-shadow: 0 0 6px rgba(62, 244, 255, 0.6); }
  .ssystem { color: var(--bt-dim); }
  .swarn { color: var(--bt-yellow); font-weight: 600; }
  .smsg { color: var(--bt-red); }
  .swarn:empty, .smsg:empty { display: none; }
  .sbtns { display: flex; flex-direction: column; gap: 10px; margin-top: 4px; }
  .start { color: var(--bt-yellow); font-size: 12px; padding: 14px; box-shadow: 0 0 10px rgba(255, 233, 77, 0.4); }
  .start:not(:disabled) { animation: pulse 1.6s ease-in-out infinite; }
  .cancel { color: var(--bt-dim); box-shadow: none; }

  @keyframes blink { 50% { opacity: 0.25; } }
  @keyframes pulse { 50% { box-shadow: 0 0 18px rgba(255, 233, 77, 0.75); } }

  @media (max-width: 600px) {
    .bar { padding: 8px 12px; gap: 10px; }
    .cover { width: 44px; height: 44px; }
    .ctl { flex: 1 1 100%; }
    .ctl button { flex: 1; }
    .browse { padding: 0 12px 24px; }
    .grid { grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 10px; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important; }
  }
`;

class BatoceraCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entities || !config.entities.player) {
      throw new Error("batocera-card: entities.player required");
    }
    this._config = config;
    this._built = false;
  }

  getCardSize() {
    return 12;
  }

  connectedCallback() {
    // Back on the page: the clock ticks again, the tiles' covers come again.
    if (!this._built || !this._hass) return;
    this._renderGrid();
    this._update();
  }

  disconnectedCallback() {
    clearInterval(this._ticker);
    this._ticker = undefined;
    clearTimeout(this._sureTimer);
    clearTimeout(this._startTimer);
    if (this._observer) this._observer.disconnect();
    this._observer = undefined;
    this._queue = [];
    for (const src of (this._blobs || new Map()).values()) URL.revokeObjectURL(src);
    if (this._blobs) this._blobs.clear();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    if (this._control === undefined && !this._asking) this._askAccess();
    this._update();
  }

  // --- reading the entities ------------------------------------------

  _state(role) {
    const id = this._config.entities[role];
    return id ? this._hass.states[id] : undefined;
  }

  _player() {
    const s = this._state("player");
    return { state: s ? s.state : "unavailable", attr: (s && s.attributes) || {} };
  }

  // May this user launch, pause and stop? The services check again.
  _askAccess() {
    if (!this._config.entry_id || !this._hass.callWS) {
      this._control = false;
      return;
    }
    this._asking = true;
    Promise.resolve(this._hass.callWS({ type: "batocera/access", entry_id: this._config.entry_id }))
      .then((r) => { this._control = !!(r && r.control); })
      .catch(() => { this._control = false; })
      .finally(() => {
        this._asking = false;
        this._update();
      });
  }

  _call(service, data = {}) {
    // The frontend shows a failed call's error itself.
    return Promise.resolve(
      this._hass.callService("media_player", service, { entity_id: this._config.entities.player, ...data }),
    );
  }

  // --- the page -------------------------------------------------------

  _build() {
    loadFont();
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>${STYLE}</style>
      <div class="wrap">
        <header class="bar">
          <div class="cover"><img alt=""></div>
          <div class="now">
            <div class="title"></div>
            <div class="sub"></div>
          </div>
          <div class="clock"><div class="label"></div><div class="left"></div></div>
          <div class="ctl">
            <button class="pause" type="button"></button>
            <button class="kill" type="button"></button>
          </div>
        </header>
        <div class="banner"><div class="big"></div><div class="note"></div></div>
        <section class="browse">
          <div class="nav">
            <button class="back" type="button">${STRINGS.back}</button>
            <div class="where"></div>
            <div class="tabs">${TABS.map(([kind, label]) =>
              `<button type="button" data-kind="${kind}">${STRINGS[label]}</button>`).join("")}</div>
          </div>
          <input class="search" type="search" placeholder="${STRINGS.search}" aria-label="${STRINGS.search}">
          <div class="status"><div class="big"></div><div class="note"></div></div>
          <div class="grid"></div>
        </section>
        <div class="sheet hidden" role="dialog" aria-modal="true">
          <div class="panel">
            <div class="art"><span class="nosignal">${STRINGS.noSignal}</span><img alt=""></div>
            <div class="stitle"></div>
            <div class="ssystem"></div>
            <div class="swarn"></div>
            <div class="smsg"></div>
            <div class="sbtns">
              <button class="start" type="button">${STRINGS.pressStart}</button>
              <button class="cancel" type="button">${STRINGS.cancel}</button>
            </div>
          </div>
        </div>
      </div>`;
    const $ = (sel) => root.querySelector(sel);
    this.$ = {
      wrap: $(".wrap"), cover: $(".cover"), img: $(".cover img"), title: $(".title"), sub: $(".sub"),
      clock: $(".clock"), label: $(".clock .label"), left: $(".clock .left"), ctl: $(".ctl"),
      pause: $(".pause"), kill: $(".kill"), banner: $(".banner"), big: $(".banner .big"),
      note: $(".banner .note"), back: $(".back"), where: $(".where"), tabs: $(".tabs"),
      search: $(".search"), status: $(".status"), statusBig: $(".status .big"),
      statusNote: $(".status .note"), grid: $(".grid"), sheet: $(".sheet"),
      sart: $(".sheet .art"), simg: $(".sheet .art img"), stitle: $(".stitle"),
      ssystem: $(".ssystem"), swarn: $(".swarn"), smsg: $(".smsg"), start: $(".start"),
      cancel: $(".cancel"),
    };
    this.$.pause.addEventListener("click", () => this._pause());
    this.$.kill.addEventListener("click", () => this._kill());
    this.$.back.addEventListener("click", () => this._back());
    this.$.tabs.addEventListener("click", (e) => {
      const tab = e.target.closest("button");
      if (tab) this._tab(tab.dataset.kind);
    });
    this.$.search.addEventListener("input", () => this._renderGrid());
    this.$.grid.addEventListener("click", (e) => {
      const tile = e.target.closest(".tile");
      if (!tile) return;
      const item = this._items[Number(tile.dataset.index)];
      if (item && item.can_expand) this._open(item.media_content_id, item.title);
      else if (item && item.can_play) this._pick(item);
    });
    this.$.start.addEventListener("click", () => this._start());
    this.$.cancel.addEventListener("click", () => this._close());
    this.$.sheet.addEventListener("click", (e) => {
      if (e.target === this.$.sheet) this._close();
    });
    this.$.sheet.addEventListener("keydown", (e) => {
      if (e.key === "Escape") this._close();
    });
    this._stack = [{ id: "", title: "" }];
    this._systems = {};  // system name -> its full name, from the root
    this._blobs = new Map();  // cover URL without its token -> blob URL
    this._queue = [];
    this._fetching = 0;
    this._built = true;
  }

  // --- browsing ---------------------------------------------------------

  _here() {
    return this._stack[this._stack.length - 1];
  }

  _open(id, title) {
    this._stack.push({ id, title });
    this.$.search.value = "";
    this._browse();
  }

  _back() {
    if (this._stack.length > 1) this._stack.pop();
    this.$.search.value = "";
    this._browse();
  }

  // Within a system: all its games, its favourites, or those played last.
  _tab(kind) {
    const [, system] = split(this._here().id);
    if (!system) return;
    this._here().id = `${kind}:${system}`;
    this._browse();
  }

  _browse() {
    const here = this._here();
    const seq = (this._seq = (this._seq || 0) + 1);
    this._folder = undefined;
    this._failed = false;
    this._renderGrid();
    Promise.resolve(this._hass.callWS({
      type: "media_player/browse_media", entity_id: this._config.entities.player,
      media_content_id: here.id, media_content_type: "directory",
    }))
      .then((folder) => {
        if (seq !== this._seq) return;
        this._folder = folder;
        if (!here.id) {
          for (const child of folder.children || []) {
            const [kind, system] = split(child.media_content_id);
            if (kind === "system") this._systems[system] = child.title;
          }
        }
      })
      .catch(() => {
        if (seq !== this._seq) return;
        // A system that is gone: back to the top.
        if (this._stack.length > 1) {
          this._stack = [this._stack[0]];
          this._browse();
          return;
        }
        this._failed = true;
      })
      .finally(() => {
        if (seq === this._seq) this._renderGrid();
      });
  }

  // --- the launch sheet -------------------------------------------------

  _pick(item) {
    this._sheet = { item };
    const art = this.$.sart;
    art.classList.remove("loaded", "none");
    this.$.simg.removeAttribute("src");
    if (item.thumbnail) {
      this._cover(art, this.$.simg, item.thumbnail, false);
      this._pump();
    } else {
      art.classList.add("none");
    }
    this._renderSheet();
    this.$.start.focus();
  }

  _close() {
    this._sheet = undefined;
    clearTimeout(this._startTimer);
    this._renderSheet();
  }

  _start() {
    const sheet = this._sheet;
    if (!sheet || sheet.until) return;
    sheet.until = Date.now() + START_MS;
    sheet.message = "";
    clearTimeout(this._startTimer);
    this._startTimer = setTimeout(() => this._renderSheet(), START_MS + 50);
    this._renderSheet();
    this._call("play_media", {
      media_content_id: sheet.item.media_content_id, media_content_type: "game",
    }).catch((err) => {
      if (this._sheet !== sheet) return;
      sheet.until = undefined;
      sheet.message = (err && err.message) || String(err);
      this._renderSheet();
    });
  }

  _renderSheet() {
    const sheet = this._sheet;
    this.$.sheet.classList.toggle("hidden", !sheet);
    if (!sheet) return;
    const { state, attr } = this._player();
    const playing = state === "playing" || state === "paused";
    const id = sheet.item.media_content_id;
    // Started: the box plays it now.
    if (sheet.until && playing && attr.media_content_id === id) {
      this._close();
      return;
    }
    if (sheet.until && Date.now() > sheet.until) {
      sheet.until = undefined;
      sheet.message = STRINGS.didNotStart;
    }
    const lock = this._state("locked");
    const locked = !!lock && lock.state === "on";
    const online = playing || state === "idle";
    const [, system] = split(id);
    const same = playing && attr.media_content_id === id && !sheet.until;
    this.$.stitle.textContent = sheet.item.title;
    this.$.ssystem.textContent = this._systems[system.split(":")[0]] || "";
    this.$.swarn.textContent = same ? STRINGS.alreadyPlaying
      : playing && !sheet.until ? STRINGS.quits(attr.media_title || "") : "";
    let why = "";
    if (!this._control) why = STRINGS.browseOnly;
    else if (!online) why = STRINGS.offline;
    else if (locked) why = STRINGS.lockReasons[lock.attributes.reason] || STRINGS.locked;
    this.$.smsg.textContent = sheet.message || why;
    this.$.start.classList.toggle("hidden", !this._control || same);
    this.$.start.disabled = !!why || !!sheet.until;
    this.$.start.textContent = sheet.until ? STRINGS.loading : STRINGS.pressStart;
  }

  _tile(item, index, playingId) {
    const tile = el("button", item.can_expand ? "tile dir" : "tile game");
    tile.type = "button";
    tile.dataset.index = String(index);
    const art = el("div", "art");
    if (item.can_expand) {
      const [kind] = split(item.media_content_id);
      tile.classList.add(kind);
      art.textContent = { fav: "★", recent: "⟲" }[kind] || "";
      tile.append(art, el("span", "tn", item.title));
      return tile;
    }
    tile.dataset.id = item.media_content_id;
    art.append(el("span", "nosignal", STRINGS.noSignal));
    if (item.thumbnail) {
      const img = el("img");
      img.alt = "";
      art.append(img);
      this._cover(art, img, item.thumbnail);
    } else {
      art.classList.add("none");
    }
    tile.append(art, el("span", "tn", item.title));
    if (item.media_content_id === playingId) {
      tile.classList.add("now");
      tile.append(el("span", "badge", STRINGS.nowPlaying));
    }
    return tile;
  }

  // Covers come through Home Assistant's image proxy, with the user's token:
  // a few at a time, once each, and only for tiles in sight when the
  // browser can tell.
  _cover(art, img, url, lazy = true) {
    const key = url.split("?")[0];
    const known = this._blobs.get(key);
    if (known) {
      img.src = known;
      art.classList.add("loaded");
      return;
    }
    const job = { art, img, url, key };
    if (!lazy || typeof IntersectionObserver === "undefined") {
      this._queue.push(job);  // fetched once the tile is on the page
      return;
    }
    if (!this._observer) {
      this._observer = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          this._observer.unobserve(entry.target);
          this._queue.push(entry.target._job);
        }
        this._pump();
      }, { rootMargin: "300px" });
    }
    art._job = job;
    this._observer.observe(art);
  }

  _pump() {
    while (this._fetching < COVERS_AT_ONCE && this._queue.length) {
      const job = this._queue.shift();
      if (!job.art.isConnected) continue;  // its tile is gone
      const known = this._blobs.get(job.key);
      if (known) {
        job.img.src = known;
        job.art.classList.add("loaded");
        continue;
      }
      if (!this._hass.fetchWithAuth) {  // the token in the URL will do
        job.img.src = job.url;
        job.art.classList.add("loaded");
        continue;
      }
      this._fetching++;
      Promise.resolve(this._hass.fetchWithAuth(job.url))
        .then((response) => (response && response.ok ? response.blob() : null))
        .then((blob) => {
          if (!blob) throw new Error("no cover");
          const src = URL.createObjectURL(blob);
          this._blobs.set(job.key, src);
          job.img.src = src;
          job.art.classList.add("loaded");
        })
        .catch(() => job.art.classList.add("none"))
        .finally(() => {
          this._fetching--;
          this._pump();
        });
    }
  }

  _renderGrid() {
    const here = this._here();
    const [kind, system] = split(here.id);
    const root = !here.id;
    this.$.back.classList.toggle("hidden", root);
    const title = root ? this._config.name || "" : this._folder ? this._folder.title : here.title;
    this.$.where.textContent = title;
    this.$.tabs.classList.toggle("hidden", !system);
    for (const tab of this.$.tabs.children) tab.setAttribute("aria-pressed", String(tab.dataset.kind === kind));

    let items = (this._folder && this._folder.children) || [];
    const query = fold(this.$.search.value.trim());
    const shown = query ? items.filter((item) => fold(item.title).includes(query)) : items;
    let big = "";
    let note = "";
    if (this._failed) [big, note] = [STRINGS.noCatalog, STRINGS.noCatalogNote];
    else if (!this._folder) big = STRINGS.loading;
    else if (root && !items.some((i) => split(i.media_content_id)[0] === "system")) {
      [big, note] = [STRINGS.noCatalog, STRINGS.noCatalogNote];
      items = [];
    } else if (!items.length) big = STRINGS.noGames;
    else if (!shown.length) big = STRINGS.noMatch;
    this.$.statusBig.textContent = big;
    this.$.statusNote.textContent = note;
    this.$.status.classList.toggle("hidden", !big);

    const playing = this._player().attr.media_content_id;
    this._items = items.length ? shown : [];
    // The tiles are made anew: forget the old ones' covers still waiting.
    if (this._observer) this._observer.disconnect();
    this.$.grid.replaceChildren(...this._items.map((item, i) => this._tile(item, i, playing)));
    this._queue = this._queue.filter((job) => job.art.isConnected);
    this._pump();
  }

  _pause() {
    const { state } = this._player();
    this._call(state === "paused" ? "media_play" : "media_pause").catch(() => {});
  }

  _kill() {
    // A game quit by mistake loses what was not saved: tap twice.
    if (!this._sure) {
      this._sure = true;
      clearTimeout(this._sureTimer);
      this._sureTimer = setTimeout(() => {
        this._sure = false;
        this._update();
      }, SURE_MS);
      this._update();
      return;
    }
    this._sure = false;
    clearTimeout(this._sureTimer);
    this._update();
    this._call("media_stop").catch(() => {});
  }

  _update() {
    const { state, attr } = this._player();
    const playing = state === "playing" || state === "paused";
    const online = playing || state === "idle";
    // Read the folder again when the catalog changes or the box comes back,
    // and mark the game that runs now.
    const seen = `${attr.catalog || ""}|${online}`;
    if (seen !== this._seen) {
      this._seen = seen;
      this._browse();
    } else if ((attr.media_content_id || "") !== (this._playingId || "")) {
      this._renderGrid();
    }
    this._playingId = attr.media_content_id;
    const lock = this._state("locked");
    const locked = !!lock && lock.state === "on";

    // The time played goes on between the box's updates.
    if (playing && !this._ticker) this._ticker = setInterval(() => this._update(), 1000);
    if (!playing && this._ticker) {
      clearInterval(this._ticker);
      this._ticker = undefined;
    }

    this.$.wrap.classList.toggle("playing", playing);
    this.$.wrap.classList.toggle("offline", !online);
    this.$.title.textContent = playing ? attr.media_title || STRINGS.noGame : STRINGS.noGame;
    const started = this._state("started");
    const since = started && Date.parse(started.state);
    const sub = [];
    if (playing && attr.source) sub.push(attr.source.toUpperCase());
    if (playing && Number.isFinite(since)) sub.push(clock((Date.now() - since) / 1000));
    if (state === "paused") sub.push(STRINGS.paused);
    this.$.sub.textContent = sub.join(" · ");
    const picture = playing ? attr.entity_picture : undefined;
    if (picture) {
      if (this.$.img.getAttribute("src") !== picture) this.$.img.setAttribute("src", picture);
    } else {
      this.$.img.removeAttribute("src");
    }
    this.$.cover.classList.toggle("hidden", !picture);

    const left = attr.minutes_left;
    this.$.clock.classList.toggle("hidden", !playing);
    this.$.label.textContent = typeof left === "number" ? STRINGS.timeLeft : STRINGS.freePlay;
    this.$.left.textContent = typeof left === "number" ? hm(left) : "";
    this.$.clock.classList.toggle("low", typeof left === "number" && left <= 5);

    // Controls only for those who may use them, and only while a game runs.
    const control = !!this._control && playing;
    this.$.ctl.classList.toggle("hidden", !control);
    const pause = this.$.pause;
    if (state === "paused") {
      pause.textContent = STRINGS.resume;
      pause.disabled = false;
      pause.title = "";
    } else if (attr.pausable === false) {
      pause.textContent = STRINGS.noPause;
      pause.disabled = true;
      pause.title = STRINGS.noPauseTitle;
    } else {
      pause.textContent = attr.pausable === true ? STRINGS.pause : STRINGS.pauseUnsure;
      pause.disabled = false;
      pause.title = attr.pausable === true ? "" : STRINGS.pauseUnsureTitle;
    }
    if (!control) this._sure = false;
    this.$.kill.textContent = this._sure ? STRINGS.killSure : STRINGS.kill;
    this.$.kill.classList.toggle("sure", !!this._sure);

    let big = "";
    let note = "";
    let kind = "";
    if (!online) {
      [big, note, kind] = [STRINGS.offline, STRINGS.offlineNote, "offline"];
    } else if (locked) {
      [big, note, kind] = [STRINGS.locked, STRINGS.lockReasons[lock.attributes.reason] || "", "locked"];
    } else if (!playing) {
      [big, note, kind] = [STRINGS.insertCoin, this._control ? STRINGS.insertCoinNote : STRINGS.browseOnly, "coin"];
    }
    this.$.big.textContent = big;
    this.$.note.textContent = note;
    this.$.banner.className = `banner ${kind}`;
    this.$.banner.classList.toggle("hidden", !big);
    this._renderSheet();
  }
}

// The frontend swaps window.customElements for a scoped-registry polyfill
// while it boots. Loaded before that, the card lands in the native registry
// only, the polyfill never sees it and the page shows "Configuration error".
// Define again, wherever window.customElements points, once the app is up.
const define = () => {
  if (!customElements.get("batocera-card")) customElements.define("batocera-card", BatoceraCard);
};
define();
customElements.whenDefined("home-assistant").then(define);
window.customCards = window.customCards || [];
window.customCards.push({ type: "batocera-card", name: "Batocera", description: "A Batocera box's games." });
