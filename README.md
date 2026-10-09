# FPVGate RotorHazard Plugin

Use an [FPVGate](https://github.com/LouisHitchcock/FPVGate) lap timer as timing hardware in [RotorHazard](https://github.com/RotorHazard/RotorHazard).

An FPVGate with an ESP32-C5 receiver times up to eight pilots from one gate; an RX5808 FPVGate times one. Either way it appears in RotorHazard as nodes, like any other timer:

- every pass reaches RotorHazard with the gate's own timestamp, so lap times don't depend on Wi-Fi delay
- RotorHazard sets each node's frequency and enter/exit levels on the gate
- live RSSI for every node on the Run page, with crossing indicators
- all of RotorHazard's event tools work: heats, classes, formats, results, marshaling

## Requirements

- RotorHazard 4.4.0 or later
- FPVGate firmware 1.9.0 or later (older firmware still works the version 1 way, see below)
- The gate and RotorHazard on the same network

## Installation

### Community Plugins (recommended)
In RotorHazard, open **Settings > Plugins > Browse Community Plugins**, find **FPVGate** and install it.

### Manual
1. Copy `custom_plugins/fpvgate/` into your RotorHazard data `plugins/` folder (e.g. `~/rh-data/plugins/fpvgate/`).
2. Restart RotorHazard.

## Setup

1. In RotorHazard, open **Settings > FPVGate** and enter the gate's IP address.
2. Restart RotorHazard. The plugin asks the gate how many nodes it has (eight for an ESP32-C5 gate, one for RX5808) and adds them.
3. The FPVGate panel shows **Connected**, the firmware version and how closely the clocks are synchronized.

Nothing needs setting on the gate.

Frequencies come from RotorHazard's frequency setup and profiles, and replace the gate's own. An ESP32-C5 gate tunes 5180 to 5917 MHz; a node set outside that range is switched off and RotorHazard logs a warning. Enter and exit levels start from the gate's and are then adjusted in RotorHazard as usual.

**Run races on the gate too** (on by default) also starts, stops and clears the gate's own race, so its display, LEDs and speaker follow RotorHazard. Turn it off to use the gate purely as a receiver.

## How it works

The plugin registers an FPVGate hardware interface with RotorHazard and talks to the gate over HTTP:

| | |
|---|---|
| `GET /api/rh/info` | firmware, receiver, frequency range and each node's frequency and levels |
| `GET /api/rh/clock` | the gate's clock. Polled every 5 seconds to keep the clocks in step; while it is polled, the gate reports every pass |
| `POST /api/rh/node` | set one node's frequency and enter/exit levels |
| `GET /api/rh/events` | server-sent events for the plugin only: `rhPass` (a pass, timed at its RSSI peak) and `rhRssi` (every node, 10 times a second) |

The clock offset comes from the poll with the fastest round trip among the last 12, so it follows crystal drift over a long event.

## Version 1 firmware

Firmware before 1.9.0 POSTs laps to the plugin itself (`/fpvgate/lap`, clock sync from `/fpvgate/time`). That still works: set the gate's Race Synchronization role to RotorHazard with this computer's IP and the pilot's seat. Only one pilot is timed that way; update the gate for all of them.

## License

MIT
