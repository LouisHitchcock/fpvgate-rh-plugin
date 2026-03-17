# FPVGate RotorHazard Plugin

Bidirectional integration between [FPVGate](https://github.com/LouisHitchcock/FPVGate) RSSI-based lap timers and [RotorHazard](https://github.com/RotorHazard/RotorHazard).

## What it does

**FPVGate → RH (lap recording)**
When the FPVGate hardware detects a gate crossing it POSTs the hardware-measured elapsed race time to RotorHazard, which records it as a live lap on the correct pilot seat with an accurate timestamp.

**RH → FPVGate (race control)**
RotorHazard race lifecycle events are forwarded to the FPVGate:

| RH Event | FPVGate action |
|---|---|
| Stage | Clear laps + countdown beeps |
| Start | Start timer |
| Stop / Finish / Abort | Stop timer |
| Laps Clear | Clear laps |

## Requirements

- RotorHazard 4.4.0 or later
- FPVGate firmware with RotorHazard support (RH-Support branch or later)
- Both devices on the same network

## Installation

### Via RotorHazard Community Plugins (recommended)
Search for **FPVGate** in the RotorHazard UI under Settings → Community Plugins and click Install.

### Manual
1. Copy the `custom_plugins/fpvgate/` directory into your RotorHazard data `plugins/` folder (e.g. `~/rh-data/plugins/fpvgate/`).
2. Restart the RotorHazard server.

## Configuration

### On RotorHazard
1. Go to **Settings → FPVGate**.
2. Enter the IP address of your FPVGate device (e.g. `192.168.0.158`).
   Leave blank to use auto-detection — the plugin will learn the FPVGate IP from the first lap POST it receives.

### On FPVGate
1. Go to **Settings → WiFi & Connection → Race Synchronization**.
2. Set **This Device** role to **RotorHazard**.
3. Enter the RH Host IP (e.g. `192.168.0.21`) and select the Node Seat (0-indexed, matching the pilot's seat in RH).

### RotorHazard config.json
When running without real timing hardware, enable mock nodes so `lap_add()` has a valid node to reference:
```json
"MOCK_NODES": 1
```

## How it works

The plugin registers a Flask route at `/fpvgate/lap` that receives `{"node": N, "raceTimeMs": T}` POSTed by the FPVGate firmware on each gate crossing. The elapsed millisecond timestamp is converted to the absolute monotonic time that `rhapi.race.lap_add()` expects, then the lap is recorded in a gevent greenlet to avoid a Flask app-context conflict.

Race control events are sent as fire-and-forget HTTP POSTs to the FPVGate's existing timer API (`/timer/start`, `/timer/stop`, `/timer/clearLaps`, `/timer/countdown`).

## License

MIT
