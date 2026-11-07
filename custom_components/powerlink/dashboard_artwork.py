"""On-demand, bounded Plex poster lookup performed exclusively by Home Assistant.

No scanning, recurring jobs, Fire TV requests or credential-bearing URLs
are exposed. The browser requests an individual poster after a user opens a
show card. Errors are negatively cached to avoid hammering Plex.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from collections import OrderedDict

from homeassistant.helpers.aiohttp_client import async_get_clientsession

MAX_METADATA_BYTES = 750_000
MAX_POSTER_BYTES = 320_000
CACHE_SIZE = 96
GOOD_TTL = 6 * 3600
MISS_TTL = 15 * 60
THUMB_PATTERN = re.compile(r"^/library/metadata/[0-9]+/thumb(?:/[a-zA-Z0-9_-]+)?$")


def poster_path(metadata: object, guid: str) -> str | None:
    """Identity-check Plex's show before choosing its internal poster path."""
    if not isinstance(metadata, dict) or not isinstance(guid, str):
        return None
    container = metadata.get("MediaContainer")
    rows = container.get("Metadata", []) if isinstance(container, dict) else []
    if not isinstance(rows, list):
        return None
    for row in rows[:100]:
        if not isinstance(row, dict) or row.get("type") not in (None, "show"):
            continue
        if row.get("guid") != guid:
            continue
        path = row.get("thumb")
        if isinstance(path, str) and len(path) < 240 and THUMB_PATTERN.fullmatch(path):
            return path
    return None


class PowerLinkPosters:
    def __init__(self, plex) -> None:
        self._plex = plex
        self._cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._limit = asyncio.Semaphore(2)

    def _cached(self, key: str) -> dict | None:
        stored = self._cache.get(key)
        if stored is None:
            return None
        expires, result = stored
        if time.monotonic() >= expires:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return result

    def _put(self, key: str, result: dict) -> dict:
        lifetime = GOOD_TTL if result.get("available") else MISS_TTL
        self._cache[key] = (time.monotonic() + lifetime, result)
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return result

    async def poster(self, key: str, *, title: str, guid: str, server_id: str) -> dict:
        if self._plex.server_id and server_id != self._plex.server_id:
            return {"available": False}
        cached = self._cached(key)
        if cached is not None:
            return cached
        async with self._limit:
            cached = self._cached(key)
            if cached is not None:
                return cached
            result = await self._fetch(title, guid)
            return self._put(key, result)

    async def _fetch(self, title: str, guid: str) -> dict:
        sess = async_get_clientsession(self._plex.hass)
        headers = {
            "X-Plex-Token": self._plex.token,
            "X-Plex-Product": "PowerLink",
            "Accept": "application/json",
        }
        try:
            async with asyncio.timeout(12):
                async with sess.get(
                    self._plex.base_url + "/library/all",
                    params={
                        "type": "2", "title": "=" + title,
                        "X-Plex-Container-Size": "100",
                    },
                    headers=headers,
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        return {"available": False}
                    body = await response.content.read(MAX_METADATA_BYTES + 1)
                    if len(body) > MAX_METADATA_BYTES:
                        return {"available": False}
                    path = poster_path(json.loads(body), guid)
                if path is None:
                    return {"available": False}
                # The optional Plex thumbnail transcoder bounds image dimensions.
                async with sess.get(
                    self._plex.base_url + "/photo/:/transcode",
                    params={
                        "url": path, "width": "300", "height": "450",
                        "minSize": "1", "upscale": "0",
                    },
                    headers={"X-Plex-Token": self._plex.token},
                    allow_redirects=False,
                ) as response:
                    kind = response.headers.get("Content-Type", "").split(";")[0].lower()
                    if response.status != 200 or kind not in (
                        "image/jpeg", "image/png", "image/webp"
                    ):
                        return {"available": False}
                    data = await response.content.read(MAX_POSTER_BYTES + 1)
                    if not data or len(data) > MAX_POSTER_BYTES:
                        return {"available": False}
                    return {
                        "available": True, "content_type": kind,
                        "base64": base64.b64encode(data).decode("ascii"),
                    }
        except (OSError, ValueError, TimeoutError, json.JSONDecodeError):
            return {"available": False}
        except Exception:
            # Optional artwork must never disclose a Plex token or prevent
            # opening the dashboard. No raw exception or URL in HA logs.
            return {"available": False}
