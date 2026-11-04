"""No Home Assistant runtime needed: load only the pure queue core."""
import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "powerlink"
package = types.ModuleType("powerlink_stage")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("powerlink_stage", package)
handoff = importlib.import_module("powerlink_stage.handoff")

QueueSnapshot = handoff.QueueSnapshot
WindowPlanner = handoff.WindowPlanner
DevicePolicy = handoff.DevicePolicy
HandoffError = handoff.HandoffError


def entry(title, rating, *, height=1080, guid="plex://episode/123", lib="TV Shows"):
    return {
        "kind": "episode", "title": title, "guid": guid,
        "server_id": "plex-1", "rating_key": rating,
        "show_guid": "plex://show/1", "show_title": "Test Sitcom",
        "season": 2, "episode": 4,
        "copies": [{
            "server_id": "plex-1", "rating_key": rating,
            "height": height, "bitrate_kbps": 5000,
            "guid": guid, "library_title": lib,
        }],
    }


def snapshot(items, *, selected=0, shuffle=True, offset=105001):
    return QueueSnapshot.parse({
        "schema": 1, "items": items, "current_index": selected,
        "position_ms": offset, "shuffled": shuffle,
        "context_key": "favorite-sitcom-shuffle",
        "source_device": "media_player.living_room_fire_tv_stick_4k_plus",
        "profile_id": "plex-home-main",
    })


class QueueSnapshotTests(unittest.TestCase):
    def test_captures_full_order_and_duplicates(self):
        items = [entry("B", "22"), entry("A", "11"), entry("B", "22")]
        s = snapshot(items, selected=2)
        self.assertEqual([x["rating_key"] for x in s.items], ["22", "11", "22"])
        self.assertEqual(s.header()["current_index"], 2)
        self.assertTrue(s.header()["shuffled"])
        self.assertEqual(s.header()["position_ms"], 105001)
        self.assertEqual(s.header()["total"], 3)

    def test_nonshuffle_queue_is_not_reshuffled(self):
        s = snapshot([entry("1", "1"), entry("2", "2")], shuffle=False)
        self.assertFalse(s.shuffled)
        self.assertEqual([x["rating_key"] for x in s.items], ["1", "2"])

    def test_bad_selected_index_rejected(self):
        with self.assertRaises(HandoffError):
            snapshot([entry("1", "1")], selected=1)

    def test_untrusted_media_rejected(self):
        item = entry("A", "5")
        item["kind"] = "music"
        with self.assertRaises(HandoffError):
            snapshot([item])

    def test_nonboolean_shuffle_rejected(self):
        with self.assertRaises(HandoffError):
            QueueSnapshot.parse({
                "schema": 1, "items": [entry("A", "5")],
                "shuffled": "true", "position_ms": 0, "current_index": 0
            })


class MediaPolicyTests(unittest.TestCase):
    def test_lite_moves_from_4k_to_matching_1080(self):
        e = entry("Pilot", "4k", height=2160, lib="TV Shows [4K]")
        e["copies"].append({
            "server_id": "plex-1", "rating_key": "1080", "height": 1080,
            "bitrate_kbps": 10000, "guid": e["guid"], "library_title": "TV Shows",
        })
        choice = handoff.choose_copy(e, e["copies"], DevicePolicy(1080))
        self.assertEqual(choice["rating_key"], "1080")

    def test_living_room_promotes_1080_to_4k(self):
        e = entry("Pilot", "1080", height=1080)
        e["copies"].append({
            "server_id": "plex-1", "rating_key": "4k", "height": 2160,
            "bitrate_kbps": 24000, "guid": e["guid"], "library_title": "TV Shows [4K]",
        })
        choice = handoff.choose_copy(e, e["copies"], DevicePolicy(2160, True))
        self.assertEqual(choice["rating_key"], "4k")

    def test_low_bit_rate_does_not_beat_resolution(self):
        e = entry("A", "1")
        e["copies"].append({
            "server_id": "plex-1", "rating_key": "4k", "height": 2160,
            "bitrate_kbps": 2000, "guid": e["guid"], "library_title": "TV Shows [4K]",
        })
        self.assertEqual(handoff.choose_copy(e, e["copies"], DevicePolicy(2160))["rating_key"], "4k")

    def test_known_4k_not_allowed_on_lite_even_without_height(self):
        e = entry("A", "1", height=2160)
        e["copies"][0]["height"] = None
        e["copies"][0]["library_title"] = "TV Shows [4K]"
        self.assertIsNone(handoff.choose_copy(e, e["copies"], DevicePolicy(1080)))

    def test_missing_guid_on_unverified_alternate_is_rejected(self):
        e = entry("A", "1", height=2160)
        e["copies"].append({
            "server_id": "plex-1", "rating_key": "unverified", "height": 1080,
            "bitrate_kbps": 2000, "library_title": "TV Shows",
        })
        self.assertIsNone(handoff.choose_copy(e, e["copies"], DevicePolicy(1080)))

    def test_balanced_policy_can_prefer_full_hd_on_4k_device(self):
        e = entry("A", "1080", height=1080)
        e["copies"].append({
            "server_id": "plex-1", "rating_key": "4k", "height": 2160,
            "bitrate_kbps": 24000, "guid": e["guid"], "library_title": "TV Shows [4K]",
        })
        self.assertEqual(
            handoff.choose_copy(e, e["copies"], DevicePolicy(2160, False))["rating_key"],
            "1080",
        )

    def test_mismatched_guid_not_selected(self):
        e = entry("A", "1", height=2160)
        e["copies"].append({
            "server_id": "plex-1", "rating_key": "different-movie", "height": 1080,
            "bitrate_kbps": 1000, "guid": "plex://episode/unrelated",
            "library_title": "TV Shows",
        })
        self.assertIsNone(handoff.choose_copy(e, e["copies"], DevicePolicy(1080)))


class WindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_lazy_window_only_resolves_requested_indices(self):
        items = [entry(str(i), str(i), height=2160) for i in range(100)]
        calls = []

        async def lookup(item):
            calls.append(item["rating_key"])
            await asyncio.sleep(0.001)
            return [{
                "server_id": "plex-1", "rating_key": "hd-" + item["rating_key"],
                "height": 1080, "bitrate_kbps": 5000,
                "guid": item["guid"], "library_title": "TV Shows",
            }]

        planner = WindowPlanner(snapshot(items, selected=30), DevicePolicy(1080), lookup)
        result = await planner.window(30, 8)
        self.assertEqual(len(calls), 8)
        self.assertEqual([x["index"] for x in result["items"]], list(range(30, 38)))
        self.assertEqual(result["items"][0]["media"]["rating_key"], "hd-30")
        await planner.window(30, 8)
        self.assertEqual(len(calls), 8)
        self.assertEqual(result["total"], 100)

    async def test_verified_1080_needs_no_remote_plex_lookup(self):
        calls = []
        async def lookup(item):
            calls.append(item)
            return []
        planner = WindowPlanner(snapshot([entry("A", "hd", height=1080)]),
                                DevicePolicy(1080), lookup)
        await planner.ensure_current_playable()
        self.assertEqual(calls, [])

    async def test_missing_copy_fails_before_any_wake(self):
        planner = WindowPlanner(snapshot([entry("A", "4k", height=2160)]), DevicePolicy(1080))
        with self.assertRaises(HandoffError):
            await planner.ensure_current_playable()

    async def test_partial_window_preserves_unavailable_slot(self):
        planner = WindowPlanner(snapshot([
            entry("A", "ok", height=1080), entry("B", "uhd", height=2160),
            entry("A", "ok", height=1080),
        ]), DevicePolicy(1080))
        window = await planner.window(0, 3)
        self.assertEqual([x["index"] for x in window["items"]], [0, 1, 2])
        self.assertEqual([x["status"] for x in window["items"]],
                         ["resolved", "unavailable", "resolved"])




class PlexServerQueueCaptureTests(unittest.TestCase):
    def _source(self):
        return {
            "schema": 1, "source_device": "media_player.game_room_fire_tv_stick_lite",
            "profile_id": "plex-home", "context_key": "comedy-shuffle",
            "position_ms": 423000, "shuffled": True,
            "queue_source": {
                "type": "plex_play_queue", "server_id": "plex-1",
                "play_queue_id": 701, "selected_item_id": 3002, "version": 5,
            }
        }

    def _response(self):
        def row(queue_id, rating_key, resolution):
            return {
                "playQueueItemID": queue_id, "ratingKey": rating_key,
                "type": "episode", "title": "Same episode",
                "guid": "plex://episode/123",
                "grandparentGuid": "plex://show/1",
                "grandparentTitle": "Comedy", "parentIndex": 1, "index": 2,
                "librarySectionTitle": "TV Shows",
                "Media": [{"videoResolution": resolution, "bitrate": 4000}],
            }
        return {
            "playQueueID": 701, "playQueueVersion": 5,
            "playQueueSelectedItemOffset": 1,
            "playQueueShuffled": True, "playQueueTotalCount": 3,
            "Metadata": [row(3001, "111", "1080"), row(3002, "222", "4k"),
                         row(3003, "111", "1080")],
        }

    def test_server_captures_duplicate_ratings_without_reordering(self):
        s = handoff.from_plex_queue_response(self._source(), self._response())
        self.assertEqual([item["rating_key"] for item in s.items],
                         ["111", "222", "111"])
        self.assertEqual(s.current_index, 1)
        self.assertEqual(s.position_ms, 423000)
        self.assertTrue(s.shuffled)
        self.assertEqual(s.items[1]["copies"][0]["height"], 2160)

    def test_truncated_server_queue_fails_closed(self):
        response = self._response()
        response["Metadata"].pop()
        with self.assertRaises(HandoffError):
            handoff.from_plex_queue_response(self._source(), response)

    def test_changed_queue_version_fails_closed(self):
        response = self._response()
        response["playQueueVersion"] = 6
        with self.assertRaises(HandoffError):
            handoff.from_plex_queue_response(self._source(), response)

    def test_selected_item_changed_fails_closed(self):
        response = self._response()
        response["Metadata"][1]["playQueueItemID"] = 9999
        with self.assertRaises(HandoffError):
            handoff.from_plex_queue_response(self._source(), response)

    def test_shuffle_change_fails_closed(self):
        response = self._response()
        response["playQueueShuffled"] = False
        with self.assertRaises(HandoffError):
            handoff.from_plex_queue_response(self._source(), response)

if __name__ == "__main__":
    unittest.main()
