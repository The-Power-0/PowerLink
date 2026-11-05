# Pairing PowerPlay 1.3.7 for player-initiated handoff

**Development preview** — requires PowerLink 0.2.0 and PowerPlay 1.3.7 app changes. This feature is not included in the published 1.3.6 APK.

## Pair each Fire Stick

1. In Home Assistant, open **Settings → Devices & services → PowerLink → Configure**. Register the Fire Sticks you want to use. Configure their resolution limits, wake routine, and launch method. Enable **Verified PowerLink v1** only on devices that successfully play existing handoffs.
2. In PowerLink **Configure**, choose **Pair PowerPlay player**, select the particular Fire Stick, and note the displayed nine-digit one-time code. It expires in three minutes.
3. On that same device open **PowerPlay → Settings → PowerLink**, enter a Home Assistant URL the Fire Stick can reach, and type the code. Choose **Pair this Fire Stick**.
4. Select **Refresh PowerLink destinations**. Only other devices previously registered and verified in Home Assistant appear. Repeat pairing on your other Fire Sticks.
5. Start a Plex movie or TV queue. Open its player controls, select the **PowerLink cast icon**, choose a destination, and allow HA to launch it.

## Expected behavior

- Home Assistant handles wake routines, 4K versus 1080p version selection, and transfer of the exact queue and playhead.
- The source continues playing until the destination acknowledges starting the correct media. Only then does HA send an explicit pause command to the source. An accepted request does not by itself prove playback started.
- Moving an imported queue again preserves the full shuffle/order by referring to Home Assistant's existing immutable transfer manifest; the Fire Stick does not upload the 32-item partial queue window. This works while the HA transfer remains active and acknowledged.
- No LAN broadcast, background Fire Stick polling, admin HA token, or persistent synchronization socket is required.

## Troubleshooting and security

- **No PowerLink section**: this requires a test build containing the 1.3.7 changes.
- **Code rejected**: issue a new code for that exact registered source device; enter it within three minutes. Multiple wrong guesses are rate-limited.
- **Missing destinations**: configure and verify the other HA devices. The paired source is intentionally excluded.
- **Missing queue**: launch playback from a full Plex shuffle or sequential queue first. If a previously imported PowerLink manifest has expired, start a fresh queue.
- **Source failed to pause**: listen for the Home Assistant event `powerlink_handoff_acknowledged` and inspect its `source_paused` and `source_pause_error` fields. A successful pause command is not an independent measurement of video state.
- The per-device token is saved encrypted via Android Keystore and stored only as a SHA-256 digest in HA. A new pairing rotates it; removing the registered HA device revokes it. Keep local HTTP on trusted networks, or use HTTPS for routed/public access.

Existing HA-driven `powerlink.capture_handoff` actions remain available and unchanged.
