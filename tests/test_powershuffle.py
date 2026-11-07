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

    async def test_history_survives_restart(self):
        await self.dispatch("started")
        await self.dispatch("skip_five_days")
        other = engine.PowerShuffleManager(object())
        await other.async_load()
        self.assertEqual((await other.list_shows())[0]["five_day_skips"], 1)


if __name__ == "__main__":
    unittest.main()
