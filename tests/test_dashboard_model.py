"""Pure presentation invariants: no HA, Plex or Fire TV dependencies."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "custom_components" / "powerlink" / "dashboard_model.py"
spec = importlib.util.spec_from_file_location("dashboard_model_stage", FILE)
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


class DashboardModelTests(unittest.TestCase):
    def state(self, **updates):
        s = {
            "profile_id": "profile-a",
            "show_guid": "plex://show/123",
            "server_id": "server-1",
            "title": "King of the Hill",
            "queue": ["1:2", "1:1"], "cursor": 0,
            "episodes": {
                "1:2": {"title": "Episode Two", "season": 1, "episode": 2},
                "1:1": {"title": "Episode One", "season": 1, "episode": 1},
            },
            "owner": "media_player.bedroom",
            "playing": True, "last_heartbeat": 1000,
            "position_ms": 95000, "duration_ms": 120000,
            "pending_destination": "", "pending_since": 0,
            "last_activity": 1000, "last_event": "progress",
        }
        s.update(updates)
        return s

    def summary(self):
        return {
            "cycle": 2, "total": 2, "completed": 1, "remaining": 1,
            "percent": 50, "eligible": 1, "five_day_skips": 0,
            "next_cycle_skips": 0, "completed_cycles": 1,
            "partial_cycles": 0, "total_completions": 2,
            "status": "available",
        }

    def test_stable_master_identity_is_profile_and_server_isolated(self):
        a = model.show_id("profile-a", "plex://show/123", "server-1")
        self.assertEqual(a, model.show_id("profile-a", "plex://show/123", "server-1"))
        self.assertEqual(len(a), 32)
        self.assertNotEqual(a, model.show_id("profile-b", "plex://show/123", "server-1"))
        self.assertNotEqual(a, model.show_id("profile-a", "plex://show/123", "server-2"))
        self.assertNotEqual(a, model.show_id("profile-a", "plex://show/124", "server-1"))

    def test_live_status_from_existing_12_second_events(self):
        state = self.state()
        self.assertEqual(model.session_status(state, 1025), "recently_playing")
        self.assertEqual(model.session_status(state, 1061), "stale")
        self.assertEqual(model.session_status(self.state(playing=False, last_event="exited"), 1020), "stopped")
        self.assertEqual(model.session_status(self.state(playing=False, last_event="ended"), 1020), "idle")
        self.assertEqual(model.session_status(self.state(pending_destination="media_player.game_room", pending_since=1000), 1030), "starting")
        self.assertEqual(model.session_status(self.state(pending_destination="media_player.game_room", pending_since=1000), 1091), "stale")

    def test_output_has_current_episode_but_never_queue_or_credentials(self):
        value = model.dashboard_show(self.state(), 1020, self.summary())
        self.assertEqual(value["current_episode"]["title"], "Episode Two")
        self.assertEqual(value["session_status"], "recently_playing")
        self.assertEqual(value["position_ms"], 95000)
        self.assertEqual(value["percent"], 50)
        self.assertNotIn("queue", value)
        self.assertNotIn("episodes", value)
        self.assertNotIn("token", value)

    def test_unknown_or_idle_position_is_not_misrepresented(self):
        value = model.dashboard_show(self.state(playing=False), 1100, self.summary())
        self.assertEqual(value["position_ms"], 0)
        self.assertEqual(value["duration_ms"], 0)
        self.assertEqual(value["owner"], "media_player.bedroom")
        self.assertEqual(value["session_status"], "idle")

    def test_no_index_error_on_old_or_completed_queues(self):
        value = model.dashboard_show(self.state(queue=[], cursor=0, episodes={}), 1000, self.summary())
        self.assertIsNone(value["current_episode"])


if __name__ == "__main__":
    unittest.main()
