"""Native Home Assistant setup and device-management forms for PowerLink."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN
from .device import DeviceSettings, validate_base_url
from .device_ui import HEIGHT_LABELS, WAKE_MODES, build_device_settings
from .discovery import discover_candidates
from .handoff import HandoffError

SCRIPT_PICKER = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="script")
)
PLAYER_PICKER = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["media_player", "remote"])
)


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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """The Configure button opens PowerLink's device manager."""
        return PowerLinkOptionsFlow()


class PowerLinkOptionsFlow(config_entries.OptionsFlow):
    """Manage persisted PowerLink devices without Developer Tools actions."""

    def __init__(self) -> None:
        self._target_id: str | None = None

    @property
    def _controller(self):
        return self.hass.data.get(DOMAIN)

    def _finish(self):
        # The controller writes its own Store immediately; these existing
        # config-entry options are deliberately left unchanged (no reload).
        return self.async_create_entry(title="", data=dict(self.config_entry.options))

    def _device_options(self) -> dict[str, str]:
        controller = self._controller
        if controller is None:
            return {}
        return {
            entity_id: f"{profile.name} ({entity_id})"
            for entity_id, profile in sorted(
                controller.devices.items(), key=lambda item: item[1].name.casefold()
            )
        }

    async def async_step_init(self, user_input=None):
        if self._controller is None:
            return self.async_abort(reason="controller_not_ready")
        steps = ["discover_devices", "manual_device"]
        if self._controller.devices:
            steps.append("pair_player")
        steps.extend(["powershuffle", "powershuffle_settings"])
        if self._controller.devices:
            steps.extend(["edit_device", "advanced_wake", "remove_device"])
        return self.async_show_menu(step_id="init", menu_options=steps)

    async def async_step_pair_player(self, user_input=None):
        """HA-admin-gated code for one pre-registered, verified Fire TV."""
        choices = {
            entity_id: label
            for entity_id, label in self._device_options().items()
            if self._controller.devices[entity_id].supports_protocol_v1
        }
        if not choices:
            return self.async_abort(reason="no_verified_devices")
        if user_input is not None:
            source = user_input.get("entity_id")
            if source not in choices:
                return self.async_abort(reason="device_not_selected")
            code = self._controller.issue_app_pairing(source)
            self._pairing_display_code = code
            self._pairing_display_name = choices[source]
            return await self.async_step_show_pair_code()
        return self.async_show_form(
            step_id="pair_player",
            data_schema=vol.Schema({vol.Required("entity_id"): vol.In(choices)}),
        )

    async def async_step_show_pair_code(self, user_input=None):
        if user_input is not None:
            return self._finish()
        return self.async_show_form(
            step_id="show_pair_code",
            description_placeholders={
                "code": self._pairing_display_code,
                "device": self._pairing_display_name,
            },
            data_schema=vol.Schema({}),
        )

    async def async_step_discover_devices(self, user_input=None):
        """Discover *existing HA entities*, without additional LAN scanning."""
        controller = self._controller
        if controller is None:
            return self.async_abort(reason="controller_not_ready")
        found = [
            candidate for candidate in discover_candidates(
                self.hass, set(controller.devices)
            ) if not candidate["configured"]
        ]
        choices = {
            item["entity_id"]: (
                f'{item["name"]} ({item["entity_id"]}; {item["state"]})'
            )
            for item in found
        }
        if not choices:
            return self.async_abort(reason="no_devices_found")
        errors = {}
        if user_input is not None:
            chosen = user_input["entity_id"]
            if chosen in choices and self.hass.states.get(chosen) is not None:
                self._target_id = chosen
                return await self.async_step_device_settings()
            errors["base"] = "device_unavailable"
        return self.async_show_form(
            step_id="discover_devices", errors=errors,
            data_schema=vol.Schema({
                vol.Required("entity_id"): vol.In(choices),
            }),
        )

    async def async_step_manual_device(self, user_input=None):
        """Fallback for manually managed entities, including remote bridges."""
        errors = {}
        if user_input is not None:
            entity_id = user_input["entity_id"]
            if self.hass.states.get(entity_id) is None:
                errors["base"] = "device_unavailable"
            elif entity_id in self._controller.devices:
                errors["base"] = "already_registered"
            else:
                self._target_id = entity_id
                return await self.async_step_device_settings()
        return self.async_show_form(
            step_id="manual_device", errors=errors,
            data_schema=vol.Schema({
                vol.Required("entity_id"): PLAYER_PICKER,
            }),
        )

    async def async_step_edit_device(self, user_input=None):
        if not (choices := self._device_options()):
            return self.async_abort(reason="no_registered_devices")
        if user_input is not None:
            self._target_id = user_input["entity_id"]
            return await self.async_step_device_settings()
        return self.async_show_form(
            step_id="edit_device",
            data_schema=vol.Schema({vol.Required("entity_id"): vol.In(choices)}),
        )

    def _settings_schema(self, current: DeviceSettings | None) -> vol.Schema:
        state = self.hass.states.get(self._target_id)
        name = (
            current.name if current else
            str(state.attributes.get("friendly_name") or self._target_id)
            if state else str(self._target_id)
        )
        # Script entity selectors are optional until their corresponding
        # launch/wake method is chosen.
        fields = {
            vol.Required("name", default=name[:80]): str,
            vol.Required(
                "max_height", default=current.max_height if current else 1080
            ): vol.In(HEIGHT_LABELS),
            vol.Required(
                "prefer_highest_quality",
                default=current.prefer_highest_quality if current else True,
            ): bool,
            vol.Required(
                "launch_method", default=current.launch_method if current else "adb"
            ): vol.In({
                "adb": "Home Assistant Android TV / ADB",
                "script": "Home Assistant launch script",
            }),
            vol.Optional(
                "endpoint_base_url", default=current.endpoint_base_url if current else ""
            ): str,
            vol.Required(
                "supports_protocol_v1",
                default=current.supports_protocol_v1 if current else False,
            ): bool,
            vol.Required(
                "wake_mode", default="keep" if current else "none"
            ): vol.In(WAKE_MODES),
        }
        launch_script = current.launch_script if current else ""
        fields[
            vol.Optional("launch_script", default=launch_script)
            if launch_script else vol.Optional("launch_script")
        ] = SCRIPT_PICKER
        # Only prefill a wake-script selector when it is genuinely a script.
        wake_script = ""
        if current and len(current.wake_steps) == 1:
            step = current.wake_steps[0]
            if step.get("kind") == "service" and step.get("domain") == "script":
                wake_script = step.get("entity_id") or ""
        fields[
            vol.Optional("wake_script", default=wake_script)
            if wake_script else vol.Optional("wake_script")
        ] = SCRIPT_PICKER
        return vol.Schema(fields)

    async def async_step_device_settings(self, user_input=None):
        controller = self._controller
        if controller is None:
            return self.async_abort(reason="controller_not_ready")
        if not self._target_id:
            return self.async_abort(reason="device_not_selected")
        current = controller.devices.get(self._target_id)
        errors = {}
        if user_input is not None:
            try:
                profile = build_device_settings(
                    self._target_id, user_input, current
                )
                await controller.register_device(profile.as_dict())
            except HandoffError:
                errors["base"] = "invalid_device"
            else:
                return self._finish()
        return self.async_show_form(
            step_id="device_settings",
            description_placeholders={"device": self._target_id},
            errors=errors, data_schema=self._settings_schema(current),
        )

    async def async_step_advanced_wake(self, user_input=None):
        """Edit legacy/complex power sequences in the object editor."""
        if not (choices := self._device_options()):
            return self.async_abort(reason="no_registered_devices")
        if user_input is not None:
            self._target_id = user_input["entity_id"]
            return await self.async_step_wake_steps()
        return self.async_show_form(
            step_id="advanced_wake",
            data_schema=vol.Schema({vol.Required("entity_id"): vol.In(choices)}),
        )

    async def async_step_wake_steps(self, user_input=None):
        controller = self._controller
        if controller is None or self._target_id not in controller.devices:
            return self.async_abort(reason="device_not_selected")
        current = controller.devices[self._target_id]
        errors = {}
        if user_input is not None:
            try:
                data = current.as_dict()
                data["wake_steps"] = user_input["wake_steps"]
                updated = DeviceSettings.parse(data)
                await controller.register_device(updated.as_dict())
            except HandoffError:
                errors["base"] = "invalid_wake_steps"
            else:
                return self._finish()
        return self.async_show_form(
            step_id="wake_steps", errors=errors,
            description_placeholders={"device": current.name},
            data_schema=vol.Schema({
                vol.Required(
                    "wake_steps", default=list(current.wake_steps)
                ): selector.ObjectSelector(),
            }),
        )

    async def async_step_remove_device(self, user_input=None):
        if not (choices := self._device_options()):
            return self.async_abort(reason="no_registered_devices")
        if user_input is not None:
            self._target_id = user_input["entity_id"]
            return await self.async_step_confirm_remove()
        return self.async_show_form(
            step_id="remove_device",
            data_schema=vol.Schema({vol.Required("entity_id"): vol.In(choices)}),
        )

    async def async_step_confirm_remove(self, user_input=None):
        controller = self._controller
        if controller is None or self._target_id not in controller.devices:
            return self.async_abort(reason="device_not_selected")
        errors = {}
        if user_input is not None:
            if user_input["confirm"]:
                await controller.remove_device(self._target_id)
                return self._finish()
            errors["base"] = "confirm_required"
        return self.async_show_form(
            step_id="confirm_remove", errors=errors,
            description_placeholders={
                "device": controller.devices[self._target_id].name
            },
            data_schema=vol.Schema({
                vol.Required("confirm", default=False): bool,
            }),
        )

    async def async_step_powershuffle(self, user_input=None):
        """Manage tracked shows without YAML or developer actions."""
        controller = self._controller
        if controller is None:
            return self.async_abort(reason="controller_not_ready")
        shows = await controller.powershuffle.list_shows()
        choices = {
            controller.powershuffle.key(
                show["profile_id"], show["show_guid"], show["server_id"]
            ): (
                f'{show["title"]} | {show["profile_id"]} | '
                f'cycle {show["cycle"]} | {show["percent"]}% complete'
            )
            for show in shows
        }
        if not choices:
            return self.async_abort(reason="no_powershuffle_shows")
        if user_input is not None:
            self._powershuffle_key = user_input["show_key"]
            if self._powershuffle_key not in choices:
                return self.async_abort(reason="powershuffle_show_not_found")
            self._powershuffle_show = next(
                item for item in shows
                if controller.powershuffle.key(
                    item["profile_id"], item["show_guid"], item["server_id"]
                ) == self._powershuffle_key
            )
            return await self.async_step_powershuffle_manage()
        return self.async_show_form(
            step_id="powershuffle",
            data_schema=vol.Schema({vol.Required("show_key"): vol.In(choices)}),
        )

    async def async_step_powershuffle_manage(self, user_input=None):
        controller = self._controller
        info = getattr(self, "_powershuffle_show", None)
        if controller is None or not info:
            return self.async_abort(reason="powershuffle_show_not_found")
        errors = {}
        if user_input is not None:
            if user_input["command"] == "view_history":
                return await self.async_step_powershuffle_history()
            if not user_input.get("confirm"):
                errors["base"] = "confirm_required"
            else:
                try:
                    await controller.powershuffle.manage(
                        info["profile_id"], info["show_guid"], info["server_id"],
                        user_input["command"],
                    )
                except HandoffError:
                    errors["base"] = "powershuffle_active"
                else:
                    return self._finish()
        return self.async_show_form(
            step_id="powershuffle_manage", errors=errors,
            description_placeholders={
                "show": info["title"], "profile": info["profile_id"],
                "cycle": str(info["cycle"]), "percent": str(info["percent"]),
                "deferred": str(info["next_cycle_skips"]),
                "cooling": str(info["five_day_skips"]),
            },
            data_schema=vol.Schema({
                vol.Required("command"): vol.In({
                    "reset_cycle": "Start a new cycle (keep five-day skips)",
                    "clear_cooldowns": "Clear five-day skips",
                    "reset_cycle_and_cooldowns": "New cycle and clear five-day skips",
                    "view_history": "View episode and cycle history (read-only)",
                }),
                vol.Required("confirm", default=False): bool,
            }),
        )

    async def async_step_powershuffle_settings(self, user_input=None):
        """Small HA-native form for global PowerShuffle defaults."""
        controller = self._controller
        if controller is None:
            return self.async_abort(reason="controller_not_ready")
        settings = controller.powershuffle.settings
        errors = {}
        if user_input is not None:
            try:
                await controller.powershuffle.configure(user_input)
            except HandoffError:
                errors["base"] = "powershuffle_invalid_settings"
            else:
                return self._finish()
        return self.async_show_form(
            step_id="powershuffle_settings", errors=errors,
            data_schema=vol.Schema({
                vol.Required("inactivity_hours", default=settings["inactivity_hours"]):
                    vol.In([1, 6, 12, 24, 48]),
                vol.Required("recent_days", default=settings["recent_days"]):
                    vol.In([0, 3, 7, 14, 30]),
                vol.Required("queue_window", default=settings["queue_window"]):
                    vol.In([5, 10, 15, 20, 32]),
                vol.Required("completion_percent", default=settings["completion_percent"]):
                    vol.In([80, 85, 90, 95]),
                vol.Required("include_specials", default=settings["include_specials"]): bool,
            }),
        )

    async def async_step_powershuffle_history(self, user_input=None):
        """Read-only, bounded episode list; never reset history by viewing it."""
        controller = self._controller
        info = getattr(self, "_powershuffle_show", None)
        if controller is None or not info:
            return self.async_abort(reason="powershuffle_show_not_found")
        details = await controller.powershuffle.details(
            info["profile_id"], info["show_guid"], info["server_id"]
        )
        if user_input is not None:
            return self._finish()
        # Home Assistant config form descriptions have limited screen width.
        # Show outstanding skips first, with full timestamps where available.
        pending = [
            item for item in details["episodes"]
            if item["status"] in ("skip_five_days", "skip_next_cycle")
        ]
        lines = []
        for item in pending[:30]:
            value = item["status"].replace("_", " ")
            if item["skip_until"] is not None:
                value += " until " + str(int(item["skip_until"])) + " (Unix seconds)"
            lines.append(f'{item["episode_key"]} {item["title"]}: {value}')
        if not lines:
            lines.append("No episodes currently skipped or deferred")
        cycles = details["cycle_history"]
        last_cycle = cycles[-1] if cycles else None
        summary = (
            f'Last rotation: cycle {last_cycle["cycle"]} '
            f'({last_cycle["completed"]}/{last_cycle["total"]}, '
            f'{last_cycle["reason"]})'
            if last_cycle else "No previous rotations yet"
        )
        return self.async_show_form(
            step_id="powershuffle_history",
            description_placeholders={
                "show": info["title"], "profile": info["profile_id"],
                "history": "\\n".join(lines),
                "last_cycle": summary,
            },
            data_schema=vol.Schema({}),
        )
