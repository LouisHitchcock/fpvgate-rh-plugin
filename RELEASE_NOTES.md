# FPVGate RotorHazard Plugin v2.0.0

Use an FPVGate as RotorHazard timing hardware: up to eight nodes with an ESP32-C5 receiver, or one with RX5808.

- Gate-timestamped passes and live RSSI for each node.
- Frequencies and enter/exit thresholds controlled from RotorHazard after adopting the gate's settings on first connection.
- Clock synchronization and automatic event-stream reconnection.
- Optional race mirroring for the gate's display, LEDs and speaker.
- Legacy single-pilot lap submission retained for older FPVGate firmware.

Requires RotorHazard 4.4.0 or later. The new hardware interface requires FPVGate v1.9.0-Multi-Alpha-2 or later; that firmware remains an alpha. Physical eight-pilot race testing is outstanding, and Waveshare S3-ETH firmware is untested on hardware.

Community Plugins catalogue inclusion is pending maintainer approval. For now, download the source ZIP below and copy `custom_plugins/fpvgate/` into `~/rh-data/plugins/fpvgate/`, replacing an existing copy when upgrading, then restart RotorHazard. Set the gate's IP address in Settings > FPVGate and restart again.

See the [setup guide](https://github.com/LouisHitchcock/fpvgate-rh-plugin#readme) for configuration and older-firmware instructions.
