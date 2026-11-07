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


def handoff_screen_wake_command() -> str:
    """Wake Fire TV and dismiss an existing screensaver before a destination launch.

    KEYCODE_WAKEUP (224) never toggles an already-awake Fire Stick back to
    sleep. KEYCODE_HOME (3) dismisses Fire TV's ambient/dream overlay without
    sending PLAY/PAUSE or SELECT into a possibly active player. The following
    PowerPlay deep-link launch brings the target app back to the foreground.
    No user input or other shell data is included in this command.
    """
    return "input keyevent 224 && input keyevent 3"
