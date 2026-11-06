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
};

// How long the kill button waits for its second tap.
const SURE_MS = 4000;
// Covers fetched at once: the box downscales them one by one.
const COVERS_AT_ONCE = 4;
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

const STYLE = `
  :host { display: block; height: 100%; }
  .wrap { min-height: calc(100vh - var(--header-height, 56px)); box-sizing: border-box; }
  .bar { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap;
         align-items: center; gap: 12px; padding: 10px 16px; }
  .cover { width: 56px; height: 56px; flex: none; }
  .cover img { width: 100%; height: 100%; object-fit: contain; }
  .now { flex: 1 1 160px; min-width: 0; }
  .title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ctl { display: flex; gap: 8px; }
  button { min-height: 44px; min-width: 44px; cursor: pointer; font: inherit; }
  button:disabled { cursor: default; opacity: 0.5; }
  .banner { text-align: center; padding: 16px; }
  .banner:empty, .hidden { display: none !important; }
  .browse { padding: 0 16px 24px; }
  .nav { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 8px 0; }
  .where { flex: 1 1 auto; min-width: 0; }
  .tabs { display: flex; gap: 8px; }
  .tabs button[aria-pressed="true"] { outline: 2px solid currentColor; }
  .search { min-height: 44px; box-sizing: border-box; width: 100%; font: inherit; margin: 4px 0 12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)); gap: 12px; }
  .tile { display: flex; flex-direction: column; gap: 6px; text-align: left; padding: 8px;
          content-visibility: auto; contain-intrinsic-size: 132px 200px; }
  .art { aspect-ratio: 3 / 4; display: flex; align-items: center; justify-content: center; overflow: hidden; }
  .art img { width: 100%; height: 100%; object-fit: contain; }
  .art:not(.loaded) img { display: none; }
  .tn { overflow: hidden; text-overflow: ellipsis; display: -webkit-box;
        -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  .dir .art { aspect-ratio: auto; min-height: 64px; }
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

  disconnectedCallback() {
    clearInterval(this._ticker);
    this._ticker = undefined;
    clearTimeout(this._sureTimer);
    if (this._observer) this._observer.disconnect();
    this._observer = undefined;
    this._queue = [];
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
      </div>`;
    const $ = (sel) => root.querySelector(sel);
    this.$ = {
      wrap: $(".wrap"), cover: $(".cover"), img: $(".cover img"), title: $(".title"), sub: $(".sub"),
      clock: $(".clock"), label: $(".clock .label"), left: $(".clock .left"), ctl: $(".ctl"),
      pause: $(".pause"), kill: $(".kill"), banner: $(".banner"), big: $(".banner .big"),
      note: $(".banner .note"), back: $(".back"), where: $(".where"), tabs: $(".tabs"),
      search: $(".search"), status: $(".status"), statusBig: $(".status .big"),
      statusNote: $(".status .note"), grid: $(".grid"),
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

  _pick() {}

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
  _cover(art, img, url) {
    const key = url.split("?")[0];
    const known = this._blobs.get(key);
    if (known) {
      img.src = known;
      art.classList.add("loaded");
      return;
    }
    const job = { art, img, url, key };
    if (typeof IntersectionObserver === "undefined") {
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
