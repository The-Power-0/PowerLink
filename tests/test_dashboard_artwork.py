"""Poster proxy tests: authenticated HA-only concept, no HTTP or Fire TV calls."""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

ha = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
ha.__path__ = []
helpers = sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
helpers.__path__ = []
aiohttp_module = sys.modules.setdefault(
    "homeassistant.helpers.aiohttp_client", types.ModuleType("homeassistant.helpers.aiohttp_client")
)
aiohttp_module.async_get_clientsession = lambda hass: None

file = Path(__file__).resolve().parents[1] / "custom_components" / "powerlink" / "dashboard_artwork.py"
spec = importlib.util.spec_from_file_location("powerlink_artwork_stage", file)
art = importlib.util.module_from_spec(spec)
spec.loader.exec_module(art)


class PosterParsingTests(unittest.TestCase):
    def test_only_exact_guid_returns_bounded_internal_thumb_path(self):
        meta = {"MediaContainer": {"Metadata": [
            {"type": "show", "guid": "plex://show/wrong", "thumb": "/library/metadata/1/thumb/12"},
            {"type": "show", "guid": "plex://show/right", "thumb": "/library/metadata/22/thumb/345"},
        ]}}
        self.assertEqual(art.poster_path(meta, "plex://show/right"), "/library/metadata/22/thumb/345")
        self.assertIsNone(art.poster_path(meta, "plex://show/missing"))

    def test_rejects_token_urls_redirect_paths_and_external_poster_links(self):
        for path in (
            "https://attacker.example/poster.jpg", "//attacker.example/secret",
            "/library/metadata/22/thumb/1?X-Plex-Token=bad",
            "/library/metadata/22/thumb/../../../admin",
            "/library/metadata/22/art/123",
            "/library/metadata/22/thumb/012/456",
        ):
            meta = {"MediaContainer": {"Metadata": [
                {"type": "show", "guid": "plex://show/a", "thumb": path}
            ]}}
            self.assertIsNone(art.poster_path(meta, "plex://show/a"))


class PosterCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_lazy_cache_and_server_scope(self):
        plex = types.SimpleNamespace(server_id="server-1", hass=None, token="secret", base_url="http://plex")
        provider = art.PowerLinkPosters(plex)
        provider._fetch = AsyncMock(return_value={"available": True, "content_type": "image/jpeg", "base64": "abc"})
        result = await provider.poster("key", title="Show", guid="plex://show/a", server_id="server-1")
        self.assertTrue(result["available"])
        await provider.poster("key", title="Show", guid="plex://show/a", server_id="server-1")
        provider._fetch.assert_awaited_once()
        none = await provider.poster("other-key", title="Show", guid="plex://show/a", server_id="server-2")
        self.assertFalse(none["available"])
        provider._fetch.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
