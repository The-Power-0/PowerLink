"""Persistent, profile-isolated PowerShuffle state and deterministic transitions.

PowerShuffle never reads or writes Plex watched flags. A paired PowerPlay
client sends show inventory only when launched and player events only while
PowerShuffle is active; there is no background polling or extra ADB session.
"""
from __future__ import annotations

import asyncio
import json
import random
import secrets
import time
from typing import Any, Mapping

from homeassistant.helpers.storage import Store
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .handoff import HandoffError, QueueSnapshot
from .dashboard_model import dashboard_show, show_id

SKIP_SECONDS = 5 * 24 * 3600
IDLE_SECONDS = 12 * 3600
RECENT_SECONDS = 7 * 24 * 3600
LIVE_SECONDS = 60
MAX_EPISODES = 5000
MAX_EVENTS = 2048
STORAGE_KEY = "powerlink.powershuffle"
DEFAULT_SETTINGS = {"inactivity_hours": 12, "recent_days": 7, "queue_window": 10,
                    "completion_percent": 90, "include_specials": False}
SETTINGS_OPTIONS = {"inactivity_hours": (1, 6, 12, 24, 48),
                    "recent_days": (0, 3, 7, 14, 30),
                    "queue_window": (5, 10, 15, 20, 32),
                    "completion_percent": (80, 85, 90, 95)}


def _required(value: object, name: str, size: int = 300) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > size:
        raise HandoffError(f"Invalid PowerShuffle {name}")
    return value.strip()


def _episode_key(row: Mapping[str, Any]) -> str:
    season, episode = row.get("season"), row.get("episode")
    if type(season) is not int or type(episode) is not int or not 0 <= season < 1000 or not 0 <= episode < 10000:
        raise HandoffError("PowerShuffle requires verified season and episode numbers")
    return f"{season}:{episode}"


def _parse_episode(raw: object, show_guid: str) -> tuple[str, dict]:
    if not isinstance(raw, dict):
        raise HandoffError("Invalid PowerShuffle episode")
    key = _episode_key(raw)
    server = _required(raw.get("server_id"), "episode server", 150)
    rating = _required(raw.get("rating_key"), "episode rating key", 150)
    title = _required(raw.get("title"), "episode title")
    guid = raw.get("guid") or ""
    if not isinstance(guid, str) or len(guid) > 300:
        raise HandoffError("Invalid episode GUID")
    if raw.get("show_guid") not in (None, show_guid):
        raise HandoffError("Episode belongs to a different show")
    copies = raw.get("copies") or []
    if not isinstance(copies, list) or len(copies) > 20:
        raise HandoffError("Invalid episode copies")
    # Only expected, length-bounded fields reach persistent storage or the
    # existing PowerLink QueueSnapshot parser.
    entry = {
        "kind": "episode", "title": title, "guid": guid,
        "show_guid": show_guid, "server_id": server, "rating_key": rating,
        "season": int(raw["season"]), "episode": int(raw["episode"]),
        "show_title": str(raw.get("show_title") or "")[:300],
        "copies": copies,
    }
    clean = QueueSnapshot.parse({
        "schema": 1, "items": [entry], "current_index": 0,
        "position_ms": 0, "shuffled": True, "source_device": "inventory",
        "profile_id": "inventory", "context_key": "",
    })
    # The snapshot parser bounds every field, including nested Plex copies.
    # Persist only its normalized values, never raw client-supplied metadata.
    return key, clean.items[0]


class PowerShuffleManager:
    def __init__(self, hass) -> None:
        self._hass = hass
        self._store = Store(hass, 1, STORAGE_KEY)
        self._shows: dict[str, dict] = {}
        self._settings = dict(DEFAULT_SETTINGS)
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        saved = await self._store.async_load() or {}
        self._shows = saved.get("shows", {}) if isinstance(saved.get("shows"), dict) else {}
        raw = saved.get("settings", {})
        if isinstance(raw, dict):
            for key, allowed in SETTINGS_OPTIONS.items():
                if type(raw.get(key)) is int and raw[key] in allowed:
                    self._settings[key] = raw[key]
            if type(raw.get("include_specials")) is bool:
                self._settings["include_specials"] = raw["include_specials"]

    async def _save(self) -> None:
        await self._store.async_save({"shows": self._shows, "settings": self._settings})
        async_dispatcher_send(self._hass, "powerlink_powershuffle_updated")

    @staticmethod
    def key(profile: str, show_guid: str, server_id: str) -> str:
        return json.dumps([profile, show_guid, server_id], separators=(",", ":"))

    @staticmethod
    def _new(profile: str, show_guid: str, server_id: str, title: str) -> dict:
        return {
            "profile_id": profile, "show_guid": show_guid, "server_id": server_id,
            "title": title, "cycle": 1, "episodes": {}, "completed": {},
            "skips": {}, "recent_completed": {}, "history": [],
            "queue": [], "cursor": 0, "session_id": "", "owner": "",
            "playing": False, "started": False, "position_ms": 0,
            "duration_ms": 0, "last_event": "", "last_event_at": 0,
            "pending_destination": "", "pending_since": 0,
            "coverage_episode": "", "coverage": [],
            "last_activity": 0, "last_heartbeat": 0, "event_ids": [],
            "total_completions": 0, "completed_cycles": 0, "partial_cycles": 0,
        }

    @staticmethod
    def _eligible(state: dict, now: float) -> list[str]:
        cycle = state["cycle"]
        return [key for key in state["episodes"]
                if key not in state["completed"]
                and state["skips"].get(key, {}).get("until", 0) <= now
                and state["skips"].get(key, {}).get("until_cycle") != cycle]

    def _ordered(self, state: dict, now: float) -> list[str]:
        eligible = self._eligible(state, now)
        protect_seconds = self._settings["recent_days"] * 86400
        protected = [key for key in eligible
                     if now - state["recent_completed"].get(key, -1e12) < protect_seconds]
        primary = [key for key in eligible if key not in protected]
        # Repeat protection is soft: use protected items after the preferred pool.
        random.SystemRandom().shuffle(primary)
        random.SystemRandom().shuffle(protected)
        return primary + protected

    @staticmethod
    def _cycle_status(state: dict, now: float) -> str:
        if not state["episodes"]:
            return "empty"
        remaining = [key for key in state["episodes"] if key not in state["completed"]]
        if not remaining:
            return "full"
        deferred = [k for k in remaining if state["skips"].get(k, {}).get("until_cycle") == state["cycle"]]
        timed = [k for k in remaining if state["skips"].get(k, {}).get("until", 0) > now]
        # A show with playable episodes is not "waiting" merely because
        # another episode has a timed cooldown.
        if PowerShuffleManager._eligible(state, now):
            return "available"
        # Five-day skips are hard exclusions; they outlive cycle boundaries.
        # Only an all-deferred rotation with no active timed skip may roll.
        if timed:
            return "waiting"
        if len(deferred) == len(remaining):
            return "partial"
        return "waiting"

    @staticmethod
    def _rollover(state: dict, reason: str, now: float) -> None:
        total = len(state["episodes"])
        count = len(state["completed"])
        state["history"].append({
            "cycle": state["cycle"], "reason": reason,
            "completed": count, "total": total, "ended_at": now,
        })
        state["history"] = state["history"][-100:]
        if reason == "full":
            state["completed_cycles"] += 1
        elif reason == "partial":
            state["partial_cycles"] += 1
        state["cycle"] += 1
        state["completed"] = {}
        # Only cycle-scoped deferrals expire here; five-day cooldowns survive.
        state["skips"] = {k: v for k, v in state["skips"].items()
                          if v.get("until", 0) > now}
        state["queue"] = []
        state["cursor"] = 0

    def _refresh_queue(self, state: dict, now: float, *, new_order: bool = False) -> None:
        status = self._cycle_status(state, now)
        if status in ("full", "partial"):
            self._rollover(state, status, now)
            new_order = True
        allowed = set(self._eligible(state, now))
        remaining = state["queue"][state["cursor"]:] if not new_order else []
        retained = [k for k in remaining if k in allowed]
        selected = set(retained)
        additional = [k for k in self._ordered(state, now) if k not in selected]
        state["queue"] = retained + additional
        state["cursor"] = 0

    @staticmethod
    def _summary(state: dict, now: float) -> dict:
        total = len(state["episodes"])
        completed = len(state["completed"])
        timed = sum(v.get("until", 0) > now for k, v in state["skips"].items()
                    if k in state["episodes"] and k not in state["completed"])
        deferred = sum(v.get("until_cycle") == state["cycle"] for k, v in state["skips"].items()
                       if k in state["episodes"] and k not in state["completed"])
        return {
            "profile_id": state["profile_id"], "show_guid": state["show_guid"],
            "server_id": state["server_id"], "title": state["title"],
            "cycle": state["cycle"], "total": total, "completed": completed,
            "remaining": total-completed, "percent": round(100*completed/total, 2) if total else 0,
            "eligible": len(PowerShuffleManager._eligible(state, now)),
            "five_day_skips": timed, "next_cycle_skips": deferred,
            "total_completions": state["total_completions"],
            "completed_cycles": state["completed_cycles"], "partial_cycles": state["partial_cycles"],
            "owner": state["owner"], "last_activity": state["last_activity"],
            "status": PowerShuffleManager._cycle_status(state, now),
        }

    async def start(self, source: str, payload: Mapping[str, Any]) -> dict:
        profile = _required(payload.get("profile_id"), "profile", 250)
        show_guid = _required(payload.get("show_guid"), "show GUID", 300)
        server_id = _required(payload.get("server_id"), "server ID", 150)
        title = _required(payload.get("title"), "show title", 300)
        inventory = payload.get("episodes")
        if not isinstance(inventory, list) or not 1 <= len(inventory) <= MAX_EPISODES:
            raise HandoffError("PowerShuffle inventory must contain 1 to 5000 episodes")
        parsed = {}
        for raw in inventory:
            key, episode = _parse_episode(raw, show_guid)
            # Specials excluded unless explicitly enabled by settings.
            if episode["season"] == 0 and not self._settings["include_specials"]:
                continue
            if episode["server_id"] != server_id:
                raise HandoffError("PowerShuffle inventory spans Plex servers")
            parsed.setdefault(key, episode)
        if not parsed:
            raise HandoffError("No regular episodes available for PowerShuffle")
        now = time.time()
        async with self._lock:
            key = self.key(profile, show_guid, server_id)
            state = self._shows.setdefault(key, self._new(profile, show_guid, server_id, title))
            state["title"] = title
            # Removed episodes leave the active denominator but keep lifetime
            # event and cycle history. Episodes re-added later retain any
            # current-cycle completion/skip record by stable season/index.
            state["episodes"] = parsed
            state["completed"] = {k: v for k, v in state["completed"].items() if k in parsed}
            state["skips"] = {k: v for k, v in state["skips"].items() if k in parsed}
            if state["playing"] and state["owner"] != source and now - state["last_heartbeat"] < LIVE_SECONDS:
                # The HTTP controller performs the existing safe PowerLink
                # handoff. Do NOT transfer ownership until receiver ACK.
                raw = self._snapshot(state)
                await self._save()
                return {"action": "handoff", "snapshot": raw, "summary": self._summary(state, now)}
            if state["playing"] and state["owner"] == source and now - state["last_heartbeat"] < LIVE_SECONDS:
                await self._save()
                return {"action": "already_playing", "summary": self._summary(state, now)}
            expired = now - state["last_activity"] >= self._settings["inactivity_hours"] * 3600
            self._refresh_queue(state, now, new_order=expired or not state["queue"])
            state["session_id"] = secrets.token_urlsafe(24)
            state["owner"] = source
            state["playing"] = False
            state["started"] = False
            state["position_ms"] = 0
            state["duration_ms"] = 0
            state["last_activity"] = now
            state["last_heartbeat"] = 0
            state["last_event"] = "launch_requested"
            state["last_event_at"] = now
            state["pending_destination"] = ""
            state["pending_since"] = 0
            state["coverage_episode"] = ""
            state["coverage"] = []
            result = self._queue_reply(state, now)
            await self._save()
            return result

    def _queue_reply(self, state: dict, now: float) -> dict:
        size = self._settings["queue_window"]
        return {
            "action": "play" if state["queue"] else "waiting",
            "session_id": state["session_id"], "cycle": state["cycle"],
            # The full order is authoritative on HA. The Fire Stick receives
            # only a rolling 10-item window to avoid maintaining giant queues.
            "queue": list(state["queue"][state["cursor"]:state["cursor"] + size]),
            "settings": dict(self._settings), "current_index": 0,
            "summary": PowerShuffleManager._summary(state, now),
        }

    async def launch_from_home(
        self, show_title: str | None, destination: str,
        *, profile_id: str | None = None, server_id: str | None = None,
        dashboard_id: str | None = None,
    ) -> dict:
        """Begin a *previously registered* show using the existing receiver.

        A PowerPlay deep link for PowerShuffle itself does not exist in
        v1.4.0. This reuses the authenticated PowerLink transfer receiver,
        including profile selection, destination quality matching and ACK.
        Only HA-authenticated service calls may invoke this method.
        """
        title = _required(show_title, "show title") if dashboard_id is None else None
        _required(destination, "destination device", 250)
        if profile_id is not None:
            profile_id = _required(profile_id, "profile", 250)
        if server_id is not None:
            server_id = _required(server_id, "server", 150)
        now = time.time()
        async with self._lock:
            candidates = (
                [self._by_dashboard_id(dashboard_id)]
                if dashboard_id is not None else self._shows.values()
            )
            matches = [
                st for st in candidates
                if (title is None or st["title"].casefold() == title.casefold())
                and (profile_id is None or st["profile_id"] == profile_id)
                and (server_id is None or st["server_id"] == server_id)
            ]
            if not matches:
                raise HandoffError(
                    "This show has not been registered in PowerShuffle. "
                    "Start it once in PowerPlay on a paired Fire Stick."
                )
            if len(matches) != 1:
                raise HandoffError(
                    "This show has more than one Plex profile or server. "
                    "Provide profile_id and server_id to choose an exact rotation."
                )
            state = matches[0]
            if state["playing"] and now - state["last_heartbeat"] < LIVE_SECONDS:
                if state["owner"] == destination:
                    return {"action": "already_playing", "destination": destination}
                return {
                    "action": "handoff", "source": state["owner"],
                    "destination": destination,
                }
            if now - state.get("pending_since", 0) < LIVE_SECONDS:
                if state.get("pending_destination") == destination:
                    return {"action": "already_pending", "destination": destination}
                raise HandoffError(
                    "Another PowerShuffle launch is being prepared; try again "
                    "after the previous receiver has acknowledged or timed out."
                )
            expired = (
                now - state["last_activity"] >=
                self._settings["inactivity_hours"] * 3600
            )
            self._refresh_queue(state, now, new_order=expired or not state["queue"])
            if not state["queue"]:
                raise HandoffError(
                    "No episodes are currently eligible. Respect active "
                    "five-day skips or adjust them in PowerLink settings."
                )
            state["session_id"] = secrets.token_urlsafe(24)
            # A fresh HA launch has no source TV to pause on receiver ACK.
            state["owner"] = ""
            state["playing"] = False
            state["started"] = False
            state["position_ms"] = 0
            state["duration_ms"] = 0
            state["coverage"] = []
            state["coverage_episode"] = ""
            state["last_activity"] = now
            state["last_heartbeat"] = 0
            state["last_event"] = "launch_requested"
            state["last_event_at"] = now
            state["pending_destination"] = destination
            state["pending_since"] = now
            snapshot = self._snapshot(state)
            await self._save()
            return {"action": "launch", "snapshot": snapshot,
                    "session_id": state["session_id"], "destination": destination}

    async def cancel_pending_launch(self, session_id: str) -> None:
        """Make a failed HA-driven launch retryable, without erasing history."""
        async with self._lock:
            for state in self._shows.values():
                if (state.get("session_id") == session_id
                        and state.get("pending_destination")
                        and not state["started"] and not state["playing"]):
                    state["pending_destination"] = ""
                    state["pending_since"] = 0
                    state["session_id"] = ""
                    state["owner"] = ""
                    await self._save()
                    return

    @staticmethod
    def _snapshot(state: dict) -> dict:
        queue = [k for k in state["queue"] if k in state["episodes"]]
        if not queue:
            raise HandoffError("PowerShuffle queue has no transferable episodes")
        current = min(state["cursor"], len(queue)-1)
        return {
            "schema": 1, "items": [state["episodes"][k] for k in queue],
            "current_index": current, "position_ms": int(state["position_ms"]),
            "source_device": state["owner"], "profile_id": state["profile_id"],
            "shuffled": True, "context_key": "powershuffle:" + state["session_id"],
        }

    async def snapshot_for_handoff(
        self, source: str, session_id: str, profile_id: str,
        position_ms: object,
    ) -> dict:
        """Bind a real source playhead to HA's complete logical queue.

        The playing Fire Stick has only a 10-item window; a PowerLink
        transfer must still carry the full rotation including its future
        entries and preserve the current playback position.
        """
        _required(session_id, "session ID", 100)
        if type(position_ms) is not int or not 0 <= position_ms <= 86_400_000:
            raise HandoffError("Invalid PowerShuffle handoff playhead")
        async with self._lock:
            for state in self._shows.values():
                if state.get("session_id") != session_id:
                    continue
                if state.get("owner") != source or state.get("profile_id") != profile_id:
                    raise HandoffError("PowerShuffle handoff identity does not match owner")
                if not state.get("started") or not state.get("playing"):
                    raise HandoffError("PowerShuffle must be playing before a handoff")
                snapshot = self._snapshot(state)
                snapshot["position_ms"] = position_ms
                return snapshot
        raise HandoffError("Unknown PowerShuffle session for handoff")

    async def handoff_ack(self, session_id: str, destination: str, started: bool) -> None:
        if not started:
            return
        now = time.time()
        async with self._lock:
            for state in self._shows.values():
                if state.get("session_id") == session_id:
                    state["owner"] = destination
                    state["playing"] = True
                    state["last_activity"] = now
                    state["last_heartbeat"] = now
                    state["started"] = True
                    state["last_event"] = "receiver_ack"
                    state["last_event_at"] = now
                    state["pending_destination"] = ""
                    state["pending_since"] = 0
                    await self._save()
                    return

    async def event(self, source: str, payload: Mapping[str, Any]) -> dict:
        profile = _required(payload.get("profile_id"), "profile", 250)
        show_guid = _required(payload.get("show_guid"), "show GUID", 300)
        server_id = _required(payload.get("server_id"), "server ID", 150)
        session_id = _required(payload.get("session_id"), "session", 100)
        event_id = _required(payload.get("event_id"), "event ID", 150)
        action = payload.get("action")
        if action not in ("started", "progress", "completed", "skip_five_days", "skip_next_cycle", "ended", "exited"):
            raise HandoffError("Unsupported PowerShuffle event")
        now = time.time()
        async with self._lock:
            state = self._shows.get(self.key(profile, show_guid, server_id))
            if state is None or state.get("session_id") != session_id:
                raise HandoffError("PowerShuffle session is no longer active")
            if state["owner"] != source:
                raise HandoffError("PowerShuffle playback belongs to another device")
            if event_id in state["event_ids"]:
                return {"accepted": True, "duplicate": True, **self._queue_reply(state, now)}
            item = _required(payload.get("episode_key"), "episode key", 30)
            if item not in state["episodes"]:
                raise HandoffError("PowerShuffle episode is not in the tracked show")
            if action != "exited":
                # Stale events from an earlier queue member cannot update
                # the currently playing episode after a skip/advance.
                if not state["queue"] or state["queue"][state["cursor"]] != item:
                    raise HandoffError("PowerShuffle event is for a stale episode")
            position = payload.get("position_ms", 0)
            if type(position) is not int or not 0 <= position <= 86_400_000:
                raise HandoffError("Invalid PowerShuffle playback position")
            if action == "started":
                if not state["started"] or state.get("coverage_episode") != item:
                    state["coverage_episode"] = item
                    state["coverage"] = []
                state["started"] = True
                state["playing"] = True
            elif action == "progress":
                if not state["started"]:
                    raise HandoffError("Cannot record progress before playback starts")
                duration = payload.get("duration_ms", 0)
                if type(duration) is int and 0 < duration <= 86_400_000:
                    self._merge_coverage(state, payload.get("ranges", []), duration)
                state["playing"] = True
            elif action == "completed":
                if not state["started"]:
                    raise HandoffError("Cannot complete an episode before actual playback")
                duration = payload.get("duration_ms")
                if type(duration) is not int or not 0 < duration <= 86_400_000:
                    raise HandoffError("Invalid episode duration")
                # Only unique observed time ranges, never seek destinations,
                # count. Ranges survive PowerLink handoff of this attempt.
                coverage = self._merge_coverage(state, payload.get("ranges", []), duration)
                viewed = sum(end - start for start, end in coverage)
                if viewed * 100 < duration * self._settings["completion_percent"]:
                    raise HandoffError("Completion requires 90% of unique content viewed")
                if item not in state["completed"]:
                    state["completed"][item] = now
                    state["recent_completed"][item] = now
                    state["total_completions"] += 1
                # A threshold completion records history immediately but
                # does not move the cursor while the final 10% is still
                # playing. EOF or a deliberate Next performs the advance.
            elif action == "ended":
                if not state["started"]:
                    raise HandoffError("Cannot advance before actual playback")
                state["playing"] = False
                state["started"] = False
                state["position_ms"] = 0
                state["coverage"] = []
                state["coverage_episode"] = ""
                self._advance(state, now)
            elif action in ("skip_five_days", "skip_next_cycle"):
                if not state["started"]:
                    raise HandoffError("Cannot skip an episode before it begins playback")
                if item not in state["completed"]:
                    state["skips"][item] = (
                        {"until": now + SKIP_SECONDS, "kind": "five_days", "created": now}
                        if action == "skip_five_days" else
                        {"until_cycle": state["cycle"], "kind": "next_cycle", "created": now}
                    )
                state["playing"] = False
                state["started"] = False
                state["position_ms"] = 0
                requested = payload.get("requested_episode_key")
                if requested is not None and not isinstance(requested, str):
                    raise HandoffError("Invalid requested PowerShuffle episode")
                self._advance(state, now)
                if requested and requested in state["queue"]:
                    state["queue"].remove(requested)
                    state["queue"].insert(0, requested)
            elif action == "exited":
                # An early normal exit never creates a skip.
                state["playing"] = False
                state["started"] = False
                state["coverage"] = []
                state["coverage_episode"] = ""
            if action in ("progress", "started"):
                state["position_ms"] = position
                duration = payload.get("duration_ms")
                if type(duration) is int and 0 < duration <= 86_400_000:
                    state["duration_ms"] = duration
                state["last_heartbeat"] = now
            state["last_activity"] = now
            state["last_event"] = action
            state["last_event_at"] = now
            state["event_ids"].append(event_id)
            state["event_ids"] = state["event_ids"][-MAX_EVENTS:]
            await self._save()
            return {"accepted": True, **self._queue_reply(state, now)}

    @staticmethod
    def _merge_coverage(state: dict, supplied: object, duration: int) -> list[list[int]]:
        if not isinstance(supplied, list) or len(supplied) > 128:
            raise HandoffError("Invalid PowerShuffle playback coverage")
        all_ranges = list(state.get("coverage", []))
        for pair in supplied:
            if (not isinstance(pair, list) or len(pair) != 2
                    or type(pair[0]) is not int or type(pair[1]) is not int
                    or not 0 <= pair[0] < pair[1] <= duration):
                raise HandoffError("Invalid viewed content range")
            all_ranges.append(pair)
        all_ranges.sort()
        merged: list[list[int]] = []
        for start, end in all_ranges:
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        if len(merged) > 128:
            raise HandoffError("Too many playback coverage ranges")
        state["coverage"] = merged
        return merged

    def _advance(self, state: dict, now: float) -> None:
        # Remove consumed/invalid entries and refill from eligible episodes.
        state["cursor"] += 1
        self._refresh_queue(state, now)


    async def attach(self, source: str, session_id: str) -> dict:
        """Receiver joins only after the existing PowerLink playback ACK."""
        _required(session_id, "session ID", 100)
        async with self._lock:
            for state in self._shows.values():
                if state.get("session_id") == session_id and state["owner"] == source:
                    return {
                        "action": "attached", "session_id": session_id,
                        "profile_id": state["profile_id"],
                        "show_guid": state["show_guid"],
                        "server_id": state["server_id"],
                        "cycle": state["cycle"],
                        "queue": list(state["queue"][state["cursor"]:state["cursor"] + self._settings["queue_window"]]),
                        "current_index": 0,
                        "settings": dict(self._settings),
                        "coverage_episode": state.get("coverage_episode", ""),
                        "coverage": state.get("coverage", []),
                        "summary": self._summary(state, time.time()),
                    }
        raise HandoffError("PowerShuffle receiver is not the acknowledged owner")

    @property
    def settings(self) -> dict:
        return dict(self._settings)

    async def configure(self, requested: Mapping[str, Any]) -> dict:
        validated = {}
        if set(requested) != set(DEFAULT_SETTINGS):
            raise HandoffError("PowerShuffle settings must be specified together")
        for key, allowed in SETTINGS_OPTIONS.items():
            if type(requested.get(key)) is not int or requested[key] not in allowed:
                raise HandoffError(f"Invalid PowerShuffle {key}")
            validated[key] = requested[key]
        if type(requested.get("include_specials")) is not bool:
            raise HandoffError("Invalid PowerShuffle specials setting")
        validated["include_specials"] = requested["include_specials"]
        async with self._lock:
            now = time.time()
            if any(
                show.get("playing") and now - show.get("last_heartbeat", 0) < LIVE_SECONDS
                for show in self._shows.values()
            ):
                raise HandoffError(
                    "Stop active PowerShuffle playback before editing global settings"
                )
            self._settings = validated
            await self._save()
            return dict(validated)

    def _by_dashboard_id(self, dashboard_id: str) -> dict:
        if not isinstance(dashboard_id, str) or len(dashboard_id) != 32 or any(
            c not in "0123456789abcdef" for c in dashboard_id
        ):
            raise HandoffError("Invalid PowerShuffle dashboard show ID")
        for state in self._shows.values():
            if show_id(state["profile_id"], state["show_guid"], state["server_id"]) == dashboard_id:
                return state
        raise HandoffError("Unknown PowerShuffle dashboard show ID")

    async def dashboard_snapshot(self, *, offset: int = 0, limit: int = 100) -> dict:
        """Compact, paginated, read-only view of the HA master history."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise HandoffError("Invalid PowerShuffle dashboard pagination")
        now = time.time()
        async with self._lock:
            ordered = sorted(
                self._shows.values(),
                key=lambda row: (-row.get("last_activity", 0),
                                 row.get("title", "").casefold(), row["profile_id"]),
            )
            shows = [
                dashboard_show(row, now, self._summary(row, now))
                for row in ordered[offset:offset + limit]
            ]
            return {
                "version": 1,
                "total_shows": len(ordered),
                "offset": offset,
                "limit": limit,
                "shows": shows,
            }

    async def dashboard_identity(self, dashboard_id: str) -> dict:
        """Lookup identity for an authenticated, on-demand artwork request."""
        async with self._lock:
            state = self._by_dashboard_id(dashboard_id)
            return {
                "title": state["title"],
                "guid": state["show_guid"],
                "server_id": state["server_id"],
            }

    async def dashboard_details(
        self, dashboard_id: str, *, offset: int = 0, limit: int = 50,
    ) -> dict:
        """Paginate individual episodes; never put 5000 entries in a HA state."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise HandoffError("Invalid PowerShuffle episode pagination")
        now = time.time()
        async with self._lock:
            state = self._by_dashboard_id(dashboard_id)
            keys = sorted(
                state["episodes"],
                key=lambda key: tuple(int(n) for n in key.split(":")),
            )
            rows = []
            for key in keys[offset:offset + limit]:
                episode = state["episodes"][key]
                skip = state["skips"].get(key, {})
                if key in state["completed"]:
                    status = "completed"
                elif skip.get("until", 0) > now:
                    status = "skip_five_days"
                elif skip.get("until_cycle") == state["cycle"]:
                    status = "skip_next_cycle"
                else:
                    status = "available"
                rows.append({
                    "episode_key": key,
                    "title": episode["title"],
                    "season": episode["season"],
                    "episode": episode["episode"],
                    "status": status,
                    "skip_until": skip.get("until") if status == "skip_five_days" else None,
                    "completed_at": state["completed"].get(key),
                })
            return {
                "show": dashboard_show(state, now, self._summary(state, now)),
                "total_episodes": len(keys),
                "offset": offset,
                "limit": limit,
                "episodes": rows,
                "cycle_history": list(state["history"][-30:]),
            }

    async def list_shows(self) -> list[dict]:
        now = time.time()
        async with self._lock:
            return [self._summary(s, now) for s in self._shows.values()]

    async def details(self, profile: str, show_guid: str, server_id: str) -> dict:
        """On-demand episode details for the UI; no per-episode HA entities."""
        now = time.time()
        async with self._lock:
            state = self._shows.get(self.key(profile, show_guid, server_id))
            if state is None:
                raise HandoffError("Unknown tracked PowerShuffle show")
            episodes = []
            for key, entry in sorted(
                state["episodes"].items(), key=lambda row: tuple(
                    int(x) for x in row[0].split(":")
                ),
            ):
                skip = state["skips"].get(key, {})
                if key in state["completed"]:
                    status = "completed"
                elif skip.get("until", 0) > now:
                    status = "skip_five_days"
                elif skip.get("until_cycle") == state["cycle"]:
                    status = "skip_next_cycle"
                else:
                    status = "available"
                episodes.append({
                    "episode_key": key, "title": entry["title"], "status": status,
                    "skip_until": skip.get("until") if status == "skip_five_days" else None,
                    "completed_at": state["completed"].get(key),
                })
            return {
                "summary": self._summary(state, now),
                "cycle_history": list(state["history"][-30:]),
                "episodes": episodes,
            }

    async def manage(self, profile: str, show_guid: str, server_id: str, command: str) -> dict:
        if command not in ("reset_cycle", "clear_cooldowns", "reset_cycle_and_cooldowns"):
            raise HandoffError("Unsupported PowerShuffle management action")
        now = time.time()
        async with self._lock:
            state = self._shows.get(self.key(profile, show_guid, server_id))
            if state is None:
                raise HandoffError("Unknown tracked show")
            if state["playing"] and now - state["last_heartbeat"] < LIVE_SECONDS:
                raise HandoffError("Stop PowerShuffle playback before resetting a cycle")
            if command in ("reset_cycle", "reset_cycle_and_cooldowns"):
                self._rollover(state, "manual", now)
            if command in ("clear_cooldowns", "reset_cycle_and_cooldowns"):
                state["skips"] = {k: v for k, v in state["skips"].items() if v.get("kind") == "next_cycle"}
            state["session_id"] = ""
            state["owner"] = ""
            state["playing"] = False
            state["pending_destination"] = ""
            state["pending_since"] = 0
            state["queue"] = []
            await self._save()
            return self._summary(state, now)
