"""Read an authoritative, unmodified Plex queue through bounded center windows.

Plex may cap playQueue window sizes, even when a client asks for thousands
of rows. An incomplete list must never be misinterpreted as the complete
shuffled queue. This helper runs on Home Assistant only, not the Fire Stick.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .handoff import HandoffError, MAX_QUEUE_ITEMS


def _positive(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HandoffError(f"Invalid Plex {name}")
    return value


def _container(response: dict[str, Any]) -> dict[str, Any]:
    value = response.get("MediaContainer")
    if not isinstance(value, dict):
        raise HandoffError("Plex queue response has no MediaContainer")
    return value


def _items(container: dict[str, Any]) -> list[dict[str, Any]]:
    value = container.get("Metadata")
    if not isinstance(value, list):
        raise HandoffError("Plex returned no play queue item list")
    ids = []
    for entry in value:
        if not isinstance(entry, dict):
            raise HandoffError("Malformed Plex play queue item")
        ids.append(_positive(entry.get("playQueueItemID"), "playQueueItemID"))
    if len(ids) != len(set(ids)):
        raise HandoffError("Plex returned duplicated playQueueItemID values")
    return value


def _ids(items: list[dict[str, Any]]) -> list[int]:
    return [_positive(row["playQueueItemID"], "playQueueItemID") for row in items]


async def collect_plex_play_queue(
    fetch: Callable[[int, int], Awaitable[dict[str, Any]]],
    *,
    play_queue_id: int,
    selected_item_id: int,
    max_requests: int = 160,
) -> dict[str, Any]:
    """Return the complete, version-consistent Plex MediaContainer.

    fetch(center, window) must return a Plex JSON response. Server queue item
    IDs distinguish duplicates. Any contradictory page fails closed.
    """
    _positive(play_queue_id, "playQueueID")
    _positive(selected_item_id, "selected item ID")
    first = _container(await fetch(selected_item_id, 250))
    count = _positive(first.get("playQueueTotalCount"), "playQueueTotalCount")
    if count > MAX_QUEUE_ITEMS:
        raise HandoffError("Plex queue exceeds PowerLink's bounded limit")
    version = _positive(first.get("playQueueVersion"), "playQueueVersion")
    shuffled = first.get("playQueueShuffled")
    if shuffled not in (True, False, 0, 1):
        raise HandoffError("Plex queue has an invalid shuffle flag")
    if first.get("playQueueID") != play_queue_id:
        raise HandoffError("Plex returned a different play queue")
    ordered = list(_items(first))
    initial_ids = _ids(ordered)
    if not initial_ids or selected_item_id not in initial_ids or len(ordered) > count:
        raise HandoffError("Plex queue does not contain the requested selected item")
    seen = set(initial_ids)
    requests = 1

    async def page(center: int) -> list[dict[str, Any]]:
        nonlocal requests
        if requests >= max_requests:
            raise HandoffError("Plex queue reconstruction exceeded request budget")
        requests += 1
        container = _container(await fetch(center, 120))
        if container.get("playQueueID") != play_queue_id:
            raise HandoffError("Plex queue ID changed during capture")
        if container.get("playQueueVersion") != version:
            raise HandoffError("Plex queue version changed during capture")
        if container.get("playQueueTotalCount") != count:
            raise HandoffError("Plex queue total changed during capture")
        if container.get("playQueueShuffled") != shuffled:
            raise HandoffError("Plex queue shuffle state changed during capture")
        segment = _items(container)
        if center not in _ids(segment):
            raise HandoffError("Plex queue page does not contain its anchor")
        return segment

    while len(ordered) < count:
        advanced = False
        for before in (True, False):
            if len(ordered) == count:
                break
            anchor = _ids(ordered)[0 if before else -1]
            segment = await page(anchor)
            anchor_index = _ids(segment).index(anchor)
            extras = segment[:anchor_index] if before else segment[anchor_index + 1:]
            new_ids = _ids(extras)
            if any(k in seen for k in new_ids):
                # IDs behind the current head (or after tail) must be entirely
                # new. Known overlaps in that region imply contradictory order.
                raise HandoffError("Noncontiguous or reordered Plex queue page")
            if extras:
                ordered = [*extras, *ordered] if before else [*ordered, *extras]
                seen.update(new_ids)
                advanced = True
            if len(ordered) > count:
                raise HandoffError("Plex returned more entries than its queue total")
        if not advanced:
            raise HandoffError("Plex returned incomplete, unexpandable queue windows")

    if len(seen) != count or selected_item_id not in seen:
        raise HandoffError("Plex queue reconstruction lost or duplicated entries")
    selected_index = _ids(ordered).index(selected_item_id)
    return {
        **first,
        "Metadata": ordered,
        "playQueueSelectedItemID": selected_item_id,
        "playQueueSelectedItemOffset": selected_index,
        "playQueueTotalCount": count,
        "playQueueVersion": version,
    }
