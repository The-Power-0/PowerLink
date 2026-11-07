"""PowerLink is a Home Assistant-side handoff controller for PowerPlay.

No idle Plex polling, LAN subnet scans, custom ADB connections, or device CPU
tasks. The controller uses entities already managed by Home Assistant.
"""
from __future__ import annotations

from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .controller import PowerLinkController
from .http import (
    PowerLinkSessionView, PowerLinkAcknowledgmentView, PowerLinkCaptureView,
    PowerLinkAppPairView, PowerLinkAppDevicesView, PowerLinkAppHandoffView,
    PowerShuffleStartView, PowerShuffleEventView, PowerShuffleAttachView,
)
from .services import async_setup_services
from .dashboard_ws import async_register as register_dashboard_ws

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass, config) -> bool:
    async_setup_services(hass)
    register_dashboard_ws(hass)
    hass.http.register_view(PowerLinkSessionView())
    hass.http.register_view(PowerLinkAcknowledgmentView())
    hass.http.register_view(PowerLinkCaptureView())
    hass.http.register_view(PowerLinkAppPairView())
    hass.http.register_view(PowerLinkAppDevicesView())
    hass.http.register_view(PowerLinkAppHandoffView())
    hass.http.register_view(PowerShuffleStartView())
    hass.http.register_view(PowerShuffleEventView())
    hass.http.register_view(PowerShuffleAttachView())
    return True


async def async_setup_entry(hass, entry) -> bool:
    controller = PowerLinkController(hass, entry.data)
    await controller.async_load()
    hass.data[DOMAIN] = controller
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True


async def async_unload_entry(hass, entry) -> bool:
    result = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if result:
        hass.data.pop(DOMAIN, None)
    return result
