"""Safe Plex play queue response decoding, independent from Home Assistant.

Never echo Plex tokens, response bodies, or private media metadata in errors.
Aiohttp streams may deliver the body in multiple chunks.
"""
from __future__ import annotations

import json

from .handoff import HandoffError

MAX_QUEUE_PAGE_BYTES = 1_500_000


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
            raise HandoffError(
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
