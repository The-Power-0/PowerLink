"""PowerShuffle cycle transitions with a minimal Home Assistant Store stub."""
from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "powerlink"
package = types.ModuleType("powerlink_stage")
package.__path__ = [str(BASE)]
sys.modules.setdefault("powerlink_stage", package)

# PowerShuffle's state machine is intentionally unit-testable without HA.
ha = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
ha.__path__ = []
helpers = sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
helpers.__path__ = []
storage = sys.modules.setdefault("homeassistant.helpers.storage", types.ModuleType("homeassistant.helpers.storage"))
dispatcher = sys.modules.setdefault("homeassistant.helpers.dispatcher", types.ModuleType("homeassistant.helpers.dispatcher"))


class FakeStore:
    data = {}
    def __init__(self, hass, version, key):
        self.key = key
    async def async_load(self):
        return self.data.get(self.key)
    async def async_save(self, value):
        import copy
        self.data[self.key] = copy.deepcopy(value)


storage.Store = FakeStore
dispatcher.async_dispatcher_send = lambda hass, name: None
engine = importlib.import_module("powerlink_stage.powershuffle")

PROFILE = "plex-home-profile"
SHOW = "plex://show/abc"
SERVER = "plex-server"
DEVICE = "media_player.bedroom"


def episode(n):
    return {
        "title": f"Episode {n}", "guid": f"plex://episode/{n}",
        "server_id": SERVER, "rating_key": str(n),
        "show_guid": SHOW, "season": 1, "episode": n,
        "copies": [{"server_id": SERVER, "rating_key": str(n), "guid": f"plex://episode/{n}", "height": 1080}],
    }


class PowerShuffleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeStore.data = {}
        self.manager = engine.PowerShuffleManager(object())
        await self.manager.async_load()
        self.payload = {
            "profile_id": PROFILE, "show_guid": SHOW, "server_id": SERVER,
            "title": "Example Show", "episodes": [episode(1), episode(2), episode(3)]
        }
        self.now = 1_800_000_000
        self.clock = patch.object(engine.time, "time", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.session = await self.manager.start(DEVICE, self.payload)

    async def dispatch(self, action, item=None, suffix=None, **kw):
        state = self.manager._shows[self.manager.key(PROFILE, SHOW, SERVER)]
        item = item or state["queue"][state["cursor"]]
        viewed = kw.get("viewed_ms")
        if action == "completed" and isinstance(viewed, int):
            kw["ranges"] = [[0, viewed]]
        data = {
            "profile_id": PROFILE, "show_guid": SHOW, "server_id": SERVER,
            "session_id": self.session["session_id"], "episode_key": item,
            "event_id": suffix or f"{action}-{self.now}-{item}", "action": action,
            "position_ms": kw.pop("position_ms", 5000), **kw,
        }
        return await self.manager.event(DEVICE, data)

    async def test_skip_until_next_cycle_does_not_award_full_completion(self):
        await self.dispatch("started")
        await self.dispatch("skip_next_cycle")
        for i in range(2):
            await self.dispatch("started", suffix=f"started-{i}")
            await self.dispatch("completed", suffix=f"complete-{i}", viewed_ms=90000, duration_ms=100000)
            await self.dispatch("ended", suffix=f"ended-{i}")
        summary = (await self.manager.list_shows())[0]
        self.assertEqual(summary["cycle"], 2)
        self.assertEqual(summary["partial_cycles"], 1)
        self.assertEqual(summary["completed_cycles"], 0)
        self.assertEqual(summary["total_completions"], 2)
        self.assertEqual(summary["next_cycle_skips"], 0)

    async def test_five_day_cooldown_is_hard_across_cycle_reset(self):
        await self.dispatch("started")
        await self.dispatch("skip_five_days")
        await self.manager.manage(PROFILE, SHOW, SERVER, "reset_cycle")
        state = self.manager._shows[self.manager.key(PROFILE, SHOW, SERVER)]
        self.assertEqual(sum(v.get("until", 0) > self.now for v in state["skips"].values()), 1)
        await self.manager.manage(PROFILE, SHOW, SERVER, "clear_cooldowns")
        self.assertEqual(state["skips"], {})

    async def test_completion_requires_90_percent_unique_content(self):
        await self.dispatch("started")
        with self.assertRaises(engine.HandoffError):
            await self.dispatch("completed", viewed_ms=89999, duration_ms=100000)
        summary = (await self.manager.list_shows())[0]
        self.assertEqual(summary["completed"], 0)
        await self.dispatch("completed", viewed_ms=90000, duration_ms=100000)
        summary = (await self.manager.list_shows())[0]
        self.assertEqual(summary["completed"], 1)

    async def test_normal_exit_never_creates_cooldown(self):
        await self.dispatch("started")
        await self.dispatch("exited")
        self.assertEqual((await self.manager.list_shows())[0]["five_day_skips"], 0)
        self.assertEqual((await self.manager.list_shows())[0]["next_cycle_skips"], 0)

    async def test_duplicate_events_do_not_double_count(self):
        await self.dispatch("started")
        await self.dispatch("completed", suffix="unique", viewed_ms=90000, duration_ms=100000)
        second = await self.dispatch("completed", suffix="unique", viewed_ms=90000, duration_ms=100000)
        self.assertTrue(second["duplicate"])
        self.assertEqual((await self.manager.list_shows())[0]["total_completions"], 1)

    async def test_wrong_device_cannot_write_session(self):
        state = self.manager._shows[self.manager.key(PROFILE, SHOW, SERVER)]
        request = {
            "profile_id": PROFILE, "show_guid": SHOW, "server_id": SERVER,
            "session_id": state["session_id"], "event_id": "bad-source",
            "episode_key": state["queue"][0], "action": "started", "position_ms": 0,
        }
        with self.assertRaises(engine.HandoffError):
            await self.manager.event("media_player.someone_else", request)

    async def test_receiver_handoff_uses_full_ha_queue_not_tv_window(self):
        self.payload["episodes"] = [episode(i) for i in range(1, 13)]
        self.session = await self.manager.start(DEVICE, self.payload)
        self.assertEqual(len(self.session["queue"]), 10)
        await self.dispatch("started")
        snapshot = await self.manager.snapshot_for_handoff(
            DEVICE, self.session["session_id"], PROFILE, 17000,
        )
        self.assertEqual(len(snapshot["items"]), 12)
        self.assertEqual(snapshot["position_ms"], 17000)
        with self.assertRaises(engine.HandoffError):
            await self.manager.snapshot_for_handoff(
                "media_player.stranger", self.session["session_id"], PROFILE, 17000,
            )

    async def test_configurable_completion_and_global_settings_persist(self):
        chosen = {
            "inactivity_hours": 6, "recent_days": 14, "queue_window": 5,
            "completion_percent": 95, "include_specials": False,
        }
        self.assertEqual(await self.manager.configure(chosen), chosen)
        other = engine.PowerShuffleManager(object())
        await other.async_load()
        self.assertEqual(other.settings, chosen)
        self.session = await other.start(DEVICE, self.payload)
        self.assertEqual(self.session["settings"], chosen)
        self.manager = other
        await self.dispatch("started")
        with self.assertRaises(engine.HandoffError):
            await self.dispatch("completed", suffix="early-95", viewed_ms=90000, duration_ms=100000)
        await self.dispatch("completed", suffix="correct-95", viewed_ms=95000, duration_ms=100000)
        self.assertEqual((await self.manager.list_shows())[0]["completed"], 1)

    async def test_details_show_current_skip_and_cycle_history(self):
        await self.dispatch("started")
        await self.dispatch("skip_five_days")
        detail = await self.manager.details(PROFILE, SHOW, SERVER)
        skipped = [entry for entry in detail["episodes"] if entry["status"] == "skip_five_days"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["skip_until"], self.now + 120 * 3600)

    async def test_available_status_when_another_episode_is_cooling_down(self):
        await self.dispatch("started")
        await self.dispatch("skip_five_days")
        info = (await self.manager.list_shows())[0]
        self.assertEqual(info["status"], "available")
        self.assertEqual(info["eligible"], 2)

    async def test_ha_launch_reuses_tracked_episode_inventory_and_preserves_profile(self):
        state = self.manager._shows[self.manager.key(PROFILE, SHOW, SERVER)]
        state["playing"] = False
        result = await self.manager.launch_from_home(
            "example show", "media_player.game_room",
        )
        self.assertEqual(result["action"], "launch")
        snapshot = result["snapshot"]
        self.assertEqual(snapshot["profile_id"], PROFILE)
        self.assertEqual(snapshot["source_device"], "")
        self.assertEqual(snapshot["position_ms"], 0)
        self.assertEqual(len(snapshot["items"]), 3)
        self.assertTrue(snapshot["context_key"].startswith("powershuffle:"))
        self.assertEqual(state["pending_destination"], "media_player.game_room")
        again = await self.manager.launch_from_home(
            "Example Show", "media_player.game_room",
        )
        self.assertEqual(again["action"], "already_pending")
        with self.assertRaises(engine.HandoffError):
            await self.manager.launch_from_home("Example Show", "media_player.bedroom")
        await self.manager.handoff_ack(result["session_id"], "media_player.game_room", True)
        self.assertEqual(state["owner"], "media_player.game_room")
        self.assertEqual(state["pending_destination"], "")
        self.assertEqual((await self.manager.launch_from_home(
            "Example Show", "media_player.game_room",
        ))["action"], "already_playing")
        moved = await self.manager.launch_from_home(
            "Example Show", "media_player.living_room",
        )
        self.assertEqual(moved["action"], "handoff")
        self.assertEqual(moved["source"], "media_player.game_room")

    async def test_failed_ha_launch_can_retry_without_losing_history(self):
        state = self.manager._shows[self.manager.key(PROFILE, SHOW, SERVER)]
        state["playing"] = False
        first = await self.manager.launch_from_home("Example Show", "media_player.game_room")
        await self.manager.cancel_pending_launch(first["session_id"])
        self.assertEqual(state["session_id"], "")
        self.assertEqual(state["pending_destination"], "")
        second = await self.manager.launch_from_home("Example Show", "media_player.game_room")
        self.assertNotEqual(second["session_id"], first["session_id"])
        self.assertEqual(state["cycle"], 1)
        self.assertEqual(state["total_completions"], 0)

    async def test_ha_launch_requires_exact_show_and_disambiguates_profiles(self):
        with self.assertRaisesRegex(engine.HandoffError, "not been registered"):
            await self.manager.launch_from_home("Never Tracked", "media_player.game_room")
        self.payload["profile_id"] = "another-profile"
        await self.manager.start("media_player.other", self.payload)
        with self.assertRaisesRegex(engine.HandoffError, "more than one Plex profile"):
            await self.manager.launch_from_home("Example Show", "media_player.game_room")
        first = self.manager._shows[self.manager.key(PROFILE, SHOW, SERVER)]
        first["playing"] = False
        launch = await self.manager.launch_from_home(
            "Example Show", "media_player.game_room", profile_id=PROFILE,
        )
        self.assertEqual(launch["action"], "launch")

    async def test_ha_launch_never_ignores_five_day_cooldown(self):
        await self.dispatch("started")
        await self.dispatch("skip_five_days")
        state = self.manager._shows[self.manager.key(PROFILE, SHOW, SERVER)]
        state["playing"] = False
        launched = await self.manager.launch_from_home(
            "Example Show", "media_player.game_room",
        )
        self.assertEqual(launched["action"], "launch")
        keys = {(item["season"], item["episode"]) for item in launched["snapshot"]["items"]}
        skipped = [(int(a), int(b)) for a, b in (
            [key.split(":") for key, row in state["skips"].items() if row.get("until", 0) > self.now]
        )]
        self.assertTrue(skipped)
        self.assertTrue(all(key not in keys for key in skipped))

    async def test_dashboard_snapshot_and_paginated_details_use_master_history(self):
        await self.dispatch("started")
        await self.dispatch("skip_five_days")
        snapshot = await self.manager.dashboard_snapshot()
        self.assertEqual(snapshot["total_shows"], 1)
        self.assertEqual(snapshot["version"], 1)
        card = snapshot["shows"][0]
        self.assertEqual(card["title"], "Example Show")
        self.assertEqual(card["five_day_skips"], 1)
        self.assertEqual(card["cycle"], 1)
        self.assertEqual(card["session_status"], "idle")
        self.assertEqual(len(card["id"]), 32)
        page = await self.manager.dashboard_details(card["id"], offset=0, limit=2)
        self.assertEqual(page["total_episodes"], 3)
        self.assertEqual(len(page["episodes"]), 2)
        self.assertEqual(page["episodes"][0]["episode_key"], "1:1")
        page2 = await self.manager.dashboard_details(card["id"], offset=2, limit=2)
        self.assertEqual(len(page2["episodes"]), 1)
        self.assertTrue(any(
            row["status"] == "skip_five_days"
            for row in page["episodes"] + page2["episodes"]
        ))
        identity = await self.manager.dashboard_identity(card["id"])
        self.assertEqual(identity["server_id"], SERVER)
        self.assertEqual(identity["guid"], SHOW)

    async def test_dashboard_exact_id_launch_disambiguates_profiles(self):
        self.payload["profile_id"] = "other-profile"
        await self.manager.start("media_player.other", self.payload)
        with self.assertRaisesRegex(engine.HandoffError, "more than one Plex profile"):
            await self.manager.launch_from_home("Example Show", "media_player.game_room")
        summaries = await self.manager.dashboard_snapshot()
        primary = next(card for card in summaries["shows"] if card["profile_id"] == PROFILE)
        launched = await self.manager.launch_from_home(
            None, "media_player.game_room", dashboard_id=primary["id"]
        )
        self.assertEqual(launched["action"], "launch")
        self.assertEqual(launched["snapshot"]["profile_id"], PROFILE)

    async def test_dashboard_rejects_bad_ids_and_pagination(self):
        with self.assertRaises(engine.HandoffError):
            await self.manager.dashboard_details("not-valid")
        with self.assertRaises(engine.HandoffError):
            await self.manager.dashboard_details("a" * 32, limit=101)
        with self.assertRaises(engine.HandoffError):
            await self.manager.dashboard_snapshot(limit=0)
        with self.assertRaises(engine.HandoffError):
            await self.manager.dashboard_snapshot(offset=-1)
        with self.assertRaises(engine.HandoffError):
            await self.manager.launch_from_home(
                None, "media_player.game_room", dashboard_id="b" * 32
            )

    async def test_history_survives_restart(self):
        await self.dispatch("started")
        await self.dispatch("skip_five_days")
        other = engine.PowerShuffleManager(object())
        await other.async_load()
        self.assertEqual((await other.list_shows())[0]["five_day_skips"], 1)


if __name__ == "__main__":
    unittest.main()
