"""Constants for the Batocera integration."""

DOMAIN = "batocera"
CONF_TOPIC = "topic"
CONF_HOST_KEY = "host_key"
CONF_PRIVATE_KEY = "private_key"
# The box's login on the Mosquitto add-on, when Home Assistant added it.
CONF_MQTT_LOGIN = "mqtt_login"
# Options: the users, besides admins, who may launch, pause and stop games.
CONF_CONTROL_USERS = "control_users"
# Options, under Advanced: how to wake the box, over what it reports.
CONF_WAKE_MAC = "wake_mac"
CONF_WAKE_BROADCAST = "wake_broadcast"
DEFAULT_TOPIC = "batocera/batocera"
DEFAULT_HOST = "batocera.local"
EVENT_WARNING = "batocera_warning"
