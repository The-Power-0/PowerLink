"""Adaptive Plex queue retrieval tests for large shuffled TV-show queues.

Pure Python tests: no Home Assistant, Plex token, or actual TV required.
"""
from __future__ import annotations

import importlib
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "powerlink"
pkg = types.ModuleType("powerlink_stage")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("powerlink_stage", pkg)
collector = importlib.import_module("powerlink_stage.queue_collect")
http = importlib.import_module("powerlink_stage.plex_queue_response")
HandoffError = importlib.import_module("powerlink_stage.handoff").HandoffError


class AdaptiveQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_page_retries_smaller_window_not_bigger_buffer(self):
        calls = []

        async def fetch(center, window):
            calls.append((center, window))
            if window > 8:
                raise http.PlexQueuePageTooLarge("fake > 1.5 MB")
            return {"ok": True, "center": center, "window": window}

        result = await http.fetch_adaptive_queue_page(fetch, 1001, 32)
        self.assertEqual(result, {"ok": True, "center": 1001, "window": 8})
        self.assertEqual(calls, [(1001, 32), (1001, 16), (1001, 8)])

    async def test_even_single_item_too_large_fails_without_removing_memory_cap(self):
        calls = []

        async def fetch(center, window):
            calls.append(window)
            raise http.PlexQueuePageTooLarge("fake huge item")

        with self.assertRaisesRegex(HandoffError, "smallest queue window"):
            await http.fetch_adaptive_queue_page(fetch, 1001, 8)
        self.assertEqual(calls, [8, 4, 2, 1])
        self.assertEqual(http.MAX_QUEUE_PAGE_BYTES, 1_500_000)

    async def test_large_queue_paged_at_every_edge_preserves_order_and_duplicates(self):
        # 75 queue positions but only 4 unique rating keys, as can occur
        # with repeats in a randomized queue. Plex item IDs disambiguate.
        source = [
            {
                "playQueueItemID": 3000 + i,
                "ratingKey": str(i % 4),
                "guid": "plex://episode/" + str(i % 4),
                "type": "episode",
                "title": "Sample",
                "Media": [{"height": 1080}],
            } for i in range(75)
        ]
        calls = []

        async def fetch(center, window):
            calls.append((center, window))
            # Plex responses can exceed the byte cap even for a small
            # number of metadata-heavy episodes. Simulate that by raising
            # the same classified error that the actual HTTP reader raises.
            if window > 8:
                raise http.PlexQueuePageTooLarge("large Plex response")
            index = center - 3000
            low = max(0, index - window)
            high = min(len(source), index + window + 1)
            return {"MediaContainer": {
                "playQueueID": 77,
                "playQueueVersion": 13,
                "playQueueTotalCount": len(source),
                "playQueueShuffled": True,
                "Metadata": source[low:high],
            }}

        async def adapter(center, window):
            return await http.fetch_adaptive_queue_page(fetch, center, window)

        out = await collector.collect_plex_play_queue(
            adapter, play_queue_id=77, selected_item_id=3040
        )
        self.assertEqual(len(out["Metadata"]), 75)
        self.assertEqual(out["playQueueSelectedItemOffset"], 40)
        self.assertEqual(
            [x["playQueueItemID"] for x in out["Metadata"]],
            list(range(3000, 3075)),
        )
        self.assertEqual(
            [x["ratingKey"] for x in out["Metadata"]],
            [str(i % 4) for i in range(75)],
        )
        self.assertEqual(out["playQueueShuffled"], True)
        self.assertGreater(len(calls), 5)
        self.assertTrue(all(1 <= w <= 32 for _, w in calls))

    async def test_regular_queue_preserves_unshuffled_flag_and_selected_cursor(self):
        original = [
            {"playQueueItemID": i, "ratingKey": str(i), "guid": "plex://movie/" + str(i),
             "type": "movie", "title": "Movie"} for i in range(1, 15)
        ]

        async def fetch(center, window):
            ix = center - 1
            return {"MediaContainer": {
                "playQueueID": 90,
                "playQueueVersion": 2,
                "playQueueTotalCount": len(original),
                "playQueueShuffled": False,
                "Metadata": original[max(0, ix-window):min(len(original), ix+window+1)],
            }}

        async def adapter(center, window):
            return await http.fetch_adaptive_queue_page(fetch, center, window)

        out = await collector.collect_plex_play_queue(
            adapter, play_queue_id=90, selected_item_id=7
        )
        self.assertFalse(out["playQueueShuffled"])
        self.assertEqual(out["playQueueSelectedItemOffset"], 6)

    async def test_invalid_adaptive_window_is_rejected(self):
        async def fetch(center, window):
            raise AssertionError("should not fetch")

        with self.assertRaises(ValueError):
            await http.fetch_adaptive_queue_page(fetch, 5, 0)


if __name__ == "__main__":
    unittest.main()
