"""Verify post-handoff pause is idempotent and source-specific.

This is a pure asyncio test with fake Home Assistant services; it does not
send ADB commands to a real device.
"""
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "powerlink"
pkg = types.ModuleType("powerlink_stage")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("powerlink_stage", pkg)
pause = importlib.import_module("powerlink_stage.source_pause")
device = importlib.import_module("powerlink_stage.device")
HandoffError = importlib.import_module("powerlink_stage.handoff").HandoffError


class FakeServices:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.calls = []

    def has_service(self, domain, service):
        return self.enabled

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, data, blocking))


class FakeHass:
    def __init__(self, enabled=True):
        self.services = FakeServices(enabled)


class SourcePauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_bedroom_source_gets_explicit_pause_not_toggle_or_stop(self):
        hass = FakeHass()
        settings = device.DeviceSettings.parse({
            "entity_id": "media_player.bedroom_fire_tv_stick_lite",
            "launch_method": "adb", "max_height": 1080,
        })
        await pause.pause_handoff_source(hass, settings)
        self.assertEqual(hass.services.calls, [(
            "androidtv", "adb_command",
            {"entity_id": settings.entity_id, "command": "input keyevent 127"},
            True,
        )])

    async def test_source_is_specific_to_registered_origin_not_destination(self):
        hass = FakeHass()
        source = device.DeviceSettings.parse({
            "entity_id": "media_player.game_room_fire_tv_stick_lite",
            "launch_method": "adb", "max_height": 1080,
        })
        await pause.pause_handoff_source(hass, source)
        self.assertEqual(hass.services.calls[0][2]["entity_id"], source.entity_id)

    async def test_script_bridged_media_player_uses_standard_ha_pause(self):
        hass = FakeHass()
        settings = device.DeviceSettings.parse({
            "entity_id": "media_player.remote_living_room",
            "launch_method": "script", "launch_script": "script.launch_remote",
            "max_height": 2160,
        })
        await pause.pause_handoff_source(hass, settings)
        self.assertEqual(hass.services.calls, [(
            "media_player", "media_pause", {"entity_id": settings.entity_id}, True,
        )])

    async def test_missing_adb_action_does_not_run_unrelated_service(self):
        hass = FakeHass(enabled=False)
        settings = device.DeviceSettings.parse({
            "entity_id": "media_player.bedroom_fire_tv_stick_lite",
            "launch_method": "adb", "max_height": 1080,
        })
        with self.assertRaisesRegex(HandoffError, "unavailable"):
            await pause.pause_handoff_source(hass, settings)
        self.assertEqual(hass.services.calls, [])

    async def test_remote_only_bridge_fails_explicitly(self):
        hass = FakeHass()
        settings = device.DeviceSettings.parse({
            "entity_id": "remote.remote_stick",
            "launch_method": "script", "launch_script": "script.launch_remote",
            "max_height": 1080,
        })
        with self.assertRaisesRegex(HandoffError, "Remote-only"):
            await pause.pause_handoff_source(hass, settings)
        self.assertEqual(hass.services.calls, [])


if __name__ == "__main__":
    unittest.main()
