"""Pure ADB command builders for app launch and safe PowerPlay intents."""

from __future__ import annotations

import shlex

from .const import APP_ID


def launcher_command() -> str:
    """Launch the Android package; media_player.select_source is not reliable."""
    return (
        "monkey -p " + shlex.quote(APP_ID)
        + " -c android.intent.category.LAUNCHER 1"
    )


def deep_link_command(uri: str) -> str:
    """Hand off an already-validated deep link without shell interpretation."""
    return (
        "am start -a android.intent.action.VIEW -d "
        + shlex.quote(uri) + " " + shlex.quote(APP_ID)
    )
