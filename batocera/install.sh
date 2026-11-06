#!/bin/bash
# Install (or upgrade) ha-batocera on a Batocera box. Run it there as root,
# from the folder that holds this script. It asks for the MQTT settings the
# first time; later runs keep them. Settings can also come from the
# environment: DEVICE, MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASSWORD.
set -eu

SRC=$(cd "$(dirname "$0")" && pwd)
HOME_DIR=/userdata/system/ha-batocera
SERVICE=/userdata/system/services/ha_batocera
HOOK=/userdata/system/scripts/ha_batocera
ES=/userdata/system/configs/emulationstation

for cmd in python3 mosquitto_pub mosquitto_sub ffmpeg batocera-services batocera-es-swissknife; do
    command -v "$cmd" >/dev/null || { echo "$cmd not found: is this Batocera?" >&2; exit 1; }
done

mkdir -p "$HOME_DIR" "$(dirname "$SERVICE")" "$(dirname "$HOOK")"
chmod 700 "$HOME_DIR"
install -m 755 "$SRC/ha_batocera.py" "$HOME_DIR/ha_batocera.py"
install -m 755 "$SRC/service" "$SERVICE"
install -m 755 "$SRC/hook" "$HOOK"

if [ ! -f "$HOME_DIR/ha-batocera.conf" ]; then
    default=$(hostname | tr '[:upper:]' '[:lower:]')
    device=${DEVICE-}
    [ -n "$device" ] || read -rp "Device name for the MQTT topics [$default]: " device
    printf 'device=%s\n' "${device:-$default}" > "$HOME_DIR/ha-batocera.conf"
fi

# mosquitto_pub and mosquitto_sub read their options, one per line, from
# $XDG_CONFIG_HOME/mosquitto_pub and mosquitto_sub; the daemon points
# XDG_CONFIG_HOME at $HOME_DIR. This keeps the password off command lines.
if [ ! -f "$HOME_DIR/mosquitto_pub" ]; then
    host=${MQTT_HOST-}; port=${MQTT_PORT-}; user=${MQTT_USER-}; pass=${MQTT_PASSWORD-}
    [ -n "$host" ] || read -rp "MQTT broker host: " host
    [ -n "$port" ] || read -rp "MQTT broker port [1883]: " port
    [ -n "$user" ] || read -rp "MQTT user (empty for none): " user
    if [ -n "$user" ] && [ -z "$pass" ]; then
        read -rsp "MQTT password: " pass
        echo
    fi
    (
        umask 077
        {
            echo "-h $host"
            echo "-p ${port:-1883}"
            if [ -n "$user" ]; then
                echo "-u $user"
                echo "-P $pass"
            fi
        } > "$HOME_DIR/mosquitto_pub"
        cp "$HOME_DIR/mosquitto_pub" "$HOME_DIR/mosquitto_sub"
    )
fi

batocera-services enable ha_batocera
batocera-services stop ha_batocera >/dev/null 2>&1 || true
batocera-services start ha_batocera
echo "ha-batocera is running. Settings: $HOME_DIR/ha-batocera.conf," \
    "log: /userdata/system/logs/ha-batocera.log"

# What follows is advice only: nothing below is changed for you.

if ls "$ES"/scripts/*/mqtt_events.sh /userdata/system/scripts/mqtt_events.sh \
      /userdata/system/services/mqtt_events >/dev/null 2>&1; then
    host=$(hostname)
    cat <<EOF

NOTE: the "mqtt_events" script from the Batocera wiki is installed. It
publishes under batocera/$host/ too and its game events would confuse Home
Assistant. To remove it:

    batocera-services stop mqtt_events
    batocera-services disable mqtt_events
    rm -f $ES/scripts/*/mqtt_events.sh /userdata/system/scripts/mqtt_events.sh
    rm -f /userdata/system/services/mqtt_events

then clear the retained messages it left behind, and restart ha-batocera so
that its own retained state is published again:

    XDG_CONFIG_HOME=$HOME_DIR mosquitto_sub --remove-retained -W 3 \\
        -t 'batocera/$host/emulationstation/#' -t 'batocera/$host/game/#' \\
        -t 'batocera/$host/system/#' -t 'batocera/$host/sensors/#' \\
        -t 'batocera/$host/game-selected/#'
    batocera-services stop ha_batocera; batocera-services start ha_batocera

If you used its "advanced" version, it also created Home Assistant devices
through MQTT discovery: delete them from the MQTT integration page.
EOF
fi

if grep -qs '"PublicWebAccess" value="true"' "$ES/es_settings.cfg"; then
    cat <<EOF

NOTE: EmulationStation's web API (port 1234) is open to the whole network,
without authentication (PublicWebAccess=true): anyone on the LAN can launch or
kill games and send input. ha-batocera does not use it. To restrict it to the
box itself, with EmulationStation stopped (it rewrites its settings on exit):

    /etc/init.d/S31emulationstation stop
    sed -i 's/"PublicWebAccess" value="true"/"PublicWebAccess" value="false"/' \\
        $ES/es_settings.cfg
    /etc/init.d/S31emulationstation start
EOF
fi

case " $(batocera-settings-get system.services 2>/dev/null) " in
    *" es_web_notifier "*)
        cat <<EOF

NOTE: the es_web_notifier service is enabled. It listens on port 8080 for the
whole network, without authentication. ha-batocera does not use it. If nothing
else does:

    batocera-services stop es_web_notifier
    batocera-services disable es_web_notifier
EOF
        ;;
esac
