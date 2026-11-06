# ha-batocera

Connects a [Batocera](https://batocera.org) retro-gaming box to
[Home Assistant](https://www.home-assistant.io) over MQTT:

- what is being played: game, system, emulator, cover art, since when;
- how long it was played today;
- a stop button, which quits the game the way the exit hotkey does, so saves
  are written;
- parental-style limits: a daily playtime cap and a time window in which games
  are allowed, with a warning before the end. Batocera has no user profiles,
  so limits apply to the whole box.

Two parts live in this repository:

| Part | Where it runs | What it is |
|------|---------------|------------|
| `custom_components/batocera/` | Home Assistant | a custom integration, installable with HACS |
| `custom_components/batocera/daemon/` | the Batocera box | a small daemon (Python 3, standard library only), a launch hook and a service script |

The daemon lives inside the integration so that HACS ships it too.

Nothing else is needed: the daemon talks to the broker through the
`mosquitto_pub` and `mosquitto_sub` clients Batocera already ships, and the
integration uses Home Assistant's built-in MQTT integration. It does not use
EmulationStation's web API (port 1234).

```
 Batocera                                   MQTT broker           Home Assistant
 launcher --gameStart/gameStop--> hook
            (FIFO, never blocks)   |
                                   v
                          ha_batocera.py --- batocera/<device>/* ---> batocera integration
          batocera-es-swissknife <-------- cmd (stop), lock <-------- limits, stop button
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
- The state is published as retained MQTT messages. A persistent
  `mosquitto_sub` receives commands and holds the MQTT will that marks the box
  offline if the daemon or the network goes away.
- Broker host, port and credentials live in mosquitto client config files
  (mode 600), never on a command line where `ps` would show them.
- `stop` runs `batocera-es-swissknife --emukill`; if the launcher is still
  alive 5 seconds later, the daemon kills its process tree.

## Install on Batocera

You need a Batocera release that has `batocera-services` (user services in
`/userdata/system/services`), and an MQTT broker reachable from the box.

Copy the `custom_components/batocera/daemon/` folder to the box and run the installer there as root,
for example:

```sh
scp -r custom_components/batocera/daemon root@batocera.local:/tmp/ha-batocera
ssh root@batocera.local /tmp/ha-batocera/install.sh
```

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
  kill games and send input. ha-batocera does not need it. The installer
  prints how to restrict it to the box itself.
- **`es_web_notifier`.** When enabled, this service listens on port 8080 for
  the whole network, without authentication. The installer prints how to turn
  it off, if nothing else of yours uses it.

### Uninstall

```sh
/userdata/system/ha-batocera/uninstall.sh
```

It stops and removes the service, the hook and the daemon, and clears the
retained messages under `batocera/<device>/`.

## Install the Home Assistant integration

The [MQTT integration](https://www.home-assistant.io/integrations/mqtt/) must
be set up first, connected to the same broker.

With [HACS](https://hacs.xyz): HACS, three dots menu, Custom repositories, add
`https://github.com/vjt/ha-batocera` with type Integration, then install
"Batocera" and restart Home Assistant.

Manually: copy `custom_components/batocera/` into the `custom_components/`
folder of your Home Assistant configuration and restart.

Then Settings, Devices & services, Add integration, "Batocera". Give the box a
name and its topic prefix, `batocera/<device>`. Add the integration once per
box.

### Entities

For a box named "Arcade":

| Entity | What |
|--------|------|
| `media_player.arcade` | playing / idle / off; title = game, source = system, app = emulator, picture = cover. Stop quits the game |
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
| `last_session` | yes | box | JSON: `game`, `system`, `emulator`, `started_at`, `ended_at`, `seconds` |
| `cmd` | no | Home Assistant | `stop` |
| `lock` | yes | Home Assistant | `ON` / `OFF` |

An empty retained message clears the topic. When a game ends, `last_session`
is published before `state` turns `idle`.

If your broker uses ACLs, the box needs to publish to `batocera/<device>/#`
and to subscribe to its `cmd`, `lock` and `availability` topics; Home
Assistant needs to read `batocera/<device>/#` and to publish `cmd` and `lock`.

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
- **Daemon restarted during a game.** The daemon does not look for a game that
  was already running when it started; that session is not reported.
- **Gamelists in other places.** Only the `gamelist.xml` next to the ROMs, or
  in a folder above them up to `/userdata/roms`, is read. Without one, the
  game name is the ROM file name and there is no cover.

## Development

Daemon tests, standard library only, need `ffmpeg` and `ffprobe` for the cover
tests (they are skipped otherwise):

```sh
python3 -m unittest discover -s tests/daemon -v
```

Integration tests, with
[pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component)
(last run with 0.13.375, that is Home Assistant 2026.10.4), in a throwaway
container:

```sh
docker run --rm -v "$PWD":/src:ro python:3.14-slim sh -c '
  pip install -q pytest-homeassistant-custom-component &&
  cp -r /src /repo && cd /repo && python -m pytest -p no:cacheprovider'
```

## License

MIT, see [LICENSE](LICENSE).
