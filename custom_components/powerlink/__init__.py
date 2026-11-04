"""PowerLink is a Home Assistant-side handoff controller for PowerPlay.

No idle Plex polling, LAN subnet scans, custom ADB connections, or device CPU
tasks. The controller uses entities already managed by Home Assistant.
"""
from __future__ import annotations

from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .controller import PowerLinkController
from .http import PowerLinkSessionView, PowerLinkAcknowledgmentView, PowerLinkCaptureView
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass, config) -> bool:
    async_setup_services(hass)
    hass.http.register_view(PowerLinkSessionView())
    hass.http.register_view(PowerLinkAcknowledgmentView())
    hass.http.register_view(PowerLinkCaptureView())
    return True


async def async_setup_entry(hass, entry) -> bool:
    controller = PowerLinkController(hass, entry.data)
    await controller.async_load()
    hass.data[DOMAIN] = controller
    return True


async def async_unload_entry(hass, entry) -> bool:
    hass.data.pop(DOMAIN, None)
    return True
