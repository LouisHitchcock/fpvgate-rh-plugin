# FPVGate RotorHazard Plugin v2.1.4

Use an FPVGate as RotorHazard timing hardware: up to eight nodes with an ESP32-C5 receiver, or one with RX5808.

## Changes since v2.1.3

- **Marshal page works.** The gate now declares full RSSI marshaling, so RotorHazard's Marshal page shows each node's RSSI trace and can recalculate laps from new Enter/Exit levels, instead of a graph made up from the lap peaks.
- **No more pass floods.** A dip in the signal as a quad flies through could reach RotorHazard as several passes in a fraction of a second, and enough of them could overwhelm RotorHazard during a race. Passes for a node less than 0.5 s apart are now treated as one. Firmware 1.9.0-Multi-Alpha-3 and later fixes this at the gate as well, and sends RSSI 20 times a second with every peak kept, for a cleaner Marshal trace.
- **RSSI keeps flowing when RotorHazard is busy.** Passes used to be handed to RotorHazard by the same task that reads the gate's RSSI, so when RotorHazard fell behind, the RSSI waited too and the Marshal trace slipped against the laps. Passes now wait in their own queue. If more than 64 back up, new ones are dropped, a warning is logged and shown in the FPVGate panel. Their RSSI is still recorded, so the Marshal page can recalculate those laps.
- Firmware that timestamps its RSSI messages (`"us"` in `rhRssi`) has its RSSI timed by the gate rather than on arrival.

## Changes since v2.1.2

- **Uses only RotorHazard's public plugin API again**, so it passes RHFest and can be listed in Community Plugins. v2.1.2's workaround for RotorHazard 4.5.1-dev reached into RotorHazard internals and has been removed.
- **Says when RotorHazard drops the gate's nodes.** Some RotorHazard 4.5.1-dev builds discard timing hardware added by plugins, so the gate connected but RotorHazard showed "No receiver nodes found". The plugin now detects this at startup and explains it in an alert, the FPVGate panel and the log. The bug has been reported to RotorHazard; until it's fixed, use RotorHazard 4.5.0.

## Changes since v2.0.0
- **Smooth RSSI and prompt laps.** The plugin read the gate's event stream in 512-byte blocks, so RSSI and passes arrived in bursts about a second apart: Sensor Tuning graphs looked stepped and laps showed up late. Events now arrive as the gate sends them, ten times a second. Lap times were never affected; passes carry the gate's own timestamp.
- **Full-resolution RSSI option.** Shows an ESP32-C5 gate's RSSI as 0-1023 instead of 0-255. EnterAt/ExitAt switch to the same scale and snap to steps of 4. Needs gate firmware later than 1.9.0-Multi-Alpha-2; off by default.
- **No more "System settings modified" banner after every start.** Saving the gate's node count at startup raised RotorHazard's restart banner each time.
- **Startup log line** saying how many nodes the gate added, or that no address is set.
- Levels sent to the gate are clamped to its 0-255 range.

## Requirements

Requires RotorHazard 4.4.0 or later (tested on 4.4.0 and 4.5.0-beta.1). The hardware interface requires FPVGate v1.9.0-Multi-Alpha-2 or later; that firmware remains an alpha. Physical eight-pilot race testing is outstanding, and Waveshare S3-ETH firmware is untested on hardware.

Community Plugins catalogue inclusion is pending maintainer approval. For now, download the source ZIP below and copy `custom_plugins/fpvgate/` into `~/rh-data/plugins/fpvgate/`, replacing an existing copy when upgrading, then restart RotorHazard. Set the gate's IP address in Settings > FPVGate and restart again.

See the [setup guide](https://github.com/LouisHitchcock/fpvgate-rh-plugin#readme) for configuration and older-firmware instructions.
