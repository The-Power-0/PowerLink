"""Fire TV destination wake preflight tests (no Home Assistant installation needed)."""
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

# Only these HA symbols are used by launcher.py; keep the test independent
# of Home Assistant and real Fire TV hardware.
ha = types.ModuleType("homeassistant")
ha.__path__ = []
core = types.ModuleType("homeassistant.core")
core.HomeAssistant = object
core.Context = object
helpers = types.ModuleType("homeassistant.helpers")
helpers.__path__ = []
event = types.ModuleType("homeassistant.helpers.event")
event.async_track_state_change_event = lambda *args, **kwargs: lambda: None
for key, value in [
    ("homeassistant", ha),
    ("homeassistant.core", core),
    ("homeassistant.helpers", helpers),
    ("homeassistant.helpers.event", event),
]:
    sys.modules.setdefault(key, value)

commands = importlib.import_module("powerlink_stage.adb_commands")
launcher = importlib.import_module("powerlink_stage.launcher")
DeviceSettings = importlib.import_module("powerlink_stage.device").DeviceSettings


class FakeServices:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def async_call(self, domain, action, data, blocking=False, context=None):
        self.calls.append((domain, action, data, blocking, context))
        if self.fail:
            raise RuntimeError("ADB offline")


class FakeHass:
    def __init__(self):
        self.services = FakeServices()


class DestinationWakeTests(unittest.IsolatedAsyncioTestCase):
    def _device(self, method="adb"):
        config = {
            "entity_id": "media_player.bedroom_fire_tv_stick_lite",
            "launch_method": method,
            "max_height": 1080,
        }
        if method == "script":
            config["launch_script"] = "script.remote_powerplay"
        return DeviceSettings.parse(config)

    def test_only_wakeup_and_home_no_power_toggle_or_play_pause(self):
        self.assertEqual(
            commands.handoff_screen_wake_command(),
            "input keyevent 224 && input keyevent 3",
        )
        self.assertNotIn("force-stop", commands.handoff_screen_wake_command())
        self.assertNotIn("127", commands.handoff_screen_wake_command())

    async def test_wake_then_deep_link_bedroom_in_order(self):
        hass = FakeHass()
        context = object()
        device = self._device()
        await launcher.wake_destination_screen_for_handoff(hass, device, context)
        await launcher.launch_powerplay(
            hass, device, context,
            uri="powerplay://powerlink?v=1&token=abc&base=http%3A%2F%2Fha",
        )
        calls = hass.services.calls
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], (
            "androidtv", "adb_command",
            {"entity_id": device.entity_id,
             "command": "input keyevent 224 && input keyevent 3"},
            True, context,
        ))
        self.assertEqual(calls[1][0:2], ("androidtv", "adb_command"))
        self.assertEqual(calls[1][2]["entity_id"], device.entity_id)
        self.assertTrue(calls[1][2]["command"].startswith("am start "))
        self.assertNotIn("force-stop", calls[1][2]["command"])

    async def test_script_based_launch_remains_unchanged(self):
        hass = FakeHass()
        device = self._device("script")
        await launcher.wake_destination_screen_for_handoff(hass, device, object())
        self.assertEqual(hass.services.calls, [])

    async def test_adb_wake_failure_prevents_false_success(self):
        hass = FakeHass()
        hass.services.fail = True
        device = self._device()
        with self.assertRaisesRegex(launcher.LaunchError, "wake command"):
            await launcher.wake_destination_screen_for_handoff(hass, device, object())
        self.assertEqual(len(hass.services.calls), 1)


if __name__ == "__main__":
    unittest.main()
