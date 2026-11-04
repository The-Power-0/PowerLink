"""Server-side Plex queue paging tests; no Home Assistant process needed."""
from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "powerlink"
pkg = types.ModuleType("powerlink_stage")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("powerlink_stage", pkg)

handoff = importlib.import_module("powerlink_stage.handoff")
collect = importlib.import_module("powerlink_stage.queue_collect")
HandoffError = handoff.HandoffError


def queue_item(n: int, *, rating: str = "111"):
    return {
        "type": "episode", "title": "Episode",
        "guid": "plex://episode/example",
        "ratingKey": rating,
        "playQueueItemID": n,
        "parentIndex": 2, "index": 3,
        "Media": [{"height": 1080}],
    }


def page(rows, *, version=3, total=5, shuffled=True, queue_id=701):
    return {"MediaContainer": {
        "playQueueID": queue_id, "playQueueVersion": version,
        "playQueueTotalCount": total, "playQueueShuffled": shuffled,
        "Metadata": list(rows),
    }}


class PlexQueueCollectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rows = [
            queue_item(200, rating="11"),
            queue_item(400, rating="22"),
            queue_item(500, rating="11"),
            queue_item(700, rating="33"),
            queue_item(990, rating="44"),
        ]

    async def test_full_queue_needs_one_request(self):
        calls = []
        async def fetch(center, window):
            calls.append((center, window))
            return page(self.rows)
        result = await collect.collect_plex_play_queue(
            fetch, play_queue_id=701, selected_item_id=500
        )
        self.assertEqual(calls, [(500, 250)])
        self.assertEqual(result["playQueueSelectedItemOffset"], 2)
        self.assertEqual([r["ratingKey"] for r in result["Metadata"]],
                         ["11", "22", "11", "33", "44"])

    async def test_reassembles_capped_before_and_after_pages(self):
        calls = []
        async def fetch(center, window):
            calls.append(center)
            if center == 500:
                return page(self.rows[1:4])
            if center == 400:
                return page(self.rows[:3])
            if center == 700:
                return page(self.rows[2:])
            raise AssertionError(f"unexpected page anchor: {center}")
        result = await collect.collect_plex_play_queue(
            fetch, play_queue_id=701, selected_item_id=500
        )
        self.assertEqual(calls, [500, 400, 700])
        self.assertEqual([r["playQueueItemID"] for r in result["Metadata"]],
                         [200, 400, 500, 700, 990])
        self.assertEqual(result["playQueueSelectedItemOffset"], 2)

    async def test_changed_shuffle_is_rejected(self):
        async def fetch(center, window):
            return page(self.rows[1:4] if center == 500 else self.rows[:3],
                        shuffled=center == 500)
        with self.assertRaises(HandoffError):
            await collect.collect_plex_play_queue(
                fetch, play_queue_id=701, selected_item_id=500
            )

    async def test_changed_version_is_rejected(self):
        async def fetch(center, window):
            return page(self.rows[1:4] if center == 500 else self.rows[:3],
                        version=3 if center == 500 else 4)
        with self.assertRaises(HandoffError):
            await collect.collect_plex_play_queue(
                fetch, play_queue_id=701, selected_item_id=500
            )

    async def test_wrong_queue_id_is_rejected(self):
        async def fetch(center, window):
            return page(self.rows, queue_id=702)
        with self.assertRaises(HandoffError):
            await collect.collect_plex_play_queue(
                fetch, play_queue_id=701, selected_item_id=500
            )

    async def test_missing_current_item_is_rejected(self):
        async def fetch(center, window):
            return page(self.rows[:2])
        with self.assertRaises(HandoffError):
            await collect.collect_plex_play_queue(
                fetch, play_queue_id=701, selected_item_id=500
            )

    async def test_repeated_queue_item_ids_are_rejected(self):
        async def fetch(center, window):
            return page([self.rows[2], self.rows[2]], total=2)
        with self.assertRaises(HandoffError):
            await collect.collect_plex_play_queue(
                fetch, play_queue_id=701, selected_item_id=500
            )

    async def test_unexpandable_server_window_is_rejected(self):
        async def fetch(center, window):
            return page(self.rows[1:4])
        with self.assertRaises(HandoffError):
            await collect.collect_plex_play_queue(
                fetch, play_queue_id=701, selected_item_id=500
            )

    async def test_paging_request_limit_is_enforced(self):
        async def fetch(center, window):
            return page(self.rows[1:4] if center == 500 else self.rows[:3])
        with self.assertRaises(HandoffError):
            await collect.collect_plex_play_queue(
                fetch, play_queue_id=701, selected_item_id=500, max_requests=1
            )

    async def test_queue_over_limit_is_rejected(self):
        async def fetch(center, window):
            return page(self.rows, total=5001)
        with self.assertRaises(HandoffError):
            await collect.collect_plex_play_queue(
                fetch, play_queue_id=701, selected_item_id=500
            )


if __name__ == "__main__":
    unittest.main()
