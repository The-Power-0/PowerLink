"""Execute explicit HA wake sequences, then start PowerPlay via existing ADB or HA script."""
from __future__ import annotations

import asyncio
import shlex

from homeassistant.core import HomeAssistant, Context
from homeassistant.helpers.event import async_track_state_change_event

from .const import APP_ID
from .device import DeviceSettings


class LaunchError(RuntimeError):
    """PowerLink failed before the destination acknowledged a handoff."""


async def _wait_for_state(hass: HomeAssistant, entity_id: str,
                          states: list[str], timeout: float) -> None:
    current = hass.states.get(entity_id)
    if current is not None and current.state in states:
        return
    ready = asyncio.Event()

    def changed(event) -> None:
        next_state = event.data.get("new_state")
        if next_state is not None and next_state.state in states:
            ready.set()

    unsubscribe = async_track_state_change_event(hass, [entity_id], changed)
    try:
        # Catch a state change between the first read and listener registration.
        current = hass.states.get(entity_id)
        if current is not None and current.state in states:
            return
        await asyncio.wait_for(ready.wait(), timeout=timeout)
    except TimeoutError as err:
        raise LaunchError(f"{entity_id} never entered {states}") from err
    finally:
        unsubscribe()


async def execute_wake(hass: HomeAssistant, device: DeviceSettings,
                       context: Context) -> None:
    """Only executes when asked to launch. No permanent background polling."""
    for index, step in enumerate(device.wake_steps):
        try:
            kind = step["kind"]
            if kind == "delay":
                await asyncio.sleep(step["seconds"])
            elif kind == "wait_state":
                await _wait_for_state(hass, step["entity_id"], step["states"], step["timeout"])
            elif kind == "service":
                entity_id = step.get("entity_id") or device.entity_id
                state = hass.states.get(entity_id)
                if state is not None and state.state in step["when_not_states"]:
                    continue
                when = step.get("when") or {}
                if when:
                    conditional_state = hass.states.get(when["entity_id"])
                    if conditional_state is None:
                        # Fail closed rather than switch power during a stale
                        # or unknown state (especially smart plugs/CEC).
                        continue
                    if when["states"] and conditional_state.state not in when["states"]:
                        continue
                    if conditional_state.state in when["not_states"]:
                        continue
                if not hass.services.has_service(step["domain"], step["service"]):
                    raise LaunchError(f"Required HA action missing: {step['domain']}.{step['service']}")
                data = dict(step["data"])
                data.setdefault("entity_id", entity_id)
                await asyncio.wait_for(
                    hass.services.async_call(step["domain"], step["service"], data,
                                             blocking=True, context=context),
                    timeout=30,
                )
        except Exception as err:
            if step.get("continue_on_error") is True:
                continue
            raise LaunchError(f"Wake sequence failed at step {index + 1}: {step['kind']}") from err


async def launch_powerplay(hass: HomeAssistant, device: DeviceSettings,
                           context: Context, *, uri: str | None = None) -> None:
    """ADB can deliver deep links. Custom scripts can bridge other networks.

    Never insert user-controlled shell fragments: only the generated URL is
    shell-quoted. Package identity is fixed.
    """
    if device.launch_method == "adb":
        if uri:
            command = "am start -a android.intent.action.VIEW -d " + shlex.quote(uri) + " " + APP_ID
            await hass.services.async_call(
                "androidtv", "adb_command",
                {"entity_id": device.entity_id, "command": command},
                blocking=True, context=context,
            )
        else:
            await hass.services.async_call(
                "media_player", "select_source",
                {"entity_id": device.entity_id, "source": APP_ID},
                blocking=True, context=context,
            )
    else:
        variables = {"powerlink_app_id": APP_ID}
        if uri:
            variables["powerlink_uri"] = uri
        await hass.services.async_call(
            "script", "turn_on",
            {"entity_id": device.launch_script, "variables": variables},
            blocking=True, context=context,
        )
