"""Home Assistant actions; configuration mutations require an administrator."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.core import SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DOMAIN, SERVICE_DISCOVER, SERVICE_REGISTER, SERVICE_OPEN,
    SERVICE_PREPARE, SERVICE_HANDOFF, SERVICE_CAPTURE,
)
from .device import DeviceSettings
from .discovery import discover_candidates
from .handoff import HandoffError


def _controller(hass):
    controller = hass.data.get(DOMAIN)
    if controller is None:
        raise HomeAssistantError("PowerLink must be configured under Devices & Services")
    return controller


def _translate_error(err: Exception) -> HomeAssistantError:
    # Avoid leaking tokens and other private handoff metadata into traces.
    return HomeAssistantError(str(err) if isinstance(err, HandoffError) else "PowerLink action failed")


def async_setup_services(hass) -> None:
    async def discover(call):
        controller = _controller(hass)
        return {"devices": discover_candidates(hass, set(controller.devices))}

    async def register(call):
        controller = _controller(hass)
        try:
            settings = DeviceSettings.parse(call.data)
            await controller.register_device(settings.as_dict())
        except HandoffError as err:
            raise _translate_error(err) from err

    async def open_device(call):
        controller = _controller(hass)
        try:
            await controller.open_device(call.data["entity_id"], call.context)
        except HandoffError as err:
            raise _translate_error(err) from err

    async def prepare(call):
        controller = _controller(hass)
        try:
            return await controller.prepare(call.data["snapshot"], call.data["destination"])
        except HandoffError as err:
            raise _translate_error(err) from err

    async def handoff(call):
        controller = _controller(hass)
        try:
            result = await controller.handoff(call.data["snapshot"], call.data["destination"], call.context)
        except HandoffError as err:
            raise _translate_error(err) from err
        return result if call.return_response else None

    async def capture_handoff(call):
        controller = _controller(hass)
        try:
            result = await controller.capture_handoff(
                call.data["source"], call.data["destination"], call.context
            )
        except HandoffError as err:
            raise _translate_error(err) from err
        return result if call.return_response else None

    # Registration can configure arbitrary service sequences, so require admin.
    async_register_admin_service(
        hass, DOMAIN, SERVICE_REGISTER, register,
        schema=vol.Schema({
            vol.Required("entity_id"): cv.entity_id,
            vol.Optional("name"): cv.string,
            vol.Required("max_height"): vol.Coerce(int),
            vol.Optional("prefer_highest_quality", default=True): cv.boolean,
            vol.Optional("launch_method", default="adb"): vol.In(("adb", "script")),
            vol.Optional("launch_script", default=""): str,
            vol.Optional("endpoint_base_url", default=""): str,
            vol.Optional("wake_steps", default=list): list,
            vol.Optional("supports_protocol_v1", default=False): cv.boolean,
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DISCOVER, discover,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_OPEN, open_device,
        schema=vol.Schema({vol.Required("entity_id"): cv.entity_id}),
    )
    schema = vol.Schema({
        vol.Required("destination"): cv.entity_id,
        vol.Required("snapshot"): dict,
    })
    hass.services.async_register(
        DOMAIN, SERVICE_PREPARE, prepare, schema=schema,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_HANDOFF, handoff, schema=schema,
        supports_response=SupportsResponse.OPTIONAL,
    )

    # Requests a fresh in-player snapshot with a one-time capability.
    # The source continues playing through capture and destination launch.
    hass.services.async_register(
        DOMAIN, SERVICE_CAPTURE, capture_handoff,
        schema=vol.Schema({
            vol.Required("source"): cv.entity_id,
            vol.Required("destination"): cv.entity_id,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )
