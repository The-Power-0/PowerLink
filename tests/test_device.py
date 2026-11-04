"""Transport and wake settings are installation-defined, not room-specific."""
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


class DeviceSettingsTests(unittest.TestCase):
    def test_living_room_example(self):
        profile = device.DeviceSettings.parse({
            "entity_id": "media_player.living_room_fire_tv_stick_4k_plus",
            "name": "Living Room", "max_height": 2160,
            "prefer_highest_quality": True,
            "wake_steps": [
                {"kind": "service", "domain": "media_player", "service": "turn_on",
                 "entity_id": "media_player.living_room_fire_tv_stick_4k_plus",
                 "when_not_states": ["playing", "paused", "idle"]},
                {"kind": "delay", "seconds": 2},
            ],
            "launch_method": "adb",
        })
        self.assertEqual(profile.policy.max_height, 2160)
        self.assertEqual(profile.launch_method, "adb")
        self.assertEqual(len(profile.wake_steps), 2)
        self.assertEqual(device.DeviceSettings.parse(profile.as_dict()), profile)

    def test_other_network_script_bridge(self):
        profile = device.DeviceSettings.parse({
            "entity_id": "media_player.bedroom_fire_tv_stick_lite",
            "max_height": 1080,
            "launch_method": "script",
            "launch_script": "script.powerlink_launch_remote_stick",
            "endpoint_base_url": "https://ha.example.net",
        })
        self.assertEqual(profile.launch_script, "script.powerlink_launch_remote_stick")
        self.assertEqual(profile.endpoint_base_url, "https://ha.example.net")

    def test_blocks_public_http_for_token_url(self):
        with self.assertRaises(handoff.HandoffError):
            device.validate_base_url("http://ha.example.org")
        self.assertEqual(device.validate_base_url("http://10.0.0.10:8123/"),
                         "http://10.0.0.10:8123")
        self.assertEqual(device.validate_base_url("https://ha.example.org/"),
                         "https://ha.example.org")

    def test_disallows_credential_in_url(self):
        with self.assertRaises(handoff.HandoffError):
            device.validate_base_url("https://user:pass@ha.example.com")

    def test_cross_entity_wake_conditions(self):
        steps = device.validate_steps([{
            "kind": "service",
            "domain": "switch", "service": "turn_on",
            "data": {"entity_id": "switch.tv_power"},
            "when": {
                "entity_id": "media_player.bedroom_fire_tv_stick_lite",
                "states": ["off", "standby"],
            },
        }])
        self.assertEqual(steps[0]["data"]["entity_id"], "switch.tv_power")
        self.assertEqual(steps[0]["when"]["states"], ["off", "standby"])

    def test_bounds_wake_steps(self):
        with self.assertRaises(handoff.HandoffError):
            device.validate_steps([{"kind": "delay", "seconds": 120}])
        with self.assertRaises(handoff.HandoffError):
            device.validate_steps([{"kind": "arbitrary_shell", "command": "rm -rf /"}])


if __name__ == "__main__":
    unittest.main()
