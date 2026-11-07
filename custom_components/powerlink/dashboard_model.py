"""Small pure-Python presentation helpers for the PowerLink dashboard.

This module intentionally has no Home Assistant, Plex or Fire TV imports.
The backend is authoritative; clients never calculate or mutate rotations.
"""
from __future__ import annotations

import hashlib
import json
from typing import Mapping

LIVE_SECONDS = 60
STARTING_SECONDS = 90


def show_id(profile_id: str, show_guid: str, server_id: str) -> str:
    """Stable, opaque-ish identifier for one Plex profile/show/server rotation."""
    key = json.dumps(
        [profile_id, show_guid, server_id], separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def session_status(state: Mapping, now: float) -> str:
    """Truthful, bounded indication based on *existing* app progress events.

    A recent progress report proves the video was advancing recently; it does
    not prove that a user has not pressed pause in the following seconds.
    """
    pending_since = state.get("pending_since") or 0
    if state.get("pending_destination") and 0 <= now - pending_since < STARTING_SECONDS:
        return "starting"
    last_event_at = state.get("last_event_at") or 0
    if (
        state.get("last_event") == "launch_requested"
        and 0 <= now - last_event_at < STARTING_SECONDS
        and not state.get("playing")
    ):
        return "starting"
    if state.get("playing"):
        heartbeat = state.get("last_heartbeat") or 0
        if heartbeat > 0 and 0 <= now - heartbeat <= LIVE_SECONDS:
            return "recently_playing"
        return "stale"
    if state.get("last_event") == "exited":
        return "stopped"
    return "idle"


def dashboard_show(state: Mapping, now: float, summary: Mapping) -> dict:
    """Bounded presentation data; no full queue, credentials or Plex tokens."""
    cursor = state.get("cursor", 0)
    queue = state.get("queue") or []
    key = queue[cursor] if isinstance(cursor, int) and 0 <= cursor < len(queue) else None
    episode = (state.get("episodes") or {}).get(key) if key else None
    current = None
    if isinstance(episode, dict):
        current = {
            "key": key,
            "title": episode.get("title", ""),
            "season": episode.get("season"),
            "episode": episode.get("episode"),
        }
    status = session_status(state, now)
    return {
        "id": show_id(state["profile_id"], state["show_guid"], state["server_id"]),
        "title": state["title"],
        "profile_id": state["profile_id"],
        "show_guid": state["show_guid"],
        "server_id": state["server_id"],
        "cycle": summary["cycle"],
        "total": summary["total"],
        "completed": summary["completed"],
        "remaining": summary["remaining"],
        "percent": summary["percent"],
        "eligible": summary["eligible"],
        "five_day_skips": summary["five_day_skips"],
        "next_cycle_skips": summary["next_cycle_skips"],
        "completed_cycles": summary["completed_cycles"],
        "partial_cycles": summary["partial_cycles"],
        "total_completions": summary["total_completions"],
        "cycle_status": summary["status"],
        "session_status": status,
        "owner": state.get("owner") or "",
        "pending_destination": (
            state.get("pending_destination", "")
            if status == "starting" else ""
        ),
        "current_episode": current,
        "position_ms": state.get("position_ms", 0) if status in ("recently_playing", "stale") else 0,
        "duration_ms": state.get("duration_ms", 0) if status in ("recently_playing", "stale") else 0,
        "last_activity": state.get("last_activity", 0),
        "last_heartbeat": state.get("last_heartbeat", 0),
    }


def apply_transfer_status(shows: list[dict], transfers: object, now_monotonic: float) -> None:
    """Overlay *unacknowledged* live handoffs, without touching master state.

    This reads only the relay operations PowerLink already knows about.
    Long-lived relay capabilities are not themselves proof of playback:
    only unacknowledged transfers younger than 12 minutes are displayed.
    """
    for transfer in transfers:
        if transfer.acknowledged or not 0 <= now_monotonic - transfer.created_at <= 720:
            continue
        snapshot = transfer.snapshot
        if not snapshot.source_device or not snapshot.context_key.startswith("powershuffle:"):
            continue
        if not snapshot.items:
            continue
        first = snapshot.items[0]
        for show in shows:
            if (
                show["profile_id"] == snapshot.profile_id
                and show["show_guid"] == first.get("show_guid")
                and show["server_id"] == first.get("server_id")
                and show["owner"] == snapshot.source_device
            ):
                show["session_status"] = "transferring"
                show["pending_destination"] = transfer.destination.entity_id
                break
