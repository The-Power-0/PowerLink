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

SKIP_SECONDS = 5 * 24 * 3600
IDLE_SECONDS = 12 * 3600
RECENT_SECONDS = 7 * 24 * 3600
LIVE_SECONDS = 60
MAX_EPISODES = 5000
MAX_EVENTS = 2048
STORAGE_KEY = "powerlink.powershuffle"


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
    QueueSnapshot.parse({
        "schema": 1, "items": [entry], "current_index": 0,
        "position_ms": 0, "shuffled": True, "source_device": "inventory",
        "profile_id": "inventory", "context_key": "",
    })
    return key, entry


class PowerShuffleManager:
    def __init__(self, hass) -> None:
        self._hass = hass
        self._store = Store(hass, 1, STORAGE_KEY)
        self._shows: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        saved = await self._store.async_load() or {}
        self._shows = saved.get("shows", {}) if isinstance(saved.get("shows"), dict) else {}

    async def _save(self) -> None:
        await self._store.async_save({"shows": self._shows})
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

    @staticmethod
    def _ordered(state: dict, now: float) -> list[str]:
        eligible = PowerShuffleManager._eligible(state, now)
        protected = [key for key in eligible
                     if now - state["recent_completed"].get(key, -1e12) < RECENT_SECONDS]
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
        # Hard five-day cooldowns prevent rollover, even if some other
        # episodes are marked next-cycle. No skip can shorten a timed cooldown.
        if timed:
            return "waiting"
        if len(deferred) == len(remaining):
            return "partial"
        return "available"

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

    @classmethod
    def _refresh_queue(cls, state: dict, now: float, *, new_order: bool = False) -> None:
        status = cls._cycle_status(state, now)
        if status in ("full", "partial"):
            cls._rollover(state, status, now)
            new_order = True
        allowed = set(cls._eligible(state, now))
        remaining = state["queue"][state["cursor"]:] if not new_order else []
        retained = [k for k in remaining if k in allowed]
        selected = set(retained)
        additional = [k for k in cls._ordered(state, now) if k not in selected]
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
            if episode["season"] == 0 and payload.get("include_specials") is not True:
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
            expired = now - state["last_activity"] >= IDLE_SECONDS
            self._refresh_queue(state, now, new_order=expired or not state["queue"])
            state["session_id"] = secrets.token_urlsafe(24)
            state["owner"] = source
            state["playing"] = False
            state["started"] = False
            state["position_ms"] = 0
            state["last_activity"] = now
            state["last_heartbeat"] = 0
            state["coverage_episode"] = ""
            state["coverage"] = []
            result = self._queue_reply(state, now)
            await self._save()
            return result

    @staticmethod
    def _queue_reply(state: dict, now: float) -> dict:
        return {
            "action": "play" if state["queue"] else "waiting",
            "session_id": state["session_id"], "cycle": state["cycle"],
            "queue": list(state["queue"]), "current_index": state["cursor"],
            "summary": PowerShuffleManager._summary(state, now),
        }

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
                if viewed * 10 < duration * 9:
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
                state["last_heartbeat"] = now
            state["last_activity"] = now
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

    @classmethod
    def _advance(cls, state: dict, now: float) -> None:
        # Remove consumed/invalid entries and refill from eligible episodes.
        state["cursor"] += 1
        cls._refresh_queue(state, now)


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
                        "cycle": state["cycle"], "queue": list(state["queue"]),
                        "current_index": state["cursor"],
                        "coverage_episode": state.get("coverage_episode", ""),
                        "coverage": state.get("coverage", []),
                        "summary": self._summary(state, time.time()),
                    }
        raise HandoffError("PowerShuffle receiver is not the acknowledged owner")

    async def list_shows(self) -> list[dict]:
        now = time.time()
        async with self._lock:
            return [self._summary(s, now) for s in self._shows.values()]

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
            state["queue"] = []
            await self._save()
            return self._summary(state, now)
