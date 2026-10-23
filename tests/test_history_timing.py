"""Marshal recalculation against live passes for a C5 gate.

Runs the gate's own lap detection (lib/C5LINK/c5multipilot.cpp in the FPVGate
repo, built on the PC by tests/native/build_rh_timing_sim.sh) on simulated
multipath passes, feeds the rhPass and rhRssi events it would send through
this plugin's FPVGateInterface and RotorHazard's own history code, then
recalculates the laps as RotorHazard's Marshal page does
(src/server/static/marshal.js). With the same EnterAt/ExitAt, recalculated
passes must land on the live ones.

    RH_SOURCE=<RotorHazard checkout> GATE_RH_SIM=<rh_timing_sim.exe> \\
        python -m unittest discover -s tests -p test_history_timing.py

Needs gevent and requests; skipped without GATE_RH_SIM.
"""
import importlib.util
import math
import os
import random
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
RH_SOURCE = Path(os.environ['RH_SOURCE'])
sys.path[:0] = [str(RH_SOURCE / 'src/interface'), str(RH_SOURCE / 'src/server/util')]
GATE_RH_SIM = os.environ.get('GATE_RH_SIM')

# interface.py only, without the plugin package (which needs RH's web stack).
spec = importlib.util.spec_from_file_location('fpvgate_interface', ROOT / 'custom_plugins/fpvgate/interface.py')
interface = importlib.util.module_from_spec(spec)
spec.loader.exec_module(interface)

ENTER, EXIT = 140, 110          # the stub gate's levels (tests/native/stubs/config.h)
NEW_FIELDS = ('levelUs', 'us', 'peakAge', 'levelPeakAge')


def marshal_recalc(values, times, enter_at, exit_at):
    """RH 4.4's Marshal recalculation, lap times only."""
    laps = []
    crossing = False
    peak = peak_first = peak_last = 0
    for rssi, time in zip(values, times):
        if not crossing and rssi > enter_at:
            crossing = True
        if rssi >= peak:
            peak_last = time
            if rssi > peak:
                peak_first = time
                peak = rssi
        if crossing and rssi < exit_at:
            laps.append((peak_last + peak_first) / 2)
            crossing = False
            peak = 0
    if crossing:
        laps.append((peak_last + peak_first) / 2)
    return laps


def gate_events(seed, poll_ms=50):
    """The events a gate sends for ten multipath passes on slot 0."""
    rng = random.Random(seed)
    passes = [(2000 + 1500 * i, rng.uniform(30, 80), rng.uniform(0, math.tau)) for i in range(10)]
    lines = []
    next_poll = poll_ms
    for ms in range(1, passes[-1][0] + 1000):
        level = 75 + rng.gauss(0, 3)
        for centre, period, phase in passes:
            if abs(ms - centre) < 600:
                bump = 115 * math.exp(-0.5 * ((ms - centre) / 150) ** 2)
                level += bump * (1 - 0.25 * abs(math.sin(math.tau * (ms - centre) / period + phase)))
        lines.append(f'sample {ms * 1000 + rng.randint(0, 999)} 0 {max(0, min(1023, round(level * 4)))}')
        if ms >= next_poll:
            lines.append(f'poll {ms * 1000 + 999}')
            next_poll = ms + poll_ms + rng.randint(-5, 5)
    out = subprocess.run([GATE_RH_SIM], input='\n'.join(lines) + '\n', capture_output=True, text=True, check=True)
    events = []
    for line in out.stdout.splitlines():
        name, _, body = line.partition(' ')
        events.append((name, interface.json.loads(body)))
    return events, len(passes)


@unittest.skipUnless(GATE_RH_SIM, 'set GATE_RH_SIM to the FPVGate repo\'s rh_timing_sim')
class HistoryTimingTest(unittest.TestCase):
    def run_gate(self, seed, full_rssi, strip=False, clock_jump=False):
        info = {'rawMax': 1023, 'nodes': [{'frequency': 5658, 'enter': ENTER, 'exit': EXIT}]}
        gate = interface.FPVGateInterface('sim', 8, info=info, full_rssi=full_rssi)
        gate._offset = 0.0  # gate clock == RotorHazard's
        gate.race_status = interface.BaseHardwareInterface.RACE_STATUS_RACING
        events, count = gate_events(seed)
        clock = [0.0]
        with patch.object(interface, 'monotonic', side_effect=lambda: clock[0]):
            for name, msg in events:
                if 'us' in msg:
                    clock[0] = msg['us'] / 1e6 + 0.003  # arrives a few ms after it was sent
                if strip:  # what an older gate (or plugin) gives RotorHazard
                    for key in NEW_FIELDS:
                        if not (name == 'rhPass' and key == 'us'):
                            msg.pop(key, None)
                if clock_jump and name == 'rhPass':
                    # A better HTTP clock measurement arrives after the peak
                    # history, just before the delayed live pass is processed.
                    self.assertTrue(gate._nodes[0].crossing_flag)
                    response = Mock()
                    response.json.return_value = {'us': round((clock[0] + 0.021) * 1e6)}
                    with patch.object(interface, 'monotonic', side_effect=[clock[0], clock[0] + 0.002]), \
                            patch.object(gate._session, 'get', return_value=response):
                        gate._sample_clock_locked()
                    clock_jump = False
                (gate._on_pass if name == 'rhPass' else gate._on_rssi)(msg)
        live = []
        while not gate._passes.empty():
            live.append(gate._passes.get_nowait()[1])
        node = gate._nodes[0]
        recalc = marshal_recalc(node.history_values, node.history_times, ENTER * gate.scale, EXIT * gate.scale)
        return live, recalc, count

    def offsets(self, live, recalc):
        return [round((min(recalc, key=lambda r: abs(r - t)) - t) * 1000, 1) for t in live]

    def test_recalculated_passes_match_live_passes(self):
        for full_rssi in (False, True):
            for seed in range(1, 6):
                with self.subTest(full_rssi=full_rssi, seed=seed):
                    live, recalc, count = self.run_gate(seed, full_rssi)
                    self.assertEqual(len(live), count)
                    self.assertEqual(len(recalc), count)
                    offsets = self.offsets(live, recalc)
                    self.assertLessEqual(max(map(abs, offsets)), 1.0, f'recalc minus live, ms: {offsets}')

    def test_without_peak_times_history_lags_the_passes(self):
        # Gates before this change: history timed when each reading was sent.
        live, recalc, _ = self.run_gate(1, False, strip=True)
        offsets = self.offsets(live, recalc)
        self.assertGreater(min(offsets), 0, f'recalc minus live, ms: {offsets}')

    def test_clock_correction_during_pass_preserves_history_timing(self):
        for full_rssi in (False, True):
            with self.subTest(full_rssi=full_rssi):
                live, recalc, count = self.run_gate(1, full_rssi, clock_jump=True)
                self.assertEqual(len(live), count)
                self.assertEqual(len(recalc), count)
                self.assertLessEqual(max(map(abs, self.offsets(live, recalc))), 1.0)

    def test_clock_correction_is_adopted_between_passes(self):
        gate = interface.FPVGateInterface('sim', 1)
        gate._offset = 0.0
        gate._nodes[0].crossing_flag = True
        response = Mock()
        response.json.side_effect = [{'us': 100_022_000}, {'us': 101_031_000}]
        with patch.object(gate._session, 'get', return_value=response), \
                patch.object(interface, 'monotonic', side_effect=[100, 100.004, 101, 101.002]):
            gate._sample_clock_locked()
            self.assertEqual(gate._offset, 0.0)
            gate._nodes[0].crossing_flag = False
            gate._sample_clock_locked()
        self.assertAlmostEqual(gate._offset, 0.03)
        self.assertEqual(len(gate._clock), 2)

    def test_clock_restart_resyncs_even_during_a_crossing(self):
        gate = interface.FPVGateInterface('sim', 1)
        gate._offset = 20.0
        gate._last_clock_reading = 1000.0
        gate._nodes[0].crossing_flag = True
        response = Mock()
        response.json.return_value = {'us': 100_022_000}
        with patch.object(gate._session, 'get', return_value=response), \
                patch.object(interface, 'monotonic', side_effect=[100, 100.004]):
            gate._sample_clock_locked()
        self.assertAlmostEqual(gate._offset, 0.02)


if __name__ == '__main__':
    unittest.main()
