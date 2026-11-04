"""Regressions for real Fire TV app launching and Plex response diagnostics.

No Home Assistant installation, Plex server or Fire Stick required.
"""
from __future__ import annotations

import importlib
import json
import shlex
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "powerlink"
pkg = types.ModuleType("powerlink_stage")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("powerlink_stage", pkg)

from importlib import import_module

commands = import_module("powerlink_stage.adb_commands")
queue_http = import_module("powerlink_stage.plex_queue_response")
HandoffError = import_module("powerlink_stage.handoff").HandoffError


class ADBLaunchTests(unittest.TestCase):
    def test_no_uri_launch_uses_verified_fire_tv_package_command(self):
        self.assertEqual(
            commands.launcher_command(),
            "monkey -p com.power.powerplay -c android.intent.category.LAUNCHER 1",
        )

    def test_handoff_intent_quotes_special_characters(self):
        uri = "powerplay://powerlink?v=1&token=A_B-123&base=https%3A%2F%2Fha.example.net"
        self.assertEqual(
            shlex.split(commands.deep_link_command(uri)),
            ["am", "start", "-a", "android.intent.action.VIEW",
             "-d", uri, "com.power.powerplay"],
        )

    def test_launcher_and_deep_link_stay_separate(self):
        self.assertNotIn("select_source", commands.launcher_command())
        self.assertTrue(commands.deep_link_command("powerplay://open").startswith("am start "))


class FakeBody:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, status=200, chunks=()):
        self.status = status
        self.content = FakeBody(chunks)


class PlexResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_entire_json_when_network_delivers_multiple_chunks(self):
        expected = {"MediaContainer": {
            "playQueueID": 12,
            "playQueueVersion": 3,
            "playQueueTotalCount": 1,
            "Metadata": [{"playQueueItemID": 101}],
        }}
        payload = json.dumps(expected).encode()
        response = FakeResponse(chunks=[payload[:13], payload[13:50], payload[50:]])
        self.assertEqual(await queue_http.decode_plex_queue_page(response), expected)

    async def test_unauthorized_token_explains_same_account_requirement(self):
        for status in (401, 403):
            with self.subTest(status=status), self.assertRaisesRegex(
                HandoffError, "token.*account/profile"
            ):
                await queue_http.decode_plex_queue_page(FakeResponse(status=status))

    async def test_missing_queue_explains_expiry_or_account_mismatch(self):
        with self.assertRaisesRegex(HandoffError, "HTTP 404"):
            await queue_http.decode_plex_queue_page(FakeResponse(status=404))

    async def test_redirect_does_not_follow_external_plex_login(self):
        with self.assertRaisesRegex(HandoffError, "HTTP 302"):
            await queue_http.decode_plex_queue_page(FakeResponse(status=302))

    async def test_invalid_or_incomplete_json_is_diagnosed(self):
        with self.assertRaisesRegex(HandoffError, "incomplete JSON"):
            await queue_http.decode_plex_queue_page(
                FakeResponse(chunks=[b'{"MediaContainer":'])
            )

    async def test_non_object_json_is_rejected(self):
        with self.assertRaisesRegex(HandoffError, "non-object"):
            await queue_http.decode_plex_queue_page(
                FakeResponse(chunks=[b'[]'])
            )

    async def test_large_queue_page_fails_closed(self):
        with self.assertRaisesRegex(HandoffError, "1.5 MB"):
            await queue_http.decode_plex_queue_page(
                FakeResponse(chunks=[b' ' * 800_000, b' ' * 800_001])
            )

    async def test_error_does_not_echo_a_potential_secret_body(self):
        secret = b'sensitive-plex-token-value'
        with self.assertRaises(HandoffError) as caught:
            await queue_http.decode_plex_queue_page(
                FakeResponse(status=401, chunks=[secret])
            )
        self.assertNotIn(secret.decode(), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
