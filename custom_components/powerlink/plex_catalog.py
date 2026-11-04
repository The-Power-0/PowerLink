"""Targeted Plex equivalent lookup on HA, with bounded concurrent requests and cache.

This is NOT a library scanner. Each query is only made for the currently
requested queue window and verified against stable Plex/media identities.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from typing import Any, Mapping

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .handoff import HandoffError, from_plex_queue_response
from .queue_collect import collect_plex_play_queue


def _height(value: Any) -> int | None:
    if isinstance(value, int) and 0 < value <= 8640:
        return value
    if not isinstance(value, str):
        return None
    text = value.lower()
    if text in ("4k", "uhd", "2160p"):
        return 2160
    if text in ("sd", "480p"):
        return 480
    text = text.rstrip("p")
    if text.isdecimal():
        number = int(text)
        if 100 <= number <= 8640:
            return number
    return None


def _identity_matches(entry: Mapping, row: Mapping) -> bool:
    """Never equate two titles solely on a matching name."""
    incoming = entry.get("guid", "")
    candidate = row.get("guid", "")
    if incoming and candidate and incoming == candidate:
        return True
    if entry.get("kind") == "episode":
        if entry.get("season") is None or entry.get("episode") is None:
            return False
        if row.get("parentIndex") != entry["season"] or row.get("index") != entry["episode"]:
            return False
        if entry.get("show_guid") and row.get("grandparentGuid"):
            return entry["show_guid"] == row["grandparentGuid"]
        # Matching episode title/season without authoritative show identity
        # is unsafe: many different TV series have an episode called "Pilot".
        return False
    # A movie may have a different Plex GUID between libraries, but an
    # explicit shared external GUID is still required for safe cross-copy
    # matching. Title/year alone is insufficient for exact queue handoff.
    known = {g.get("id") for g in row.get("Guid", []) if isinstance(g, dict)}
    return bool(incoming and incoming in known)


class PlexCopyCatalog:
    """Optional Plex server resolver, never a poller or long-lived socket."""

    def __init__(self, hass, base_url: str, token: str, *, server_id: str = "") -> None:
        self.hass = hass
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.server_id = server_id
        self._cache: OrderedDict[tuple, tuple[float, list[dict]]] = OrderedDict()
        self._semaphore = asyncio.Semaphore(3)

    async def snapshot_from_plex_queue(self, source: Mapping[str, Any]):
        """Retrieve the *complete* Plex queue even if PMS caps each window.

        The Fire Stick sends a tiny queue reference. HA does all pagination,
        bounds its own memory/HTTP time and verifies count/version/order before
        any launch or device wake sequence can begin.
        """
        ref = source.get("queue_source")
        if not isinstance(ref, Mapping) or ref.get("type") != "plex_play_queue":
            raise HandoffError("Expected queue_source.type=plex_play_queue")
        queue_id, selected = ref.get("play_queue_id"), ref.get("selected_item_id")
        if isinstance(queue_id, bool) or not isinstance(queue_id, int) or queue_id < 1:
            raise HandoffError("Invalid source play_queue_id")
        if isinstance(selected, bool) or not isinstance(selected, int) or selected < 1:
            raise HandoffError("Invalid source selected_item_id")
        if self.server_id and ref.get("server_id") != self.server_id:
            raise HandoffError("Source queue belongs to a different Plex server")

        session = async_get_clientsession(self.hass)

        async def fetch_page(center: int, window: int) -> dict[str, Any]:
            async with session.get(
                f"{self.base_url}/playQueues/{queue_id}",
                params={
                    "center": str(center), "window": str(window),
                    "includeBefore": "1", "includeAfter": "1", "own": "0",
                },
                headers={"X-Plex-Token": self.token, "Accept": "application/json"},
                allow_redirects=False,
            ) as response:
                response.raise_for_status()
                body = await response.content.read(1_500_001)
                if len(body) > 1_500_000:
                    raise HandoffError("Plex queue page exceeds its response limit")
                decoded = json.loads(body)
                if not isinstance(decoded, dict):
                    raise HandoffError("Plex queue page is not an object")
                return decoded

        try:
            async with asyncio.timeout(45):
                container = await collect_plex_play_queue(
                    fetch_page, play_queue_id=queue_id, selected_item_id=selected
                )
        except HandoffError:
            raise
        except Exception as err:
            # Do not log Plex tokens or the response URL.
            raise HandoffError("Cannot retrieve complete authoritative Plex queue on Home Assistant") from err
        return from_plex_queue_response(source, container)

    async def find_copies(self, entry: Mapping[str, Any]) -> list[dict[str, Any]]:
        key = (entry.get("kind"), entry.get("title"), entry.get("guid"),
               entry.get("show_guid"), entry.get("season"), entry.get("episode"))
        saved = self._cache.get(key)
        if saved is not None and time.monotonic() - saved[0] < 600:
            self._cache.move_to_end(key)
            return list(saved[1])
        async with self._semaphore:
            # Double-check after waiting on another queue-window request.
            saved = self._cache.get(key)
            if saved is not None and time.monotonic() - saved[0] < 600:
                return list(saved[1])
            copies = await self._lookup(entry)
            self._cache[key] = (time.monotonic(), copies)
            self._cache.move_to_end(key)
            while len(self._cache) > 256:
                self._cache.popitem(last=False)
            return list(copies)

    async def _lookup(self, entry: Mapping[str, Any]) -> list[dict[str, Any]]:
        type_id = 1 if entry.get("kind") == "movie" else 4
        # PMS supports the title==value exact query on /library/all. The
        # result is always rechecked by immutable Plex/external identifiers.
        params = {
            "type": str(type_id), "title": "=" + entry["title"],
            "includeGuids": "1", "X-Plex-Container-Size": "100",
        }
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(12):
                async with session.get(
                    f"{self.base_url}/library/all", params=params,
                    headers={"X-Plex-Token": self.token, "Accept": "application/json"},
                ) as response:
                    response.raise_for_status()
                    body = await response.content.read(1_500_001)
                    if len(body) > 1_500_000:
                        return []
                    data = json.loads(body)
        except Exception:
            # Best effort: a failed optional Plex lookup must not prevent
            # using a previously verified copy embedded in the snapshot.
            # We deliberately don't log response bodies, authentication or
            # URLs containing sensitive server metadata.
            return []

        container = data.get("MediaContainer", {}) if isinstance(data, dict) else {}
        metadata = container.get("Metadata", [])
        if not isinstance(metadata, list):
            return []
        found = []
        for row in metadata[:100]:
            if not isinstance(row, dict) or not _identity_matches(entry, row):
                continue
            rating = row.get("ratingKey")
            if not rating:
                continue
            source_id = self.server_id or entry.get("server_id", "")
            if not source_id:
                continue
            media = row.get("Media") or []
            if not isinstance(media, list):
                continue
            for media_index, version in enumerate(media[:10]):
                if not isinstance(version, dict):
                    continue
                resolution = _height(version.get("height")) or _height(version.get("videoResolution"))
                kbps = version.get("bitrate") or 0
                try:
                    kbps = int(kbps)
                except (TypeError, ValueError):
                    kbps = 0
                found.append({
                    "server_id": str(source_id),
                    "rating_key": str(rating),
                    # The search row has already been verified by immutable GUID
                    # (or strong show+episode identity). Carry the verified
                    # source identity forward for copy selection.
                    "guid": str(entry.get("guid") or row.get("guid") or ""),
                    "library_title": str(row.get("librarySectionTitle") or ""),
                    "height": resolution,
                    "bitrate_kbps": max(0, kbps),
                    "video_codec": str(version.get("videoCodec") or ""),
                    "media_index": media_index,
                    "media_id": str(version.get("id") or ""),
                })
        return found
