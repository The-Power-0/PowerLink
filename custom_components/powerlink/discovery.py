"""No LAN scans or new ADB connections: discover registered HA device entities."""
from __future__ import annotations

from homeassistant.helpers import entity_registry as er


def discover_candidates(hass, registered: set[str]) -> list[dict]:
    registry = er.async_get(hass)
    results = []
    for state in hass.states.async_all("media_player"):
        entity = registry.async_get(state.entity_id)
        name = str(state.attributes.get("friendly_name") or state.entity_id)
        platform = entity.platform if entity is not None else ""
        # Fire TV uses HA's androidtv integration; also allow informative names
        # for devices added through other compatible integrations.
        if platform not in ("androidtv", "fire_tv") and not any(
            marker in name.lower() for marker in ("fire tv", "fire stick", "firestick", "android tv")
        ):
            continue
        results.append({
            "entity_id": state.entity_id, "name": name, "platform": platform,
            "state": state.state, "configured": state.entity_id in registered,
        })
    return sorted(results, key=lambda item: (item["name"].lower(), item["entity_id"]))
