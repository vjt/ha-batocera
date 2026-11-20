"""Play statistics: the sessions Home Assistant keeps, and what the page's
high scores show of them (batocera/stats).

Today, this month and the last 30 days come from the session log, kept
here since this version: every last_session the box sends, and the games
cut short when the box went away mid-game. All time comes from
EmulationStation's own counts in the catalog (time played, play count),
which hold the history from before ha-batocera.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

if TYPE_CHECKING:
    from .box import Box

KEEP = timedelta(days=400)  # thirteen months and some
DAYS = 30  # the chart's
TOP_TODAY, TOP = 10, 30  # games on the board: today, this month and all time


@dataclass(frozen=True)
class Session:
    start: datetime
    end: datetime
    system: str
    game_id: str | None  # None from daemons before game_id in last_session
    name: str
    cut: bool = False  # the box went away mid-game: ended then, as far as we know

    def overlaps(self, other: Session) -> bool:
        """Sessions never overlap on a box: two that do are the same one,
        seen again, or cut short and then reported in full."""
        return self.start == other.start or (self.start < other.end and other.start < self.end)

    def row(self) -> list[Any]:
        return [
            self.start.isoformat(),
            self.end.isoformat(),
            self.system,
            self.game_id,
            self.name,
            self.cut,
        ]


def _session(row: Any) -> Session | None:
    try:
        start, end, system, game_id, name, cut = row
        start, end = dt_util.parse_datetime(start), dt_util.parse_datetime(end)
    except (TypeError, ValueError):
        return None
    if start is None or end is None or end < start:
        return None
    return Session(start, end, str(system), game_id or None, str(name), bool(cut))


class SessionLog:
    """The sessions of one box, oldest first, kept for KEEP."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.sessions: list[Session] = []
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{entry_id}.sessions")

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        found = [s for row in data.get("sessions") or [] if (s := _session(row))]
        self.sessions = self._kept(sorted(found, key=lambda s: s.start))

    def add(self, session: Session) -> None:
        """Keep a session, in place of any it overlaps."""
        kept = [s for s in self.sessions if not s.overlaps(session)]
        kept.append(session)
        self.sessions = self._kept(sorted(kept, key=lambda s: s.start))
        self._store.async_delay_save(self._data, 1)

    def _kept(self, sessions: list[Session]) -> list[Session]:
        since = dt_util.now() - KEEP
        return [s for s in sessions if s.end >= since]

    def _data(self) -> dict:
        return {"sessions": [s.row() for s in self.sessions]}


def _days(start: datetime, end: datetime):
    """(local date, seconds) of a session, split at local midnight."""
    at = dt_util.as_local(start)
    end = dt_util.as_local(end)
    while at < end:
        midnight = dt_util.start_of_local_day(at.date() + timedelta(days=1))
        upto = min(end, midnight)
        yield at.date(), (upto - at).total_seconds()
        at = upto


class _Range:
    """Seconds per game and per system over some days."""

    def __init__(self, first: date, last: date) -> None:
        self.first, self.last = first, last
        self.games: dict[tuple[str, str], dict[str, Any]] = {}

    def add(self, session: Session, playing: bool) -> None:
        seconds = sum(s for d, s in _days(session.start, session.end) if self.first <= d <= self.last)
        if seconds <= 0:
            return
        key = (session.system, session.game_id or session.name)
        game = self.games.setdefault(
            key,
            {"id": session.game_id, "name": session.name, "system": session.system,
             "seconds": 0.0, "count": 0},
        )
        game["seconds"] += seconds
        game["count"] += 1
        if playing:
            game["playing"] = True

    def result(self, top: int) -> dict[str, Any]:
        games = sorted(
            ({**g, "seconds": round(g["seconds"])} for g in self.games.values()),
            key=lambda g: (-g["seconds"], g["name"].casefold()),
        )
        games = [g for g in games if g["seconds"] > 0]
        return {
            "seconds": sum(g["seconds"] for g in games),
            "games": games[:top],
            "systems": _systems(games),
        }


def _systems(games: list[dict[str, Any]], **extra: dict[str, int]) -> list[dict[str, Any]]:
    """Per system, from games already ranked: seconds, how many games, the
    top one; extra adds a field per system (name -> value)."""
    systems: dict[str, dict[str, Any]] = {}
    for game in games:
        system = systems.setdefault(
            game["system"],
            {"system": game["system"], "seconds": 0, "games": 0, "top": game["name"]},
        )
        system["seconds"] += game["seconds"]
        system["games"] += 1
    for name, values in extra.items():
        for system in systems.values():
            system[name] = values.get(system["system"], 0)
    return sorted(systems.values(), key=lambda s: (-s["seconds"], s["system"]))


def _all_time(box: Box) -> dict[str, Any] | None:
    """EmulationStation's counts over the catalog; None when its rows do not
    say the time played (a daemon before it)."""
    games = list(box.games.values())
    if not any(g.gametime is not None for g in games):
        return None
    ranked = sorted(
        (g for g in games if g.gametime),
        key=lambda g: (-(g.gametime or 0), g.name.casefold()),
    )
    rows = [
        {"id": g.id, "name": g.name, "system": g.system, "seconds": g.gametime, "count": g.playcount}
        for g in ranked
    ]
    played: dict[str, int] = defaultdict(int)
    owned: dict[str, int] = defaultdict(int)
    for g in games:
        owned[g.system] += 1
        played[g.system] += g.playcount > 0
    systems = _systems(rows, owned=owned)
    for system in systems:  # games played, not games with time
        system["games"] = played[system["system"]]
    return {
        "seconds": sum(g.gametime or 0 for g in games),
        "played": sum(played.values()),
        "owned": len(games),
        "games": rows[:TOP],
        "systems": systems,
    }


def compute(box: Box, now: datetime | None = None) -> dict[str, Any]:
    """What batocera/stats answers: see docs/internals.md."""
    now = dt_util.as_local(now or dt_util.now())
    today = now.date()
    sessions = list(box.session_log.sessions)
    running = box.running_session(now)
    if running:
        sessions = [s for s in sessions if not s.overlaps(running)] + [running]

    first = today - timedelta(days=DAYS - 1)
    per_day: dict[date, dict[str, float]] = {first + timedelta(days=i): {} for i in range(DAYS)}
    today_range = _Range(today, today)
    month_range = _Range(today.replace(day=1), today)
    for session in sessions:
        for day, seconds in _days(session.start, session.end):
            if day in per_day:
                per_day[day][session.system] = per_day[day].get(session.system, 0) + seconds
        today_range.add(session, session is running)
        month_range.add(session, session is running)

    days = [
        {"date": day.isoformat(),
         "seconds": {s: round(v) for s, v in by.items() if round(v) > 0}}
        for day, by in per_day.items()
    ]
    since = min((s.start for s in sessions), default=None)
    result: dict[str, Any] = {
        "date": today.isoformat(),
        "log_since": dt_util.as_local(since).date().isoformat() if since else None,
        "days": days,
        "today": today_range.result(TOP_TODAY),
        "month": month_range.result(TOP),
        "all": _all_time(box),
    }
    named = {s for d in days for s in d["seconds"]}
    for key in ("today", "month", "all"):
        if result[key]:
            named |= {s["system"] for s in result[key]["systems"]}
    fullnames = {e["name"]: str(e.get("fullname") or e["name"]) for e in box.systems}
    result["names"] = {s: fullnames.get(s, s) for s in sorted(named)}
    return result
