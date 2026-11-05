"""No HA dependency: pairing codes, scope and persisted token digests."""
import importlib
import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "powerlink"
pkg = types.ModuleType("powerlink_stage")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("powerlink_stage", pkg)

pairing = importlib.import_module("powerlink_stage.app_pairing")

BEDROOM = "media_player.bedroom_fire_tv_stick_lite"
LIVING = "media_player.living_room_fire_tv_stick_4k_plus"


class AppPairingTests(unittest.TestCase):
    def setUp(self):
        self.registry = pairing.AppPairings()

    def test_code_is_nine_digits_expires_and_is_single_use(self):
        code = self.registry.issue(BEDROOM)
        self.assertRegex(code, r"^[0-9]{9}$")
        source, token = self.registry.redeem(code, "192.168.10.3")
        self.assertEqual(source, BEDROOM)
        self.assertGreaterEqual(len(token), 40)
        self.assertTrue(self.registry.authenticate(BEDROOM, token))
        self.assertFalse(self.registry.authenticate(LIVING, token))
        with self.assertRaises(pairing.PairingDenied):
            self.registry.redeem(code, "192.168.10.3")

    def test_plaintext_token_is_not_persisted(self):
        source, token = self.registry.redeem(self.registry.issue(BEDROOM), "10.0.0.4")
        serialized = repr(self.registry.token_digests)
        self.assertEqual(source, BEDROOM)
        self.assertNotIn(token, serialized)
        self.assertRegex(self.registry.token_digests[BEDROOM], r"^[a-f0-9]{64}$")
        reload = pairing.AppPairings(self.registry.token_digests)
        self.assertTrue(reload.authenticate(BEDROOM, token))

    def test_repair_rotates_and_revokes_old_token(self):
        _, first = self.registry.redeem(self.registry.issue(BEDROOM), "10.0.0.4")
        _, second = self.registry.redeem(self.registry.issue(BEDROOM), "10.0.0.4")
        self.assertNotEqual(first, second)
        self.assertFalse(self.registry.authenticate(BEDROOM, first))
        self.assertTrue(self.registry.authenticate(BEDROOM, second))
        self.registry.revoke(BEDROOM)
        self.assertFalse(self.registry.authenticate(BEDROOM, second))

    def test_reissue_invalidates_previous_code(self):
        first = self.registry.issue(BEDROOM)
        second = self.registry.issue(BEDROOM)
        # If random happens to collide with invalidated code, it is a new
        # code; mock time/random instead to guarantee distinct values.
        if first != second:
            with self.assertRaises(pairing.PairingDenied):
                self.registry.redeem(first, "10.0.0.4")

    def test_expired_code_fails(self):
        with patch.object(pairing.time, "monotonic", return_value=500):
            code = self.registry.issue(BEDROOM)
        with patch.object(pairing.time, "monotonic", return_value=681):
            with self.assertRaises(pairing.PairingDenied):
                self.registry.redeem(code, "10.0.0.4")

    def test_brute_force_limit(self):
        code = self.registry.issue(BEDROOM)
        for i in range(8):
            with self.assertRaises(pairing.PairingDenied):
                self.registry.redeem("111111111", "10.0.0.4")
        with self.assertRaises(pairing.PairingRateLimited):
            self.registry.redeem(code, "10.0.0.4")
        # The independent source is not locked by another caller's failure.
        source, _ = self.registry.redeem(code, "10.0.0.5")
        self.assertEqual(source, BEDROOM)

    def test_bad_token_and_wrong_source_fail(self):
        _, token = self.registry.redeem(self.registry.issue(BEDROOM), "10.0.0.4")
        self.assertFalse(self.registry.authenticate(BEDROOM, token + "x"))
        self.assertFalse(self.registry.authenticate(BEDROOM, "short"))
        self.assertFalse(self.registry.authenticate(LIVING, token))
        self.assertFalse(self.registry.authenticate(BEDROOM, ""))

    def test_revoke_also_invalidates_pending_code(self):
        code = self.registry.issue(BEDROOM)
        self.registry.revoke(BEDROOM)
        with self.assertRaises(pairing.PairingDenied):
            self.registry.redeem(code, "10.0.0.4")


if __name__ == "__main__":
    unittest.main()
