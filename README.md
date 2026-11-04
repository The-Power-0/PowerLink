# PowerLink

**The standalone HACS integration for [PowerPlay](https://github.com/The-Power-0/PowerPlay)** — a Home Assistant controller for device discovery, configurable power-on routines, and Plex playback handoff.

> **Development preview.** This is *not* yet a tested production HACS integration. Current stable PowerPlay 1.2.4 does **not** implement the PowerLink v1 receiver, so complete queue transfers cannot work until the PowerPlay 1.3.0 app-side implementation is tested. Handoff launching is disabled for unverified clients by default. The `open_device` action, discovery and policy planner can be tested independently.

## What PowerLink does

- **No LAN scans or competing ADB sessions.** It discovers existing Home Assistant Android Debug Bridge `media_player` entities and lets an administrator register them. It delegates ADB to Home Assistant's existing Android Debug Bridge integration.
- **Device policies, not room names.** Configurable `max_height`, preference for highest quality, launch method (HA Android TV ADB or custom HA script), and per-device wake-up sequences.
- **Exact queue continuity.** PowerPlay will export an ordered snapshot of the current **Plex** queue, its shuffled flag, cursor and exact playback offset. PowerLink retains every entry and duplicate in order. It does **not** reshuffle or rebuild a guessed queue.
- **1080p and 4K matching.** For every requested queue window, PowerLink selects a copy whose verified resolution fits the destination. 4K-capable targets can choose a true 2160p copy; Full HD targets never receive a known UHD-only copy. Media identity is checked by Plex GUID or other high-confidence identifiers, not title alone.
- **Server-side, lazy matching.** Queue windows of up to 32 entries are resolved **only on demand** with a maximum of 3 Plex queries in flight and a bounded 10-minute catalog cache. No idle traffic, full-library indexing or large payloads sent to the Fire Stick.
- **Different homes/networks.** The Home Assistant controller's API URL can be a reachable private LAN URL or a TLS-protected external/VPN URL. Device wake/app launch can use the local ADB integration or a custom Home Assistant script/bridge on a different network. This is *routed connectivity*, not automatic internet tunneling.
- **Safe handoff.** The current source is never stopped merely because the destination woke or accepted an Android intent. A receiver must acknowledge successful playback. A short-lived 256-bit secret grants access only to one handoff manifest, not HA/Plex credentials.

## Installation (development preview)

In HACS open **Custom repositories**, add `https://github.com/The-Power-0/PowerLink`, choose **Integration**, then install PowerLink. Restart Home Assistant and add **PowerLink** under **Settings → Devices & Services**. For manual installation, copy `custom_components/powerlink` into your Home Assistant `/config/custom_components/`. Home Assistant Container works without Supervisor or an add-on store.

In setup, provide a **Home Assistant base URL reachable by the Fire Sticks** (e.g. `http://10.0.0.10:8123` on your LAN or `https://home.example.net` across networks). Optional Plex URL/token enable Home Assistant-side targeted equivalent lookup, which is the preferred way to map your distinct `Movies [4K]` and `TV Shows [4K]` libraries.

### Example device policies (your three Fire Sticks)

This is an example only. The integration **does not** hard-code names, addresses, model-to-room mappings or an assumed local subnet.

| Existing Home Assistant entity | Preferred max | Launch | Quality policy |
| --- | ---: | --- | --- |
| `media_player.living_room_fire_tv_stick_4k_plus` | 2160p | ADB or custom script | Prefer highest available 4K copy |
| `media_player.bedroom_fire_tv_stick_lite` | 1080p | ADB or custom script | Match HD version |
| `media_player.game_room_fire_tv_stick_lite` | 1080p | ADB or custom script | Match HD version |

### Manage devices from Home Assistant's UI

Once PowerLink is installed and configured, go to **Settings → Devices & Services → PowerLink → Configure**. No Developer Tools actions or YAML are required for normal device management.

1. **Find Fire TV / Android TV devices** lists matching media player entities already managed by Home Assistant, including their current status. Choose one, then save its settings. PowerLink never opens a second ADB connection or scans your subnet.
2. **Add a device manually** selects another existing media player or remote entity. Use a custom launch script for remote-network bridges or integrations without Android TV ADB support.
3. **Edit a registered device** changes its name, supported resolution (select 1080p for HD-only devices; 2160p for 4K-capable devices), preferred quality, app-launch method, HA endpoint override and verified PowerLink v1 receiver status.
4. **Power-on routine** can be None, a safe state-aware \`media_player.turn_on\`, or a chosen Home Assistant script (called directly and awaited; a failed script blocks launch). Existing complex routines are preserved when editing normal settings. **Edit advanced wake routine** opens a JSON/object editor for conditional multi-step sequences.
5. **Remove a device** requires confirmation and unregisters it from PowerLink without deleting the Home Assistant entity.

The discovery list contains entities **already integrated with Home Assistant**; it does not automatically discover a completely unpaired Fire Stick on the LAN. First add such a stick through Home Assistant's Android TV / ADB integration. The v1 receiver checkbox defaults to off because a handoff cannot be considered verified until PowerPlay 1.3.0+ is installed and end-to-end tested.

The existing Developer Tools actions remain available for advanced automation and programmatic registration. For example, \`powerlink.register_device\` can configure custom wake steps:

~~~yaml
action: powerlink.register_device
data:
  entity_id: media_player.living_room_fire_tv_stick_4k_plus
  name: Living Room
  max_height: 2160
  prefer_highest_quality: true
  launch_method: adb
  # Initially false. Set only after PowerPlay 1.3.0 receiver is verified.
  supports_protocol_v1: false
  wake_steps:
    - kind: service
      domain: media_player
      service: turn_on
      entity_id: media_player.living_room_fire_tv_stick_4k_plus
      when_not_states: [playing, paused, idle]
      continue_on_error: true
    - kind: wait_state
      entity_id: media_player.living_room_fire_tv_stick_4k_plus
      states: [idle, playing, paused, on]
      timeout: 20
~~~

Each device's wake steps are optional. For displays requiring CEC, smart plugs, input switching, delays, or other routines, use additional HA service/wait/delay steps. Every step has a bounded timeout and only executes when launching. **Never toggle a smart plug that powers a running device unexpectedly**; configure your own sequence. For cooldown-sensitive TVs, select **Run a Home Assistant script** and choose a wake-only script that verifies safety guards and power confirmation before returning. PowerLink calls that script directly and waits up to 600 seconds; errors abort launch. Do not choose a shuffle/playback script as a wake script or PowerPlay may start too early. In the advanced editor, use direct `script.<script_id>` calls rather than `script.turn_on`.

For a remote/second network, choose `launch_method: script` and `launch_script: script.my_fire_tv_handoff`. The script receives `powerlink_uri` as a variable and can forward it through an existing secure VPN, broker, remote HA instance or service. That transport must be configured by the user; it isn't bundled.

The existing 1.2.4 app can be opened with:

~~~yaml
action: powerlink.open_device
data:
  entity_id: media_player.living_room_fire_tv_stick_4k_plus
~~~

### One-tap on-demand handoff

After registering **both** devices and verifying the PowerPlay 1.3.0 receiver on each, Home Assistant can initiate a complete handoff without requiring a local UI action on the source:

~~~yaml
action: powerlink.capture_handoff
data:
  source: media_player.living_room_fire_tv_stick_4k_plus
  destination: media_player.bedroom_fire_tv_stick_lite
response_variable: powerlink_transfer
~~~

Home Assistant creates a cryptographically random **one-time capture token**, sends a `powerplay://powerlink-capture?v=1&base=...&token=...&source=...` intent to the *already playing* source Fire Stick, and awaits a fresh snapshot for at most 15 seconds. PowerPlay's active player captures its current Plex queue ID, selected Plex queue-item ID, and current playhead **only when requested**, then POSTs that tiny payload to `/api/powerlink/v1/capture/<token>`. The source is not paused, and the capture token cannot authorize any other queue or device.

Once Home Assistant validates the current item has a compatible Plex file at the destination, it executes that destination's configured power-on sequence and launch adapter. The new app fetches 32-entry windows and explicitly selects the destination's Plex media **ID and index** (1080p for HD-only devices; verified highest quality for 4K devices). It ACKs `started` only after the player reports the correct item, first frame, and seek position.

This action is gated behind both devices' `supports_protocol_v1` flag. It must not be enabled until the destination receiver, source capture, routed network path and end-to-end playback are tested. An HA or Plex outage, missing HD/4K counterpart or failed launch keeps the original device playing.

## Handoff protocol for the PowerPlay receiver

### Preferred lightweight export: Plex queue reference

For Plex server-managed queues (shuffled **or** sequential), the PowerPlay 1.3.0 sender should **not** fetch thousands of episodes on the Fire Stick. Instead it sends just its existing Plex queue ID, selected `playQueueItemID`, queue version, current offset and profile:

~~~json
{
  "schema": 1,
  "source_device": "media_player.game_room_fire_tv_stick_lite",
  "profile_id": "plex-home-main",
  "context_key": "shuffle",
  "position_ms": 253400,
  "shuffled": true,
  "queue_source": {
    "type": "plex_play_queue",
    "server_id": "plex-main",
    "play_queue_id": 701,
    "selected_item_id": 3002,
    "version": 5
  }
}
~~~

Home Assistant uses the configured Plex token **for the same Plex account/profile** to GET `/playQueues/701` and reconstruct the entire ordered queue using **multiple bounded, overlapping windows** if Plex caps the response. Every window must have the same queue ID, queue version, total count and shuffle state. Duplicate episodes retain distinct `playQueueItemID` positions. A missing, contradictory or truncated queue, or a wrong Plex identity, aborts without waking or stopping the source. The frozen manifest is served to the destination in batches of at most 32 items.

This means the Fire Stick's handoff CPU/network cost is approximately constant rather than proportional to the queue length. The full `items` snapshot described below is retained as a fallback for local/non-Plex queues, but the preferred 1.3.0 Plex path is the lightweight queue reference.

A PowerPlay 1.3.0 source will provide a **complete ordered queue snapshot** to Home Assistant. Example:

~~~json
{
  "schema": 1,
  "source_device": "media_player.living_room_fire_tv_stick_4k_plus",
  "profile_id": "plex-home-main",
  "context_key": "my-favorites-shuffle",
  "current_index": 1,
  "position_ms": 253400,
  "shuffled": true,
  "items": [
    {
      "kind": "episode", "title": "Episode 3", "guid": "plex://episode/example3",
      "server_id": "plex-main", "rating_key": "456",
      "show_guid": "plex://show/example", "show_title": "My Series",
      "season": 1, "episode": 3,
      "copies": [
        {"server_id": "plex-main", "rating_key": "456",
         "guid": "plex://episode/example3", "height": 2160,
         "bitrate_kbps": 25000, "library_title": "TV Shows [4K]"}
      ]
    },
    {
      "kind": "episode", "title": "Episode 8", "guid": "plex://episode/example8",
      "server_id": "plex-main", "rating_key": "789",
      "show_guid": "plex://show/example", "show_title": "My Series",
      "season": 1, "episode": 8,
      "copies": [
        {"server_id": "plex-main", "rating_key": "789",
         "guid": "plex://episode/example8", "height": 1080,
         "bitrate_kbps": 8000, "library_title": "TV Shows"}
      ]
    }
  ]
}
~~~

The controller accepts the snapshot through `powerlink.prepare_handoff` (returns an opaque handoff link without waking a device) or, **once a verified 1.3.0 receiver exists**, `powerlink.handoff` (wakes the destination and launches the prepared link).

The receiver will handle a `powerplay://powerlink?v=1&base=<url>&token=<secret>` intent and GET:

`<base>/api/powerlink/v1/session/<token>?start=<index>&count=<1..32>`

The response preserves the original `current_index`, `position_ms`, `shuffled`, `total` and indexed `items`. The receiver must build an **external, server-provided local queue**, not ask Plex to reshuffle or recreate a new queue. It should fetch more indexed windows as playback advances. Unavailable items are retained with `status: unavailable` and must show a recoverable playback error rather than silently jumping to another episode.

After opening and seeking successfully, the receiver POSTs `{"status":"started"}` to `.../session/<token>/ack`. If it cannot play, POST `{"status":"failed"}`. HA emits `powerlink_handoff_acknowledged` with the source, destination and outcome. The source should stop **only after a verified playback-start acknowledgment**, via a separately configured automation/policy.

### Security and performance boundaries

- Snapshot capped at 2 MB and 5,000 entries; on-demand windows capped at 32 and Plex lookup concurrency capped at 3.
- Transfer capability tokens are cryptographically random and held in RAM for up to 12 hours; they expire on Home Assistant restart. Anyone who has the token can fetch that **one** queue. Use HTTPS for public/cross-network access, keep tokens out of dashboards, logs and publicly exposed scripts, and do not share raw action responses.
- Plex tokens are used only by Home Assistant's Plex resolver, sent in request headers, and never included in deep links, app messages or transfer responses.
- The `handoff` action refuses to launch on clients not marked `supports_protocol_v1`. Do not mark a device ready until a v1.3.0+ receiver has actually been deployed and tested.
- The optional Plex resolver is a conservative *targeted* `/library/all` lookup; it will never match unrelated media on title alone. Server-specific Plex naming/metadata differences may require later refinements and additional fixture tests.

## Development and release status

This repository now contains the independent **PowerLink Home Assistant integration**. The initial sources and tests were migrated from [PowerPlay staging PR #39](https://github.com/The-Power-0/PowerPlay/pull/39). Development and issues for this integration belong here; the Flutter APK is maintained separately in [PowerPlay](https://github.com/The-Power-0/PowerPlay).

**Version 0.1.0 is a development preview, not a production-ready, verified handoff release.** The current stable PowerPlay 1.2.4 does not implement the required PowerLink v1 receiver; app-side work is underway on the [PowerPlay 1.3.0 development branch](https://github.com/The-Power-0/PowerPlay/tree/feature/powerlink-v1.3.0-app). Keep device `supports_protocol_v1` disabled until complete source-to-destination tests are successful.

For local checks:

```bash
python -m compileall -q custom_components/powerlink tests
python -m unittest discover -s tests -p 'test_*.py' -v
```

GitHub Actions runs the same checks on changes to this repository. Once app compatibility and real Fire TV transfers are validated, create an independent PowerLink release tag for HACS.

## License

PowerLink is licensed under the [GNU General Public License v3](LICENSE). The PowerPlay app is a separate project with independent releases and its own license and upstream credits.
