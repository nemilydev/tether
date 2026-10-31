# Tether

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Validate](https://github.com/nemilydev/tether/actions/workflows/validate.yml/badge.svg)](https://github.com/nemilydev/tether/actions/workflows/validate.yml)
[![Version](https://img.shields.io/badge/version-1.0.2-blue)](https://github.com/nemilydev/tether/releases/tag/v1.0.2)
[![Downloads](https://img.shields.io/github/downloads/nemilydev/tether/total?style=for-the-badge)](https://github.com/nemilydev/tether/releases)
[![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/nemilydev/tether?tab=MIT-1-ov-file)

A Home Assistant integration for controlling Lovense toys, either directly over your local network or through the Lovense Standard API.

Tether creates entities from each toy's reported capabilities: actuator lights and sliders, a preset selector, a stop button, and connectivity and battery diagnostics. Toys you connect in Lovense Remote later appear in Home Assistant automatically, without reloading the integration.

## Requirements

- Home Assistant **2025.2.0** or newer.
- The **Lovense Remote** app (mobile or PC) with your toys connected.
- For a local connection: Home Assistant and Lovense Remote on a network that allows device-to-device HTTP.
- For the Standard API: a Lovense developer account and a callback URL that Lovense Remote can reach (see below).

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=nemilydev&repository=tether&category=integration)

1. Select the button above, or in HACS open the menu ⋮ → **Custom repositories** and add `https://github.com/nemilydev/tether` with type **Integration**.
2. Download **Tether**.
3. Restart Home Assistant.

### Manual

1. Download the latest release from the [releases page](https://github.com/nemilydev/tether/releases).
2. Copy `custom_components/tether` into the `custom_components` folder of your Home Assistant configuration directory, so that `custom_components/tether/manifest.json` exists.
3. Restart Home Assistant.

### Add the integration

[![Open your Home Assistant instance and start setting up Tether.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=tether)

Or go to **Settings → Devices & services → Add integration** and search for **Tether**. You can add several Tether entries, for example one per phone or Lovense account.

## Connection types

### Direct local connection (recommended)

Commands go straight from Home Assistant to Lovense Remote on your network; no Lovense server is involved.

1. In Lovense Remote, open the **Toy** tab (mobile) or settings (PC), open **Game Mode** and turn on **Enable LAN**.
2. Note the IP address and port it shows. The HTTP port is usually `20010`.
3. In Tether, choose **Direct local connection** and enter that address, for example `192.168.1.50`, and port.
4. Leave **Use HTTPS** off unless you use Lovense Remote's HTTPS port (usually `30010`). Certificate verification can be turned off for its self-signed certificate.

Tether sends a test request before creating the entry, so a wrong address is reported immediately. Give the phone or PC a fixed IP address (DHCP reservation) so the address does not change. If it does change, use **Reconfigure** on the integration entry.

### Lovense Standard API

Use this when Home Assistant cannot reach Lovense Remote directly, or to use Lovense's pairing flow.

1. In the [Lovense Developer Dashboard](https://www.lovense.com/user/developer/info), copy your **developer token**.
2. Set the dashboard's **Callback URL** to your Home Assistant address followed by `/api/tether/callback`, for example:

   ```
   https://ha.example.com/api/tether/callback
   ```

   Lovense Remote must be able to reach this URL. Use a secure remote-access method such as Home Assistant Cloud (Nabu Casa), a Cloudflare Tunnel or Tailscale. **Do not expose Home Assistant through an unprotected port forward.**
3. In Tether, choose **Lovense Standard API** and enter the developer token, a user ID of your choice (for example `home-assistant`), a display name, and the same callback URL.
4. Tether shows the pairing QR code. Scan it with the Lovense Remote mobile app, or enter the shown code in Lovense Remote for PC, then select **Submit**. QR codes expire after 4 hours.

Tether waits for Lovense Remote's pairing callback so it can confirm the link and remember the app's address across restarts. If your callback URL is not reachable yet, tick **Continue without waiting for the callback**; Tether then uses the Lovense cloud API until the first callback arrives.

#### Callback security

The callback endpoint does not use Home Assistant authentication, because Lovense Remote cannot send Home Assistant credentials. Instead, every callback must contain the user ID and a random secret token that Tether generated for that entry during pairing; anything else is rejected with HTTP 401. Callbacks must be JSON objects smaller than 1 MiB, and the reported address must be a `*.lovense.club` host with valid ports.

## Entities

Each toy becomes a device linked to its Tether entry, with entities for the functions it reports:

| Platform | Entity | Created when | Range |
|---|---|---|---|
| Light | Vibration | Toy reports Vibrate | Brightness 0–100%, effects: pulse, wave, fireworks, earthquake |
| Light | Pump | Toy has a pump (Max 2) | 0–3 |
| Light | Stroke | Toy strokes and stroke controls include lights | Thrusting speed 0–20 |
| Number | Vibrate, Vibrate 1–3, Rotate, Thrusting, Fingering, Suction, Oscillate | Toy reports the function | 0–20 |
| Number | Pump level, Depth | Toy reports the function | 0–3 |
| Number | Stroke range | Toy strokes and stroke controls include numbers | 0–100 |
| Number | Position | Solace models, or toys reporting stroke/position functions | 0–100 |
| Select | Preset | Always | pulse, wave, fireworks, earthquake |
| Button | Stop | Always | Stops every actuator on the toy |
| Binary sensor | Connectivity (diagnostic) | Always | Whether the toy is connected to Lovense Remote |
| Sensor | Battery (diagnostic) | Always | 0–100% |

Control entities become unavailable while their toy is disconnected.

## Options

Open **Settings → Devices & services → Tether → Configure** to change:

| Option | Default | Description |
|---|---|---|
| Polling interval | 30 s | How often toy state is read (5–3600 s). |
| Default command duration | 0 s | Duration for commands that do not set one; `0` runs until stopped. |
| Verify TLS certificates | Off for local, on for Standard API | Verify HTTPS certificates when talking to Lovense Remote. |
| Stroke controls | Lights | Expose stroking toys as lights, numbers, or both. |

## Services

All services target Tether entities or devices, so one call can command several toys.

### `tether.send_function`

Send one or more actuator actions.

| Field | Required | Description |
|---|---|---|
| `action` | Yes | Comma-separated actions, for example `Vibrate:10,Rotate:5`. Levels are 0–20 (Pump and Depth 0–3). `Stroke:20-80` sets a stroke range and must be combined with `Thrusting`. `Stop` stops everything. |
| `timeSec` | No | Duration in seconds (0–3600). Defaults to the option above. |
| `loopRunningSec` | No | Seconds to run in each loop cycle. |
| `loopPauseSec` | No | Seconds to pause in each loop cycle. |
| `stopPrevious` | No | Stop actuators not mentioned in this action (default `false`). |

### `tether.play_preset`

| Field | Required | Description |
|---|---|---|
| `name` | Yes | `pulse`, `wave`, `fireworks` or `earthquake`. |
| `timeSec` | No | Duration in seconds (0–3600). |

### `tether.play_pattern`

| Field | Required | Description |
|---|---|---|
| `pattern` | Yes | Semicolon-separated vibration strengths 0–20, up to 50 steps, for example `4;8;12;16;20`. |
| `interval` | No | Milliseconds per step, 100–5000 (default `1000`). |
| `timeSec` | No | Duration in seconds (0–3600). |
| `loopRunningSec` / `loopPauseSec` | No | Loop timing in seconds. |

### `tether.ramp`

Climb to a peak vibration, hold it, then decay to zero.

| Field | Required | Description |
|---|---|---|
| `peak` | No | Peak vibration 1–20 (default `20`). |
| `climb` / `hold` / `decay` | No | Seconds for each phase, 0–300 (default `30` each). |

### `tether.stop_all`

Immediately stop every actuator on the targeted toys.

### Example

```yaml
action: tether.ramp
target:
  entity_id: light.my_lush_vibration
data:
  peak: 16
  climb: 60
  hold: 120
  decay: 60
```

## Supported devices

Any toy that Lovense Remote lists, including Lush, Edge, Nora, Domi, Max (including Max 2's pump) and Solace / Solace Pro. Entities are based on the functions each toy reports rather than a fixed model list.

## Troubleshooting

- **"Failed to connect" during local setup.** Check that Game Mode → Enable LAN is on, that the IP and port match what Lovense Remote shows, and that the phone is awake and on the same network as Home Assistant. Some guest Wi-Fi networks block device-to-device traffic.
- **Integration shows "Retrying setup".** Home Assistant could not reach Lovense Remote at startup. Tether retries automatically; open Lovense Remote to speed this up.
- **Pairing never completes.** Open `https://<your-callback-host>/api/tether/callback` from your phone's mobile data. A `405 Method Not Allowed` response means the URL is reachable. Make sure the dashboard callback URL matches the one entered in Tether exactly.
- **Logs show "Rejected Lovense callback with unknown credentials".** The callback came from a QR code of a removed or reconfigured entry. Pair again with the current QR code.
- **Toys are unavailable.** The toy is disconnected from Lovense Remote, or Lovense Remote is closed. The Connectivity sensor shows the toy's state.
- **Reporting an issue.** Download diagnostics from the integration's menu and attach them to an [issue](https://github.com/nemilydev/tether/issues). Diagnostics redact tokens, user IDs, callback URLs, addresses and toy identifiers.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements_test.txt
pytest
```

The [Validate workflow](.github/workflows/validate.yml) runs hassfest, HACS validation and the test suite on every push and pull request.

## Origin

Tether began as a fork of [Jacid23's lovense_api](https://github.com/Jacid23/lovense_api) — thank you for the foundation. It has since become its own integration with a new domain, a rewritten coordinator, local LAN support, pump support, pattern and ramp services, and capability-based entities. `lovense_api` config entries do not migrate; remove the old entry and add Tether.

## License

MIT — see [LICENSE](LICENSE). Contains code originally from [Jacid23/lovense_api](https://github.com/Jacid23/lovense_api), also MIT.
