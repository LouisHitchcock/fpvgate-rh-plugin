# FPVGate RotorHazard Plugin v2.1.2

Use an FPVGate as RotorHazard timing hardware: up to eight nodes with an ESP32-C5 receiver, or one with RX5808.

## Changes since v2.0.0

- **Works on RotorHazard 4.5.1-dev.** RotorHazard's development branch clears every timing interface before setting up its own, after plugins have added theirs, so the gate connected but RotorHazard showed "No receiver nodes found". The plugin now puts plugin interfaces back after that reset. It does nothing on released RotorHazard versions, and will be removed once RotorHazard keeps plugin interfaces itself.
- **Smooth RSSI and prompt laps.** The plugin read the gate's event stream in 512-byte blocks, so RSSI and passes arrived in bursts about a second apart: Sensor Tuning graphs looked stepped and laps showed up late. Events now arrive as the gate sends them, ten times a second. Lap times were never affected; passes carry the gate's own timestamp.
- **Full-resolution RSSI option.** Shows an ESP32-C5 gate's RSSI as 0-1023 instead of 0-255. EnterAt/ExitAt switch to the same scale and snap to steps of 4. Needs gate firmware later than 1.9.0-Multi-Alpha-2; off by default.
- **No more "System settings modified" banner after every start.** Saving the gate's node count at startup raised RotorHazard's restart banner each time.
- **Startup log line** saying how many nodes the gate added, or that no address is set.
- Levels sent to the gate are clamped to its 0-255 range.

## Requirements

Requires RotorHazard 4.4.0 or later (tested on 4.4.0 and 4.5.0-beta.1). The hardware interface requires FPVGate v1.9.0-Multi-Alpha-2 or later; that firmware remains an alpha. Physical eight-pilot race testing is outstanding, and Waveshare S3-ETH firmware is untested on hardware.

Community Plugins catalogue inclusion is pending maintainer approval. For now, download the source ZIP below and copy `custom_plugins/fpvgate/` into `~/rh-data/plugins/fpvgate/`, replacing an existing copy when upgrading, then restart RotorHazard. Set the gate's IP address in Settings > FPVGate and restart again.

See the [setup guide](https://github.com/LouisHitchcock/fpvgate-rh-plugin#readme) for configuration and older-firmware instructions.
