#!/bin/bash
# Remove ha-batocera from a Batocera box, including the retained MQTT
# messages it published (and the lock Home Assistant retained for it).
set -u

HOME_DIR=/userdata/system/ha-batocera

batocera-services stop ha_batocera
batocera-services disable ha_batocera

if [ -f "$HOME_DIR/mosquitto_sub" ]; then
    device=$(sed -n 's/^device *= *//p' "$HOME_DIR/ha-batocera.conf" 2>/dev/null)
    prefix=$(sed -n 's/^topic_prefix *= *//p' "$HOME_DIR/ha-batocera.conf" 2>/dev/null)
    base=${prefix:-batocera}/${device:-$(hostname | tr '[:upper:]' '[:lower:]')}
    echo "Clearing retained messages under $base/"
    XDG_CONFIG_HOME=$HOME_DIR mosquitto_sub --remove-retained -W 3 -t "$base/#" >/dev/null 2>&1
fi

rm -f /userdata/system/services/ha_batocera /userdata/system/scripts/ha_batocera
rm -rf "$HOME_DIR"
echo "ha-batocera removed. Its log is still in /userdata/system/logs/ha-batocera.log"
