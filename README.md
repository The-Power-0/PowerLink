<div align="center">

<img src="https://raw.githubusercontent.com/The-Power-0/PowerPlay/main/docs/artwork/powerplay-icon.webp" alt="PowerPlay companion artwork" width="116" />

# PowerLink

**The Home Assistant companion for PowerPlay. Connect your Fire TVs, move playback between rooms, and power smarter show shuffling.**

[![Latest PowerLink](https://img.shields.io/badge/HACS-Latest%20Release-7C3AED?style=for-the-badge&logo=github&logoColor=white)](https://github.com/The-Power-0/PowerLink/releases/latest)
[![PowerShuffle](https://img.shields.io/badge/Feature-PowerShuffle-6D28D9?style=for-the-badge)](#powershuffle)
[![Home Assistant](https://img.shields.io/badge/Platform-Home%20Assistant-30303B?style=for-the-badge&logo=homeassistant&logoColor=white)](#installation)
[![GPLv3](https://img.shields.io/badge/License-GPLv3-5930A5?style=for-the-badge)](LICENSE)

*An independent, open-source Home Assistant integration built for [PowerPlay](https://github.com/The-Power-0/PowerPlay).*

[Install](#installation) · [PowerShuffle](#powershuffle) · [Dashboard API](#powerlink-040-dashboard-backend) · [Device setup](#registering-your-fire-tvs) · [Pairing](#pair-powerplay-with-home-assistant) · [Handoffs](#powerlink-handoffs) · [Automation](#home-assistant-actions)

</div>

---

## One integration. Two ways to watch.

PowerLink sits between **PowerPlay**, **Home Assistant** and your Plex library. PowerPlay stays focused on the player and Fire TV remote; Home Assistant handles devices, wake routines, queue transfer and PowerShuffle history.

| | |
| --- | --- |
| **PowerShuffle** | Smart, single-show episode rotation with independent history, skip options and cycle tracking. |
| **Room-to-room handoff** | Continue the same Plex episode, playback position and ordered queue on another paired Fire Stick. |
| **Device discovery** | Select Fire TV / Android TV entities that already exist in Home Assistant — without subnet scans or a second ADB connection. |
| **Wake routines** | Use a simple device wake command, a custom Home Assistant script or a bounded multistep routine. |
| **1080p / 4K awareness** | Choose a verified matching copy for the destination's configured maximum resolution. |
| **Home Assistant UI** | Register and edit devices, generate pairing codes, see tracked PowerShuffle shows and change settings without editing YAML. |

<div align="center">

**POWERPLAY ON YOUR FIRE TV** ↔ **POWERLINK IN HOME ASSISTANT** ↔ **YOUR PLEX LIBRARY**

</div>

### Release compatibility

| Component | Version |
| --- | --- |
| **PowerLink HACS integration** | **[0.4.0](https://github.com/The-Power-0/PowerLink/releases/tag/v0.4.0)** (dashboard backend) |
| **PowerPlay Fire TV app** | **[1.4.0](https://github.com/The-Power-0/PowerPlay/releases/tag/powerplay-v1.4.0)** or later for PowerShuffle |
| **Home Assistant** | 2025.12.0 or later |
| **Deployment** | Home Assistant Container is supported — no Supervisor add-on store required |

The ordinary PowerLink handoff features also work with earlier compatible PowerPlay builds. **PowerShuffle specifically requires the 0.3.0 / 1.4.0 combination or later.**

## PowerShuffle

**Watch a show in a new order without immediately repeating episodes you've already completed in PowerShuffle.**

PowerShuffle is an opt-in action on a Plex show's detail page in PowerPlay. Each Plex profile gets its own rotation, separate from Plex watched/unwatched flags and PowerPlay's existing standard Shuffle feature.

### Exactly two skip options

| Skip choice | Effect |
| --- | --- |
| **Skip for 5 days** | Excludes that episode for **120 elapsed hours**, including across cycle changes. |
| **Skip until next cycle** | Removes the episode from this rotation; it becomes eligible when a new rotation starts. |

An unfinished episode prompts for a skip choice only when the user explicitly skips or changes episodes. Normal exits, pauses, shutdowns and transfers do **not** trigger a skip.

### Cycles, history and playback

- **Completed** means the configured proportion of unique viewed content has been reached (default **90%**). Jumping near the end does not qualify; verified automatic intro/credits skips can count.
- **Incomplete** episodes stay in the pool. When PowerShuffle chooses one again, it starts at 0:00; a live PowerLink handoff preserves the exact ongoing attempt and playhead.
- **Full rotation:** when all episodes complete, record a completed cycle and start the next.
- **Deferred rotation:** if all remaining episodes are **Skip until next cycle**, record a *partial* rotation, not a fully completed one, and automatically begin the next.
- **Recent-completion protection:** avoid episodes completed within the last seven days by default, but relax that soft protection when necessary to keep playback moving. A five-day skip remains a **hard** exclusion.
- **New episodes:** a newly discovered Plex episode joins the current rotation without wiping history.

The rotation order is retained for up to 12 hours of inactivity by default. After that, a fresh order is generated from eligible episodes. Home Assistant keeps the **complete logical rotation** while PowerPlay receives a smaller rolling queue window (default **10 episodes**) to reduce Fire Stick work.

### Manage PowerShuffle from Home Assistant

Open **Settings → Devices & Services → PowerLink → Configure**:

| Menu | What it controls |
| --- | --- |
| **Manage PowerShuffle shows** | View tracked shows and profiles, current completion, five-day skips and next-cycle deferrals. Reset a cycle, clear cooldowns or inspect the read-only episode history. |
| **PowerShuffle global settings** | Change the completion threshold, queue inactivity, repeat-protection period, upcoming window and whether Season 0 specials are included. |

Each tracked show/profile gets a `sensor.powershuffle_*` summary entity. Progress is the **percentage actually completed**, not the percentage skipped or deferred. Home Assistant stores the detailed episode ledger separately from sensor attributes.

Example Home Assistant action to retrieve all tracked show summaries:

~~~yaml
action: powerlink.powershuffle_list
response_variable: powershuffle_status
~~~

The `powerlink.powershuffle_manage` action is also available for advanced administration. Ordinary users can manage everything from the integration's configuration screens.

## Installation

### 1. Install PowerLink using HACS

1. In Home Assistant, open **HACS → Custom repositories**.
2. Add `https://github.com/The-Power-0/PowerLink` and select **Integration** as the repository type.
3. Install the [latest stable release](https://github.com/The-Power-0/PowerLink/releases/latest), then **restart Home Assistant**.
4. Go to **Settings → Devices & Services → Add integration → PowerLink**.

For a manual installation, copy the `custom_components/powerlink` folder into `/config/custom_components/powerlink` and restart Home Assistant. Installation does not require a separate database, MQTT broker or additional ADB server.

### 2. Configure connectivity

Provide the **Home Assistant base URL** that each Fire Stick can reach, such as a private LAN URL or an HTTPS URL reachable over an existing routed network. PowerLink can optionally use configured Plex server credentials for **targeted matching** of HD and 4K copies.

A reachable URL is required for the PowerPlay app to contact Home Assistant. PowerLink **does not create VPNs, public tunnels or firewall rules** for you.

## Registering your Fire TVs

Open **Settings → Devices & Services → PowerLink → Configure**.

- **Find Fire TV / Android TV devices:** choose from existing Home Assistant media player entities.
- **Add a device manually:** select another supported `media_player` or `remote` entity, using a custom launch script where needed.
- **Edit a registered device:** configure its display-resolution limit, quality preference, launch method and power-on routine.
- **Edit advanced wake routine:** customize ordered service calls, waits and delays with safety limits.
- **Remove a device:** unregister it from PowerLink without deleting its Home Assistant entity.

**Important:** PowerLink discovers devices **already integrated with Home Assistant**. For a new Fire Stick, configure it in Home Assistant's Android TV / ADB integration first. PowerLink delegates commands to that existing integration and does not open a second ADB session.

### Example resolution policies

| Device capability | Maximum height | Typical result |
| --- | ---: | --- |
| 4K Fire TV Stick | 2160p | Prefer a verified matching UHD copy when one exists. |
| Fire TV Stick Lite / 1080p device | 1080p | Select a matching HD copy; avoid known UHD-only media. |

These policies are per device, **not hard-coded by room name**. An HDMI output temporarily set to 1080p does not change a configured 4K-capable device's resolution policy. If no trustworthy matching media can be found, the transfer should report an error rather than silently choose the wrong episode.

### Power-on routines

Choose the approach that matches how the TV actually turns on:

| Option | Best for |
| --- | --- |
| **None** | An already-awake Fire Stick or a separately controlled display. |
| **Turn on if necessary** | A straightforward Home Assistant entity with reliable state reporting. |
| **Run a Home Assistant script** | CEC, input switching, protected smart plugs, receiver startup or other custom safeguards. |
| **Advanced wake steps** | A sequence of service calls, waits and delays managed in the PowerLink UI. |

Scripts are called directly and awaited; failed required wake steps prevent the launch. **Avoid blind power toggles** that might switch off an already-running television or receiver.

## Pair PowerPlay with Home Assistant

Pair each Fire Stick independently so it can request destinations and handoffs directly from its player controls.

1. Register the device in PowerLink and mark its **PowerLink v1 receiver as verified** only after a successful playback test.
2. Under **PowerLink → Configure**, choose **Pair PowerPlay player** and select the target Fire Stick.
3. Enter the **nine-digit, one-time code** in **PowerPlay → Settings → PowerLink**, along with the reachable Home Assistant URL. The code expires after three minutes.
4. Select **Pair**, then refresh the device list. Repeat for any other Fire Sticks.

Each device receives its own scoped credential stored using Android Keystore encryption. Pairing does not require placing a full Home Assistant administrator token on a Fire Stick.

## PowerLink handoffs

### Transfer from the player

During Plex playback, open the controls, select the **PowerLink** icon with the D-pad and choose another paired room. Home Assistant validates the queue and media copy, executes the destination's wake routine, launches playback and waits for confirmation.

**The source remains playing until the destination confirms it started the requested media.** If preparation, quality matching or playback fails, the transfer is rejected rather than treating a successful app launch as successful playback.

PowerLink maintains the original episode order, shuffle state and playback offset for both shuffled and sequential Plex queues. It can also carry the **active PowerShuffle rotation**, including cycle identity and in-progress completion tracking.

Starting the *same active show's PowerShuffle* on another paired Fire Stick can also initiate a handoff automatically.

### Transfer from Home Assistant

To capture the exact current Plex queue and move it without opening the picker:

~~~yaml
action: powerlink.capture_handoff
data:
  source: media_player.living_room_fire_tv_stick_4k_plus
  destination: media_player.bedroom_fire_tv_stick_lite
response_variable: powerlink_transfer
~~~

Replace both example entities with devices registered in your installation. The integration asks the **actively playing source** for a fresh position and queue snapshot before preparing the transfer.

For additional controls, see the [available Home Assistant actions](custom_components/powerlink/services.yaml).

### Start PowerShuffle directly from Home Assistant (0.3.1+)

Once a show has been started at least once in PowerPlay, PowerLink retains its
episode inventory and cycle history. You can then launch or continue that
tracked rotation directly on a paired Fire Stick **without rebuilding
PowerPlay**:

```yaml
action: powerlink.powershuffle_launch
data:
  show_title: King of the Hill
  destination: media_player.game_room_fire_tv_stick_lite
```

A Lovelace dashboard can reuse existing input selectors without creating
additional helpers or scripts:

```yaml
action: powerlink.powershuffle_launch
data:
  show_entity: input_select.plezy_shuffle_show
  destination_entity: input_select.plezy_shuffle_fire_tv
```

If the show is playing on another device, the action performs a normal
verified PowerLink handoff. Otherwise it starts the next eligible episode
with the original queue and cycle tracking. Multiple profiles with the same
show require an explicit `profile_id` to avoid writing history to the wrong
profile. No tracked show or no currently eligible episode returns a clear
error instead of silently falling back to Plex's regular shuffle.

A compact **PowerLink PowerShuffle Library** sensor exposes up to 25 tracked
show summaries in its `shows` attribute for simple dashboard cards. Since
0.4.0, the authenticated WebSocket API can retrieve *all* tracked shows in
pages without putting huge episode lists in Home Assistant entity states.
Existing per-show sensors and the read-only
`powerlink.powershuffle_list` action remain supported.

## PowerLink 0.4.0 dashboard backend

This release is **Home Assistant only**. **PowerPlay 1.4.0 requires no rebuild**.
PowerLink remains the single authoritative record for one Plex profile, show,
and Plex server. The device keeps only the rolling playback queue; the iPhone
dashboard reads Home Assistant directly, not your Fire Sticks.

### Dashboard data and live updates

The authenticated Home Assistant WebSocket API now supports:

| WebSocket message `type` | What it returns |
| --- | --- |
| `powerlink/dashboard` | Tracked shows, cycle progress, recent session status, available rooms and non-sensitive settings. Optional `offset` and `limit` (up to 100) paginate the catalogue. |
| `powerlink/subscribe_dashboard` | Pushes dashboard changes from existing PowerShuffle events and HA entity state changes, and expires stale statuses on HA without device polling. |
| `powerlink/show_details` | On-demand episode/cooldown history; requires `show_id`, supports `offset` and `limit` (up to 100 episodes). |
| `powerlink/show_artwork` | Optional on-demand Plex poster for an exact tracked `show_id`. Only available when Plex credentials are already configured in PowerLink. Images are served as bounded base64 data through the authenticated HA socket. |

The `show_id` returned by `powerlink/dashboard` identifies the exact master
rotation by Plex profile, show GUID and server. It avoids accidentally combining
profiles with identical show names. It is not a Plex rating key.

A dashboard custom card can use a normal Home Assistant connection:

```javascript
const snapshot = await hass.connection.sendMessagePromise({
  type: "powerlink/dashboard", offset: 0, limit: 100
});
const show = snapshot.shows.find((entry) => entry.title === "King of the Hill");
const history = await hass.connection.sendMessagePromise({
  type: "powerlink/show_details", show_id: show.id, offset: 0, limit: 50
});
// The normal HA frontend subscribeMessage API can subscribe to
// type: "powerlink/subscribe_dashboard" for push updates.
```

For explicit launch actions, prefer the stable `show_id` instead of a
title/selector pair:

```yaml
action: powerlink.powershuffle_launch
data:
  show_id: "PASTE_THE_ID_RETURNED_BY_POWERLINK_DASHBOARD"
  destination: media_player.game_room_fire_tv_stick_lite
```

This still runs the destination's **existing registered PowerLink wake
routine**, including any protected TV power script, and uses the existing
first-frame-verified handoff receiver. When another Fire Stick owns a live
session, the action transfers that same shared queue rather than forking a
new rotation.

**Session-status semantics:** `recently_playing` means PowerLink received a
recent progress/receiver event from the Fire Stick, not a promise that
playback is advancing at this exact moment. `stale` indicates that event
confirmation aged out; `starting` indicates an unconfirmed HA/PowerPlay
launch; `transferring` indicates a PowerLink handoff has been prepared but
has not yet received a first-frame acknowledgment.
`idle` and `stopped` are not an invented paused-state signal. To avoid
additional device traffic, 0.4.0 does not add a background pause probe.

**Artwork:** PowerLink makes targeted Plex show metadata requests only for
posters actually requested by the dashboard, checks immutable Plex show
GUIDs, follows no redirects, transcodes to a small image, and caches results
in RAM for up to six hours (15 minutes for misses). It does not scan your
library, return Plex tokens, or load poster data on Fire TV.

### Performance policy

- **Zero new Fire TV timers, polling, sensors or RPCs.** PowerPlay's current
  12-second progress events and on-action handoffs remain unchanged.
- Only Home Assistant performs dashboard subscriptions and expiry checks;
  those checks exist while a browser is subscribed.
- Large episode ledgers remain in the HA master store; episode history and
  posters load on demand instead of expanding sensor attributes.
- This version exposes the backend for a future custom frontend card. It does
  **not** automatically replace your existing Lovelace dashboard YAML.

## Home Assistant actions

| Action | Purpose |
| --- | --- |
| `powerlink.discover_devices` | Show supported entities already connected to Home Assistant. |
| `powerlink.register_device` | Register or update a device's policy. |
| `powerlink.open_device` | Execute its wake routine and open PowerPlay without changing playback. |
| `powerlink.capture_handoff` | Capture current playback from a source and transfer to a destination. |
| `powerlink.prepare_handoff` / `powerlink.handoff` | Advanced ordered-queue preparation and launch. |
| `powerlink.powershuffle_list` | Return tracked PowerShuffle show summaries. |
| `powerlink.powershuffle_manage` | Reset a stopped rotation or clear five-day cooldowns. |
| `powerlink.powershuffle_launch` | Start or hand off an existing tracked rotation using an exact `show_id` or legacy show/room selectors. |

Normal device and PowerShuffle setup is available through the UI. The actions above are mainly for dashboards, automation and advanced workflows.

## How it stays lightweight and secure

- **No LAN scan, extra ADB connection or background Fire TV polling.** The existing Home Assistant integrations handle device communication.
- **Queue identity is verified.** Episodes are matched using Plex identifiers and season/episode identity, not a title-only guess. HD/4K copies are chosen per destination.
- **Work stays on Home Assistant.** The complete PowerShuffle rotation, completion history and settings live in Home Assistant's persistent storage. PowerPlay carries only the active playback window and scoped events.
- **Bounded playback transfers.** Handoff manifests are limited in size, sent in windows (up to 32 entries), and authorized by short-lived per-transfer tokens.
- **No raw Plex or Home Assistant admin credentials in handoff URLs.** Use HTTPS for externally reachable endpoints; keep pairing and handoff links private.
- **Failures stay recoverable.** Home Assistant does not count an episode as completed simply because it was queued, and the existing source is not deliberately paused before the destination acknowledges playback.

PowerLink is a controller, **not a Plex server, a remote-access tunnel, or an ADB replacement**.

## Releases and development

| Version | Milestone |
| --- | --- |
| **[0.4.0](https://github.com/The-Power-0/PowerLink/releases/tag/v0.4.0)** | **Dashboard backend:** authenticated WebSocket master catalogue, session status, on-demand episode history and cached Plex posters. No extra Fire TV work. |
| [0.3.1](https://github.com/The-Power-0/PowerLink/releases/tag/v0.3.1) | **Home Assistant launch:** start an existing PowerShuffle rotation directly from a dashboard, with safe receiver handoffs and a library summary sensor. |
| [0.3.0](https://github.com/The-Power-0/PowerLink/releases/tag/v0.3.0) | **PowerShuffle**: persistent per-show/profile history, two skip choices, cycles, sensors, configurable global settings and handoff ownership. |
| [0.2.1](https://github.com/The-Power-0/PowerLink/releases/tag/v0.2.1) | Destination wake and screensaver-dismiss reliability. |
| [0.2.0](https://github.com/The-Power-0/PowerLink/releases/tag/v0.2.0) | Player pairing, device UI and Fire TV-initiated transfers. |
| **0.1.x · early development** | Initial standalone Home Assistant controller and remote handoff support, before the published 0.2.x releases. |

PowerLink is maintained separately from the [PowerPlay Fire TV app](https://github.com/The-Power-0/PowerPlay). Use [Latest releases](https://github.com/The-Power-0/PowerLink/releases/latest) for the current HACS integration.

Local validation:

~~~bash
python -m compileall -q custom_components/powerlink tests
python -m unittest discover -s tests -p 'test_*.py' -v
~~~

GitHub Actions validates changes before release. Automated tests cannot prove the behavior of every physical Fire Stick, HDMI chain, custom wake routine or Plex library mapping.

## License

PowerLink is distributed under the [GNU General Public License v3](LICENSE). PowerPlay is a separate project with independent releases, attribution and source archives.

<div align="center">

**PowerLink + PowerPlay**

*Start in one room. Continue in another. Make every shuffle count.*

[PowerPlay](https://github.com/The-Power-0/PowerPlay) · [Latest PowerLink](https://github.com/The-Power-0/PowerLink/releases/latest) · [Report an issue](https://github.com/The-Power-0/PowerLink/issues)

</div>
