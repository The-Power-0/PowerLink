"""Safe Plex play queue response decoding, independent from Home Assistant.

Never echo Plex tokens, response bodies, or private media metadata in errors.
Aiohttp streams may deliver the body in multiple chunks.
"""
from __future__ import annotations

import json

from .handoff import HandoffError

MAX_QUEUE_PAGE_BYTES = 1_500_000


class PlexQueuePageTooLarge(HandoffError):
    """A single Plex response was too large; retry with a smaller window."""


async def fetch_adaptive_queue_page(fetch, center: int, window: int) -> dict:
    """Retry oversized queue responses with progressively smaller windows.

    Plex `window` means items on EACH side of `center`, not the total
    response count. Keep the per-response memory cap and fail closed when
    even one centered item exceeds it. No queue mutation or re-shuffle.
    """
    if type(window) is not int or window < 1:
        raise ValueError("Plex queue window must be a positive integer")
    while True:
        try:
            return await fetch(center, window)
        except PlexQueuePageTooLarge as err:
            if window == 1:
                raise HandoffError(
                    "Plex returned more than 1.5 MB even for the smallest "
                    "queue window. The server may be ignoring the requested "
                    "window, or an individual item has unusually large "
                    "metadata. Source playback is unchanged."
                ) from err
            window = max(1, window // 2)


async def decode_plex_queue_page(response) -> dict:
    """Parse a bounded JSON queue response; classify common PMS failures."""
    status = response.status
    if status in (401, 403):
        raise HandoffError(
            "Plex rejected the configured token for this play queue "
            f"(HTTP {status}). Check PowerLink's Plex token "
            "and that it belongs to the same Plex account/profile as "
            "the source Fire Stick."
        )
    if status == 404:
        raise HandoffError(
            "Plex could not find the active play queue (HTTP 404). "
            "Verify PowerLink points to the same Plex server and "
            "account/profile as the source; the queue may have expired. "
            "Start a fresh queue on the source and retry."
        )
    if status != 200:
        raise HandoffError(
            f"Plex play queue request returned HTTP {status}. "
            "Check the configured Plex server address and its permissions."
        )

    # A single read(n) can return before the full streamed JSON arrives.
    body = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        body.extend(chunk)
        if len(body) > MAX_QUEUE_PAGE_BYTES:
            raise PlexQueuePageTooLarge(
                "Plex play queue window exceeded the 1.5 MB safety limit"
            )
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError) as err:
        raise HandoffError(
            "Plex returned invalid or incomplete JSON for the play queue. "
            "Check the configured Plex URL points directly to Plex Media Server."
        ) from err
    if not isinstance(value, dict):
        raise HandoffError("Plex returned a non-object play queue response")
    return value
