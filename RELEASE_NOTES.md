# FPVGate RotorHazard Plugin v2.3.2-rc.1

24 October 2026. RotorHazard timing test prerelease, paired with gate firmware
[v1.9.0-Alpha-4-RH-1](https://github.com/LouisHitchcock/FPVGate/releases/tag/v1.9.0-Alpha-4-RH-1).

## Timing fixes

- RSSI history uses the gate's peak sample timestamps so Marshal recalculation
  and live passes refer to the same samples, at both RSSI resolutions.
- Clock corrections are adopted between crossings; a correction during a
  crossing no longer gives its history and live pass different clock offsets.
- Gate restarts still invalidate the old mapping and resynchronize immediately.

This is based on the development plugin, including the existing split-gate
and Sensor Tuning integration described below. Normal lap timing supports
RotorHazard 4.4.0 or later. Split timing/tuning features still need the relevant
RH APIs; they are not the focus of this test.

## Install

1. Back up the RotorHazard database and current plugin folder.
2. Stop RotorHazard. Extract `fpvgate-rh-plugin-v2.3.2-rc.1.zip` from this
   release and copy its `custom_plugins/fpvgate/` folder into the RH data
   `plugins/` directory, replacing the existing `fpvgate` folder. Keep only
   one installed copy. The archive also includes the MIT license.
3. Upgrade the S3 gate to `1.9.0-Alpha-4-RH-1` using that release's board-specific
   firmware/filesystem pair, then restart RotorHazard.
4. Confirm the FPVGate panel connects and the RH startup log reports
   `FPVGate plugin 2.3.2-rc.1`.

Keep the current frequencies, thresholds and RSSI resolution for the first
test. No separate C5 receiver update is needed for an already working Alpha-4
setup. Save the original RH database before recalculating laps. An exporter
should preserve live/recalculated crossing timestamps separately, relative
to race start, including Gate 1 and the node/pilot identifiers.

Camera validation and physical multi-transmitter tests are still outstanding.
Matching live and recalculated times is not proof of RF accuracy.

## Development changes included from v2.3.1

- Split gates appear directly in **Settings > Sensor Tuning** on RotorHazard builds with the proposed receiver tuning API (RHAPI 1.11). Each split node has RH's graph, pause, crossing indicator, EnterAt/ExitAt controls, threshold dragging, and a three-second RSSI capture using receiver samples.
- Split thresholds are saved independently to the selected gate; split receivers do not become extra pilot seats. The standalone tuning page, its HTTP APIs and its history buffer have been removed. Older RH builds can still use split timing, with thresholds configured on the gate itself.
- Gate reboot/reconnect invalidates old clock samples before timestamped passes resume. Frequency caches retain confirmed settings when a hardware write fails, and pending frequencies are retried on reconnection.
- Native tuning routes use RH's administrator authentication. Fractional and boolean thresholds are rejected. Split overload warnings no longer suggest recovery through Marshal.

# FPVGate RotorHazard Plugin v2.3.0

## Changes since v2.2.0

- **Split-gate live tuning graphs.** Open the graph from Settings > FPVGate to see each split receiver's recent RSSI, crossing state and Enter/Exit levels. Adjust and save each node's thresholds directly to that split gate.
- Split gate tuning is available only to authenticated RotorHazard users, and the graph keeps split receivers out of the pilot seat list.

# FPVGate RotorHazard Plugin v2.2.0

Use an FPVGate as RotorHazard timing hardware: up to eight nodes with an ESP32-C5 receiver, or one with RX5808.

## Changes since v2.1.4

- **Split gates.** More FPVGates part way round the course give each pilot split times. Each split gate becomes a RotorHazard split timer, so RotorHazard records, shows, announces and saves its splits exactly as for its own secondary timers, with speeds if you give the distances. Add them in **Settings > FPVGate**, a row each with **Add Split Gate**, in course order. Each is tuned to the finish gate's frequencies and follows RotorHazard's changes; set its Enter/Exit levels on the gate itself. See [Split times](https://github.com/LouisHitchcock/fpvgate-rh-plugin#split-times).
- Split gates need RotorHazard with split timers for plugins (RHAPI 1.10). On older RotorHazard the FPVGate panel says so and everything else works as before.
- Race start, stop and clear go to the split gates as well, when **Run races on the gate too** is on.

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

Requires RotorHazard 4.4.0 or later (tested on 4.4.0 and 4.5.0-beta.1); split gates need RotorHazard with RHAPI 1.10. The hardware interface requires FPVGate v1.9.0-Multi-Alpha-2 or later, and v1.9.0-Multi-Alpha-3 is recommended; that firmware remains an alpha. Physical eight-pilot race testing is outstanding.

Community Plugins catalogue inclusion is pending maintainer approval. For now, download the source ZIP below and copy `custom_plugins/fpvgate/` into `~/rh-data/plugins/fpvgate/`, replacing an existing copy when upgrading, then restart RotorHazard. Set the gate's IP address in Settings > FPVGate and restart again.

See the [setup guide](https://github.com/LouisHitchcock/fpvgate-rh-plugin#readme) for configuration and older-firmware instructions.
