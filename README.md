# FPVGate RotorHazard Plugin

Use an [FPVGate](https://github.com/LouisHitchcock/FPVGate) lap timer as timing hardware in [RotorHazard](https://github.com/RotorHazard/RotorHazard).

An FPVGate with an ESP32-C5 receiver times up to eight pilots from one gate; an RX5808 FPVGate times one. Either way it appears in RotorHazard as nodes, like any other timer:

- every pass reaches RotorHazard with the gate's own timestamp, so lap times don't depend on Wi-Fi delay
- RotorHazard sets each node's frequency and enter/exit levels on the gate
- live RSSI for every node on the Run page, with crossing indicators
- all of RotorHazard's event tools work: heats, classes, formats, results, marshaling

## Requirements

- RotorHazard 4.4.0 or later
- FPVGate firmware [v1.9.0-Multi-Alpha-2](https://github.com/LouisHitchcock/FPVGate/releases/tag/v1.9.0-Multi-Alpha-2) or later for the hardware interface (older firmware still works the version 1 way, see below)
- The gate and RotorHazard on the same network

## Installation

### Community Plugins
Community catalogue inclusion is pending. Once approved, open **Settings > Plugins > Browse Community Plugins** in RotorHazard, find **FPVGate** and install it. Until then, use the manual installation below.

### Manual
1. Download the source ZIP from the [latest release](https://github.com/LouisHitchcock/fpvgate-rh-plugin/releases/latest), extract it, and copy `custom_plugins/fpvgate/` into your RotorHazard data `plugins/` folder (e.g. `~/rh-data/plugins/fpvgate/`). If upgrading, replace the existing `fpvgate` plugin folder rather than installing a second copy.
2. Restart RotorHazard.

## Setup

1. In RotorHazard, open **Settings > FPVGate** and enter the gate's IP address.
2. Restart RotorHazard. The plugin asks the gate how many nodes it has (eight for an ESP32-C5 gate, one for RX5808) and adds them.
3. The FPVGate panel shows **Connected**, the firmware version and how closely the clocks are synchronized.

Nothing needs setting on the gate.

On the first connection, the gate's frequencies and enter/exit levels are copied into RotorHazard's current profile. Subsequent frequency and threshold changes in RotorHazard are sent to the gate. An ESP32-C5 gate tunes 5180 to 5917 MHz; a node set outside that range is switched off and RotorHazard logs a warning.

**Run races on the gate too** (on by default) also starts, stops and clears the gate's own race, so its display, LEDs and speaker follow RotorHazard. Turn it off to use the gate purely as a receiver.

**Full-resolution RSSI** (off by default) shows an ESP32-C5 gate's RSSI as 0-1023 instead of 0-255, for finer graphs in Sensor Tuning. EnterAt and ExitAt switch to the same scale and snap to steps of 4, the gate's own steps. Restart RotorHazard after changing it. It needs gate firmware that reports `rawMax` in `/api/rh/info` (later than 1.9.0-Multi-Alpha-2); with older firmware the plugin logs a warning and stays on 0-255.

## Split times

More FPVGates part way round the course give each pilot split times. RotorHazard records them as its own split timers: on the Race page, announced, saved with the race, and with a speed if you give the distance between gates. This needs RotorHazard with split timers for plugins (RHAPI 1.10); on older versions the FPVGate panel says so, and lap timing works as before.

1. Set up the start/finish gate as above.
2. In **Settings > FPVGate**, enter the first split gate's address in **Split Gate 1**. For more split gates, press **Add Split Gate** for another row; add them in the order pilots pass them. Restart RotorHazard. The FPVGate panel shows each split gate's connection.
3. Each split gate's node 1 follows the finish gate's node 1, node 2 follows node 2, and so on: they are tuned to the same frequencies, and follow changes made in RotorHazard. A split gate with fewer nodes (an RX5808 gate has one) times the first seats only.
4. In **Settings > Sensor Tuning**, each split gate appears below the main gate's receivers, with RH's RSSI graph, crossing indicator, and independent EnterAt/ExitAt controls. Change a value or drag a threshold line to save levels directly to that split gate. **Capture** averages that receiver's samples for three seconds and keeps EnterAt at least five RSSI units below a known node peak, matching RH's existing capture behavior. A disconnect cancels capture. Each gate keeps its own thresholds because it sees a pilot at a different strength. This integration needs the proposed `rhapi.ui.register_sensor_tuning` API (RHAPI 1.11); older RH builds retain the separate tuning page linked in the FPVGate settings panel. The fallback page offers graphs and manual threshold saves, without Capture.
5. Optional: a **Split Distance** for each gate, the distance from the gate before it, gives a speed for each split (metres give m/s, metres x 3.6 give km/h). **Split Callouts** chooses what is announced.
6. To remove a split gate, press its **Remove Split Gate** button and restart RotorHazard: its row goes, and the split gates after it move up a number. (Clearing its address does the same at the next restart.)
7. If you also use RotorHazard secondary timers as split timers, set **First Split Number** to the number after theirs.

RotorHazard decides what counts as a split, as for its own split timers: split 1 after each lap pass, then each split after the one before; a split out of order or after a missed one is ignored, and there must be a pilot in the seat.

## Testing status

The companion Multi-Alpha-2 firmware is a testing release. Release testing covered a Seeed XIAO ESP32S3 with an ESP32-C5 receiver over USB networking, including 28 RotorHazard races. Physical eight-pilot race testing is still outstanding. The Waveshare ESP32-S3-ETH firmware has not been tested on hardware.

## How it works

The plugin registers an FPVGate hardware interface with RotorHazard and talks to the gate over HTTP:

| | |
|---|---|
| `GET /api/rh/info` | firmware, receiver, frequency range and each node's frequency and levels |
| `GET /api/rh/clock` | the gate's clock. Polled every 5 seconds to keep the clocks in step; while it is polled, the gate reports every pass |
| `POST /api/rh/node` | set one node's frequency and enter/exit levels |
| `GET /api/rh/events` | server-sent events for the plugin only: `rhPass` (a pass, timed at its RSSI peak) and `rhRssi` (every node, 20 times a second, each the highest RSSI since the last; 10 on older firmware) |

RotorHazard keeps that RSSI history for each race (the gate declares full RSSI marshaling), so the Marshal page can show the trace and recalculate laps from new Enter/Exit levels.

The clock offset comes from the poll with the fastest round trip among the last 12, so it follows crystal drift over a long event.

## Version 1 firmware

Firmware before 1.9.0 POSTs laps to the plugin itself (`/fpvgate/lap`, clock sync from `/fpvgate/time`). That still works: set the gate's Race Synchronization role to RotorHazard with this computer's IP and the pilot's seat. Only one pilot is timed that way; update the gate for all of them. Firmware after 1.9.0-Multi-Alpha-2 no longer has that role, because the plugin connects to the gate instead.

## License

MIT
