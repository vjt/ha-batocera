"""One Batocera box: its state as published over MQTT, playtime and limits."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
import hashlib
import json
import math

from homeassistant.components import mqtt
from homeassistant.components.mqtt import ReceiveMessage
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_TOPIC, DOMAIN, EVENT_WARNING

WARN_BEFORE = timedelta(minutes=5)
RESEND_STOP = timedelta(minutes=1)


class Box:
    """State of one box, shared by all its entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry_id = entry.entry_id
        self.name = entry.title
        self.prefix: str = entry.data[CONF_TOPIC]

        # Mirrored from the daemon's retained topics.
        self.online = False
        self.state: str | None = None
        self.game: str | None = None
        self.system: str | None = None
        self.emulator: str | None = None
        self.started_at: datetime | None = None
        self.cover: bytes | None = None
        self.cover_hash: str | None = None

        # Limits, set from the config entities. 0 minutes means no daily
        # limit; equal bounds mean no time window.
        self.daily_limit = 0
        self.allowed_from = time(0)
        self.allowed_until = time(0)
        self.manual_lock = False

        # Playtime of the sessions that ended today.
        self.day = dt_util.now().date()
        self.played = 0.0
        self.counted_start: datetime | None = None  # of the last session counted

        self.lock_reason: str | None = None
        self._lock_sent: bool | None = None
        self._stop_sent: datetime | None = None
        self._warned: datetime | None = None
        self._listeners: list[Callable[[], None]] = []
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")

    async def async_setup(self, entry: ConfigEntry) -> None:
        if data := await self._store.async_load():
            self.daily_limit = data["daily_limit"]
            self.allowed_from = time.fromisoformat(data["allowed_from"])
            self.allowed_until = time.fromisoformat(data["allowed_until"])
            self.manual_lock = data["manual_lock"]
            self.day = date.fromisoformat(data["day"])
            self.played = data["played"]
            if data["counted_start"]:
                self.counted_start = dt_util.parse_datetime(data["counted_start"])
        entry.async_on_unload(
            await mqtt.async_subscribe(
                self.hass, f"{self.prefix}/+", self._on_message, qos=1, encoding=None
            )
        )
        entry.async_on_unload(
            async_track_time_interval(self.hass, self._tick, timedelta(minutes=1))
        )

    # --- listeners ------------------------------------------------------

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _update(self) -> None:
        self._evaluate()
        for listener in list(self._listeners):
            listener()

    # --- MQTT -----------------------------------------------------------

    @callback
    def _on_message(self, msg: ReceiveMessage) -> None:
        leaf = msg.topic.rsplit("/", 1)[1]
        payload: bytes = msg.payload  # type: ignore[assignment]
        if leaf == "cover":
            self.cover = payload or None
            self.cover_hash = hashlib.sha256(payload).hexdigest()[:16] if payload else None
            self._update()
            return
        text = payload.decode(errors="replace")
        if leaf == "availability":
            self.online = text == "online"
        elif leaf == "state":
            self.state = text or None
        elif leaf in ("game", "system", "emulator"):
            setattr(self, leaf, text or None)
        elif leaf == "started_at":
            self.started_at = dt_util.parse_datetime(text) if text else None
        elif leaf == "last_session":
            self._count_session(text)
        elif leaf == "lock":  # our own retained message
            self._lock_sent = text == "ON"
            return
        else:
            return
        self._update()

    def _publish(self, leaf: str, payload: str, retain: bool = False) -> None:
        self.hass.async_create_task(
            mqtt.async_publish(self.hass, f"{self.prefix}/{leaf}", payload, 1, retain)
        )

    async def async_stop(self) -> None:
        await mqtt.async_publish(self.hass, f"{self.prefix}/cmd", "stop", 1, False)

    # --- playtime -------------------------------------------------------

    def _count_session(self, text: str) -> None:
        try:
            session = json.loads(text)
            start = dt_util.parse_datetime(session["started_at"])
            end = dt_util.parse_datetime(session["ended_at"])
        except (ValueError, KeyError, TypeError):
            return
        if start is None or end is None:
            return
        if self.counted_start and start <= self.counted_start:
            return  # already counted, e.g. retained and seen again on restart
        self.counted_start = start
        self._roll_day()
        midnight = dt_util.start_of_local_day()
        if end > midnight:
            self.played += (end - max(start, midnight)).total_seconds()
        self._save()

    def _roll_day(self) -> None:
        if (today := dt_util.now().date()) != self.day:
            self.day, self.played = today, 0.0
            self._save()

    def playtime(self) -> float:
        """Seconds played today, the running session included."""
        now = dt_util.now()
        total = self.played if self.day == now.date() else 0.0
        # The daemon sends last_session just before "idle": from then on the
        # running session is counted in self.played.
        if self.state == "playing" and self.started_at and self.started_at != self.counted_start:
            since = max(self.started_at, dt_util.start_of_local_day(now))
            total += max(0.0, (now - since).total_seconds())
        return total

    # --- limits ---------------------------------------------------------

    def set_option(self, name: str, value: int | time | bool) -> None:
        setattr(self, name, value)
        self._save()
        self._update()

    def _window_left(self, now: datetime) -> float | None:
        """Seconds until the allowed window closes, 0 outside it, None if unset."""
        start, end = self.allowed_from, self.allowed_until
        if start == end:
            return None
        t = now.time()
        closes = datetime.combine(now.date(), end, now.tzinfo)
        if start < end:
            inside = start <= t < end
        else:  # overnight, e.g. 20:00-01:00
            inside = t >= start or t < end
            if t >= start:
                closes += timedelta(days=1)
        return (closes - now).total_seconds() if inside else 0.0

    @callback
    def _evaluate(self) -> None:
        now = dt_util.now()
        limit_left = self.daily_limit * 60 - self.playtime() if self.daily_limit else None
        window_left = self._window_left(now)
        if self.manual_lock:
            self.lock_reason = "manual"
        elif window_left == 0:
            self.lock_reason = "window"
        elif limit_left is not None and limit_left <= 0:
            self.lock_reason = "limit"
        else:
            self.lock_reason = None

        locked = self.lock_reason is not None
        if locked != self._lock_sent:
            self._lock_sent = locked
            self._publish("lock", "ON" if locked else "OFF", retain=True)

        if self.state != "playing":
            return
        if locked:
            if self._stop_sent is None or now - self._stop_sent >= RESEND_STOP:
                self._stop_sent = now
                self._publish("cmd", "stop")
            return
        left = {r: s for r, s in (("limit", limit_left), ("window", window_left)) if s is not None}
        if not left:
            return
        reason = min(left, key=left.__getitem__)
        if left[reason] <= WARN_BEFORE.total_seconds() and self._warned != self.started_at:
            self._warned = self.started_at
            self.hass.bus.async_fire(
                EVENT_WARNING,
                {
                    "entry_id": self.entry_id,
                    "name": self.name,
                    "reason": reason,
                    "minutes_left": math.ceil(left[reason] / 60),
                },
            )

    @callback
    def _tick(self, _now: datetime) -> None:
        self._roll_day()
        self._update()

    # --- persistence ----------------------------------------------------

    def _save(self) -> None:
        self._store.async_delay_save(self._data, 1)

    def _data(self) -> dict:
        return {
            "daily_limit": self.daily_limit,
            "allowed_from": self.allowed_from.isoformat(),
            "allowed_until": self.allowed_until.isoformat(),
            "manual_lock": self.manual_lock,
            "day": self.day.isoformat(),
            "played": self.played,
            "counted_start": self.counted_start.isoformat() if self.counted_start else None,
        }
