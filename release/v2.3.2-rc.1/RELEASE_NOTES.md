# FPVGate RotorHazard Plugin v2.3.2-rc.1

24 October 2026. RH timing test prerelease, paired with gate firmware
[v1.9.0-alpha-5](https://github.com/LouisHitchcock/FPVGate/releases/tag/v1.9.0-alpha-5).
Install both parts for the timing comparison.

## Changes

- RSSI history uses peak sample timestamps from the gate, aligning Marshal
  recalculation with live passes at both RSSI resolutions.
- Clock corrections are adopted between crossings, so a crossing and its
  history keep the same clock mapping. Gate restarts still resynchronize
  immediately.
- The package is based on the development plugin, including split-gate
  support and the optional Sensor Tuning integration. Those features require
  the corresponding RH APIs; this test focuses on normal lap timing.

Normal timing requires RotorHazard 4.4.0 or later. All 16 timing and tuning
regression tests pass against RH 4.4.0 with the real gate detector simulator.
Camera and physical multi-transmitter validation remain outstanding.
Matching live and recalculated timestamps does not establish RF accuracy.

## Installation

1. Back up the RH database and existing plugin, then stop RotorHazard.
2. Download `fpvgate-rh-plugin-v2.3.2-rc.1.zip` from this release. Extract it
   and copy its `custom_plugins/fpvgate/` folder into your RH data `plugins/`
   directory, replacing the existing `fpvgate` folder. Keep one installed copy.
3. Install your S3 gate's matching `1.9.0-alpha-5` firmware/filesystem
   pair using that release's instructions, then restart RotorHazard.
4. Confirm the FPVGate panel connects and the startup log reports
   `FPVGate plugin 2.3.2-rc.1`.

Keep the current frequencies, thresholds and RSSI resolution for the first
comparison. No C5 receiver update is needed for an already working Alpha-4
setup. Preserve the original saved RH database before Marshal recalculation,
and record live and recalculated crossing timestamps separately, relative to
race start, including Gate 1 and the node/pilot identifiers.

`SHA256SUMS.txt` contains the checksum of the plugin ZIP.
