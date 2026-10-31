## Tether

Control Lovense toys from Home Assistant, directly over your local network or through the Lovense Standard API.

- **Two connection types:** direct LAN (Lovense Remote Game Mode) or Standard API with in-dialog QR pairing.
- **Capability-based entities:** vibration, pump and stroke lights; per-function sliders; preset select; stop button; connectivity and battery diagnostics.
- **Services:** `send_function`, `play_preset`, `play_pattern`, `ramp` and `stop_all`, targeting any number of toys.
- **Automatic discovery:** toys connected later in Lovense Remote appear without a reload.
- **Privacy-aware:** local mode needs no Lovense server, and diagnostics redact tokens, addresses and toy IDs.

Requires Home Assistant 2025.2.0 or newer. See the [README](https://github.com/nemilydev/tether#readme) for setup, the entity reference and troubleshooting.
