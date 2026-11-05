"""PowerLink HA coordinator: device registry and short-lived windowed handoffs."""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, replace
from typing import Any, Mapping
from urllib.parse import urlencode

from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION, STORAGE_KEY, SESSION_TTL_SECONDS
from .device import DeviceSettings
from .handoff import HandoffError, QueueSnapshot, WindowPlanner
from .launcher import execute_wake, launch_powerplay
from .plex_catalog import PlexCopyCatalog
from .source_pause import pause_handoff_source
from .app_pairing import AppPairings


@dataclass
class ActiveTransfer:
    snapshot: QueueSnapshot
    destination: DeviceSettings
    planner: WindowPlanner
    created_at: float
    acknowledged: bool = False


@dataclass
class PendingCapture:
    source: str
    future: asyncio.Future
    created_at: float


class PowerLinkController:
    def __init__(self, hass, data: Mapping[str, Any]) -> None:
        self.hass = hass
        self.options = dict(data)
        self.devices: dict[str, DeviceSettings] = {}
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._transfers: dict[str, ActiveTransfer] = {}
        # One-shot requests only; no socket listener, background timer or
        # remote polling is installed on any Fire Stick.
        self._captures: dict[str, PendingCapture] = {}
        self.app_pairings = AppPairings()
        self._lock = asyncio.Lock()
        plex_url, plex_token = data.get("plex_base_url"), data.get("plex_token")
        self.plex = PlexCopyCatalog(
            hass, plex_url, plex_token, server_id=data.get("plex_server_id", "")
        ) if plex_url and plex_token else None

    async def async_load(self) -> None:
        saved = await self._store.async_load() or {}
        self.app_pairings = AppPairings(saved.get("paired_tokens", {}))
        for raw in saved.get("devices", []):
            try:
                settings = DeviceSettings.parse(raw)
                self.devices[settings.entity_id] = settings
            except HandoffError:
                continue

    async def _save_registry(self) -> None:
        """HA Store persists only token digests, never credentials or codes."""
        await self._store.async_save({
            "devices": [dev.as_dict() for dev in self.devices.values()],
            "paired_tokens": dict(self.app_pairings.token_digests),
        })

    def issue_app_pairing(self, source: str) -> str:
        device = self.device(source)
        if not device.supports_protocol_v1:
            raise HandoffError("Enable verified PowerLink v1 before pairing")
        return self.app_pairings.issue(source)

    async def redeem_app_pairing(self, code: object, client: str) -> dict[str, str]:
        async with self._lock:
            source, token = self.app_pairings.redeem(code, client)
            if source not in self.devices:
                self.app_pairings.revoke(source)
                raise HandoffError("Device no longer registered")
            await self._save_registry()
            return {
                "source_device": source,
                "source_name": self.devices[source].name,
                "token": token,
                "protocol": 2,
            }

    def authorized_app(self, source: str, token: str) -> bool:
        return (source in self.devices
                and self.devices[source].supports_protocol_v1
                and self.app_pairings.authenticate(source, token))

    def discover_app_destinations(self, source: str) -> dict[str, Any]:
        self.device(source)
        targets = []
        for entity_id, device in sorted(
            self.devices.items(), key=lambda pair: pair[1].name.casefold()
        ):
            if entity_id == source or not device.supports_protocol_v1:
                continue
            state = self.hass.states.get(entity_id)
            targets.append({
                "entity_id": entity_id,
                "name": device.name,
                "max_height": device.max_height,
                "available": state is not None
                and state.state not in ("unavailable", "unknown"),
            })
        return {
            "protocol": 2, "source_device": source,
            "source_name": self.devices[source].name, "destinations": targets,
        }

    async def app_handoff(self, source: str, destination: str,
                          snapshot: Mapping[str, Any], context) -> dict[str, Any]:
        """Source-app-initiated handoff; no source ADB capture intent needed."""
        if source == destination:
            raise HandoffError("Choose another destination")
        if not self.device(source).supports_protocol_v1:
            raise HandoffError("Source has not been verified for PowerLink")
        if not isinstance(snapshot, Mapping) or snapshot.get("schema") != 1:
            raise HandoffError("Invalid source queue snapshot")
        if snapshot.get("source_device") != source:
            raise HandoffError("Snapshot did not originate from paired device")
        source_reference = snapshot.get("queue_source")
        if isinstance(source_reference, Mapping) and source_reference.get("type") == "powerlink_transfer":
            token = source_reference.get("session_token")
            previous = self._session(token)
            if not previous.acknowledged or previous.destination.entity_id != source:
                raise HandoffError("Imported queue does not belong to paired playing source")
            cursor = snapshot.get("current_index")
            position = snapshot.get("position_ms")
            if type(cursor) is not int or not 0 <= cursor < len(previous.snapshot.items):
                raise HandoffError("Imported queue index is invalid")
            if type(position) is not int or not 0 <= position <= 86_400_000:
                raise HandoffError("Imported queue playhead is invalid")
            if snapshot.get("profile_id") != previous.snapshot.profile_id:
                raise HandoffError("Imported Plex Home profile differs from the original queue")
            # Reuse HA's authoritative full immutable queue instead of
            # transmitting/re-serializing 5000 items from the Fire Stick.
            snapshot = replace(
                previous.snapshot, source_device=source,
                current_index=cursor, position_ms=position,
            )
        result = await self.handoff(snapshot, destination, context)
        # Do not return a destination bearer URL or token to the source.
        return {
            "accepted": True, "destination": result["destination"],
            "queue_length": result["queue_length"],
            "message": "Destination launch requested; source pauses after verified playback",
        }

    async def register_device(self, config: Mapping[str, Any]) -> DeviceSettings:
        settings = DeviceSettings.parse(config)
        if self.hass.states.get(settings.entity_id) is None:
            raise HandoffError(f"No Home Assistant entity: {settings.entity_id}")
        async with self._lock:
            self.devices[settings.entity_id] = settings
            await self._save_registry()
        return settings

    async def remove_device(self, entity_id: str) -> None:
        """Unregister a device; never remove its Home Assistant entity."""
        async with self._lock:
            if entity_id not in self.devices:
                raise HandoffError(f"Unregistered device: {entity_id}")
            del self.devices[entity_id]
            self.app_pairings.revoke(entity_id)
            await self._save_registry()

    def device(self, entity_id: str) -> DeviceSettings:
        device = self.devices.get(entity_id)
        if device is None:
            raise HandoffError(f"Unregistered destination: {entity_id}")
        return device

    def _purge_expired(self) -> None:
        now = time.monotonic()
        for key, item in list(self._transfers.items()):
            if now - item.created_at >= SESSION_TTL_SECONDS:
                del self._transfers[key]

    async def prepare(self, raw: Mapping[str, Any], target: str) -> dict[str, Any]:
        device = self.device(target)
        if isinstance(raw, QueueSnapshot):
            snapshot = raw
        elif isinstance(raw.get("queue_source"), Mapping):
            if self.plex is None:
                raise HandoffError("Configure Plex URL/token for server-side queue capture")
            snapshot = await self.plex.snapshot_from_plex_queue(raw)
        else:
            snapshot = QueueSnapshot.parse(raw)
        planner = WindowPlanner(
            snapshot, device.policy,
            self.plex.find_copies if self.plex is not None else None,
        )
        current = await planner.ensure_current_playable()
        base = device.endpoint_base_url or self.options.get("base_url")
        if not base:
            raise HandoffError("Configure a Home Assistant URL reachable from this destination")
        token = secrets.token_urlsafe(32)
        session = ActiveTransfer(snapshot, device, planner, time.monotonic())
        self._purge_expired()
        self._transfers[token] = session
        # Deep-link carries only the token and API base, never a Plex token,
        # queue manifest or Home Assistant long-lived API bearer.
        # Use the stable Plex Home UUID as the destination profile selector.
        # PowerPlay's existing deep-link machinery switches profiles and
        # asks for a PIN on protected accounts *before* touching playback.
        profile = snapshot.profile_id
        uri = "powerplay://powerlink?" + urlencode({
            "v": "1", "base": base, "token": token,
            **({"profile": profile} if profile else {}),
        })
        return {
            "destination": target,
            "deep_link": uri,
            "queue_length": len(snapshot.items),
            "current_index": snapshot.current_index,
            "position_ms": snapshot.position_ms,
            "shuffled": snapshot.shuffled,
            "initial_media": current["media"],
            "expires_in": SESSION_TTL_SECONDS,
        }

    def _session(self, token: str) -> ActiveTransfer:
        self._purge_expired()
        if not isinstance(token, str) or len(token) > 128 or token not in self._transfers:
            raise HandoffError("Unknown or expired transfer")
        return self._transfers[token]

    async def window(self, token: str, start: int, count: int) -> dict[str, Any]:
        session = self._session(token)
        result = await session.planner.window(start, count)
        result["destination"] = session.destination.entity_id
        return result

    async def acknowledge(self, token: str, status: str) -> None:
        session = self._session(token)
        if status not in ("started", "failed"):
            raise HandoffError("Invalid acknowledgment status")
        if session.acknowledged:
            return
        session.acknowledged = True
        source_paused = False
        pause_error = None
        if status == "started":
            # A launcher return only proves the Android intent was delivered.
            # The PowerPlay receiver sends 'started' exclusively after the
            # destination confirms the right item, playhead and first frame.
            # Until then the source must continue playing. Do not stop or
            # power off the source, and never toggle an already paused player.
            source = self.devices.get(session.snapshot.source_device)
            if source is None:
                pause_error = "SourceDeviceNotRegistered"
            else:
                try:
                    await pause_handoff_source(self.hass, source)
                    source_paused = True
                except Exception as err:
                    # Source may remain playing if ADB/remote is unavailable.
                    # The destination must not be rolled back because of it.
                    pause_error = type(err).__name__
                    self.hass.bus.async_fire("powerlink_source_pause_failed", {
                        "source_device": session.snapshot.source_device,
                        "destination_device": session.destination.entity_id,
                        "reason": pause_error,
                    })
        self.hass.bus.async_fire("powerlink_handoff_acknowledged", {
            "source_device": session.snapshot.source_device,
            "destination_device": session.destination.entity_id,
            "status": status,
            "source_paused": source_paused,
            "source_pause_error": pause_error,
            "queue_length": len(session.snapshot.items),
            "shuffled": session.snapshot.shuffled,
        })
        if status == "failed":
            self._transfers.pop(token, None)

    async def open_device(self, entity_id: str, context) -> None:
        device = self.device(entity_id)
        await execute_wake(self.hass, device, context)
        await launch_powerplay(self.hass, device, context)

    async def handoff(self, raw: Mapping[str, Any], target: str, context) -> dict[str, Any]:
        device = self.device(target)
        if not device.supports_protocol_v1:
            raise HandoffError(
                "Destination has not been confirmed to support PowerLink v1 "
                "(requires PowerPlay 1.3.0+ client-side receiver)"
            )
        # Plan and verify the actual current item BEFORE waking/moving focus.
        prepared = await self.prepare(raw, target)
        await execute_wake(self.hass, device, context)
        await launch_powerplay(self.hass, device, context, uri=prepared["deep_link"])
        # The receiver must explicitly acknowledge playback via the relay
        # before any source-stopping action may be taken by HA automations.
        return prepared


    async def capture_handoff(self, source: str, destination: str, context) -> dict[str, Any]:
        """Ask PowerPlay for its *current* queue, then prepare and hand off.

        A nonce authorizes only this one upload, is source-bound, and expires
        after a short timeout. The old device is never stopped here.
        """
        source_device = self.device(source)
        target_device = self.device(destination)
        if source == destination:
            raise HandoffError("A transfer requires two different devices")
        if not source_device.supports_protocol_v1 or not target_device.supports_protocol_v1:
            raise HandoffError("Both source and destination require verified PowerPlay 1.3.0+ clients")
        base = source_device.endpoint_base_url or self.options.get("base_url")
        if not base:
            raise HandoffError("Configure a reachable HA endpoint for the source device")
        nonce = secrets.token_urlsafe(32)
        loop = asyncio.get_running_loop()
        pending = PendingCapture(source, loop.create_future(), time.monotonic())
        self._captures[nonce] = pending
        uri = "powerplay://powerlink-capture?" + urlencode({
            "v": "1", "base": base, "token": nonce, "source": source
        })
        try:
            # Source must already have an active player. Do not run a wake
            # sequence that might toggle power while it is playing.
            await launch_powerplay(self.hass, source_device, context, uri=uri)
            try:
                snapshot = await asyncio.wait_for(pending.future, timeout=15)
            except TimeoutError as err:
                raise HandoffError("Source PowerPlay did not return its current queue within 15 seconds") from err
            return await self.handoff(snapshot, destination, context)
        finally:
            self._captures.pop(nonce, None)

    async def accept_capture(self, nonce: str, snapshot: Mapping[str, Any]) -> None:
        """Complete one HA-initiated authenticated-by-capability capture."""
        pending = self._captures.get(nonce)
        if pending is None or time.monotonic() - pending.created_at > 20:
            raise HandoffError("Capture token expired or already used")
        if not isinstance(snapshot, Mapping) or snapshot.get("source_device") != pending.source:
            raise HandoffError("Snapshot source does not match pending capture")
        if snapshot.get("schema") != 1:
            raise HandoffError("Unsupported queue capture schema")
        if pending.future.done():
            raise HandoffError("Capture already completed")
        self._captures.pop(nonce, None)
        pending.future.set_result(dict(snapshot))
