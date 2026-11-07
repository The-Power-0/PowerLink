"""Authenticated, event-driven dashboard API for PowerLink.

Only existing HA master records and HA state are read. No Fire TV polling,
ADB probes, Plex scans or additional device events are introduced.
"""
from __future__ import annotations

from datetime import timedelta

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import EVENT_STATE_CHANGED, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from .handoff import HandoffError


def _controller(hass):
    return hass.data.get(DOMAIN)


def _devices(controller) -> list[dict]:
    devices = []
    for entity_id, device in sorted(
        controller.devices.items(), key=lambda pair: pair[1].name.casefold()
    ):
        state = controller.hass.states.get(entity_id)
        state_name = state.state if state is not None else "unknown"
        devices.append({
            "entity_id": entity_id,
            "name": device.name,
            "max_height": device.max_height,
            "supports_protocol_v1": device.supports_protocol_v1,
            "state": state_name,
            "available": state_name not in ("unknown", "unavailable"),
        })
    return devices


async def _snapshot(hass, *, offset: int = 0, limit: int = 100) -> dict:
    controller = _controller(hass)
    payload = await controller.powershuffle.dashboard_snapshot(offset=offset, limit=limit)
    payload["devices"] = _devices(controller)
    payload["settings"] = controller.powershuffle.settings
    payload["artwork_supported"] = controller.plex is not None
    return payload


@websocket_api.websocket_command({
    vol.Required("type"): "powerlink/dashboard",
    vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0, max=1000000)),
    vol.Optional("limit", default=100): vol.All(int, vol.Range(min=1, max=100)),
})
@websocket_api.async_response
async def ws_dashboard(hass, connection, msg):
    if _controller(hass) is None:
        connection.send_error(msg["id"], "not_ready", "PowerLink is not configured")
        return
    connection.send_result(
        msg["id"], await _snapshot(hass, offset=msg["offset"], limit=msg["limit"])
    )


@websocket_api.websocket_command({
    vol.Required("type"): "powerlink/show_details",
    vol.Required("show_id"): str,
    vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0, max=5000)),
    vol.Optional("limit", default=50): vol.All(int, vol.Range(min=1, max=100)),
})
@websocket_api.async_response
async def ws_show_details(hass, connection, msg):
    controller = _controller(hass)
    if controller is None:
        connection.send_error(msg["id"], "not_ready", "PowerLink is not configured")
        return
    try:
        result = await controller.powershuffle.dashboard_details(
            msg["show_id"], offset=msg["offset"], limit=msg["limit"]
        )
    except HandoffError:
        connection.send_error(msg["id"], "not_found", "Unknown tracked show or invalid page")
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({
    vol.Required("type"): "powerlink/subscribe_dashboard",
})
@websocket_api.async_response
async def ws_subscribe_dashboard(hass, connection, msg):
    """Subscribe only while a browser actually has the dashboard open."""
    controller = _controller(hass)
    if controller is None:
        connection.send_error(msg["id"], "not_ready", "PowerLink is not configured")
        return
    current = await _snapshot(hass)
    subscription_id = msg["id"]
    connection.send_result(subscription_id, current)
    closed = False
    scheduled = False
    last_sent = current

    async def send_if_changed():
        nonlocal scheduled, last_sent
        try:
            if closed or _controller(hass) is None:
                return
            current_snapshot = await _snapshot(hass)
            if not closed and current_snapshot != last_sent:
                last_sent = current_snapshot
                connection.send_message(
                    websocket_api.event_message(
                        subscription_id, current_snapshot
                    )
                )
        finally:
            scheduled = False

    @callback
    def changed():
        nonlocal scheduled
        if closed or scheduled:
            return
        scheduled = True
        hass.async_create_task(send_if_changed())

    @callback
    def state_changed(event):
        if event.data.get("entity_id") in controller.devices:
            changed()

    unsubscribe_dispatch = async_dispatcher_connect(
        hass, "powerlink_powershuffle_updated", changed
    )
    unsubscribe_state = hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)
    # Expire "recently_playing" and "starting" without traffic to Fire Sticks.
    unsubscribe_clock = async_track_time_interval(
        hass, lambda _now: changed(), timedelta(seconds=30)
    )

    @callback
    def unsubscribe():
        nonlocal closed
        closed = True
        unsubscribe_dispatch()
        unsubscribe_state()
        unsubscribe_clock()

    connection.subscriptions[subscription_id] = unsubscribe


def async_register(hass) -> None:
    websocket_api.async_register_command(hass, ws_dashboard)
    websocket_api.async_register_command(hass, ws_show_details)
    websocket_api.async_register_command(hass, ws_subscribe_dashboard)
