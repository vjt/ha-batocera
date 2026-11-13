# ha-batocera

Connects a [Batocera](https://batocera.org) retro-gaming box to
[Home Assistant](https://www.home-assistant.io) over MQTT:

- what is being played: game, system, emulator, cover art, since when;
- what the TV shows, menus included: a screenshot every 15 seconds;
- how long it was played today;
- a stop button, which quits the game the way the exit hotkey does, so saves
  are written;
- parental-style limits: a daily playtime cap and a time window in which games
  are allowed, with a warning before the end. Batocera has no user profiles,
  so limits apply to the whole box;
- a Batocera page in Home Assistant's sidebar: the box's games to browse, with
  their covers, and to launch, pause and quit, for admins and the users you
  allow. The limits apply to games launched from there too.

Two parts live in this repository:

| Part | Where it runs | What it is |
|------|---------------|------------|
| `custom_components/batocera/` | Home Assistant | a custom integration, installable with HACS |
| `custom_components/batocera/daemon/` | the Batocera box | a small daemon (Python 3, standard library only), a launch hook and a service script |

The daemon lives inside the integration so that HACS ships it too.

Nothing else is needed: the daemon talks to the broker through the
`mosquitto_pub` and `mosquitto_sub` clients Batocera already ships, and the
integration uses Home Assistant's built-in MQTT integration. The daemon reads
the games from EmulationStation's web API, and asks it to launch them, on the
box's own loopback address only: the browser never talks to the box.

```
 Batocera                                   MQTT broker           Home Assistant
 launcher --gameStart/gameStop--> hook
            (FIFO, never blocks)   |
                                   v
                          ha_batocera.py --- batocera/<device>/* ---> batocera integration
          batocera-es-swissknife <-------- cmd (stop), lock <-------- limits, stop button
 EmulationStation  <-127.0.0.1:1234-- |  --- catalog/*, resp/* ----->  media player, panel
 (games, launch)                      | <-------- req (cover, launch, pause, refresh)
```

## How it works

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
- The state is published as retained MQTT messages. A persistent
  `mosquitto_sub` receives commands and holds the MQTT will that marks the box
  offline if the daemon or the network goes away.
- Broker host, port and credentials live in mosquitto client config files
  (mode 600), never on a command line where `ps` would show them.
- `stop` runs `batocera-es-swissknife --emukill`; if the launcher is still
  alive 5 seconds later, the daemon kills its process tree.
- The catalog of games comes from EmulationStation's web API on
  `127.0.0.1:1234` and is published as retained messages, one per system,
  gzip-compressed (see "The catalog"). Covers for it are sent on request,
  downscaled to 256 pixels at the lowest CPU priority, and kept by Home
  Assistant for a week.
- A launch quits the running game first, then asks EmulationStation to launch
  the new one, as if it were picked in its menus: the hook, the lock and the
  limits see an ordinary launch.
- Pause sends the emulator's pause hotkey through Batocera's `hotkeygen`,
  offered when the emulator has one.

## Install

You need an MQTT broker that the box can reach, Home Assistant's
[MQTT integration](https://www.home-assistant.io/integrations/mqtt/) connected
to it, and a Batocera release that has `batocera-services` (user services in
`/userdata/system/services`). Each box is one entry of the integration.

### The Home Assistant integration

With [HACS](https://hacs.xyz): HACS, three dots menu, Custom repositories, add
`https://github.com/vjt/ha-batocera` with type Integration, then install
"Batocera" and restart Home Assistant.

Manually: copy `custom_components/batocera/` into the `custom_components/`
folder of your Home Assistant configuration and restart.

### The daemon on the box, from Home Assistant

Settings, Devices & services, Add integration, "Batocera", then "Install
ha-batocera on the box". Give the box a name (its topics go under
`batocera/<name>`, in lower case with underscores for spaces), its host name or
address, its root password (Batocera's default is `linux`) and the broker
settings for the box.

Home Assistant then connects to the box over SSH as root, with the password:

- it trusts the box's SSH host key from this first connection on (Batocera
  keeps its host keys in `/userdata/system/ssh`, so they survive upgrades);
- it generates an ed25519 key pair of its own and adds the public key to
  `/userdata/system/.ssh/authorized_keys`, root's, with the comment
  `ha-batocera`, replacing the key of any earlier pairing;
- it copies the daemon to the box and runs its installer with your settings,
  the MQTT password on the installer's standard input, never on a command
  line.

The root password is used for that connection only and is not stored. Home
Assistant keeps the private key and the host key in its config entry.

The broker settings are asked for, not copied from Home Assistant's MQTT
integration: that may name a broker only Home Assistant can reach
(`core-mosquitto`, `localhost`), and its account usually has far more rights
than a box needs. Give the box an account of its own (see the ACLs under
"MQTT topics").

**Updates.** Each time the box comes online, as soon as no game is running,
Home Assistant compares the daemon version on the box with the one it ships.
If they differ, it installs its own and restarts the service, keeping the
settings on the box. A game in progress is never interrupted: restarting the
daemon would lose that session.

**Problems** show up under Settings, Repairs. A failed update is tried again
the next time the box comes online, or when the integration is reloaded. If
the box presents an SSH host key other than the one trusted at setup, Home
Assistant does not connect to it and says so. If Batocera was reinstalled, or
the key was replaced on purpose, reconfigure the integration (three dots menu
of the box, Reconfigure) with the root password: Home Assistant trusts the key
the box has now, installs a new key of its own and reinstalls the daemon.
Reconfigure is also the way out for a box whose daemon does not run (it never
comes online, so it is never updated), and it hands a box set up by hand over
to Home Assistant.

### The daemon on the box, without Home Assistant

If you would rather not give Home Assistant root on the box, install the
daemon yourself, as root on the box. Either with
[`get.sh`](get.sh), which downloads this release's source archive from GitHub,
over HTTPS only, and runs the installer from it:

```sh
curl -fsSLo /tmp/ha-batocera-get.sh https://raw.githubusercontent.com/vjt/ha-batocera/v0.4.2/get.sh && sh /tmp/ha-batocera-get.sh
```

or by copying the `custom_components/batocera/daemon/` folder to the box and
running the installer there, for example:

```sh
scp -r custom_components/batocera/daemon root@batocera.local:/tmp/ha-batocera
ssh root@batocera.local /tmp/ha-batocera/install.sh
```

Then, in Home Assistant: Settings, Devices & services, Add integration,
"Batocera", "The box already runs ha-batocera". Give the box a name and its
topic prefix, `batocera/<device>`. Home Assistant does not update a box set up
this way: run either command again to upgrade it.

### The installer

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
| `/userdata/system/ha-batocera/ha-batocera.conf` | `device=` and, optionally, `topic_prefix=` (default `batocera`) |
| `/userdata/system/ha-batocera/mosquitto_pub`, `mosquitto_sub` | broker options, one per line: `-h mqtt.example.lan`, `-p 1883`, `-u user`, `-P password`; TLS options such as `--cafile` work too |
| `/userdata/system/logs/ha-batocera.log` | the daemon's log |

After changing any of them: `batocera-services stop ha_batocera; batocera-services start ha_batocera`.

### Things the installer points out, but does not change

- **The wiki's `mqtt_events` script.** The Batocera wiki's home automation
  page suggests a script that publishes raw events under the same
  `batocera/<host>/` prefix. The installer detects it and prints the commands
  to remove it and clear the retained messages it left.
- **EmulationStation's web API.** With `PublicWebAccess` on, port 1234 is open
  to the whole network without authentication: anyone on the LAN can launch or
  kill games and send input. ha-batocera uses it from the box itself only. The
  installer prints how to restrict it to the box, and Home Assistant shows the
  same advice as a warning under Settings, Repairs for as long as the box
  reports the setting on. Neither changes it for you.
- **`es_web_notifier`.** When enabled, this service listens on port 8080 for
  the whole network, without authentication. The installer prints how to turn
  it off, if nothing else of yours uses it.

### Uninstall

On the box:

```sh
/userdata/system/ha-batocera/uninstall.sh
```

It stops and removes the service, the hook and the daemon, removes Home
Assistant's SSH key, and clears the retained messages under
`batocera/<device>/`. Then delete the box from the integration in Home
Assistant.

## Entities

For a box named "Arcade":

| Entity | What |
|--------|------|
| `media_player.arcade` | playing / paused / idle / off; title = game, source = system, app = emulator, picture = cover. Browse lists the box's games; play media launches one; pause and play pause and resume it; stop quits it. Attributes: `pausable`, `minutes_left` (while playing; null when nothing limits the time), `catalog` |
| `image.arcade_screen` | what the TV shows, menus included; updated when the screen changes, checked every 15 seconds; empty when there is no screenshot |
| `binary_sensor.arcade_playing` | on while a game runs |
| `sensor.arcade_game`, `sensor.arcade_system` | the running game and system |
| `sensor.arcade_session_start` | when the running game started |
| `sensor.arcade_playtime_today` | minutes played since local midnight |
| `button.arcade_stop_game` | quits the running game |
| `number.arcade_daily_limit` | daily limit in minutes, 0 = none |
| `time.arcade_allowed_from`, `time.arcade_allowed_until` | the window in which games are allowed |
| `switch.arcade_manual_lock` | locks the box until turned off |
| `binary_sensor.arcade_locked` | on while games are not allowed; attribute `reason`: `manual`, `window` or `limit` |

The entities that mirror the box become unavailable when it is unreachable
(MQTT will). After a clean shutdown the media player shows "off" instead.
Playtime and limits are kept by Home Assistant and stay available.

## The Batocera page

The integration adds a **Batocera** page to the sidebar, with one tab per box.
At the top, what is playing: cover, name, system, time played, and the time
left before the daily limit or the end of the window (or FREE PLAY), with
pause and a two-tap kill. Beside it, a preview of what the TV shows (the
latest screenshot, menus too) while the box is on: tap it to see it as big as
the page allows, tap again or press Escape to close it. Below, the box's games:
favourites, recently played and one tile per system, with the system's logo
when the theme has one. Inside a system, the ROM folders (for example MAME's
genres) are tiles of their own. The search at the top covers every system;
inside a system, that system with its folders; in favourites or recently
played, that list. Tap a game, then PRESS START: the running game is quit and
the new one launched.

Everyone who can open Home Assistant sees the page and browses the games.
**Launching, pausing and stopping games** takes an admin, or a user picked in
the integration's options (Settings, Devices & services, Batocera, Configure:
"Who may play"). Nobody else by default. This applies to the media player and
the stop button everywhere in Home Assistant, not only to the page; calls from
automations, which have no user, are allowed. Before this page existed, any
user could stop a game.

Launching is refused while the box is locked (manual lock, outside the window
or over the daily limit), and the box itself refuses a launch while its lock
is on. A game launched some other way while locked is still stopped as soon
as it starts.

The games can also be browsed and launched from Home Assistant's own media
browser, and launched by automations with `media_player.play_media`, using the
ids the browser shows (`game:<system>:<id>`).

When EmulationStation is in Kid mode, only the games marked as kid games are
listed. Hidden games never are.

## Limits

Home Assistant decides, the box obeys:

- Playtime is computed by the integration from the sessions the box reports,
  and survives Home Assistant restarts. A session across midnight counts for
  the new day only from midnight.
- Five minutes before the daily limit is reached or the window closes, the
  integration fires a `batocera_warning` event, once per game session, with
  `entry_id`, `name`, `reason` (`limit` or `window`) and `minutes_left`.
- At the limit, outside the window or under manual lock, it publishes a
  retained `lock` (`ON`) and sends `stop` to the running game, again every
  minute while a game keeps running.
- While the lock is on, the daemon stops any game as soon as it is launched.
  The launch itself cannot be refused: Batocera ignores the hook's exit code.

The window runs from "allowed from" to "allowed until". When "until" is
earlier than "from" it spans midnight (for example 20:00 to 01:00). When both
are equal there is no window.

The warning is an event, so you choose how to deliver it. For example, a
notification to a phone:

```yaml
automation:
  - alias: Batocera, time almost up
    triggers:
      - trigger: event
        event_type: batocera_warning
    actions:
      - action: notify.mobile_app_phone
        data:
          message: >
            {{ trigger.event.data.name }}: {{ trigger.event.data.minutes_left }}
            minutes left.
```

Different limits on different days are left to automations, which can set the
limit and window entities. For example, two hours at weekends, one on
weekdays:

```yaml
automation:
  - alias: Batocera, daily limit by weekday
    triggers:
      - trigger: time
        at: "00:00:00"
    actions:
      - action: number.set_value
        target:
          entity_id: number.arcade_daily_limit
        data:
          value: "{{ 120 if now().weekday() >= 5 else 60 }}"
```

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
| `catalog/index` | yes | box | JSON: the systems in the catalog, see below |
| `catalog/system/<name>` | yes | box | gzip-compressed JSON: the games of one system, see below |
| `catalog/logo/<name>` | yes | box | raw SVG, PNG or JPEG bytes (at most 256 KB): the system's logo from the theme; empty when there is none |
| `cmd` | no | Home Assistant | `stop` |
| `lock` | yes | Home Assistant | `ON` / `OFF` |
| `req` | no | Home Assistant | JSON: a request, see below |
| `resp/<id>` | no | box | JSON: the answer to request `<id>` |

An empty retained message clears the topic. When a game ends, `last_session`
is published before `state` turns `idle`.

If your broker uses ACLs, the box needs to publish to `batocera/<device>/#`
and to subscribe to its `cmd`, `lock`, `req`, `availability` and
`catalog/index` topics; Home Assistant needs to read `batocera/<device>/#` and
to publish `cmd`, `lock` and `req`.

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
[["9b1c4e0d2a37", "Sonic the Hedgehog", 1991, "Platform", "1", 3, 12, "20261102T183000", "Platform"],
 ["0d5e7a9b3c21", "Streets of Rage", 1991, "Beat'em up", "1-2", 0, 0, "", ""]]
```

That is: id, name, year (or `null`), genre, players, flags, play count, last
played (EmulationStation's local time, or empty), folder. Flags: 1 =
favourite, 2 = has a cover. The folder is where the ROM sits under its
system's ROM folder (`Fighter` for `/userdata/roms/mame/Fighter/kinst.zip`,
`a/b` when nested), empty for a ROM at the top. The id is the first 12 hex digits of the MD5 of the ROM's path:
`game_id` uses the same, so the running game can be found in the catalog.

The catalog is published when the daemon starts (and again until
EmulationStation answers), after each game (that system only), every 30
minutes while no game runs, and on request. Systems that disappear are
cleared.

### Requests

Home Assistant sends requests on `req`, as JSON with an `id` of 8 to 32
lower-case hex digits and an `op`; the box answers on `resp/<id>`. Requests
bigger than 4 KB, or without a valid id, are dropped.

| `op` | Other fields | Answer when it works |
|------|--------------|----------------------|
| `cover` | `system`, `game` (an id from the catalog) | `jpeg`: the cover, base64, at most 256 px a side |
| `launch` | `system`, `game` | nothing more, once EmulationStation was asked to launch it |
| `pause` | `paused`: `true` or `false` | `paused`: what the daemon believes now |
| `refresh` | none | nothing more; the catalog is read again |

Answers are `{"id": ..., "ok": true, ...}` or `{"id": ..., "ok": false,
"error": ...}`, where the error is one of `bad_request`, `unknown_game`,
`no_cover`, `locked`, `busy`, `not_playing`, `not_pausable`,
`es_unreachable` or `failed`.

The box launches only games from its own catalog: a request names a game by
its id, never by a path. It refuses to launch while the lock is on. A launch
first quits the running game, as `stop` does, and waits for EmulationStation
to be idle again.

Pause sends the emulator its pause hotkey through Batocera's `hotkeygen`.
The hotkey toggles, and the emulator says nothing back: `paused` is the
daemon's belief, not a measurement.

## Open points

- **Broker unreachable.** What the box should do about the lock when it cannot
  reach the broker (keep the last known lock, lock, or allow) is not designed
  yet. Today the daemon enforces the last lock it received while it keeps
  running, and starts unlocked until the broker delivers the retained lock.
  Home Assistant cannot stop a game while the broker is down.
- **On-screen warning.** The warning does not appear inside the running game.
  EmulationStation's notifications are not known to show over a running
  emulator; RetroArch's network commands might work for libretro cores. Not
  explored.
- **Daemon restarted during a game.** A game already running when the daemon
  starts is found from the launcher's command line, and its session counts
  from the launcher's start. The emulator is then the one hotkeygen names
  (`retroarch` for any libretro core), without the core.
- **Gamelists in other places.** Only the `gamelist.xml` next to the ROMs, or
  in a folder above them up to `/userdata/roms`, is read. Without one, the
  game name is the ROM file name and there is no cover.
- **Not checked on a box yet.** The catalog, launch and pause were written from
  EmulationStation's and Batocera's sources and tested against made-up
  answers: the shape of the web API's answers, the path it gives for a game
  (the catalog's ids hash it, and must match the hook's ROM path), the media
  keys, how long a launch takes after a game was quit, the name and value of
  the Kid mode setting, and where and how `hotkeygen` keeps the running
  emulator's hotkeys (newer Batocera releases may move them to D-Bus, and
  pause would then show as not known).
- **Pause has no feedback.** The pause hotkey toggles and nothing reports
  back: if the game is paused or resumed from the pad, `paused` is wrong until
  the game ends. Play time keeps counting while paused.

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
`manifest.json`, and `get.sh` and this README must fetch the matching `v` tag
(a daemon test checks all three): Home Assistant reinstalls the daemon on
boxes that report another version, so bump them together for every release,
and tag it.

## License

MIT, see [LICENSE](LICENSE).
