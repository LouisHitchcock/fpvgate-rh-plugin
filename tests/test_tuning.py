"""Protocol and calibration regressions using mocked gates; no device connections."""
import base64
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
from unittest.mock import Mock, patch
import gevent.event

ROOT = Path(__file__).resolve().parents[1]
RH_SOURCE = Path(os.environ['RH_SOURCE'])
sys.path[:0] = [str(ROOT), str(RH_SOURCE / 'src/server'), str(RH_SOURCE / 'src/server/util'),
               str(RH_SOURCE / 'src/interface')]

from flask import Flask
import FlaskAppObj
FlaskAppObj.APP = Flask('mock-rh-plugin-tests')
import Results  # RH initializes Results before the mutually dependent race/UI modules.
from custom_plugins.fpvgate import FPVGateProvider
from custom_plugins.fpvgate import interface


class GateTuningTest(unittest.TestCase):
    def split(self):
        finish = SimpleNamespace(nodes=[SimpleNamespace(frequency=5658)])
        split = interface.FPVGateSplitInterface(0, 'mock-only', finish, Mock())
        split._apply_info({'nodes': [{'frequency': 5658, 'enter': 100, 'exit': 50}]})
        split.connected = True
        split._push = Mock(side_effect=lambda node, **values: dict(values))
        return split

    def test_clock_reset_discards_old_offset_and_pass_filter(self):
        gate = interface.FPVGateInterface('mock-only', 1)
        gate._clock.append((0.001, 10.0))
        gate._last_clock_reading = 20.0
        gate._last_pass_s[0] = 19.0
        gate._session.get = Mock(return_value=SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'us': 500000}))
        with patch.object(interface, 'monotonic', side_effect=[100.0, 100.01]):
            gate._sample_clock()
        self.assertAlmostEqual(gate._offset, -99.505)
        self.assertAlmostEqual(gate.server_time(500000), 100.005)
        self.assertEqual(len(gate._clock), 1)
        self.assertEqual(gate._last_pass_s, {})

    def test_reconnection_uses_only_a_fresh_clock_sample(self):
        gate = interface.FPVGateInterface('mock-only', 1)
        gate._clock.append((0.001, 10.0))
        gate._last_pass_s[0] = 1.0
        gate._session.get = Mock(return_value=SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'us': 2000000}))
        with patch.object(interface, 'monotonic', side_effect=[100.0, 100.01]):
            gate._resync_clock()
        self.assertAlmostEqual(gate.server_time(2000000), 100.005)
        self.assertEqual(len(gate._clock), 1)
        self.assertEqual(gate._last_pass_s, {})

    def test_stream_resynchronizes_before_admitting_a_pass(self):
        gate = interface.FPVGateInterface('mock-only', 1)
        gate._running = True
        gate._clock.append((0.001, 10.0))
        gate._apply_info = Mock()
        clock_response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'us': 2000000})
        stream_response = SimpleNamespace(raise_for_status=lambda: None,
            iter_lines=lambda **kwargs: iter(['event: rhPass', 'data: {}', '']))
        gate._session.get = Mock(side_effect=[clock_response, nullcontext(stream_response)])
        admitted = []
        def pass_received(event, data):
            admitted.append((event, gate.connected, gate.server_time(2000000)))
            gate._running = False
        gate._handle_event = pass_received
        with patch.object(interface, 'get_info', return_value={'api': 2, 'nodes': []}), \
             patch.object(interface, 'monotonic', side_effect=[100.0, 100.01]):
            gate._stream_loop()
        self.assertEqual(len(admitted), 1)
        self.assertEqual(admitted[0][:2], ('rhPass', True))
        self.assertAlmostEqual(admitted[0][2], 100.005)

    def test_hardware_rejected_frequency_is_not_reported_as_enabled(self):
        gate = interface.FPVGateInterface('mock-only', 1)
        gate._ever_connected = True
        gate._push = Mock(return_value={'frequency': 0})
        gate.set_frequency(0, 6000)
        self.assertEqual(gate.nodes[0].frequency, 0)
        self.assertIn('gate accepted 0 MHz', gate.nodes[0].frequency_error)

    def test_failed_frequency_write_keeps_confirmed_frequency(self):
        gate = interface.FPVGateInterface('mock-only', 1)
        gate._ever_connected = True
        node = gate.nodes[0]
        node.frequency = 5658
        gate._push = Mock(return_value=None)
        gate._frequency_changed = Mock()
        gate.set_frequency(0, 5917)
        self.assertEqual(node.frequency, 5658)
        self.assertEqual(node.requested_frequency, 5917)
        self.assertIsNotNone(node.frequency_error)
        gate._frequency_changed.assert_not_called()
        gate._push.return_value = {'frequency': 5917}
        gate._apply_info({'nodes': [{'frequency': 5658, 'enter': 100, 'exit': 50}]})
        self.assertEqual(node.frequency, 5917)
        self.assertIsNone(node.frequency_error)
        gate._frequency_changed.assert_called_once_with(node)

    def test_split_frequency_write_failure_is_visible(self):
        split = self.split()
        split.finish.nodes[0].frequency = 5917
        split._push.return_value = None
        split._push.side_effect = None
        split.follow(0)
        self.assertEqual(split.nodes[0].frequency, 5658)
        self.assertIn('could not confirm', split.sensor_tuning_snapshot()['message'])

    def test_capture_averages_receiver_samples(self):
        split = self.split()
        def samples(seconds):
            self.assertEqual(seconds, 3.0)
            for value in (70, 80, 90):
                split._on_rssi({'rssi': [value], 'in': 0})
        with patch.object(interface.gevent, 'sleep', side_effect=samples):
            result = split.capture_level(0, 'enter')
        self.assertEqual(result, {'ok': True, 'enter': 80, 'exit': 50})
        self.assertEqual(split._captures, {})

    def test_capture_applies_enter_peak_margin(self):
        split = self.split()
        def samples(seconds):
            for value in (80, 82, 84):
                split._on_rssi({'rssi': [value], 'in': 0})
        with patch.object(interface.gevent, 'sleep', side_effect=samples):
            self.assertEqual(split.capture_level(0, 'enter')['enter'], 79)

    def test_capture_disconnect_or_missing_samples_does_not_save(self):
        split = self.split()
        with patch.object(interface.gevent, 'sleep', side_effect=lambda seconds: split._set_connected(False)):
            self.assertFalse(split.capture_level(0, 'enter')['ok'])
        split._push.assert_not_called()
        split.connected = True
        with patch.object(interface.gevent, 'sleep'):
            self.assertFalse(split.capture_level(0, 'exit')['ok'])
        split._push.assert_not_called()

    def test_concurrent_capture_and_writes_are_rejected(self):
        split = self.split()
        split._captures[0] = {'total': 0, 'count': 0}
        self.assertFalse(split.capture_level(0, 'enter')['ok'])
        self.assertFalse(split.set_levels(0, 100, 50)['ok'])
        split._push.assert_not_called()

    def test_invalid_levels_or_unconfirmed_write_are_not_saved(self):
        split = self.split()
        for enter, exit in ((72.9, 68), (True, 0), (68, 68), (256, 50)):
            self.assertFalse(split.set_levels(0, enter, exit)['ok'])
        split._push.assert_not_called()
        split._push.side_effect = None
        split._push.return_value = {}
        self.assertFalse(split.set_levels(0, 110, 50)['ok'])
        self.assertEqual(split.nodes[0].enter_at_level, 100)

    def test_fallback_routes_authenticate_and_reject_fractional_levels(self):
        split = self.split()
        values = {'ADMIN_USERNAME': 'test-admin', 'ADMIN_PASSWORD': 'test-password'}
        provider = SimpleNamespace(_rhapi=SimpleNamespace(config=SimpleNamespace(get=lambda section, key: values[key])), splits=[split])
        app = Flask('fallback-test')
        app.register_blueprint(FPVGateProvider._tuning_blueprint(provider))
        client = app.test_client()
        self.assertEqual(client.get('/fpvgate/api/split-tuning').status_code, 401)
        headers = {'Authorization': 'Basic ' + base64.b64encode(b'test-admin:test-password').decode()}
        self.assertEqual(client.get('/fpvgate/api/split-tuning', headers=headers).status_code, 200)
        response = client.post('/fpvgate/api/split-tuning/0/0', json={'enter': 72.9, 'exit': 68}, headers=headers)
        self.assertEqual(response.status_code, 400)
        split._push.assert_not_called()
        self.assertEqual(client.post('/fpvgate/api/split-tuning/0/0', json={'enter': 100, 'exit': 50}, headers=headers).status_code, 200)


if __name__ == '__main__':
    unittest.main()
