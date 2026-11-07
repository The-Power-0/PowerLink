"""Short-lived PowerLink transfer endpoint.

Possession of the 256-bit random token authorizes only one queue manifest,
not Home Assistant itself. Deploy HTTPS for cross-network clients. Never
expose an HA API key or Plex access token through this endpoint.
"""
from __future__ import annotations

import json

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import Context

from .app_pairing import PairingDenied, PairingRateLimited

from .const import DOMAIN
from .handoff import HandoffError, MAX_WINDOW_ITEMS


class PowerLinkSessionView(HomeAssistantView):
    url = "/api/powerlink/v1/session/{token}"
    name = "api:powerlink:session"
    requires_auth = False

    async def get(self, request: web.Request, token: str) -> web.Response:
        controller = request.app["hass"].data.get(DOMAIN)
        if controller is None:
            raise web.HTTPNotFound()
        try:
            start = int(request.query.get("start", 0))
            count = int(request.query.get("count", 16))
            if count < 1 or count > MAX_WINDOW_ITEMS:
                raise ValueError("Window too large")
            result = await controller.window(token, start, count)
        except (HandoffError, ValueError):
            raise web.HTTPNotFound() from None
        return web.json_response(result, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


class PowerLinkAcknowledgmentView(HomeAssistantView):
    url = "/api/powerlink/v1/session/{token}/ack"
    name = "api:powerlink:ack"
    requires_auth = False

    async def post(self, request: web.Request, token: str) -> web.Response:
        controller = request.app["hass"].data.get(DOMAIN)
        if controller is None:
            raise web.HTTPNotFound()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("Expected object")
            await controller.acknowledge(token, data.get("status"))
        except (HandoffError, ValueError):
            raise web.HTTPNotFound() from None
        return web.json_response({"acknowledged": True},
                                 headers={"Cache-Control": "no-store"})



class PowerLinkCaptureView(HomeAssistantView):
    """Accept one source snapshot using only a bound, short-lived capability."""

    url = "/api/powerlink/v1/capture/{token}"
    name = "api:powerlink:capture"
    requires_auth = False

    async def post(self, request: web.Request, token: str) -> web.Response:
        controller = request.app["hass"].data.get(DOMAIN)
        if controller is None:
            raise web.HTTPNotFound()
        try:
            # Full local queues can be up to 2 MB; server-backed Plex queues
            # normally upload only a few hundred bytes.
            limit = 2_000_000
            if request.content_length is not None and request.content_length > limit:
                raise web.HTTPRequestEntityTooLarge(
                    max_size=limit, actual_size=request.content_length
                )
            payload = await request.content.read(limit + 1)
            if len(payload) > limit:
                raise web.HTTPRequestEntityTooLarge(
                    max_size=limit, actual_size=len(payload)
                )
            decoded = json.loads(payload)
            await controller.accept_capture(token, decoded)
        except HandoffError as err:
            raise web.HTTPNotFound() from err
        except (ValueError, TypeError) as err:
            raise web.HTTPBadRequest(text="Invalid PowerLink queue snapshot") from err
        return web.json_response(
            {"accepted": True}, headers={"Cache-Control": "no-store"}
        )


# App-initiated requests use per-device credentials, not Home Assistant
# administrator access tokens. Home Assistant's normal Bearer middleware is
# intentionally not involved: X-PowerLink-Token is scoped to one paired device.
def _app_identity(request: web.Request, controller) -> str:
    source = request.headers.get("X-PowerLink-Device", "")
    secret = request.headers.get("X-PowerLink-Token", "")
    if not controller.authorized_app(source, secret):
        raise web.HTTPUnauthorized(text="Pair PowerPlay with this device in PowerLink settings")
    return source


async def _read_bounded_json(request: web.Request, limit: int) -> dict:
    if request.content_length is not None and request.content_length > limit:
        raise web.HTTPRequestEntityTooLarge(
            max_size=limit, actual_size=request.content_length
        )
    # StreamReader.read(n) can return an incomplete JSON chunk. Consume
    # until EOF while retaining the same hard size cap used by old endpoints.
    data = bytearray()
    async for chunk in request.content.iter_chunked(65536):
        data.extend(chunk)
        if len(data) > limit:
            raise web.HTTPRequestEntityTooLarge(
                max_size=limit, actual_size=len(data)
            )
    try:
        parsed = json.loads(data)
    except (ValueError, UnicodeError) as err:
        raise web.HTTPBadRequest(text="Expected JSON object") from err
    if not isinstance(parsed, dict):
        raise web.HTTPBadRequest(text="Expected JSON object")
    return parsed


class PowerLinkAppPairView(HomeAssistantView):
    """Redeem a short-lived code generated by the HA device manager."""

    url = "/api/powerlink/v1/app/pair"
    name = "api:powerlink:app:pair"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        controller = request.app["hass"].data.get(DOMAIN)
        if controller is None:
            raise web.HTTPServiceUnavailable()
        payload = await _read_bounded_json(request, 1024)
        try:
            result = await controller.redeem_app_pairing(
                payload.get("code"), request.remote or "unknown"
            )
        except PairingRateLimited as err:
            raise web.HTTPTooManyRequests(
                text="Too many pairing attempts; try again later"
            ) from err
        except (HandoffError, PairingDenied):
            # Do not disclose which codes/devices exist to anonymous callers.
            raise web.HTTPForbidden(text="Invalid or expired pairing code") from None
        return web.json_response(result, headers={"Cache-Control": "no-store"})


class PowerLinkAppDevicesView(HomeAssistantView):
    """List only registered and verified destinations for a paired Fire TV."""

    url = "/api/powerlink/v1/app/devices"
    name = "api:powerlink:app:devices"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        controller = request.app["hass"].data.get(DOMAIN)
        if controller is None:
            raise web.HTTPServiceUnavailable()
        source = _app_identity(request, controller)
        return web.json_response(
            controller.discover_app_destinations(source),
            headers={"Cache-Control": "no-store"},
        )


class PowerLinkAppHandoffView(HomeAssistantView):
    """Accept the *paired source's* fresh on-device queue snapshot."""

    url = "/api/powerlink/v1/app/handoff"
    name = "api:powerlink:app:handoff"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        controller = request.app["hass"].data.get(DOMAIN)
        if controller is None:
            raise web.HTTPServiceUnavailable()
        source = _app_identity(request, controller)
        body = await _read_bounded_json(request, 2_000_000)
        target, snapshot = body.get("destination"), body.get("snapshot")
        if not isinstance(target, str) or not isinstance(snapshot, dict):
            raise web.HTTPBadRequest(text="Destination and snapshot are required")
        try:
            result = await controller.app_handoff(
                source, target, snapshot, Context()
            )
        except HandoffError as err:
            # HandoffError contains safe public descriptions, not credentials.
            raise web.HTTPConflict(text=str(err)) from err
        return web.json_response(result, headers={"Cache-Control": "no-store"})


class PowerShuffleStartView(HomeAssistantView):
    """Start a tracked show with the paired device's Plex profile."""

    url = "/api/powerlink/v1/app/powershuffle/start"
    name = "api:powerlink:app:powershuffle:start"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        controller = request.app["hass"].data.get(DOMAIN)
        if controller is None:
            raise web.HTTPServiceUnavailable()
        source = _app_identity(request, controller)
        payload = await _read_bounded_json(request, 2_000_000)
        try:
            result = await controller.powershuffle.start(source, payload)
            if result.get("action") == "handoff":
                # Reuse the verified PowerLink destination wake, queue
                # matching and first-frame acknowledgement. The old player
                # remains active until receiver confirmation.
                await controller.handoff(result["snapshot"], source, Context())
                return web.json_response(
                    {"action": "handoff", "accepted": True},
                    headers={"Cache-Control": "no-store"},
                )
        except HandoffError as err:
            raise web.HTTPConflict(text=str(err)) from err
        return web.json_response(result, headers={"Cache-Control": "no-store"})


class PowerShuffleEventView(HomeAssistantView):
    """Idempotent events from the device that owns the playing session."""

    url = "/api/powerlink/v1/app/powershuffle/event"
    name = "api:powerlink:app:powershuffle:event"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        controller = request.app["hass"].data.get(DOMAIN)
        if controller is None:
            raise web.HTTPServiceUnavailable()
        source = _app_identity(request, controller)
        payload = await _read_bounded_json(request, 4096)
        try:
            result = await controller.powershuffle.event(source, payload)
        except HandoffError as err:
            raise web.HTTPConflict(text=str(err)) from err
        return web.json_response(result, headers={"Cache-Control": "no-store"})


class PowerShuffleAttachView(HomeAssistantView):
    """Attach an acknowledged PowerLink handoff to its PowerShuffle history."""

    url = "/api/powerlink/v1/app/powershuffle/attach"
    name = "api:powerlink:app:powershuffle:attach"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        controller = request.app["hass"].data.get(DOMAIN)
        if controller is None:
            raise web.HTTPServiceUnavailable()
        source = _app_identity(request, controller)
        payload = await _read_bounded_json(request, 1024)
        try:
            result = await controller.powershuffle.attach(source, payload.get("session_id"))
        except HandoffError as err:
            raise web.HTTPConflict(text=str(err)) from err
        return web.json_response(result, headers={"Cache-Control": "no-store"})
