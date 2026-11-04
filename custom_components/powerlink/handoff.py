"""Pure-Python queue manifest and quality decisions. No imports from Home Assistant.

The source sends an *ordered* queue snapshot. A destination receives indexed
windows; the planner never shuffles, rebuilds, deduplicates, or drops entries.
The same snapshot works with ADB, VPN-routed, or future relay transport.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

MAX_QUEUE_ITEMS = 5000
MAX_SNAPSHOT_BYTES = 2_000_000
MAX_WINDOW_ITEMS = 32
ALLOWED_HEIGHTS = frozenset({480, 720, 1080, 1440, 2160, 4320})


class HandoffError(ValueError):
    """Rejected, ambiguous or unplayable handoff."""


def _integer(value: Any, name: str, *, low: int = 0, high: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise HandoffError(f"{name} must be an integer between {low} and {high}")
    return value


def _short(value: Any, name: str, *, required: bool = False, limit: int = 300) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise HandoffError(f"{name} must be a nonempty string <= {limit} characters" if required
                           else f"{name} must be a string <= {limit} characters")
    return value.strip()


def _copy(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HandoffError("Each media copy must be an object")
    server = _short(value.get("server_id"), "server_id", required=True)
    rating = _short(value.get("rating_key"), "rating_key", required=True)
    height = value.get("height")
    if height is not None:
        height = _integer(height, "height", high=8640)
    bitrate = value.get("bitrate_kbps", 0)
    bitrate = _integer(bitrate, "bitrate_kbps", high=1_000_000)
    media_index = value.get("media_index")
    if media_index is not None:
        media_index = _integer(media_index, "media_index", high=50)
    media_id = _short(value.get("media_id"), "media_id", limit=128)
    return {
        "server_id": server,
        "rating_key": rating,
        "height": height,
        "bitrate_kbps": bitrate,
        "library_title": _short(value.get("library_title"), "library_title"),
        "guid": _short(value.get("guid"), "guid"),
        "video_codec": _short(value.get("video_codec"), "video_codec", limit=40),
        "media_index": media_index,
        "media_id": media_id,
    }


def _entry(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HandoffError("Queue entries must be objects")
    kind = _short(value.get("kind"), "kind", required=True).lower()
    if kind not in ("movie", "episode"):
        raise HandoffError("PowerLink queue entries must be movies or episodes")
    copies = value.get("copies", [])
    if not isinstance(copies, list) or len(copies) > 20:
        raise HandoffError("copies must be a list of at most 20 variants")
    season = value.get("season")
    episode = value.get("episode")
    if (season is None) != (episode is None):
        raise HandoffError("season and episode must be supplied together")
    if season is not None:
        season = _integer(season, "season", high=999)
        episode = _integer(episode, "episode", high=9999)
    year = value.get("year")
    if year is not None:
        year = _integer(year, "year", low=1880, high=3000)
    ref = {
        "kind": kind,
        "title": _short(value.get("title"), "title", required=True),
        "guid": _short(value.get("guid"), "guid"),
        "server_id": _short(value.get("server_id"), "server_id"),
        "rating_key": _short(value.get("rating_key"), "rating_key"),
        "show_guid": _short(value.get("show_guid"), "show_guid"),
        "show_title": _short(value.get("show_title"), "show_title"),
        "season": season,
        "episode": episode,
        "year": year,
        "copies": [_copy(v) for v in copies],
    }
    if not (ref["guid"] or (ref["server_id"] and ref["rating_key"])
            or (kind == "episode" and ref["show_guid"] and season is not None)):
        raise HandoffError("Each entry needs a Plex GUID or server/rating-key identity")
    return ref


@dataclass(frozen=True)
class QueueSnapshot:
    """Exact playback-order snapshot, including cursor and position."""
    items: tuple[dict[str, Any], ...]
    current_index: int
    position_ms: int
    shuffled: bool
    context_key: str
    source_device: str
    profile_id: str

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> "QueueSnapshot":
        if not isinstance(raw, Mapping):
            raise HandoffError("snapshot must be an object")
        try:
            serialized = json.dumps(raw, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError) as err:
            raise HandoffError("snapshot must be JSON-serializable") from err
        if len(serialized.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
            raise HandoffError("snapshot exceeds 2 MB")
        if raw.get("schema") != 1:
            raise HandoffError("Unsupported snapshot schema; require schema=1")
        items = raw.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_QUEUE_ITEMS:
            raise HandoffError("items must contain 1 to 5000 queue entries")
        index = _integer(raw.get("current_index"), "current_index", high=len(items) - 1)
        position_ms = _integer(raw.get("position_ms"), "position_ms", high=86_400_000)
        shuffled = raw.get("shuffled")
        if not isinstance(shuffled, bool):
            raise HandoffError("shuffled must be a boolean")
        return cls(
            items=tuple(_entry(v) for v in items),
            current_index=index,
            position_ms=position_ms,
            shuffled=shuffled,
            context_key=_short(raw.get("context_key"), "context_key"),
            source_device=_short(raw.get("source_device"), "source_device"),
            profile_id=_short(raw.get("profile_id"), "profile_id"),
        )

    def header(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "total": len(self.items),
            "current_index": self.current_index,
            "position_ms": self.position_ms,
            "shuffled": self.shuffled,
            "context_key": self.context_key,
            "profile_id": self.profile_id,
        }


@dataclass(frozen=True)
class DevicePolicy:
    """Per-device playback policy independent of room names or network."""
    max_height: int = 1080
    prefer_highest_quality: bool = True

    def __post_init__(self) -> None:
        if self.max_height not in ALLOWED_HEIGHTS:
            raise HandoffError("max_height must be one of 480,720,1080,1440,2160,4320")


def choose_copy(entry: Mapping[str, Any], copies: list[Mapping[str, Any]],
                policy: DevicePolicy) -> dict[str, Any] | None:
    """Never pick a different work: Plex GUID matching is authoritative.

    A known 4K-only library is excluded on a 1080p target even when its Media
    row omitted height. Unknown resolution is not treated as safely playable.
    """
    eligible: list[dict[str, Any]] = []
    for candidate in copies:
        try:
            c = _copy(candidate)
        except HandoffError:
            continue
        # A copy without its GUID can only be accepted if it is the exact
        # original server/rating key. Never treat an unverified alternate
        # rendition of a different title as equivalent.
        if entry.get("guid") and c["guid"] != entry["guid"]:
            original = (
                c["server_id"] == entry.get("server_id")
                and c["rating_key"] == entry.get("rating_key")
            )
            if not original:
                continue
        if c["height"] is None:
            if "[4k]" in c["library_title"].lower():
                c["height"] = 2160
            else:
                continue  # fail closed, do not silently send unknown UHD to Lite
        if c["height"] > policy.max_height:
            continue
        eligible.append(c)
    if not eligible:
        return None
    # 4K-capable TVs get the highest available file when opted in; Full HD
    # targets get the best file that fits. Never use a lower-res copy just
    # because its Plex section was enumerated first.
    if policy.prefer_highest_quality:
        return max(eligible, key=lambda c: (c["height"], c["bitrate_kbps"]))
    # Balanced/compatibility mode prefers a Full HD rendition on a 4K-capable
    # device, falling back to the best available in either direction.
    preferred = [c for c in eligible if c["height"] <= min(policy.max_height, 1080)]
    return max(preferred or eligible, key=lambda c: (c["height"], c["bitrate_kbps"]))


CopyLookup = Callable[[Mapping[str, Any]], Awaitable[list[Mapping[str, Any]]]]


class WindowPlanner:
    """Bounded, lazy resolution: no background polling or full-queue lookups."""

    def __init__(self, snapshot: QueueSnapshot, policy: DevicePolicy,
                 lookup: CopyLookup | None = None) -> None:
        self.snapshot = snapshot
        self.policy = policy
        self.lookup = lookup
        self._semaphore = asyncio.Semaphore(3)
        self._windows: dict[int, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def item(self, index: int) -> dict[str, Any]:
        if not 0 <= index < len(self.snapshot.items):
            raise HandoffError("index outside queue")
        async with self._lock:
            cached = self._windows.get(index)
        if cached is not None:
            return cached
        entry = self.snapshot.items[index]
        candidates = list(entry["copies"])
        choice = choose_copy(entry, candidates, self.policy)
        needs_lookup = choice is None or (
            self.policy.prefer_highest_quality and self.policy.max_height >= 2160
            and choice["height"] < self.policy.max_height
        )
        # On a Full HD stick, an already verified HD copy is sufficient:
        # avoid even one Plex API lookup. On a 4K TV, ask the server only if
        # the queue snapshot did not already include a 4K alternative.
        if needs_lookup and self.lookup is not None:
            async with self._semaphore:
                found = await self.lookup(entry)
            candidates.extend(found)
            choice = choose_copy(entry, candidates, self.policy)
        value = {
            "index": index,
            "status": "resolved" if choice else "unavailable",
            "title": entry["title"],
            "kind": entry["kind"],
            "media": choice,
        }
        async with self._lock:
            # A duplicated item stays in each original slot; never collapse it.
            self._windows[index] = value
        return value

    async def window(self, start: int, count: int) -> dict[str, Any]:
        _integer(start, "start", high=len(self.snapshot.items) - 1)
        _integer(count, "count", low=1, high=MAX_WINDOW_ITEMS)
        end = min(start + count, len(self.snapshot.items))
        rows = await asyncio.gather(*(self.item(i) for i in range(start, end)))
        return {**self.snapshot.header(), "start": start, "items": rows}

    async def ensure_current_playable(self) -> dict[str, Any]:
        """Never wake or displace the old player for an unresolved first item."""
        row = await self.item(self.snapshot.current_index)
        if row["status"] != "resolved":
            raise HandoffError("Current item has no verified copy supported by the destination")
        return row


def from_plex_queue_response(source: Mapping[str, Any],
                             container: Mapping[str, Any]) -> QueueSnapshot:
    """Freeze the entire Plex queue in its existing order on Home Assistant.

    playQueueItemID distinguishes duplicates of the same ratingKey.
    An incomplete or changed queue must fail rather than be recreated/shuffled.
    """
    if not isinstance(source, Mapping) or not isinstance(container, Mapping):
        raise HandoffError("Invalid Plex queue reference/response")
    ref = source.get("queue_source")
    if not isinstance(ref, Mapping) or ref.get("type") != "plex_play_queue":
        raise HandoffError("Expected Plex queue_source.type=plex_play_queue")
    queue_id = _integer(ref.get("play_queue_id"), "play_queue_id", low=1)
    selected = _integer(ref.get("selected_item_id"), "selected_item_id", low=1)
    server_id = _short(ref.get("server_id"), "queue source server_id", required=True)
    if source.get("schema") != 1:
        raise HandoffError("Unsupported queue reference schema")
    expected_version = ref.get("version")
    if expected_version is not None:
        expected_version = _integer(expected_version, "queue version", low=1)
        if container.get("playQueueVersion") != expected_version:
            raise HandoffError("Plex play queue changed after capture")
    if container.get("playQueueID") != queue_id:
        raise HandoffError("Plex returned the wrong play queue")
    total = _integer(container.get("playQueueTotalCount"), "queue total",
                     low=1, high=MAX_QUEUE_ITEMS)
    rows = container.get("Metadata")
    if not isinstance(rows, list) or len(rows) != total:
        raise HandoffError("Plex returned an incomplete queue window")
    shuffled = source.get("shuffled")
    if not isinstance(shuffled, bool) or container.get("playQueueShuffled") != shuffled:
        raise HandoffError("Plex queue shuffle flag changed")
    items = []
    current_index = -1
    seen_ids = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise HandoffError("Malformed Plex queue entry")
        item_id = _integer(row.get("playQueueItemID"), "playQueueItemID", low=1)
        if item_id in seen_ids:
            raise HandoffError("Duplicate playQueueItemID inside queue response")
        seen_ids.add(item_id)
        if item_id == selected:
            current_index = index
        if row.get("type") not in ("movie", "episode"):
            raise HandoffError("Only movie/episode queues can be handed off")
        rating_key = _short(str(row.get("ratingKey") or ""), "ratingKey", required=True)
        guid = str(row.get("guid") or "")
        kind = row["type"]
        versions = row.get("Media") or []
        if not isinstance(versions, list):
            raise HandoffError("Malformed Plex Media array")
        copies = []
        for media_index, version in enumerate(versions[:10]):
            if not isinstance(version, Mapping):
                continue
            height = version.get("height")
            if isinstance(height, str) and height.isdecimal():
                height = int(height)
            if not isinstance(height, int):
                height = _plex_video_height(version.get("videoResolution"))
            bitrate = version.get("bitrate") or 0
            try:
                bitrate = max(0, int(bitrate))
            except (ValueError, TypeError):
                bitrate = 0
            copies.append({
                "server_id": server_id, "rating_key": rating_key,
                "guid": guid, "height": height, "bitrate_kbps": bitrate,
                "library_title": str(row.get("librarySectionTitle") or ""),
                "media_index": media_index,
                "media_id": str(version.get("id") or ""),
            })
        season = row.get("parentIndex") if kind == "episode" else None
        episode = row.get("index") if kind == "episode" else None
        year = row.get("year") if kind == "movie" else None
        def number_or_none(value):
            if isinstance(value, str) and value.isdecimal():
                return int(value)
            return value
        items.append({
            "kind": kind, "title": str(row.get("title") or ""),
            "guid": guid, "server_id": server_id, "rating_key": rating_key,
            "show_guid": str(row.get("grandparentGuid") or ""),
            "show_title": str(row.get("grandparentTitle") or ""),
            "season": number_or_none(season), "episode": number_or_none(episode),
            "year": number_or_none(year), "copies": copies,
        })
    if current_index < 0:
        raise HandoffError("Selected Plex queue item was not returned")
    if (container.get("playQueueSelectedItemOffset") is not None
            and container["playQueueSelectedItemOffset"] != current_index):
        raise HandoffError("Selected Plex queue offset disagrees with queue order")
    return QueueSnapshot.parse({
        "schema": 1, "items": items, "current_index": current_index,
        "position_ms": source.get("position_ms"), "shuffled": shuffled,
        "context_key": source.get("context_key"),
        "source_device": source.get("source_device"),
        "profile_id": source.get("profile_id"),
    })


def _plex_video_height(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    text = value.lower().rstrip("p")
    if text in ("4k", "uhd"):
        return 2160
    if text == "sd":
        return 480
    if text.isdecimal() and 100 <= int(text) <= 8640:
        return int(text)
    return None
