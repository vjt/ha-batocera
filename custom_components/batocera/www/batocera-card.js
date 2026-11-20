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
  sizeUnits: ["B", "kB", "MB", "GB", "TB"],
  // The high scores.
  scores: "HIGH SCORES",
  timePlayed: "TIME PLAYED",
  today: "TODAY",
  allTime: "ALL TIME",
  gamesPlayed: "GAMES PLAYED",
  consolesGames: (consoles, games) =>
    `${consoles === 1 ? "1 console" : `${consoles} consoles`} · ${games === 1 ? "1 game" : `${games} games`}`,
  sinceFirst: "since the 1st",
  since: (day) => `since ${day}`,
  fromNow: "from now on",
  asCounted: "as EmulationStation counts it",
  ofCatalog: (n) => `of ${n} in the catalog`,
  needsUpdate: "update the box to know",
  dash: "—",
  logStarts: "THE LOG STARTS NOW: TODAY AND THIS MONTH COUNT FROM HERE ON",
  last30: "LAST 30 DAYS",
  byConsole: "BY CONSOLE",
  asTable: "AS A TABLE",
  day: "Day",
  other: "OTHER",
  total: "TOTAL",
  top: (range, n) => `${range} · TOP ${n}`,
  consoles: (range) => `CONSOLES · ${range}`,
  nowBadge: "▶ NOW",
  sessions: (n) => (n === 1 ? "1 session" : `${n} sessions`),
  plays: (n) => (n === 1 ? "1 play" : `${n} plays`),
  gamesOf: (n) => (n === 1 ? "1 game" : `${n} games`),
  playedOf: (played, owned) => `${played} of ${owned} games played`,
  topGame: (name) => `top: ${name}`,
  nonePlayed: "NOTHING PLAYED YET",
  allNeedsUpdate: "ALL TIME NEEDS THE BOX UPDATED",
  noScores: "NO SCORES",
  noScoresNote: "HOME ASSISTANT DID NOT ANSWER",
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
// The high scores: a place of the card's own, not of the media browser (whose
// own page would list it empty). Its tile, at the home, after the favourites
// and recently played.
const SCORES = "scores";
const SCORES_TILE = { media_content_id: SCORES, title: STRINGS.scores, can_expand: true, can_play: false };
// How often the high scores, while open, are asked again: the running game moves.
const STATS_MS = 60000;
// How many games each range's board lists (as Home Assistant answers).
const BOARD = { today: 10, month: 30, all: 30 };
// The consoles with most time over the 30 days get a colour each, in this
// order; the rest share OTHER. The card's neon, toned down for fills: at full
// brightness, neighbours in a stack blur together for colour-blind eyes.
const PALETTE = ["#119eac", "#d8664c", "#6a7df0", "#ad8a12", "#cf3fb3"];
const OTHER = "#4a4670";

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
// 850 MB, 2.3 GB or 25 GB, for the space a game takes: SI units (1000, as
// disks are sold), one decimal under 10, none from 10 up.
const bytes = (n) => {
  const units = STRINGS.sizeUnits;
  for (let i = 0, v = n; ; i++, v /= 1000) {
    const shown = i && v < 9.95 ? v.toFixed(1) : String(Math.round(v));
    if (Number(shown) < 1000 || i === units.length - 1) return `${shown} ${units[i]}`;
  }
};
// 25m or 1h 05m, for the time played in a range; 0m for none.
const span = (seconds) => {
  const m = Math.round(Math.max(0, seconds || 0) / 60);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${pad(m % 60)}m`;
};
// 182H for all time; under an hour, as span.
const hours = (seconds) => (seconds >= 3600 ? `${Math.round(seconds / 3600)}H` : span(seconds));
// 1ST, 2ND, 3RD, 4TH, 11TH, 21ST.
const ordinal = (n) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? "TH" : ["TH", "ST", "ND", "RD"][n % 10] || "TH"}`;
// Home Assistant's "2026-11-20" as that local day; undefined when it is not one.
const isoDay = (text) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text || "");
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : undefined;
};
// A console's colour, as a small square.
const swatch = (colour) => {
  const node = el("span", "sw");
  node.style.background = colour;
  return node;
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
  .nosignal, .stitle, .status .big, .dir .tn, .pick, .apart,
  .snote, .kpi .k, .kpi .v, .h2, .ax, .legend, .tip b, summary, .rank, .rt, .cname, .ctime, .snone {
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
  /* A game's year and stars, at the foot of its tile. */
  .tm { display: flex; justify-content: space-between; gap: 6px; margin-top: auto;
        font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; text-transform: none;
        letter-spacing: 0; font-size: 11px; color: var(--bt-dim); white-space: nowrap; }
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
  /* What the game is scrolls as one, the cover going off the top under a
     long description; the warnings and PRESS START below it stay in sight. */
  .sbody {
    flex: 0 1 auto; min-height: 0; overflow-y: auto; overscroll-behavior: contain;
    display: flex; flex-direction: column; gap: 12px;
    scrollbar-width: thin; scrollbar-color: var(--bt-edge) transparent;
  }
  .sbody:focus-visible { outline: 2px solid var(--bt-yellow); outline-offset: 2px; }
  .sheet .panel > :not(.sbody), .sbody > * { flex: none; }
  /* The cover gives way on a short screen: on a phone the rest fits under it. */
  .sheet .art { width: min(60vw, 200px, 24vh); width: min(60vw, 200px, 24dvh); margin: 0 auto;
                border: 2px solid var(--bt-edge); }
  .stitle { font-size: 13px; line-height: 1.6; color: var(--bt-cyan); text-shadow: 0 0 6px rgba(62, 244, 255, 0.6); }
  .ssystem { color: var(--bt-dim); }
  .sinfo { display: flex; flex-direction: column; gap: 4px; }
  .sfacts, .scredits, .splayed { font-size: 13px; line-height: 1.4; color: var(--bt-dim); }
  .sdesc {
    font-size: 14px; line-height: 1.5; text-align: left; white-space: pre-line; overflow-wrap: anywhere;
    color: var(--bt-fg);
  }
  .sinfo > :empty, .sdesc:empty { display: none; }
  .swarn { color: var(--bt-yellow); font-weight: 600; }
  .smsg { color: var(--bt-red); }
  .swarn:empty, .smsg:empty { display: none; }
  .sbtns { display: flex; flex-direction: column; gap: 10px; margin-top: 4px; }
  .start { color: var(--bt-yellow); font-size: 12px; padding: 14px; box-shadow: 0 0 10px rgba(255, 233, 77, 0.4); }
  .start:not(:disabled) { animation: pulse 1.6s ease-in-out infinite; }
  .cancel { color: var(--bt-dim); box-shadow: none; }

  /* The high scores: their tile at the home, then their page. */
  .tile.scores { border-color: var(--bt-pink); box-shadow: 0 0 12px rgba(255, 82, 218, 0.5); }
  .minibars { display: flex; align-items: flex-end; gap: 4px; height: 30px; }
  .minibars i { width: 7px; background: var(--bt-yellow); box-shadow: 0 0 8px rgba(255, 233, 77, 0.6); }
  .snote { margin: 4px 0 12px; font-size: 8px; line-height: 1.8; color: var(--bt-yellow); }
  .snote:empty { display: none; }
  .h2 { margin: 22px 0 10px; font-size: 10px; line-height: 1.6; color: var(--bt-pink);
        text-shadow: 0 0 6px rgba(255, 82, 218, 0.6); }
  .h2 small { margin-left: 6px; font-size: 8px; color: var(--bt-dim); text-shadow: none; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
  .kpi { padding: 10px; background: var(--bt-panel); border: 2px solid var(--bt-edge); }
  .kpi .k { font-size: 8px; color: var(--bt-dim); }
  .kpi .v { margin-top: 8px; font-size: 15px; white-space: nowrap; color: var(--bt-yellow);
            text-shadow: 0 0 6px rgba(255, 233, 77, 0.6); }
  .kpi .n { margin-top: 6px; font-size: 11px; color: var(--bt-dim); }
  .chart { position: relative; padding: 10px 10px 8px 34px; background: var(--bt-panel);
           border: 2px solid var(--bt-edge); }
  .plot { position: relative; height: 140px; display: flex; align-items: flex-end; gap: 2px; }
  .gl { position: absolute; left: 0; right: 0; border-top: 1px dashed rgba(169, 166, 204, 0.18);
        pointer-events: none; }
  .ax { position: absolute; left: -32px; width: 28px; text-align: right; font-size: 7px; color: var(--bt-dim);
        transform: translateY(50%); }
  .col { position: relative; flex: 1 1 0; min-width: 0; height: 100%; display: flex;
         flex-direction: column-reverse; gap: 2px; cursor: pointer; }
  .col:hover, .col.on { background: rgba(62, 244, 255, 0.07); }
  .col.today::after { content: ""; position: absolute; left: 0; right: 0; bottom: -6px; height: 3px;
                      background: var(--bt-pink); }
  .seg { flex: none; width: 100%; }
  .seg.top { border-radius: 3px 3px 0 0; }
  .xl { display: flex; justify-content: space-between; margin-top: 10px; font-size: 11px; color: var(--bt-dim); }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 10px; font-size: 8px; color: var(--bt-dim); }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .sw { display: inline-block; flex: none; width: 10px; height: 10px; border-radius: 2px; }
  .tip { position: absolute; z-index: 3; top: 6px; min-width: 150px; padding: 8px 10px; pointer-events: none;
         font-size: 12px; line-height: 1.5; background: #05040a; border: 2px solid var(--bt-cyan);
         box-shadow: 0 0 14px rgba(62, 244, 255, 0.35); }
  .tip b { display: block; margin-bottom: 4px; font-size: 8px; font-weight: normal; color: var(--bt-cyan); }
  .tip div { display: flex; align-items: center; gap: 6px; }
  .tip div span:last-child { margin-left: auto; padding-left: 10px; color: var(--bt-fg); }
  .tip .tot { margin-top: 4px; padding-top: 4px; border-top: 1px solid var(--bt-edge); color: var(--bt-yellow); }
  details { margin-top: 8px; font-size: 12px; color: var(--bt-dim); }
  summary { cursor: pointer; font-size: 8px; }
  .tbl { overflow-x: auto; margin-top: 8px; }
  .tbl table { border-collapse: collapse; font-size: 11px; white-space: nowrap; }
  .tbl th, .tbl td { padding: 3px 6px; text-align: right; border-bottom: 1px solid var(--bt-edge); }
  .tbl th:first-child, .tbl td:first-child { text-align: left; }
  .board .tabs { margin-bottom: 8px; }
  .board .tabs button { flex: 1; padding: 10px 6px; font-size: 9px; }
  .rows { display: flex; flex-direction: column; }
  .row { display: grid; grid-template-columns: 44px minmax(0, 1fr) auto; column-gap: 10px; align-items: center;
         padding: 9px 2px 7px; border-bottom: 1px solid rgba(44, 40, 82, 0.8); }
  .rank { font-size: 9px; color: var(--bt-yellow); text-shadow: 0 0 6px rgba(255, 233, 77, 0.5); }
  .row:nth-child(n + 4) .rank { color: var(--bt-dim); text-shadow: none; }
  .rn { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
  .rn .badge { margin-left: 6px; }
  .rs { display: flex; align-items: center; gap: 6px; margin-top: 3px; overflow: hidden; white-space: nowrap;
        text-overflow: ellipsis; font-size: 11px; color: var(--bt-dim); }
  .rt { font-size: 10px; text-align: right; white-space: nowrap; color: var(--bt-cyan); }
  .rt small { display: block; margin-top: 5px; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
              text-transform: none; letter-spacing: 0; font-size: 11px; color: var(--bt-dim); }
  .mag { grid-column: 2 / 4; height: 3px; margin-top: 6px; background: rgba(62, 244, 255, 0.12); }
  .mag i { display: block; height: 100%; background: rgba(62, 244, 255, 0.55); }
  .snone { padding: 16px 0; font-size: 9px; line-height: 1.6; color: var(--bt-dim); }
  .cons { display: flex; flex-direction: column; gap: 8px; }
  .con { padding: 10px; background: var(--bt-panel); border: 2px solid var(--bt-edge); }
  .con .ctop { display: flex; align-items: center; gap: 8px; }
  .cname { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 9px; }
  .ctime { font-size: 10px; white-space: nowrap; color: var(--bt-cyan); }
  .share { height: 6px; margin: 8px 0 6px; background: rgba(169, 166, 204, 0.1); }
  .share i { display: block; height: 100%; border-radius: 0 3px 3px 0; }
  .cmeta { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 2px 10px; font-size: 11px;
           color: var(--bt-dim); }
  .cmeta span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }

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
    .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
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
    // Back on the page: the clock ticks again, the tiles' covers come again,
    // the high scores are asked again.
    if (!this._built || !this._hass) return;
    this._renderGrid();
    if (this._here().id === SCORES) this._askStats();
    this._update();
  }

  disconnectedCallback() {
    if (this._built) this._unzoom();  // no stream left open off the page
    clearInterval(this._ticker);
    this._ticker = undefined;
    clearTimeout(this._statsTimer);
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
          <div class="stats hidden"></div>
        </section>
        <div class="zoom hidden" role="dialog" aria-modal="true" aria-label="${STRINGS.screen}" tabindex="-1">
          <img alt="${STRINGS.screen}">
        </div>
        <div class="sheet hidden" role="dialog" aria-modal="true">
          <div class="panel">
            <div class="sbody" tabindex="0">
              <div class="art"><span class="nosignal">${STRINGS.noSignal}</span><img alt=""></div>
              <div class="stitle"></div>
              <div class="sinfo">
                <div class="ssystem"></div>
                <div class="sfacts"></div>
                <div class="scredits"></div>
                <div class="splayed"></div>
              </div>
              <div class="sdesc"></div>
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
      statusNote: $(".status .note"), grid: $(".grid"), stats: $(".stats"), sheet: $(".sheet"),
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
    if (id === SCORES) {  // each visit starts at today, asked anew
      this._stats = undefined;
      this._range = "today";
      this._pinnedDay = undefined;
    }
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
    if (here.id === SCORES) {
      this._filterFor(undefined);
      this._browsed = SCORES;
      this._seq = (this._seq || 0) + 1;  // a folder on its way is late
      this._folder = undefined;
      this._failed = false;
      this._dropSearch();
      this._askStats();
      this._renderGrid();
      return;
    }
    this._dropStats();
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

  // --- the high scores -------------------------------------------------

  // Time played, from Home Assistant (batocera/stats): asked when the view
  // opens, then every STATS_MS while it stays open. A failed ask keeps what
  // came before; with nothing before, it says so.
  _askStats() {
    clearTimeout(this._statsTimer);
    this._statsTimer = setTimeout(() => this._askStats(), STATS_MS);
    const seq = (this._statsSeq = (this._statsSeq || 0) + 1);
    const done = (stats) => {
      if (seq !== this._statsSeq) return;
      this._stats = stats;
      if (this._here().id === SCORES) this._renderGrid();
    };
    if (!this._config.entry_id || !this._hass.callWS) {
      done({ error: true });
      return;
    }
    Promise.resolve(this._hass.callWS({ type: "batocera/stats", entry_id: this._config.entry_id }))
      .then((answer) => done(answer && typeof answer === "object" ? { answer } : { error: true }))
      .catch(() => done({ answer: this._stats && this._stats.answer, error: true }));
  }

  // Left: no more asking, and an answer on its way is late.
  _dropStats() {
    clearTimeout(this._statsTimer);
    this._statsTimer = undefined;
    this._statsSeq = (this._statsSeq || 0) + 1;
  }

  // The totals, the 30 days by console, then the board and the consoles of
  // the range picked: today, this month (both from Home Assistant's log of
  // the sessions) or all time (as EmulationStation counts it).
  _renderStats() {
    const stats = this._stats || {};
    const answer = stats.answer;
    let big = "";
    let note = "";
    if (!answer && stats.error) [big, note] = [STRINGS.noScores, STRINGS.noScoresNote];
    else if (!answer) big = STRINGS.loading;
    this.$.statusBig.textContent = big;
    this.$.statusNote.textContent = note;
    this.$.status.classList.toggle("hidden", !big);
    this.$.count.textContent = STRINGS.timePlayed;
    this.$.stats.classList.toggle("hidden", !answer);
    if (!answer) {
      this.$.stats.replaceChildren();
      return;
    }
    const names = answer.names || {};
    const nameOf = (system) => names[system] || this._systems[system] || system;
    const days = Array.isArray(answer.days) ? answer.days : [];
    // The consoles with most time over the 30 days get a colour each.
    const totals = {};
    for (const day of days) {
      for (const [system, seconds] of Object.entries(day.seconds || {})) {
        totals[system] = (totals[system] || 0) + seconds;
      }
    }
    const played = Object.keys(totals).filter((system) => totals[system] > 0)
      .sort((a, b) => totals[b] - totals[a] || a.localeCompare(b));
    const stack = played.slice(0, PALETTE.length);
    const others = played.length > stack.length;
    const colour = (system) => PALETTE[stack.indexOf(system)] || OTHER;
    // [name, colour, seconds]: the stack's consoles, bottom up, then the rest.
    const lump = (by = {}) => {
      const out = stack.map((system) => [nameOf(system), colour(system), by[system] || 0]);
      const rest = Object.entries(by).reduce((sum, [system, s]) => sum + (stack.includes(system) ? 0 : s), 0);
      return others ? [...out, [STRINGS.other, OTHER, rest]] : out;
    };
    const today = isoDay(answer.date) || new Date();
    const month = this._when(today, { month: "long" }).toUpperCase();
    const all = answer.all && typeof answer.all === "object" ? answer.all : null;
    const view = {
      nameOf, colour, lump, all,
      label: { today: STRINGS.today, month, all: STRINGS.allTime },
      range: (key) => (key === "all" ? all : answer[key]) || null,
    };
    const since = isoDay(answer.log_since);
    const first = new Date(today.getFullYear(), today.getMonth(), 1);
    const sinceNote = !since ? STRINGS.fromNow
      : since > first ? STRINGS.since(this._when(since, { month: "short", day: "numeric" })) : STRINGS.sinceFirst;
    const day = view.range("today") || {};
    const consoles = day.systems || [];
    const games = consoles.reduce((sum, system) => sum + (system.games || 0), 0);
    const kpis = el("div", "kpis");
    for (const [key, value, about] of [
      [STRINGS.today, span(day.seconds), consoles.length ? STRINGS.consolesGames(consoles.length, games) : ""],
      [month, span((view.range("month") || {}).seconds), sinceNote],
      [STRINGS.allTime, all ? hours(all.seconds || 0) : STRINGS.dash, all ? STRINGS.asCounted : STRINGS.needsUpdate],
      [STRINGS.gamesPlayed, all ? String(all.played || 0) : STRINGS.dash, all ? STRINGS.ofCatalog(all.owned || 0) : ""],
    ]) {
      const box = el("div", "kpi");
      box.append(el("div", "k", key), el("div", "v", value), el("div", "n", about));
      kpis.append(box);
    }
    const heading = el("div", "h2", STRINGS.last30);
    heading.append(el("small", "", STRINGS.byConsole));
    this.$.stats.replaceChildren(el("div", "snote", answer.log_since ? "" : STRINGS.logStarts),
      kpis, heading, this._statsChart(days, view), this._statsBoard(view));
  }

  // Minutes per day, stacked by console: a tap (or a hover) on a day tells
  // its time per console; the same as a table, for those who read it so.
  _statsChart(days, { lump }) {
    const chart = el("div", "chart");
    chart.setAttribute("aria-label", `${STRINGS.last30}, ${STRINGS.byConsole}`);
    const plot = el("div", "plot");
    const total = (day) => Object.values(day.seconds || {}).reduce((sum, s) => sum + s, 0);
    // A gridline every two hours, or more apart when the days are long.
    const most = Math.max(0, ...days.map(total)) / 60;
    const step = 120 * Math.max(1, Math.ceil(most / 120 / 6));
    const top = Math.max(step, Math.ceil(most / step) * step);
    for (let m = 0; m <= top; m += step) {
      const line = el("div", "gl");
      const label = el("div", "ax", m ? `${m / 60}H` : "0");
      line.style.bottom = label.style.bottom = `${(m / top) * 100}%`;
      plot.append(line, label);
    }
    const long = (day) => this._when(isoDay(day.date) || new Date(), { weekday: "short", day: "numeric", month: "short" })
      .toUpperCase();
    const tip = el("div", "tip");
    tip.hidden = true;
    const show = (col, day) => {
      tip.replaceChildren(el("b", "", long(day)));
      for (const [name, colour, s] of lump(day.seconds)) {
        if (!s) continue;
        const line = el("div");
        line.append(swatch(colour), el("span", "", name), el("span", "", span(s)));
        tip.append(line);
      }
      const sum = el("div", "tot");
      sum.append(el("span", "", STRINGS.total), el("span", "", span(total(day))));
      tip.append(sum);
      tip.hidden = false;
      const box = chart.getBoundingClientRect();
      const at = col.getBoundingClientRect();
      const x = at.left - box.left + at.width / 2;
      tip.style.left = `${Math.max(8, Math.min(x - 80, box.width - 170))}px`;
    };
    const cols = days.map((day, i) => {
      const col = el("div", i === days.length - 1 ? "col today" : "col");
      col.dataset.date = day.date;
      col.setAttribute("role", "img");
      col.setAttribute("aria-label", `${long(day)}: ${span(total(day))}`);
      const parts = lump(day.seconds).filter(([, , s]) => s);
      parts.forEach(([, colour, s], j) => {
        const seg = el("div", j === parts.length - 1 ? "seg top" : "seg");
        seg.style.height = `max(0px, calc(${(s / 60 / top) * 100}% - 2px))`;
        seg.style.background = colour;
        col.append(seg);
      });
      col.addEventListener("mouseenter", () => { if (!this._pinnedDay) show(col, day); });
      col.addEventListener("mouseleave", () => { if (!this._pinnedDay) tip.hidden = true; });
      col.addEventListener("click", () => {
        for (const other of cols) other.classList.remove("on");
        this._pinnedDay = this._pinnedDay === day.date ? undefined : day.date;
        if (!this._pinnedDay) {
          tip.hidden = true;
          return;
        }
        col.classList.add("on");
        show(col, day);
      });
      return col;
    });
    plot.append(...cols);
    const shortDay = (day) => this._when(isoDay(day.date) || new Date(), { day: "numeric", month: "short" })
      .toUpperCase();
    const axis = el("div", "xl");
    if (days.length) axis.append(el("span", "", shortDay(days[0])), el("span", "", shortDay(days[Math.floor(days.length / 2)])),
      el("span", "", STRINGS.today));
    const legend = el("div", "legend");
    for (const [name, colour] of lump()) {
      const item = el("span");
      item.append(swatch(colour), name);
      legend.append(item);
    }
    const table = el("table");
    const head = el("tr");
    head.append(el("th", "", STRINGS.day), ...lump().map(([name]) => el("th", "", name)), el("th", "", STRINGS.total));
    table.append(head);
    for (const day of [...days].reverse()) {
      const row = el("tr");
      row.append(el("td", "", long(day)), ...lump(day.seconds).map(([, , s]) => el("td", "", s ? span(s) : "–")),
        el("td", "", span(total(day))));
      table.append(row);
    }
    const details = el("details");
    details.open = !!this._tableOpen;
    details.addEventListener("toggle", () => { this._tableOpen = details.open; });
    const wrap = el("div", "tbl");
    wrap.append(table);
    details.append(el("summary", "", STRINGS.asTable), wrap);
    chart.append(plot, axis, legend, details, tip);
    // The day picked stays picked when the view is drawn again.
    const pinned = cols.find((col) => col.dataset.date === this._pinnedDay);
    if (pinned) {
      pinned.classList.add("on");
      show(pinned, days[cols.indexOf(pinned)]);
    } else {
      this._pinnedDay = undefined;
    }
    return chart;
  }

  // The range's games by time, then its consoles.
  _statsBoard({ nameOf, colour, label, range }) {
    const board = el("div", "board");
    const key = this._range in BOARD ? this._range : "today";
    const tabs = el("div", "tabs");
    tabs.setAttribute("role", "group");
    for (const each of Object.keys(BOARD)) {
      const tab = el("button", "", label[each]);
      tab.type = "button";
      tab.dataset.range = each;
      tab.setAttribute("aria-pressed", String(each === key));
      tab.addEventListener("click", () => {
        this._range = each;
        this._renderGrid();
      });
      tabs.append(tab);
    }
    const shown = range(key);
    const rows = el("div", "rows");
    const games = (shown && shown.games) || [];
    const best = games.length ? games[0].seconds || 0 : 0;
    games.forEach((game, i) => {
      const row = el("div", "row");
      const name = el("div", "rn", game.name);
      if (game.playing && key === "today") name.append(el("span", "badge", STRINGS.nowBadge));
      const system = el("div", "rs");
      system.append(swatch(colour(game.system)), nameOf(game.system));
      const mid = el("div");
      mid.append(name, system);
      const time = el("div", "rt", span(game.seconds));
      time.append(el("small", "", key === "all" ? STRINGS.plays(game.count || 0) : STRINGS.sessions(game.count || 0)));
      const bar = el("i");
      bar.style.width = `${best ? ((game.seconds || 0) / best) * 100 : 0}%`;
      const magnitude = el("div", "mag");
      magnitude.append(bar);
      row.append(el("div", "rank", ordinal(i + 1)), mid, time, el("div"), magnitude);
      rows.append(row);
    });
    const consoles = el("div", "cons");
    const systems = (shown && shown.systems) || [];
    const most = systems.length ? systems[0].seconds || 0 : 0;
    for (const system of systems) {
      const box = el("div", "con");
      const top = el("div", "ctop");
      top.append(swatch(colour(system.system)), el("span", "cname", nameOf(system.system)),
        el("span", "ctime", key === "all" ? hours(system.seconds || 0) : span(system.seconds)));
      const bar = el("i");
      bar.style.width = `${most ? ((system.seconds || 0) / most) * 100 : 0}%`;
      bar.style.background = colour(system.system);
      const share = el("div", "share");
      share.append(bar);
      const meta = el("div", "cmeta");
      meta.append(el("span", "", key === "all" ? STRINGS.playedOf(system.games || 0, system.owned || 0)
        : STRINGS.gamesOf(system.games || 0)), el("span", "", system.top ? STRINGS.topGame(system.top) : ""));
      box.append(top, share, meta);
      consoles.append(box);
    }
    const none = !shown ? STRINGS.allNeedsUpdate : games.length ? "" : STRINGS.nonePlayed;
    board.append(el("div", "h2", STRINGS.top(label[key], BOARD[key])), tabs);
    if (none) board.append(el("div", "snone", none));
    board.append(rows);
    if (systems.length) board.append(el("div", "h2", STRINGS.consoles(label[key])), consoles);
    return board;
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
        if (place !== this._filterPlace) {
          this._renderGrid();  // the tiles' years and stars
          return;
        }
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

  // A game's facts, for its tile: the place's, the game's system's, or the
  // home's (every game's).
  _factOf(item) {
    const [, system, id] = item.media_content_id.split(":");  // game:<system>:<id>
    for (const place of [this._filterPlace, system, ""]) {
      const facts = this._facts.get(place);
      if (facts && facts.games && facts.games[id]) return facts.games[id];
    }
    return null;
  }

  // Games listed where no facts are asked for them, the favourites and
  // recently played of all systems or a search at the home, ask for every
  // game's: once a catalog, as a failed ask is not tried again.
  _askForTiles() {
    const place = this._filterPlace;
    if ((place !== undefined && place !== "") || !this._config.entry_id) return;
    if (!this._items.some((item) => item.can_play && !this._factOf(item))) return;
    const catalog = this._player().attr.catalog;
    if (this._tileFacts === catalog) return;
    this._tileFacts = catalog;
    this._ask("");
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
    this.$.start.focus();
  }

  // What the sheet tells of the game: the catalog's facts (batocera/games,
  // for this one game) at once, and what the box adds (batocera/info: the
  // description, who made it, the time played in all, the space it takes)
  // once Home Assistant has it. Each is shown when it comes, if the sheet is
  // still this game's; without them (an old box, a busy one) the sheet shows
  // the rest.
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
    return this._when(date, { year: "numeric", month: "short", day: "numeric" });
  }

  // A day, or its parts, as the user's language writes them.
  _when(date, options) {
    const locale = this._hass.locale;
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
      facts.players && STRINGS.players(facts.players), stars && "★".repeat(stars),
      info.size > 0 && bytes(info.size)]);
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
      } else if (kind === SCORES) {  // three bars of a chart
        const bars = el("div", "minibars");
        for (const height of [45, 100, 70]) {
          const bar = el("i");
          bar.style.height = `${height}%`;
          bars.append(bar);
        }
        art.append(bars);
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
    const fact = this._factOf(item) || {};
    const year = fact.year > 0 ? String(fact.year) : "";
    const { stars } = traits(fact);
    if (year || stars) {
      const meta = el("span", "tm");
      meta.append(el("span", "ty", year), el("span", "tr", "★".repeat(stars)));
      tile.append(meta);
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
    // The high scores take the place of the games: no filters, no search.
    const scores = here.id === SCORES;
    this.$.search.classList.toggle("hidden", scores);
    if (scores) {
      this.$.filters.classList.add("hidden");
      if (this._observer) this._observer.disconnect();
      this._items = [];
      this.$.grid.replaceChildren();
      this._renderStats();
      return;
    }
    this.$.stats.classList.add("hidden");
    this.$.stats.replaceChildren();

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
    // At the home, as it is (no filter, no search), the high scores come
    // after the favourites and recently played.
    let listing = shown;
    if (root && !filtering && !text && !big && this._config.entry_id) {
      const at = shown.reduce((after, item, i) =>
        (["fav", "recent"].includes(split(item.media_content_id)[0]) ? i + 1 : after), 0);
      listing = [...shown.slice(0, at), SCORES_TILE, ...shown.slice(at)];
    }
    this._items = big && big !== STRINGS.noMatch ? [] : [...listing, ...described];
    // The tiles are made anew: forget the old ones' covers still waiting.
    if (this._observer) this._observer.disconnect();
    const tiles = this._items.map((item, i) => this._tile(item, i, playing));
    if (described.length && tiles.length) {
      const apart = el("div", "apart", STRINGS.inDescription);
      apart.setAttribute("role", "heading");
      apart.setAttribute("aria-level", "2");
      tiles.splice(listing.length, 0, apart);
    }
    this.$.grid.replaceChildren(...tiles);
    this._askForTiles();
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
