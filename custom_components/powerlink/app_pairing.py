"""One-time Home Assistant pairing and scoped Fire TV credentials.

Only a Home Assistant operator can generate a code through the PowerLink
config-entry options flow. The code binds a *specific registered source*,
expires in three minutes, and can only be redeemed once. Device credentials
are random 256-bit values; HA persists only their SHA-256 digests.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass

from .handoff import HandoffError

PAIRING_TTL = 180
MAX_FAILURES_PER_CLIENT = 8
FAILURE_WINDOW = 180
CODE = re.compile(r"^[0-9]{9}$")
TOKEN = re.compile(r"^[A-Za-z0-9_-]{40,128}$")


class PairingDenied(HandoffError):
    """Invalid, expired, or already redeemed pairing code."""


class PairingRateLimited(PairingDenied):
    """Temporary rate limit for an untrusted client."""


@dataclass(frozen=True)
class PendingPairing:
    entity_id: str
    deadline: float


class AppPairings:
    """No background timers, no plaintext token persistence."""

    def __init__(self, saved: dict[str, str] | None = None) -> None:
        self.token_digests: dict[str, str] = dict(saved or {})
        self._codes: dict[str, PendingPairing] = {}
        self._failures: dict[str, tuple[float, int]] = {}

    def issue(self, source: str) -> str:
        """Called from HA's authenticated device-manager OptionsFlow only."""
        now = time.monotonic()
        self._codes = {k: v for k, v in self._codes.items() if v.deadline > now}
        # One pending code per source; generating a new one invalidates old.
        self._codes = {k: v for k, v in self._codes.items() if v.entity_id != source}
        code = f"{secrets.randbelow(1_000_000_000):09d}"
        while code in self._codes:
            code = f"{secrets.randbelow(1_000_000_000):09d}"
        self._codes[code] = PendingPairing(source, now + PAIRING_TTL)
        return code

    def redeem(self, code: object, client: str) -> tuple[str, str]:
        now = time.monotonic()
        client = (client or "unknown")[:128]
        started, failed = self._failures.get(client, (now, 0))
        if now - started > FAILURE_WINDOW:
            started, failed = now, 0
        if failed >= MAX_FAILURES_PER_CLIENT:
            raise PairingRateLimited("Too many pairing attempts; retry later")
        pairing = self._codes.get(code) if isinstance(code, str) and CODE.fullmatch(code) else None
        if pairing is None or pairing.deadline <= now:
            self._failures[client] = (started, failed + 1)
            raise PairingDenied("Invalid or expired pairing code")
        # Single use; never disclose this one-time secret in logs.
        del self._codes[code]
        token = secrets.token_urlsafe(32)
        self.token_digests[pairing.entity_id] = hashlib.sha256(token.encode("ascii")).hexdigest()
        self._failures.pop(client, None)
        return pairing.entity_id, token

    def authenticate(self, source: str, token: str) -> bool:
        if not isinstance(source, str) or not isinstance(token, str) or not TOKEN.fullmatch(token):
            return False
        expected = self.token_digests.get(source)
        if not isinstance(expected, str) or len(expected) != 64:
            return False
        actual = hashlib.sha256(token.encode("ascii")).hexdigest()
        return hmac.compare_digest(actual, expected)

    def revoke(self, source: str) -> None:
        self.token_digests.pop(source, None)
        self._codes = {code: value for code, value in self._codes.items() if value.entity_id != source}
