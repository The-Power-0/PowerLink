"""One PowerLink controller per Home Assistant installation."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import DOMAIN
from .device import validate_base_url
from .handoff import HandoffError


class PowerLinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up the server-side queue and Fire TV handoff coordinator."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id("powerlink-controller")
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input is not None:
            try:
                clean = dict(user_input)
                if clean.get("base_url"):
                    clean["base_url"] = validate_base_url(clean["base_url"])
                if clean.get("plex_base_url"):
                    clean["plex_base_url"] = validate_base_url(clean["plex_base_url"])
                if bool(clean.get("plex_base_url")) != bool(clean.get("plex_token")):
                    raise HandoffError("Plex server URL and token must both be supplied")
                return self.async_create_entry(title=clean.get("name") or "PowerLink", data=clean)
            except HandoffError:
                errors["base"] = "invalid_settings"
        return self.async_show_form(
            step_id="user", errors=errors,
            data_schema=vol.Schema({
                vol.Optional("name", default="PowerLink"): str,
                vol.Optional("base_url", default=""): str,
                vol.Optional("plex_base_url", default=""): str,
                vol.Optional("plex_token"): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }),
        )
