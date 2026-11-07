"""Compact, automatically discovered per-show PowerShuffle progress sensors."""
from __future__ import annotations

import hashlib
import json

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN


class PowerShuffleSensor(SensorEntity):
    _attr_has_entity_name = False
    _attr_icon = "mdi:shuffle"
    _attr_native_unit_of_measurement = "%"
    _attr_should_poll = False

    def __init__(self, info: dict) -> None:
        self._info = dict(info)
        identity = json.dumps([
            info["profile_id"], info["show_guid"], info["server_id"]
        ], separators=(",", ":"))
        self._attr_unique_id = "powershuffle_" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        self._attr_name = f"PowerShuffle {info['title']} ({info['profile_id']})"

    @property
    def native_value(self) -> float:
        return float(self._info["percent"])

    @property
    def extra_state_attributes(self) -> dict:
        return {
            k: value for k, value in self._info.items()
            if k not in ("percent", "title", "show_guid", "profile_id", "server_id")
        } | {
            "profile_id": self._info["profile_id"],
            "show_guid": self._info["show_guid"],
            "server_id": self._info["server_id"],
        }

    def update_from_summary(self, data: dict) -> None:
        self._info = dict(data)
        if self.hass is not None:
            self.async_write_ha_state()


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    controller = hass.data[DOMAIN]
    sensors: dict[str, PowerShuffleSensor] = {}

    async def update() -> None:
        missing = []
        for summary in await controller.powershuffle.list_shows():
            key = json.dumps([
                summary["profile_id"], summary["show_guid"], summary["server_id"]
            ], separators=(",", ":"))
            if key not in sensors:
                sensor = PowerShuffleSensor(summary)
                sensors[key] = sensor
                missing.append(sensor)
            else:
                sensors[key].update_from_summary(summary)
        if missing:
            async_add_entities(missing)

    await update()

    def changed() -> None:
        hass.async_create_task(update())

    unsubscribe = async_dispatcher_connect(
        hass, "powerlink_powershuffle_updated", changed
    )
    entry.async_on_unload(unsubscribe)
