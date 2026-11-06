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
};

// How long the kill button waits for its second tap.
const SURE_MS = 4000;

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
      </div>`;
    const $ = (sel) => root.querySelector(sel);
    this.$ = {
      wrap: $(".wrap"), cover: $(".cover"), img: $(".cover img"), title: $(".title"), sub: $(".sub"),
      clock: $(".clock"), label: $(".clock .label"), left: $(".clock .left"), ctl: $(".ctl"),
      pause: $(".pause"), kill: $(".kill"), banner: $(".banner"), big: $(".banner .big"),
      note: $(".banner .note"),
    };
    this.$.pause.addEventListener("click", () => this._pause());
    this.$.kill.addEventListener("click", () => this._kill());
    this._built = true;
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
