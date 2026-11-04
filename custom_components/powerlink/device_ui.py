"""Convert guided device-manager selections into validated device profiles.

Pure Python so the setup UI and existing admin services use identical rules.
"""
from __future__ import annotations

from typing import Any, Mapping

from .device import DeviceSettings, SCRIPT
from .handoff import HandoffError

HEIGHT_LABELS = {
    480: "480p",
    720: "720p",
    1080: "1080p (Full HD)",
    1440: "1440p",
    2160: "2160p (4K)",
    4320: "4320p (8K)",
}

WAKE_MODES = {
    "keep": "Keep the existing wake routine",
    "none": "Do not wake the device",
    "turn_on": "Turn on the Home Assistant media player",
    "script": "Run a Home Assistant script",
}


def build_device_settings(
    entity_id: str,
    values: Mapping[str, Any],
    existing: DeviceSettings | None = None,
) -> DeviceSettings:
    """Preserve complex wake routines unless explicitly replaced in the UI."""
    settings = existing.as_dict() if existing else {"entity_id": entity_id}
    for field in ("name", "max_height", "prefer_highest_quality",
                  "launch_method", "supports_protocol_v1"):
        if field in values:
            settings[field] = values[field]
    # Empty optional selectors intentionally clear old settings.
    settings["launch_script"] = values.get("launch_script") or ""
    settings["endpoint_base_url"] = values.get("endpoint_base_url") or ""

    wake_mode = values.get("wake_mode", "keep" if existing else "none")
    if wake_mode == "none":
        settings["wake_steps"] = []
    elif wake_mode == "turn_on":
        if not entity_id.startswith("media_player."):
            raise HandoffError("Turn on requires a media_player; select a script instead")
        settings["wake_steps"] = [{
            "kind": "service",
            "domain": "media_player",
            "service": "turn_on",
            "entity_id": entity_id,
            "when_not_states": ["playing", "paused", "idle", "on"],
        }]
    elif wake_mode == "script":
        wake_script = values.get("wake_script")
        if not isinstance(wake_script, str) or not SCRIPT.fullmatch(wake_script):
            raise HandoffError("Select a valid Home Assistant wake script")
        settings["wake_steps"] = [{
            "kind": "service",
            "domain": "script",
            "service": "turn_on",
            "entity_id": wake_script,
        }]
    elif wake_mode != "keep":
        raise HandoffError("Unknown wake routine")

    return DeviceSettings.parse(settings)
