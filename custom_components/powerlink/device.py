"""Configurable device discovery, resolution policy and wake/launch preferences."""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit

from .handoff import DevicePolicy, HandoffError

ENTITY = re.compile(r"^(media_player|remote)\.[a-z0-9_]+$")
SCRIPT = re.compile(r"^script\.[a-z0-9_]+$")


def validate_base_url(value: str) -> str:
    """Disallow cleartext delivery of bearer handoff tokens over public networks."""
    url = urlsplit(value.strip())
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise HandoffError("base_url must be an absolute HTTP(S) URL without embedded credentials")
    if url.path not in ("", "/") or url.query or url.fragment:
        raise HandoffError("base_url cannot contain a path, query or fragment")
    if url.scheme == "http":
        try:
            address = ipaddress.ip_address(url.hostname)
            private = address.is_private or address.is_loopback
        except ValueError:
            private = url.hostname in {"localhost", "homeassistant"} or url.hostname.endswith(".local")
        if not private:
            raise HandoffError("A public or cross-network PowerLink relay requires HTTPS")
    return value.strip().rstrip("/")


def validate_steps(raw: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw, list) or len(raw) > 20:
        raise HandoffError("wake_steps must be a list of at most 20 steps")
    result = []
    for step in raw:
        if not isinstance(step, dict):
            raise HandoffError("Each wake step must be a mapping")
        kind = step.get("kind")
        if kind == "delay":
            seconds = step.get("seconds", 0)
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 <= seconds <= 30:
                raise HandoffError("Delay must be between 0 and 30 seconds")
            result.append({"kind": "delay", "seconds": float(seconds)})
        elif kind == "wait_state":
            entity = step.get("entity_id")
            states = step.get("states", [])
            timeout = step.get("timeout", 15)
            if not isinstance(entity, str) or "." not in entity:
                raise HandoffError("wait_state needs entity_id")
            if not isinstance(states, list) or not 1 <= len(states) <= 10 or not all(isinstance(v, str) for v in states):
                raise HandoffError("wait_state needs a list of states")
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 60:
                raise HandoffError("wait_state timeout must be 1 to 60 seconds")
            result.append({"kind": kind, "entity_id": entity, "states": list(states), "timeout": float(timeout)})
        elif kind == "service":
            domain, service = step.get("domain"), step.get("service")
            if not isinstance(domain, str) or not re.fullmatch(r"[a-z_]+", domain or ""):
                raise HandoffError("service step needs valid domain")
            if not isinstance(service, str) or not re.fullmatch(r"[a-z_]+", service or ""):
                raise HandoffError("service step needs valid service")
            data = step.get("data", {})
            if not isinstance(data, dict) or len(str(data)) > 3000:
                raise HandoffError("service step data is invalid or too large")
            condition = step.get("when_not_states", [])
            if not isinstance(condition, list) or not all(isinstance(v, str) for v in condition):
                raise HandoffError("when_not_states must be a list of states")
            when = step.get("when", {})
            if not isinstance(when, dict):
                raise HandoffError("when must be a condition object")
            when_entity = when.get("entity_id")
            if when and (not isinstance(when_entity, str) or "." not in when_entity):
                raise HandoffError("when requires a valid entity_id")
            for key in ("states", "not_states"):
                if key in when and (not isinstance(when[key], list)
                                    or len(when[key]) > 20
                                    or not all(isinstance(v, str) for v in when[key])):
                    raise HandoffError(f"when.{key} must be a list of state strings")
            entity = step.get("entity_id")
            if entity is not None and (not isinstance(entity, str) or "." not in entity):
                raise HandoffError("service step entity_id invalid")
            result.append({
                "kind": kind, "domain": domain, "service": service, "entity_id": entity,
                "data": dict(data), "when_not_states": list(condition),
                "when": {
                    "entity_id": when_entity,
                    "states": list(when.get("states", [])),
                    "not_states": list(when.get("not_states", [])),
                } if when else {},
                "continue_on_error": step.get("continue_on_error") is True,
            })
        else:
            raise HandoffError(f"Unsupported wake step kind: {kind}")
    return tuple(result)


@dataclass(frozen=True)
class DeviceSettings:
    """An installation-independent target, not a room-specific implementation."""
    entity_id: str
    name: str
    max_height: int
    prefer_highest_quality: bool = True
    launch_method: str = "adb"
    launch_script: str = ""
    wake_steps: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    endpoint_base_url: str = ""
    supports_protocol_v1: bool = False

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> "DeviceSettings":
        if not isinstance(raw, Mapping):
            raise HandoffError("device settings must be an object")
        entity = raw.get("entity_id")
        if not isinstance(entity, str) or not ENTITY.fullmatch(entity):
            raise HandoffError("entity_id must name a media_player or remote entity")
        name = raw.get("name", entity)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise HandoffError("device name is missing or too long")
        height = raw.get("max_height", 1080)
        highest = raw.get("prefer_highest_quality", True)
        if not isinstance(highest, bool):
            raise HandoffError("prefer_highest_quality must be a boolean")
        policy = DevicePolicy(height, highest)
        method = raw.get("launch_method", "adb")
        if method not in ("adb", "script"):
            raise HandoffError("launch_method must be adb or script")
        script = raw.get("launch_script", "")
        if method == "script" and (not isinstance(script, str) or not SCRIPT.fullmatch(script)):
            raise HandoffError("A script launch method needs a valid script.<id>")
        if not isinstance(script, str):
            raise HandoffError("launch_script must be a string")
        url = raw.get("endpoint_base_url") or ""
        if not isinstance(url, str):
            raise HandoffError("endpoint_base_url must be a string")
        return cls(
            entity_id=entity, name=name.strip(), max_height=policy.max_height,
            prefer_highest_quality=policy.prefer_highest_quality,
            launch_method=method, launch_script=script,
            wake_steps=validate_steps(raw.get("wake_steps", [])),
            endpoint_base_url=validate_base_url(url) if url else "",
            supports_protocol_v1=raw.get("supports_protocol_v1") is True,
        )

    @property
    def policy(self) -> DevicePolicy:
        return DevicePolicy(self.max_height, self.prefer_highest_quality)

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id, "name": self.name, "max_height": self.max_height,
            "prefer_highest_quality": self.prefer_highest_quality,
            "launch_method": self.launch_method, "launch_script": self.launch_script,
            "wake_steps": list(self.wake_steps), "endpoint_base_url": self.endpoint_base_url,
            "supports_protocol_v1": self.supports_protocol_v1,
        }
