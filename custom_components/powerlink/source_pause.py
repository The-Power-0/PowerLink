"""Pause only the source that handed off a verified queue.

An ADB KEYCODE_MEDIA_PAUSE is idempotent (unlike PLAY_PAUSE toggle) and
works with Fire TV Stick Lite/4K. Script-bridged media_player entities use
Home Assistant's standard media_pause service. No source power-off or queue
mutation is performed.
"""
from __future__ import annotations

import asyncio

from .device import DeviceSettings
from .handoff import HandoffError


async def pause_handoff_source(hass, device: DeviceSettings) -> None:
    """Send one explicit PAUSE only after the destination ACKs 'started'."""
    if device.launch_method == "adb":
        domain, action = "androidtv", "adb_command"
        data = {
            "entity_id": device.entity_id,
            "command": "input keyevent 127",  # KEYCODE_MEDIA_PAUSE, NOT a toggle
        }
    elif device.entity_id.startswith("media_player."):
        domain, action = "media_player", "media_pause"
        data = {"entity_id": device.entity_id}
    else:
        raise HandoffError(
            "Remote-only PowerLink sources cannot be paused via media_player; "
            "configure a media_player source or a pause-capable bridge"
        )
    if not hass.services.has_service(domain, action):
        raise HandoffError(f"Source pause action unavailable: {domain}.{action}")
    await asyncio.wait_for(
        hass.services.async_call(domain, action, data, blocking=True),
        timeout=8,
    )
