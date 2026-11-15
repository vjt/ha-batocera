// Batocera card: the box's games, full page, and what it is playing.
//
// Served by the integration (see dashboard.py) and dropped into the Batocera
// dashboard, one card per box:
//   type: custom:batocera-card
//   name: Arcade
//   entry_id: <the box's config entry>
//   entities: {player: media_player.arcade, locked: binary_sensor.arcade_locked,
//              started: sensor.arcade_session_start, shutdown: button.arcade_shut_down,
//              power_on: button.arcade_power_on, ...}
// What it shows comes from those entities; what it does goes through the
// media player's services and the power buttons, which check the user's
// rights and the limits again. Missing roles are simply not shown.

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
  killSure: "💣 SURE?",
  shutDown: "⏻ SHUT DOWN",
  shutDownSure: "⏻ SURE?",
  goingOff: "⏻ BYE…",
  powerOn: "⏻ POWER ON",
  waking: "⏻ WAKING…",
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
  filters: { genre: "GENRE", decade: "DECADE", rating: "RATING" },
  any: (what) => `ANY ${what}`,
  decade: (year) => `${year}s`,
  resetFilters: "✖ RESET",
  noFit: "THE FILTERS LEAVE NO GAME",
  nowPlaying: "NOW PLAYING",
  noSignal: "NO SIGNAL",
  screen: "What the TV shows",
  pressStart: "PRESS START",
  cancel: "CANCEL",
  quits: (title) => `QUITS ${title}`,
  alreadyPlaying: "ALREADY PLAYING",
  didNotStart: "IT DID NOT START: TRY AGAIN",
  players: (n) => (n === "1" ? "1 player" : `${n} players`),
  games: (n) => (n === 1 ? "1 GAME" : `${n} GAMES`),
  found: (n, more) => `${n}${more ? "+" : ""} FOUND`,
  inDescription: "IN THE DESCRIPTION",
  played: (n) => (n === 1 ? "Played once" : `Played ${n} times`),
  lastPlayed: (day) => `last ${day}`,
  playTime: (time) => `${time} in all`,
};

// How long the kill and shut down buttons wait for their second tap.
const SURE_MS = 4000;
// How long the power button waits for the box to go off or come back.
const POWER_MS = 120000;
// Covers fetched at once: the box downscales them one by one.
const COVERS_AT_ONCE = 4;
// How long a launch may take before the card stops waiting for it.
const START_MS = 30000;
// How long typing must pause before the box's catalog is searched.
const SEARCH_MS = 250;
// The most games a search answers with (SEARCH_MAX in browse.py).
const SEARCH_MAX = 200;
// The folders of a system: all its games, its favourites, played last.
const TABS = [["system", "all"], ["fav", "favourites"], ["recent", "recent"]];

const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
// A filter's choice, on the label under its see-through select; none chosen,
// its name ("ANY GENRE" does not fit three to a row on a phone).
const showChoice = (select) => {
  select.previousElementSibling.textContent =
    select.value ? select.selectedOptions[0].textContent : STRINGS.filters[select.dataset.filter];
  select.parentElement.classList.toggle("on", Boolean(select.value));  // lit, as a pressed tab
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
// 1h 05m or 25m, for the time a game was played in all.
const duration = (seconds) => {
  const m = Math.max(1, Math.round(seconds / 60));
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${pad(m % 60)}m`;
};
// EmulationStation's local time, "20261102T183000", as that day; undefined
// when it is not one.
const esDay = (text) => {
  const m = /^(\d{4})(\d{2})(\d{2})T/.exec(text || "");
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : undefined;
};
// A node says the parts there are, joined; its text is set only when it
// changes, not to lose how far a box of it was scrolled.
const put = (node, parts, sep = " · ") => {
  const text = parts.filter(Boolean).join(sep);
  if (node.textContent !== text) node.textContent = text;
};
// For search: lower case, without accents.
const fold = (text) => (text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
// "system:snes", "fav:snes" -> ["system", "snes"]; "fav" -> ["fav", ""].
const split = (id) => {
  const at = id.indexOf(":");
  return at < 0 ? [id, ""] : [id.slice(0, at), id.slice(at + 1)];
};
// "system:snes", "fav:snes", "dir:mame:Fighter" -> the system; "fav" -> "".
const systemOf = (id) => split(id)[1].split(":")[0];
// Where filters belong: a system's views and folders to the system, the home
// to "" (every game); the favourites and recently played of all systems have
// none (undefined).
const placeOf = (id) => (id ? systemOf(id) || undefined : "");
// A cover's URL without the user's token, which changes on its own: what is
// left (media_image_id) names the picture.
const coverKey = (url) => {
  const [path, query = ""] = url.split("?");
  const kept = query.split("&").filter((p) => p && !p.startsWith("token="));
  return kept.length ? `${path}?${kept.join("&")}` : path;
};

// The filters narrow a system's games, or every game at the home, by what
// batocera/games tells of them: game id -> {genre, year, rating}.
const FILTERS = ["genre", "decade", "rating"];
// A game as the filters see it: its genre's family ("Platform" for
// "Platform / Run & Jump"), its decade ("1990"), its whole stars out of
// five. Without one ("" or 0), it passes "any" only.
const traits = (fact) => {
  const genre = String(fact.genre || "").split(" / ")[0].trim();
  return {
    genre: genre === "None" ? "" : genre,
    decade: fact.year > 0 ? String(Math.floor(fact.year / 10) * 10) : "",
    stars: fact.rating > 0 ? Math.floor(fact.rating * 5 + 1e-9) : 0,
  };
};
// The items the filters let through: the games that fit all of them, and
// the folders.
const narrow = (items, facts, filters) => items.filter((item) => {
  if (!item.can_play) return true;
  const game = traits(facts[item.media_content_id.split(":")[2]] || {});  // game:<system>:<id>
  return (!filters.genre || game.genre === filters.genre)
    && (!filters.decade || game.decade === filters.decade)
    && (!filters.rating || game.stars >= Number(filters.rating));
});
// What each filter offers, as [value, label]: what the games have, and the
// rating as a minimum, from the most stars a game has down to one.
const offers = (facts) => {
  const genres = new Set();
  const decades = new Set();
  let stars = 0;
  for (const fact of Object.values(facts)) {
    const game = traits(fact);
    if (game.genre) genres.add(game.genre);
    if (game.decade) decades.add(game.decade);
    stars = Math.max(stars, game.stars);
  }
  return {
    genre: [...genres].sort((a, b) => a.localeCompare(b)).map((g) => [g, g]),
    decade: [...decades].sort().map((d) => [d, STRINGS.decade(d)]),
    rating: Array.from({ length: stars }, (_, i) => [String(stars - i), "★".repeat(stars - i)]),
  };
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
  .nosignal, .stitle, .status .big, .dir .tn, .pick, .apart {
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
  button:focus-visible, .search:focus-visible, .pick:has(select:focus-visible) {
    outline: 2px solid var(--bt-yellow); outline-offset: 2px;
  }
  button:disabled { cursor: default; opacity: 0.45; box-shadow: none; }
  .kill { color: var(--bt-red); box-shadow: 0 0 6px rgba(255, 90, 110, 0.3); }
  /* :hover too: on a phone the tapped button stays hovered, and the plain
     hover background (more specific than .kill.sure) hid the red. */
  .kill.sure, .kill.sure:hover:not(:disabled), .power.sure, .power.sure:hover:not(:disabled) {
    background: var(--bt-red); color: var(--bt-bg);
    box-shadow: 0 0 14px rgba(255, 90, 110, 0.9), 0 0 28px rgba(255, 90, 110, 0.45);
  }

  .shot { flex: none; width: 96px; aspect-ratio: 16 / 9; min-width: 0; min-height: 0; padding: 0;
          background: #000; border: 2px solid var(--bt-edge); box-shadow: none; cursor: zoom-in; }
  .shot img { display: block; width: 100%; height: 100%; object-fit: contain; }
  .zoom {
    position: fixed; inset: 0; z-index: 6; display: flex; align-items: center; justify-content: center;
    padding: 16px; background: rgba(0, 0, 0, 0.88); cursor: zoom-out;
  }
  .zoom img { max-width: 100%; max-height: 100%; object-fit: contain; background: #000;
              border: 2px solid var(--bt-cyan); box-shadow: 0 0 24px rgba(62, 244, 255, 0.35); }
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
  .count { margin-left: 10px; font-size: 10px; white-space: nowrap; color: var(--bt-yellow);
           text-shadow: 0 0 6px rgba(255, 233, 77, 0.5); }
  .tabs { display: flex; gap: 8px; }
  .tabs button[aria-pressed="true"] { background: var(--bt-cyan); color: var(--bt-bg); }
  .search {
    min-height: 44px; box-sizing: border-box; width: 100%; margin: 4px 0 16px; padding: 10px 12px;
    font: inherit; font-size: 16px; color: var(--bt-fg); background: var(--bt-panel);
    border: 2px solid var(--bt-edge); border-radius: 0;
  }
  .search::placeholder { color: var(--bt-dim); font-family: var(--bt-pixel); font-size: 10px; }
  .filters { display: flex; flex-wrap: wrap; gap: 8px; margin: 4px 0; }
  /* A filter looks like the buttons. The select itself stays at 16px (smaller,
     and phones zoom in on a tap), see-through, over a label in the pixel font. */
  .pick {
    position: relative; flex: 1 1 120px; min-width: 0; min-height: 44px; box-sizing: border-box;
    display: flex; align-items: center; padding: 10px 26px 10px 10px; font-size: 10px;
    color: var(--bt-cyan); border: 2px solid currentColor;
    box-shadow: 0 0 6px rgba(62, 244, 255, 0.25), inset 0 0 6px rgba(62, 244, 255, 0.15);
  }
  .pick span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pick::after {  /* the arrow */
    content: ""; position: absolute; right: 9px; top: 50%; margin-top: -2px;
    border: 5px solid transparent; border-top-color: currentColor; border-bottom: 0;
  }
  .pick select {
    position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; cursor: pointer;
    font-size: 16px; color-scheme: dark;
  }
  .pick:hover:not(:has(select:disabled)) { background: rgba(62, 244, 255, 0.1); }
  /* The hover rule's :not(), not just :hover: on a phone the tapped filter
     stays hovered, and the plain hover background (more specific than
     .pick.on:hover) left the lit filter dark, its dark text on it. */
  .pick.on, .pick.on:hover:not(:has(select:disabled)) { background: var(--bt-cyan); color: var(--bt-bg); }
  .pick:has(select:disabled) { opacity: 0.45; box-shadow: none; }
  .pick select:disabled { cursor: default; }
  .status { text-align: center; padding: 32px 8px; }
  .status .big { font-size: 13px; color: var(--bt-dim); }
  .status .note { margin-top: 10px; font-size: 13px; color: var(--bt-dim); }

  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)); gap: 14px; }
  /* The games a search found by their description alone: a row of their own. */
  .apart { grid-column: 1 / -1; margin-top: 12px; font-size: 10px; line-height: 1.6; color: var(--bt-pink);
           text-shadow: 0 0 6px rgba(255, 82, 218, 0.6); }
  .apart:first-child { margin-top: 0; }
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
  .ts { font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; text-transform: none;
        letter-spacing: 0; font-size: 11px; color: var(--bt-dim); overflow: hidden; text-overflow: ellipsis;
        white-space: nowrap; }
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
  .dir.system.logo .art { display: flex; aspect-ratio: 2 / 1; min-height: 0; background: transparent; }
  .dir.system.logo .art.none { display: none; }

  /* Under Home Assistant's top bar, never over it; clear of the phone's
     notch and home bar. */
  .sheet {
    position: fixed; inset: 0; top: calc(var(--header-height, 56px) + env(safe-area-inset-top, 0px));
    z-index: 5; display: flex; align-items: center; justify-content: center;
    padding: 16px 16px calc(16px + env(safe-area-inset-bottom, 0px)); background: rgba(0, 0, 0, 0.78);
  }
  .sheet .panel {
    width: min(100%, 420px); max-height: 100%; overflow: auto; box-sizing: border-box;
    display: flex; flex-direction: column; gap: 12px; padding: 20px; text-align: center;
    background: var(--bt-panel); border: 2px solid var(--bt-cyan);
    box-shadow: 0 0 24px rgba(62, 244, 255, 0.35);
  }
  /* What the game is scrolls, when a short screen must; the warnings and
     PRESS START below it stay in sight. */
  .sbody { flex: 0 1 auto; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
  .sheet .panel > :not(.sbody), .sbody > :not(.sdesc) { flex: none; }
  /* The cover gives way on a short screen: on a phone the rest fits under it. */
  .sheet .art { width: min(60vw, 200px, 24vh); width: min(60vw, 200px, 24dvh); margin: 0 auto;
                border: 2px solid var(--bt-edge); }
  .stitle { font-size: 13px; line-height: 1.6; color: var(--bt-cyan); text-shadow: 0 0 6px rgba(62, 244, 255, 0.6); }
  .ssystem { color: var(--bt-dim); }
  .sinfo { display: flex; flex-direction: column; gap: 4px; }
  .sfacts, .scredits, .splayed { font-size: 13px; line-height: 1.4; color: var(--bt-dim); }
  /* As tall as the screen leaves, the rest a scroll away: it gives way
     first, down to a few lines, before the cover and the facts do. */
  .sdesc {
    flex: 0 1 auto; min-height: 4.4em; overflow-y: auto; overscroll-behavior: contain;
    font-size: 14px; line-height: 1.5; text-align: left; white-space: pre-line; overflow-wrap: anywhere;
    color: var(--bt-fg); scrollbar-width: thin; scrollbar-color: var(--bt-edge) transparent;
  }
  .sdesc:focus-visible { outline: 2px solid var(--bt-yellow); outline-offset: 2px; }
  .sinfo > :empty, .sdesc:empty { display: none; }
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
    .shot { width: 72px; }
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
    if (this._built) this._unzoom();  // no stream left open off the page
    clearInterval(this._ticker);
    this._ticker = undefined;
    clearTimeout(this._sureTimer);
    clearTimeout(this._sureOffTimer);
    clearTimeout(this._startTimer);
    this._powered();
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
          <button class="shot hidden" type="button" aria-label="${STRINGS.screen}"><img alt="${STRINGS.screen}"></button>
          <div class="now">
            <div class="title"></div>
            <div class="sub"></div>
          </div>
          <div class="clock"><div class="label"></div><div class="left"></div></div>
          <div class="ctl">
            <button class="pause" type="button"></button>
            <button class="kill" type="button"></button>
            <button class="power" type="button"></button>
          </div>
        </header>
        <div class="banner"><div class="big"></div><div class="note"></div></div>
        <section class="browse">
          <div class="nav">
            <button class="back" type="button">${STRINGS.back}</button>
            <div class="where"><span class="wt"></span><span class="count"></span></div>
            <div class="tabs">${TABS.map(([kind, label]) =>
              `<button type="button" data-kind="${kind}">${STRINGS[label]}</button>`).join("")}</div>
          </div>
          <div class="filters">${FILTERS.map((key) =>
            `<label class="pick"><span></span>
              <select data-filter="${key}" aria-label="${STRINGS.filters[key]}"></select></label>`).join("")}
            <button class="reset" type="button">${STRINGS.resetFilters}</button>
          </div>
          <input class="search" type="search" placeholder="${STRINGS.search}" aria-label="${STRINGS.search}">
          <div class="status"><div class="big"></div><div class="note"></div></div>
          <div class="grid"></div>
        </section>
        <div class="zoom hidden" role="dialog" aria-modal="true" aria-label="${STRINGS.screen}" tabindex="-1">
          <img alt="${STRINGS.screen}">
        </div>
        <div class="sheet hidden" role="dialog" aria-modal="true">
          <div class="panel">
            <div class="sbody">
              <div class="art"><span class="nosignal">${STRINGS.noSignal}</span><img alt=""></div>
              <div class="stitle"></div>
              <div class="sinfo">
                <div class="ssystem"></div>
                <div class="sfacts"></div>
                <div class="scredits"></div>
                <div class="splayed"></div>
              </div>
              <div class="sdesc" tabindex="0"></div>
            </div>
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
      shot: $(".shot"), shotImg: $(".shot img"), zoom: $(".zoom"), zoomImg: $(".zoom img"),
      clock: $(".clock"), label: $(".clock .label"), left: $(".clock .left"), ctl: $(".ctl"),
      pause: $(".pause"), kill: $(".kill"), power: $(".power"), banner: $(".banner"), big: $(".banner .big"),
      note: $(".banner .note"), back: $(".back"), where: $(".where"), wt: $(".where .wt"), count: $(".where .count"), tabs: $(".tabs"),
      filters: $(".filters"), reset: $(".filters .reset"),
      search: $(".search"), status: $(".status"), statusBig: $(".status .big"),
      statusNote: $(".status .note"), grid: $(".grid"), sheet: $(".sheet"),
      sbody: $(".sbody"), sart: $(".sheet .art"), simg: $(".sheet .art img"), stitle: $(".stitle"),
      ssystem: $(".ssystem"), sfacts: $(".sfacts"), scredits: $(".scredits"), splayed: $(".splayed"),
      sdesc: $(".sdesc"), swarn: $(".swarn"), smsg: $(".smsg"), start: $(".start"),
      cancel: $(".cancel"),
    };
    this.$.pause.addEventListener("click", () => this._pause());
    this.$.kill.addEventListener("click", () => this._kill());
    this.$.power.addEventListener("click", () => this._power());
    this.$.back.addEventListener("click", () => this._back());
    this.$.tabs.addEventListener("click", (e) => {
      const tab = e.target.closest("button");
      if (tab) this._tab(tab.dataset.kind);
    });
    this.$.filters.addEventListener("change", (e) => {
      this._filters[e.target.dataset.filter] = e.target.value;
      showChoice(e.target);
      this._refilter();
    });
    // Reset: the filters and the search, both gone at once.
    this.$.reset.addEventListener("click", () => {
      this.$.search.value = "";
      this._dropSearch();
      this._resetFilters();
      this._refilter();
    });
    // At the home, a filter's first use asks for every game's facts.
    for (const type of ["focusin", "pointerdown"]) {
      this.$.filters.addEventListener(type, () => this._ask(this._filterPlace));
    }
    this.$.search.addEventListener("input", () => this._onSearch());
    this.$.shot.addEventListener("click", () => {
      this._zoomed = true;
      this._renderZoom();
      this.$.zoom.focus();
      this._watchLive();
    });
    // The live stream broke (box gone, Home Assistant restarted): the preview.
    this.$.zoomImg.addEventListener("error", () => {
      if (!this._live) return;
      this._live = undefined;
      this._renderZoom();
    });
    this.$.zoom.addEventListener("click", () => this._unzoom());
    this.$.zoom.addEventListener("keydown", (e) => {
      if (e.key === "Escape") this._unzoom();
    });
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
    // While the sheet is open a drag moves only what inside it can still
    // scroll that way: never the page under it, that on iOS a pull down
    // would reload.
    let lastY = 0;
    this.$.sheet.addEventListener("touchstart", (e) => { lastY = e.touches[0].clientY; }, { passive: true });
    this.$.sheet.addEventListener("touchmove", (e) => {
      const y = e.touches[0].clientY;
      const down = y > lastY;
      lastY = y;
      for (let node = e.target; node && node !== this.$.sheet; node = node.parentElement) {
        if (node.scrollHeight <= node.clientHeight || !/auto|scroll/.test(getComputedStyle(node).overflowY)) continue;
        if (down ? node.scrollTop > 0 : node.scrollTop + node.clientHeight < node.scrollHeight - 1) return;
      }
      if (e.cancelable) e.preventDefault();
    }, { passive: false });
    this._stack = [{ id: "", title: "" }];
    this._filters = {};  // filter -> the value chosen, "" for any
    this._filterPlace = null;  // where they are for (see placeOf); none yet
    this._facts = new Map();  // place -> {catalog, games: game id -> facts}
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
  // From one of its folders, the tab is for the whole system.
  _tab(kind) {
    const system = systemOf(this._here().id);
    if (!system) return;
    while (this._stack.length > 2 && split(this._here().id)[0] === "dir") this._stack.pop();
    this._here().id = `${kind}:${system}`;
    this._browse();
  }

  // --- searching --------------------------------------------------------

  // From the top, a system or one of its folders, the box's whole catalog
  // (or the system's, sub-folders included) is searched through Home
  // Assistant: by name, and apart (in_description) the games whose
  // description alone holds the query. Favourites, recently played and what
  // the filters list (all of it, sub-folders included) are searched here, by
  // name.
  _searchRemote() {
    const [kind] = split(this._here().id);
    return (kind === "" || kind === "system" || kind === "dir") && !this._filtering();
  }

  _onSearch() {
    clearTimeout(this._searchTimer);
    const query = this.$.search.value.trim();
    if (query && this._searchRemote()) {
      this._searchTimer = setTimeout(() => this._search(query), SEARCH_MS);
    } else {
      this._dropSearch();
    }
    this._renderGrid();
  }

  // Forget the answers, and any still on their way.
  _dropSearch() {
    clearTimeout(this._searchTimer);
    this._searchSeq = (this._searchSeq || 0) + 1;
    this._found = undefined;
  }

  _search(query) {
    const here = this._here();
    const seq = (this._searchSeq = (this._searchSeq || 0) + 1);
    const msg = { type: "media_player/search_media", entity_id: this._config.entities.player, search_query: query };
    if (here.id) Object.assign(msg, { media_content_type: "directory", media_content_id: here.id });
    Promise.resolve(this._hass.callWS(msg))
      .then((answer) => {
        if (seq !== this._searchSeq) return;
        const { result, in_description: described } = answer || {};
        this._found = { items: result || [], described: described || [] };
      })
      .catch(() => {
        if (seq === this._searchSeq) this._found = { items: null };  // filter here instead
      })
      .finally(() => {
        if (seq === this._searchSeq) this._renderGrid();
      });
  }

  _browse() {
    const here = this._here();
    this._filterFor(placeOf(here.id));
    const id = (this._browsed = this._browseId());
    const seq = (this._seq = (this._seq || 0) + 1);
    const catalog = this._player().attr.catalog;
    this._folder = undefined;
    this._failed = false;
    this._dropSearch();
    const query = this.$.search.value.trim();
    if (query && this._searchRemote()) this._search(query);  // a new catalog, or tab
    // Every game of every system is a big list: kept for its catalog.
    const every = this._every;
    if (id === "all" && every && every.catalog === catalog) this._folder = every.folder;
    this._renderGrid();
    if (this._folder) return;
    Promise.resolve(this._hass.callWS({
      type: "media_player/browse_media", entity_id: this._config.entities.player,
      media_content_id: id, media_content_type: "directory",
    }))
      .then((folder) => {
        if (seq !== this._seq) return;
        this._folder = folder;
        if (id === "all") this._every = { catalog, folder };
        if (!id) {
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

  // --- filtering --------------------------------------------------------

  // The filters belong to a place: a system, through its tabs and folders,
  // or the home. Each has its own, reset when it is left. A place's genre,
  // year and rating by game id are kept per catalog; while a new catalog's
  // are on their way, the old ones serve. A system's are asked as it is
  // entered. The home's, every game's, are many: asked when a filter is
  // first used there, and for a new catalog only while one is set.
  _filterFor(place) {
    if (place !== this._filterPlace) {
      this._filterPlace = place;
      this._resetFilters();
    }
    if (place || this._filtering()) this._ask(place);
  }

  _ask(place) {
    const catalog = this._player().attr.catalog;
    const old = this._facts.get(place);
    if (typeof place !== "string" || !this._config.entry_id || (old && old.catalog === catalog)) return;
    const facts = { catalog, games: old && old.games };
    this._facts.set(place, facts);
    const msg = { type: "batocera/games", entry_id: this._config.entry_id };
    if (place) msg.system = place;
    Promise.resolve(this._hass.callWS(msg))
      .then((games) => {
        if (this._facts.get(place) !== facts) return;
        facts.games = games || {};
        if (place !== this._filterPlace) return;
        this._renderFilters();
        this._refilter();
      }, () => {
        if (this._facts.get(place) !== facts) return;
        this._facts.delete(place);  // no filters, until the next try
        if (place !== this._filterPlace) return;
        this._resetFilters();
        this._refilter();
      });
  }

  // The facts of the place shown, once they came.
  _games() {
    const facts = this._facts.get(this._filterPlace);
    return facts && facts.games;
  }

  _filtering() {
    return !!this._games() && FILTERS.some((key) => this._filters[key]);
  }

  // What to browse: while a filter is set, the home comes as every game of
  // every system (all), a system or folder as one list of its games,
  // sub-folders included (all:<system>[:<folder>]).
  _browseId() {
    const id = this._here().id;
    const [kind, rest] = split(id);
    if (!this._filtering()) return id;
    if (!id) return "all";
    return kind === "system" || kind === "dir" ? `all:${rest}` : id;
  }

  // The filters changed: browse again when that changes what to browse.
  _refilter() {
    if (this._browseId() !== this._browsed) this._browse();
    else this._renderGrid();
  }

  _resetFilters() {
    this._filters = {};
    this._renderFilters();
  }

  // Each filter offers what the place's games have. A value chosen that no
  // game has any more goes back to any. Until the games' facts come (the
  // home's: once a filter is used), a filter says they are loading.
  _renderFilters() {
    const games = this._games();
    const offer = offers(games || {});
    const option = (value, label) => Object.assign(el("option", "", label), { value });
    const loading = () => Object.assign(option("", STRINGS.loading), { disabled: true });
    for (const select of this.$.filters.querySelectorAll("select")) {
      const key = select.dataset.filter;
      const value = offer[key].some(([v]) => v === this._filters[key]) ? this._filters[key] : "";
      if (games) this._filters[key] = value;
      select.replaceChildren(option("", STRINGS.any(STRINGS.filters[key])),
        ...(games ? offer[key].map(([v, label]) => option(v, label)) : [loading()]));
      select.value = value;
      select.disabled = !!games && !offer[key].length;
      showChoice(select);
    }
  }

  // --- the launch sheet -------------------------------------------------

  _pick(item) {
    const sheet = (this._sheet = { item });
    const art = this.$.sart;
    art.classList.remove("loaded", "none");
    this.$.simg.removeAttribute("src");
    if (item.thumbnail) {
      this._cover(art, this.$.simg, item.thumbnail, false);
      this._pump();
    } else {
      art.classList.add("none");
    }
    this._about(sheet);
    this._renderSheet();
    this.$.sbody.scrollTop = 0;
    this.$.sdesc.scrollTop = 0;
    this.$.start.focus();
  }

  // What the sheet tells of the game: the catalog's facts (batocera/games,
  // for this one game) at once, and what the box adds (batocera/info: the
  // description, who made it, the time played in all) once Home Assistant
  // has it. Each is shown when it comes, if the sheet is still this game's;
  // without them (an old box, a busy one) the sheet shows the rest.
  _about(sheet) {
    const entry = this._config.entry_id;
    if (!entry || !this._hass.callWS) return;
    const game = sheet.item.media_content_id.split(":")[2];  // game:<system>:<id>
    const ask = (type, keep) => Promise.resolve(this._hass.callWS({ type, entry_id: entry, game }))
      .then((answer) => {
        if (this._sheet !== sheet || !answer) return;
        keep(answer);
        this._renderSheet();
      })
      .catch(() => {});
    ask("batocera/games", (games) => { sheet.facts = games[game]; });
    ask("batocera/info", (info) => { sheet.info = info; });
  }

  // A day as the user's language writes it, short: "Nov 2, 2026".
  _day(date) {
    const locale = this._hass.locale;
    const options = { year: "numeric", month: "short", day: "numeric" };
    try {
      return date.toLocaleDateString((locale && locale.language) || this._hass.language, options);
    } catch {  // a language the browser does not know
      return date.toLocaleDateString(undefined, options);
    }
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
    // What the game is, from what came; nothing for what did not.
    const facts = sheet.facts || {};
    const info = sheet.info || {};
    const { genre, stars } = traits(facts);
    put(this.$.sfacts, [facts.year > 0 && facts.year, genre && facts.genre,
      facts.players && STRINGS.players(facts.players), stars && "★".repeat(stars)]);
    put(this.$.scredits, [...new Set([info.developer, info.publisher])], " / ");
    const last = esDay(facts.lastplayed);
    put(this.$.splayed, [facts.playcount > 0 && STRINGS.played(facts.playcount),
      last && STRINGS.lastPlayed(this._day(last)),
      info.gametime > 0 && STRINGS.playTime(duration(info.gametime))]);
    put(this.$.sdesc, [info.desc]);
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
      if (kind === "system" && item.thumbnail) {  // the system's logo
        tile.classList.add("logo");
        const img = el("img");
        img.alt = "";
        art.append(img);
        this._cover(art, img, item.thumbnail);
      } else {
        art.textContent = { fav: "★", recent: "⟲", dir: "▤" }[kind] || "";
      }
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
    // At the home the games come from every system: each says its own.
    if (!this._here().id) {
      const system = systemOf(item.media_content_id);
      tile.append(el("span", "ts", this._systems[system] || system));
    }
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
    const key = coverKey(url);
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
          // The picture this one replaces (a system's new logo) goes.
          const path = job.key.split("?")[0];
          for (const [key, old] of this._blobs) {
            if (key !== job.key && key.split("?")[0] === path) {
              URL.revokeObjectURL(old);
              this._blobs.delete(key);
            }
          }
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
    this.$.wt.textContent = title;
    this.$.tabs.classList.toggle("hidden", !system);
    const pressed = kind === "dir" ? "system" : kind;
    for (const tab of this.$.tabs.children) tab.setAttribute("aria-pressed", String(tab.dataset.kind === pressed));

    // The filters, on what is listed, matched by game id.
    const games = this._games();
    const filtering = this._filtering();

    const items = (this._folder && this._folder.children) || [];
    const text = this.$.search.value.trim();
    const remote = !!text && this._searchRemote() && !(this._found && this._found.items === null);
    const query = fold(text);
    const found = remote ? (this._found && this._found.items) || []
      : query ? items.filter((item) => fold(item.title).includes(query)) : items;
    const shown = filtering ? narrow(found, games, this._filters) : found;
    // Found by their description alone: listed apart, after the names.
    const described = (remote && this._found && this._found.described) || [];
    const playable = (list) => list.some((item) => item.can_play);
    let big = "";
    let note = "";
    if (this._failed) [big, note] = [STRINGS.noCatalog, STRINGS.noCatalogNote];
    else if (!this._folder) big = STRINGS.loading;
    else if (!this._browsed && !items.some((i) => split(i.media_content_id)[0] === "system")) {
      [big, note] = [STRINGS.noCatalog, STRINGS.noCatalogNote];
    } else if (remote && !this._found) big = STRINGS.loading;
    else if (!items.length && !remote) big = STRINGS.noGames;
    else if (playable(found) && !playable(shown)) [big, note] = [STRINGS.noMatch, STRINGS.noFit];
    else if (!shown.length && !described.length) big = STRINGS.noMatch;
    this.$.statusBig.textContent = big;
    this.$.statusNote.textContent = note;
    this.$.status.classList.toggle("hidden", !big);
    // How many: what a search (by name and by description, both counted) or
    // the filters found; else the games listed or, at the home or a
    // system's top once their facts came, all the games there, folders down.
    const listed = shown.filter((item) => item.can_play).length + described.length;
    let count = "";
    if (text || filtering) {
      const more = remote && (found.length >= SEARCH_MAX || described.length >= SEARCH_MAX);
      if (!big) count = STRINGS.found(listed, more);
    } else if (games && (root || kind === "system")) count = STRINGS.games(Object.keys(games).length);
    else if (root && this._player().attr.games > 0) count = STRINGS.games(this._player().attr.games);
    else if (listed) count = STRINGS.games(listed);
    this.$.count.textContent = count;
    // Shown once the place's games are known; at the home, before, as using
    // one asks for them.
    const home = this._filterPlace === "" && !!this._config.entry_id;
    this.$.filters.classList.toggle("hidden", !(games || home) || big === STRINGS.noCatalog);
    this.$.reset.classList.toggle("hidden", !(filtering || text));

    const playing = this._player().attr.media_content_id;
    this._items = big && big !== STRINGS.noMatch ? [] : [...shown, ...described];
    // The tiles are made anew: forget the old ones' covers still waiting.
    if (this._observer) this._observer.disconnect();
    const tiles = this._items.map((item, i) => this._tile(item, i, playing));
    if (described.length && tiles.length) {
      const apart = el("div", "apart", STRINGS.inDescription);
      apart.setAttribute("role", "heading");
      apart.setAttribute("aria-level", "2");
      tiles.splice(shown.length, 0, apart);
    }
    this.$.grid.replaceChildren(...tiles);
    this._queue = this._queue.filter((job) => job.art.isConnected);
    this._pump();
  }

  // The screenshot, as big as the page allows: until a tap, Escape, or the
  // screenshot goes away. Live while open, when the stream can be had.
  _renderZoom() {
    const shot = this.$.shotImg.getAttribute("src");
    const open = !!this._zoomed && !!shot;
    const src = this._live || shot;
    if (open && this.$.zoomImg.getAttribute("src") !== src) this.$.zoomImg.setAttribute("src", src);
    if (!open) this.$.zoomImg.removeAttribute("src");
    this.$.zoom.classList.toggle("hidden", !open);
  }

  _unzoom() {
    this._zoomed = false;
    this._dropLive();
    this._renderZoom();
  }

  // The live screen: an MJPEG stream an <img> plays by itself. Its path is
  // signed (an <img> sends no auth header) each time the zoom opens; while it
  // is open Home Assistant keeps the box sending frames, and dropping the src
  // closes it, which stops them.
  _watchLive() {
    const id = this._config.entry_id;
    if (!id || !this._hass || !this._hass.callWS) return;
    const ask = (this._liveAsk = (this._liveAsk || 0) + 1);
    Promise.resolve(this._hass.callWS({ type: "auth/sign_path", path: `/api/batocera/${id}/live`, expires: 60 }))
      .then((signed) => {
        if (ask !== this._liveAsk || !this._zoomed || !signed || !signed.path) return;
        this._live = signed.path;
        this._renderZoom();
      })
      .catch(() => {});  // not signed: the preview, as before
  }

  _dropLive() {
    this._live = undefined;
    this._liveAsk = (this._liveAsk || 0) + 1;  // an answer on its way is late
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

  // Shut down takes two taps, as kill; power on, one. Either then waits for
  // the box to go off or come back, or for POWER_MS.
  _power() {
    const kind = this._powerKind;
    if (!kind || this._powering) return;
    if (kind === "off" && !this._sureOff) {
      this._sureOff = true;
      clearTimeout(this._sureOffTimer);
      this._sureOffTimer = setTimeout(() => {
        this._sureOff = false;
        this._update();
      }, SURE_MS);
      this._update();
      return;
    }
    this._sureOff = false;
    clearTimeout(this._sureOffTimer);
    this._powering = kind;
    this._powerTimer = setTimeout(() => this._powered(true), POWER_MS);
    this._update();
    const entity = this._config.entities[kind === "off" ? "shutdown" : "power_on"];
    // The frontend shows a refusal itself: the button comes back.
    Promise.resolve(this._hass.callService("button", "press", { entity_id: entity }))
      .catch(() => this._powered(true));
  }

  _powered(render = false) {
    this._powering = undefined;
    clearTimeout(this._powerTimer);
    if (render) this._update();
  }

  _update() {
    const { state, attr } = this._player();
    const playing = state === "playing" || state === "paused";
    const online = playing || state === "idle";
    // Read the folder again when the catalog or a logo changes or the box
    // comes back, and mark the game that runs now.
    const seen = `${attr.catalog || ""}|${attr.logos || ""}|${online}`;
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

    // What the TV shows, menus too. The image entity's state is when the
    // screenshot was taken: in the URL, so a new one is not the cached one.
    const screen = this._state("screen");
    const fresh = online && screen && !["unknown", "unavailable"].includes(screen.state);
    const base = fresh && screen.attributes.entity_picture;
    const shot = base && `${base}${base.includes("?") ? "&" : "?"}t=${encodeURIComponent(screen.state)}`;
    if (shot && this.$.shotImg.getAttribute("src") !== shot) this.$.shotImg.setAttribute("src", shot);
    if (!shot) this.$.shotImg.removeAttribute("src");
    this.$.shot.classList.toggle("hidden", !shot);
    if (!shot) { this._zoomed = false; this._dropLive(); }
    this._renderZoom();

    const left = attr.minutes_left;
    this.$.clock.classList.toggle("hidden", !playing);
    this.$.label.textContent = typeof left === "number" ? STRINGS.timeLeft : STRINGS.freePlay;
    this.$.left.textContent = typeof left === "number" ? hm(left) : "";
    this.$.clock.classList.toggle("low", typeof left === "number" && left <= 5);

    // Controls only for those who may use them: pause and kill while a game
    // runs; power while there is a button for it, to shut the box down while
    // it is on or to wake it while it is off (Home Assistant offers one at a
    // time, and Power on only once it knows how to wake the box).
    const control = !!this._control && playing;
    const usable = (role) => {
      const button = this._state(role);
      return !!button && button.state !== "unavailable";
    };
    if (this._powering === (online ? "on" : "off")) this._powered();  // done
    let power = "";
    if (this._control) power = (online ? usable("shutdown") && "off" : usable("power_on") && "on") || "";
    this._powerKind = power;
    this.$.ctl.classList.toggle("hidden", !control && !power);
    this.$.pause.classList.toggle("hidden", !control);
    this.$.kill.classList.toggle("hidden", !control);
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
    if (power !== "off") this._sureOff = false;
    const waiting = !!power && this._powering === power;
    let label = power === "on" ? STRINGS.powerOn : this._sureOff ? STRINGS.shutDownSure : STRINGS.shutDown;
    if (waiting) label = power === "on" ? STRINGS.waking : STRINGS.goingOff;
    this.$.power.classList.toggle("hidden", !power);
    this.$.power.textContent = label;
    this.$.power.disabled = waiting;
    this.$.power.classList.toggle("sure", !!this._sureOff);

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
