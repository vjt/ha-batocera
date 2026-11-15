# ha-batocera internals

How the daemon and the integration work, what the installer leaves on the box,
the MQTT protocol between them, and how to run the tests. The
[README](../README.md) covers installing and using it.

## How it works

```mermaid
flowchart LR
  subgraph box[Batocera box]
    launcher[launcher] -->|"gameStart / gameStop"| hook
    hook -->|"FIFO, never blocks"| daemon[ha_batocera.py]
    daemon -->|"games, launch on 127.0.0.1:1234"| es[EmulationStation]
    daemon -->|"stop"| swiss[batocera-es-swissknife]
  end
  daemon -->|"state, screen, catalog/*, resp/*"| broker[MQTT broker]
  broker -->|"cmd, lock, req, watch"| daemon
  broker -->|"what the box publishes"| ha["Home Assistant: media player, page, limits"]
  ha -->|"cmd, lock, req, watch"| broker
```

- Batocera runs every executable in `/userdata/system/scripts/` when a game
  starts and stops. The hook hands the event to the daemon through a FIFO,
  with a one-second timeout, so it can never delay a launch. It passes the
  launcher's pid, and the daemon checks every 15 seconds that it is still
  alive: a crash or a lost stop event still ends the session.
- The game's name and cover come from the system's `gamelist.xml` (box art,
  falling back to the image), as scraped by EmulationStation. Covers are
  downscaled with `ffmpeg` to at most 512 pixels a side.
- Every 15 seconds `grim` takes a screenshot from Batocera's Wayland
  compositor (Batocera 40 and later) and `ffmpeg` scales it down to a
  640-pixel-wide JPEG, both at the lowest CPU priority; it is published only
  when the screen changed. `screen_every=<seconds>` in `ha-batocera.conf`
  changes the interval, `screen_every=0` turns screenshots off.
- While someone watches the live screen, the box sends a 1280-pixel-wide
  frame every second the screen changed. Home Assistant asks for 60 seconds
  of them at a time and renews them while anyone watches; when the last
  viewer leaves, or Home Assistant goes away, they stop within a minute. The
  frames are not retained and go to the open streams only: no entity changes
  and the recorder never sees them. With screenshots off there is no live
  screen either.
- The state is published as retained MQTT messages. A persistent
  `mosquitto_sub` receives commands and holds the MQTT will that marks the box
  offline if the daemon or the network goes away.
- Broker host, port and credentials live in mosquitto client config files
  (mode 600), never on a command line where `ps` would show them.
- `stop` runs `batocera-es-swissknife --emukill`; if the launcher is still
  alive 5 seconds later, the daemon kills its process tree.
- A launch quits the running game first, then asks EmulationStation to launch
  the new one, as if it were picked in its menus: the hook, the lock and the
  limits see an ordinary launch.
- Pause sends the emulator's pause hotkey through Batocera's `hotkeygen`,
  offered when the emulator has one.
- Shut down does what EmulationStation's SHUTDOWN SYSTEM does: that one
  quits, saving the gamelists, touches `/tmp/shutdown.please` (with it,
  `emulationstation-standalone` does not start it again) and runs
  `shutdown -P -h now`. Its web API has no call for that, so the daemon
  quits the running game as `stop` does, waits for EmulationStation to be
  idle, touches the flag, asks `/quit`, and runs `shutdown -P -h now` once
  EmulationStation has left (60 seconds at most). Stopping EmulationStation
  with a SIGTERM, as `poweroff` and its init script do, saves nothing.
- Power on is Wake-on-LAN, sent by Home Assistant itself (UDP port 9, six
  `FF` bytes then the MAC address sixteen times) to the box's subnet's
  broadcast address: Home Assistant may have several networks, and
  `255.255.255.255` leaves from the default route's. The daemon reports
  the MAC and broadcast addresses of its default route's interface, and
  its Wake-on-LAN setting; when that is off but the card can wake on the
  magic packet, it turns it on (`ethtool -s <interface> wol g`), once
  each time it starts. Home Assistant keeps the last report for when the
  box is off; the integration's options can override both addresses,
  under Advanced.

## The installer

The installer asks for a device name (used in the topics, default: the
hostname in lower case), the broker host and port, and the MQTT user and
password. It then:

- installs the daemon in `/userdata/system/ha-batocera/`;
- installs the service as `/userdata/system/services/ha_batocera`, enables and
  starts it (it also shows up under System settings, Services, in
  EmulationStation);
- installs the hook as `/userdata/system/scripts/ha_batocera`.

Running it again upgrades the code and keeps the settings. For unattended
installs, settings can be passed in the environment, and then replace the ones
on the box: `DEVICE`, and `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`,
`MQTT_PASSWORD`. When `MQTT_HOST` is set, nothing is asked about the broker:
without `MQTT_USER` it is reached without authentication.

Files on the box:

| File | Contents |
|------|----------|
| `/userdata/system/ha-batocera/VERSION` | the installed version |
| `/userdata/system/ha-batocera/ha-batocera.conf` | `device=` and, optionally, `topic_prefix=` (default `batocera`) and `screen_every=` |
| `/userdata/system/ha-batocera/mosquitto_pub`, `mosquitto_sub` | broker options, one per line: `-h mqtt.example.lan`, `-p 1883`, `-u user`, `-P password`; TLS options such as `--cafile` work too |
| `/userdata/system/logs/ha-batocera.log` | the daemon's log |

After changing any of them: `batocera-services stop ha_batocera; batocera-services start ha_batocera`.

The installer also points out, without changing them:

- **The wiki's `mqtt_events` script**, which publishes raw events under the
  same `batocera/<host>/` prefix: it prints the commands to remove it and
  clear the retained messages it left.
- **EmulationStation's web API** open to the network (`PublicWebAccess`): it
  prints how to restrict it to the box.
- **`es_web_notifier`**, which listens on port 8080 for the whole network
  without authentication: it prints how to turn it off, if nothing else of
  yours uses it.

### Installed from Home Assistant

Home Assistant connects to the box over SSH as root, with the password:

- it trusts the box's SSH host key from this first connection on (Batocera
  keeps its host keys in `/userdata/system/ssh`, so they survive upgrades);
- it generates an ed25519 key pair of its own and adds the public key to
  `/userdata/system/.ssh/authorized_keys`, root's, with the comment
  `ha-batocera`, replacing the key of any earlier pairing;
- it copies the daemon to the box and runs the installer with the broker
  settings below, the MQTT password on the installer's standard input, never
  on a command line;
- with its own key, it checks the daemon's version, and that the box connects
  to the broker with the files the daemon reads: `mosquitto_pub`, run as the
  daemon runs it, publishes an empty message, not retained, on
  `batocera/<device>/check`.

The broker settings are the ones typed under Advanced, as given. Without them,
Home Assistant looks, from the box, over the same SSH connection and before
anything on the box changes:

- **Where.** In this order, all on the port of Home Assistant's MQTT entry
  (default 1883): the host of that entry, unless it only means something
  inside Home Assistant (`core-mosquitto`, `localhost`, a loopback address);
  Home Assistant's address as the box sees it, the first field of
  `$SSH_CLIENT` (not the local address of Home Assistant's socket, which
  behind Docker's NAT is the container's); `homeassistant.local`. The first
  one the box connects to wins, with an MQTT CONNECT by `mosquitto_pub`, its
  options in a mode-600 file of a private temporary directory, written from
  standard input. A broker Home Assistant reaches over TLS is not looked for.
- **Who.** When Home Assistant runs the Mosquitto add-on (Supervisor) and
  its MQTT entry uses it, Home Assistant adds the login `batocera-<device>`,
  with a random password, to the add-on's `logins` option, through the MQTT
  integration's own add-on manager, leaving the other options as they are.
  It then restarts the add-on and waits until its own MQTT client is
  connected to it again. Removing the entry removes the login, and restarts
  the add-on if it runs. No Home Assistant user is created. With any other
  broker, nothing is created: the box gets a broker only if it needs no
  account.

The errors name the broker tried: `mqtt_refused` (connection refused, or host
not found), `mqtt_timeout`, `mqtt_auth` (user name or password), `mqtt_tls`.
When the check after installing fails, the daemon stays installed with those
settings: installing again overwrites them.

The root password is used for that connection only and is not stored. Home
Assistant keeps the private key, the host key and the name of its add-on
login, if any, in its config entry.

Each time the box comes online, as soon as no game is running, Home Assistant
compares the daemon version on the box with the one it ships. If they differ,
it installs its own and restarts the service, keeping the settings on the
box. A game in progress is never interrupted: restarting the daemon would lose
that session. A failed update is tried again the next time the box comes
online, or when the integration is reloaded.

## MQTT topics

All under `batocera/<device>/` (the prefix is set by `topic_prefix` and
`device` in `ha-batocera.conf`). QoS 1.

| Topic | Retained | Published by | Payload |
|-------|----------|--------------|---------|
| `availability` | yes | box (will) | `online` / `offline` |
| `state` | yes | box | `playing` / `idle` / `off` |
| `game` | yes | box | game name, empty when idle |
| `system` | yes | box | system short name (e.g. `megadrive`), empty when idle |
| `emulator` | yes | box | emulator, or `emulator/core` (e.g. `libretro/genesisplusgx`), empty when idle |
| `started_at` | yes | box | ISO 8601 UTC timestamp of the game start, empty when idle |
| `cover` | yes | box | raw JPEG bytes (at most 512 px a side, 500 KB), empty when there is none |
| `screen` | yes | box | raw JPEG bytes, 640 px wide (at most 500 KB): what the TV shows; empty when there is no screenshot or the box shut down |
| `last_session` | yes | box | JSON: `game`, `system`, `emulator`, `started_at`, `ended_at`, `seconds` |
| `version` | yes | box | the daemon's version |
| `game_id` | yes | box | the running game's id in the catalog, empty when idle |
| `pausable` | yes | box | `ON` / `OFF`: whether the running emulator has a pause hotkey; empty when idle or not known |
| `paused` | yes | box | `ON` / `OFF`, as far as the daemon knows; empty when idle |
| `public_web_access` | yes | box | `ON` / `OFF`: whether EmulationStation's web API is open to the network |
| `network` | yes | box | JSON: how to wake the box, see below |
| `catalog/index` | yes | box | JSON: the systems in the catalog, see below |
| `catalog/system/<name>` | yes | box | gzip-compressed JSON: the games of one system, see below |
| `catalog/logo/<name>` | yes | box | raw SVG, PNG or JPEG bytes (at most 256 KB): the system's logo from the theme; empty when there is none |
| `live` | no | box | raw JPEG bytes, 1280 px wide: what the TV shows, while someone watches |
| `cmd` | no | Home Assistant | `stop` |
| `lock` | yes | Home Assistant | `ON` / `OFF` |
| `req` | no | Home Assistant | JSON: a request, see below |
| `watch` | no | Home Assistant | `live`: someone watches, send live frames for 60 seconds; `off`: nobody does |
| `resp/<id>` | no | box | JSON: the answer to request `<id>` |

An empty retained message clears the topic. When a game ends, `last_session`
is published before `state` turns `idle`.

`network` is the interface of the IPv4 default route, from `ip -j`, and
its Wake-on-LAN setting, from `ethtool`, checked when the daemon starts
(every 30 seconds until there is a default route) and every 30 minutes,
and published when it changes:

```json
{"interface": "eth0", "mac": "38:05:25:0a:0b:0c", "broadcast": "192.168.1.255",
 "wake_on": "g", "supports_wake_on": "pumbg"}
```

`wake_on` and `supports_wake_on` are `ethtool`'s letters, `g` being the
magic packet; a field it cannot tell is empty. Home Assistant shows them as
the diagnostic Wake-on-LAN binary sensor.

If your broker uses ACLs, the box needs to publish to `batocera/<device>/#`
and to subscribe to its `cmd`, `lock`, `req`, `watch`, `availability` and
`catalog/index` topics; Home Assistant needs to read `batocera/<device>/#` and
to publish `cmd`, `lock`, `req` and `watch`.

### The catalog

The daemon reads the games from EmulationStation's web API, on the box's
loopback address only, and publishes them. Only game systems are listed (no
collections), only the visible ones, and no hidden games; when
EmulationStation is in Kid mode, only the games marked as kid games.

`catalog/index` lists the systems that have games:

```json
[{"name": "megadrive", "fullname": "Sega Mega Drive", "manufacturer": "Sega",
  "year": 1988, "count": 2, "hash": "3f2a9c0e1b7d"}]
```

`hash` changes when the system's games do: a system is published again only
then. `catalog/system/<name>` holds, gzip-compressed, one row per game:

```json
[["9b1c4e0d2a37", "Sonic the Hedgehog", 1991, "Platform", "1", 3, 12, "20261102T183000", "Platform", 0.75],
 ["0d5e7a9b3c21", "Streets of Rage", 1991, "Beat'em up", "1-2", 0, 0, "", "", null]]
```

That is: id, name, year (or `null`), genre, players, flags, play count, last
played (EmulationStation's local time, or empty), folder, rating. Flags: 1 =
favourite, 2 = has a cover. The folder is where the ROM sits under its
system's ROM folder (`Fighter` for `/userdata/roms/mame/Fighter/kinst.zip`,
`a/b` when nested), empty for a ROM at the top. The rating is
EmulationStation's, from 0 to 1, or `null` when the game is unrated (`0`) or
the rating is not a number; rows of daemons before 0.7 stop at the folder,
and their games count as unrated. The id is the first 12 hex digits of the
MD5 of the ROM's path: `game_id` uses the same, so the running game can be
found in the catalog.

The page's genre, decade and rating filters get a system's genre, year and
rating by game id from Home Assistant, through the `batocera/games`
websocket command (`entry_id`, `system`): the media browser does not carry
them. While a filter is set, a system or one of its folders is listed with
everything under it as one list of games, no folder tiles, through the media
browser id `all:<system>` or `all:<system>:<folder>`, which the tree never
links to; favourites and recently played are flat already.

The home has the same filters, over every game. Without `system`,
`batocera/games` answers for every game of every system: the page asks for
it when a filter is first used at the home, not on every visit, and again
for a new catalog only while a filter is set there. While one is set, the
home lists the media browser id `all`, every game of every system by name,
instead of the system tiles; the page keeps that list for its catalog. The
home's filters and a system's are separate: each resets when it is left.

The media browser's root lists favourites, recently played, then the
systems by the name shown, case aside.

The catalog is published when the daemon starts (and again until
EmulationStation answers), after each game (that system only), every 30
minutes while no game runs, and on request. Systems that disappear are
cleared. Covers for it are sent on request, downscaled to 256 pixels at the
lowest CPU priority, and kept by Home Assistant for a week.

The page's launch dialog says what the game is. `batocera/games` with
`game` (a game id) answers for that game only; each game's facts there are
`genre`, `year` and `rating`, plus `players`, `playcount` and `lastplayed`
when there are (most games were never played). The rest is not in the
catalog, a description per game being too much for it: the dialog asks
`batocera/info` (`entry_id`, `game`), and Home Assistant asks the box (the
`info` request below) and answers `desc`, `developer`, `publisher`,
`gametime` (seconds played in all, as EmulationStation counts them) and
`size` (the bytes the game takes on the box), each left out when there is
none. Home Assistant keeps the answer for a week, like a cover, in memory
and next to the covers on disk, but only while the game's last played in
the catalog stays the same: a game played since is asked again, for its new
total. An answer kept before the box measured sizes (no `format` 2 in the
kept JSON) is asked again once, and serves until the box answers. When the
box cannot say (offline, busy, or a daemon before 0.8, which answers
`bad_request`), `batocera/info` answers `{}`, nothing is kept, and the
dialog shows the catalog's facts alone. The dialog shows the size with the
year, genre, players and rating, in SI units: `850 MB`, `2.3 GB`, `25 GB`.

The page's search finds games by name and, apart, by description. The
media player's search (`media_player/search_media`) answers `result`, the
games whose name holds the query, as any media search, and
`in_description`, the games whose description holds it and whose name does
not, each by name and at most 200; the page lists the second under an IN
THE DESCRIPTION heading. The descriptions never leave the box: the daemon
keeps them in memory with the catalog, folded as a search compares text (no
case, no accents, every run of blanks as one space), and Home Assistant
asks it through the `search` request below, for a query of 3 to 100
characters, of the system the search starts from or of all. When the box
cannot answer (offline, a daemon without the request, which answers
`bad_request`, or no answer within 3 seconds), `in_description` is empty and
the name matches go out alone.

### Requests

Home Assistant sends requests on `req`, as JSON with an `id` of 8 to 32
lower-case hex digits and an `op`; the box answers on `resp/<id>`. Requests
bigger than 4 KB, or without a valid id, are dropped.

| `op` | Other fields | Answer when it works |
|------|--------------|----------------------|
| `cover` | `system`, `game` (an id from the catalog) | `jpeg`: the cover, base64, at most 256 px a side |
| `info` | `system`, `game` | `desc`, `developer`, `publisher` (text; the description cut at 2 KB, the others at 256 bytes) and `gametime` (seconds played in all), from EmulationStation's `/systems/<system>/games/<id>`, one game at a time; and `size`, the bytes the game takes, measured then: a file's size; an `.m3u` with every disc it lists (and the tracks of a `.cue` among them), a `.cue` with every `FILE` it names, each relative to the list's folder; a folder, every file under it, symlinks not followed. Each file counted once; one missing or unreadable is left out. A folder of more than 50000 files and folders is not measured, and at most 64 KB of a list is read. Each field left out when empty |
| `launch` | `system`, `game` | nothing more, once EmulationStation was asked to launch it |
| `pause` | `paused`: `true` or `false` | `paused`: what the daemon believes now |
| `refresh` | none | nothing more; the catalog is read again |
| `shutdown` | none | nothing more, once EmulationStation was asked to quit; the box then powers off (see How it works). `busy` when EmulationStation is not idle 10 seconds after the game, `es_unreachable` when it does not answer: the box stays on. Without EmulationStation running, the box powers off at once |
| `search` | `query` (text), optionally `system` | `games`: the ids of the games (of that system, or of all) whose description holds the query and whose name does not, by name, at most 200; none for a query under 3 characters. Accents, case and line breaks aside; from the descriptions the daemon keeps with the catalog, EmulationStation is not asked |

Answers are `{"id": ..., "ok": true, ...}` or `{"id": ..., "ok": false,
"error": ...}`, where the error is one of `bad_request`, `unknown_game`,
`no_cover`, `locked`, `busy`, `not_playing`, `not_pausable`,
`es_unreachable`, `shutting_down` or `failed`.

The box launches only games from its own catalog: a request names a game by
its id, never by a path. It refuses to launch while the lock is on. A launch
first quits the running game, as `stop` does, and waits for EmulationStation
to be idle again.

Pause sends the emulator its pause hotkey through Batocera's `hotkeygen`.
The hotkey toggles, and the emulator says nothing back: `paused` is the
daemon's belief, not a measurement.

While the box shuts down, a launch or another shutdown answers
`shutting_down`, a launch waiting for EmulationStation is answered so, and
a game that starts anyway is quit. A daemon from before shutdowns answers
`bad_request`, and Home Assistant says it must be updated.

## Development

Daemon tests, standard library only, need `ffmpeg` and `ffprobe` for the cover
tests (they are skipped otherwise):

```sh
python3 -m unittest discover -s tests/daemon -v
```

Integration tests, with
[pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component)
(last run with 0.13.375, that is Home Assistant 2026.10.4) and the
integration's requirement from `manifest.json`, in a throwaway container. SSH
is faked: they never connect anywhere.

```sh
docker run --rm -v "$PWD":/src:ro python:3.14-slim sh -c '
  pip install -q pytest-homeassistant-custom-component asyncssh==2.24.0 &&
  cp -r /src /repo && cd /repo && python -m pytest -p no:cacheprovider'
```

The page's card, in [jsdom](https://github.com/jsdom/jsdom), also in a
throwaway container:

```sh
docker run --rm -v "$PWD":/src:ro node:22-slim sh -c '
  cp -r /src /repo && cd /repo/tests/card && npm install --silent && npm test'
```

`custom_components/batocera/daemon/VERSION` must equal the `version` in
`manifest.json`, and `get.sh` and the README must fetch the matching `v` tag
(a daemon test checks all three): Home Assistant reinstalls the daemon on
boxes that report another version, so bump them together for every release,
and tag it.
