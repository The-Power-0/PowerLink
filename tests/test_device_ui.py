"""Guided PowerLink device manager settings remain conservative and reversible."""
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
device = importlib.import_module("powerlink_stage.device")
ui = importlib.import_module("powerlink_stage.device_ui")

ENTITY = "media_player.living_room_fire_tv_stick_4k_plus"


def form(**overrides):
    return {
        "name": "Living Room",
        "max_height": 2160,
        "prefer_highest_quality": True,
        "launch_method": "adb",
        "supports_protocol_v1": False,
        "wake_mode": "none",
        **overrides,
    }


class DeviceManagerTests(unittest.TestCase):
    def test_new_4k_device_does_not_claim_protocol(self):
        result = ui.build_device_settings(ENTITY, form())
        self.assertEqual(result.max_height, 2160)
        self.assertFalse(result.supports_protocol_v1)
        self.assertEqual(result.wake_steps, ())
        self.assertEqual(result.launch_method, "adb")

    def test_turn_on_is_conditional_and_skips_playing_device(self):
        result = ui.build_device_settings(ENTITY, form(wake_mode="turn_on"))
        self.assertEqual(result.wake_steps[0]["service"], "turn_on")
        self.assertIn("playing", result.wake_steps[0]["when_not_states"])

    def test_wake_script_works_without_toggling_media_player(self):
        result = ui.build_device_settings(
            ENTITY, form(wake_mode="script", wake_script="script.tv_wake")
        )
        self.assertEqual(result.wake_steps[0]["domain"], "script")
        self.assertEqual(result.wake_steps[0]["service"], "tv_wake")
        self.assertEqual(result.wake_steps[0]["entity_id"], "script.tv_wake")
        self.assertEqual(result.wake_steps[0]["timeout"], 600.0)

    def test_existing_complex_wake_steps_survive_edits(self):
        original = device.DeviceSettings.parse({
            "entity_id": ENTITY, "name": "Living Room", "max_height": 1080,
            "wake_steps": [{"kind": "delay", "seconds": 3}],
        })
        result = ui.build_device_settings(
            ENTITY,
            form(max_height=2160, name="Family Room", wake_mode="keep"),
            original,
        )
        self.assertEqual(result.name, "Family Room")
        self.assertEqual(result.wake_steps, original.wake_steps)
        self.assertEqual(result.max_height, 2160)

    def test_explicit_disable_removes_old_wake_routine(self):
        original = device.DeviceSettings.parse({
            "entity_id": ENTITY, "wake_steps": [
                {"kind": "delay", "seconds": 3}
            ]
        })
        result = ui.build_device_settings(ENTITY, form(), original)
        self.assertFalse(result.wake_steps)

    def test_script_launch_requires_existing_script_name(self):
        with self.assertRaises(handoff.HandoffError):
            ui.build_device_settings(ENTITY, form(launch_method="script"))
        self.assertEqual(
            ui.build_device_settings(
                ENTITY, form(launch_method="script", launch_script="script.launch_tv")
            ).launch_script,
            "script.launch_tv",
        )

    def test_rejects_unsafe_url_and_invalid_script(self):
        with self.assertRaises(handoff.HandoffError):
            ui.build_device_settings(ENTITY, form(endpoint_base_url="http://example.com"))
        with self.assertRaises(handoff.HandoffError):
            ui.build_device_settings(ENTITY, form(wake_mode="script", wake_script="nope"))

    def test_remote_entity_needs_script_based_wake(self):
        with self.assertRaises(handoff.HandoffError):
            ui.build_device_settings(
                "remote.living_room_tv", form(wake_mode="turn_on")
            )


if __name__ == "__main__":
    unittest.main()
