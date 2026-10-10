"""FPVGate timing interface for RotorHazard.

An FPVGate is one receiver node (RX5808 builds) or eight (ESP32-C5 multi-pilot
builds). The plugin talks to it over HTTP:

    GET  /api/rh/info   firmware, radio, frequency range and every node's
                        frequency and enter/exit levels
    GET  /api/rh/clock  the gate's clock, in microseconds since boot. Polling
                        it also tells the gate the plugin is connected, which
                        makes it report every pass (and stop sending laps the
                        version 1 way)
    POST /api/rh/node   {"node", "frequency", "enter", "exit"}: tune one node
    GET  /api/rh/events server-sent events (api 2; api 1 firmware sends them
                        on the browser's /events among its own):
        rhPass  {"node", "us", "peak"}  a pass, timed at its RSSI peak
        rhRssi  {"rssi": [...], "in"}   every node's RSSI (0-255), 10 times a
                                        second (20 from 1.9.0-Multi-Alpha-3,
                                        each the highest since the last); "in"
                                        has bit n set while node n is inside
                                        the gate. With "us" (gate clock) the
                                        reading is timed by the gate, else on
                                        arrival
    C5 gates that report "rawMax" in /api/rh/info also send "rawPeak" and
    "raw": [...], the same RSSI at full resolution (0-1023, levels x 4).

Passes carry the gate's own timestamp, so network delay does not affect lap
times; only the clock offset does, and that is measured continuously.
"""

import json
import logging
from collections import deque
from time import monotonic

import gevent
import gevent.queue
import requests

from BaseHardwareInterface import BaseHardwareInterface, PeakNadirHistory, MarshalType
from Node import Node

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_S = 2.0
STREAM_TIMEOUT_S = (3.0, 20.0)  # connect, read (the gate sends RSSI 10-20 times a second)
CLOCK_POLL_S = 5.0              # the gate drops the link after 15 s without a poll
CLOCK_WINDOW = 12               # recent clock samples; the fastest round trip wins
RECONNECT_S = 3.0
RSSI_MAX = 255
# A node's passes closer together than this are fragments of one fly-through
# (gates before 1.9.0-Multi-Alpha-3 report each multipath dip as a pass); the
# first is kept. No real lap is this short.
PASS_FRAGMENT_S = 0.5
# Passes waiting for RotorHazard. Handing a pass over blocks while
# RotorHazard's own queue is full, so it happens away from the stream reader,
# which would otherwise stop reading RSSI too. Beyond this many waiting, new
# passes are dropped (and counted): their RSSI is still recorded, so the
# Marshal page can recalculate them.
PASS_BACKLOG_MAX = 64
OVERLOAD_LOG_S = 5.0            # at most one overload warning this often


def get_info(address, timeout=HTTP_TIMEOUT_S):
    """The gate's /api/rh/info, or None when it can't be reached."""
    try:
        response = requests.get(f"http://{address}/api/rh/info", timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.debug("FPVGate at %s: no info (%s)", address, exc)
        return None


class FPVGateNode(Node):
    def __init__(self, local_index):
        super().__init__()
        self.local_index = local_index
        # RotorHazard's newest node API: enter/exit levels, 0-255 RSSI.
        self.api_level = 35
        self.init()
        self.max_rssi_value = RSSI_MAX + 1   # readings must be below this
        # Set once RotorHazard has chosen the value, so a reconnect pushes
        # RotorHazard's choice to the gate instead of adopting the gate's.
        self.rh_frequency = False
        self.rh_levels = False


class FPVGateInterface(BaseHardwareInterface):
    def __init__(self, address, node_count, status_callback=None, info=None, frequencies_callback=None,
                 full_rssi=False):
        super().__init__()
        self.address = address
        # Called with the nodes after the gate's frequencies were adopted on
        # the first connection, for RotorHazard's profile to follow them.
        self.frequencies_callback = frequencies_callback
        self._ever_connected = False
        self._nodes = [FPVGateNode(i) for i in range(node_count)]
        # With full_rssi, RotorHazard sees the C5's 0..1023 RSSI and levels;
        # the gate stores levels as 0..255, so they are scaled by 4 each way.
        self.full_rssi = full_rssi
        self.scale = 1
        self._set_scale(info)
        # The gate's settings from startup, so RotorHazard starts from them.
        for node, slot in zip(self._nodes, (info or {}).get("nodes", [])):
            node.frequency = slot.get("frequency", 0)
            node.enter_at_level = slot.get("enter", 0) * self.scale
            node.exit_at_level = slot.get("exit", 0) * self.scale
        # The RSSI history (every node, 10-20 times a second) is what
        # RotorHazard's Marshal page recalculates laps from.
        self.marshal_type = MarshalType.FULL_RSSI
        self._last_pass_s = {}   # node index -> gate time of its last pass, for PASS_FRAGMENT_S
        self._passes = gevent.queue.Queue(maxsize=PASS_BACKLOG_MAX)
        self.passes_dropped = 0
        self.pass_backlog_peak = 0
        self._overload_logged_s = None
        self._pass_thread = None
        self.ready_failure_msg = "FPVGate is not connected. Check the gate, then reconnect in Settings."
        self.status_callback = status_callback
        self.info = None
        self.connected = False
        self.sync_ms = None
        self._clock = deque(maxlen=CLOCK_WINDOW)
        self._offset = None
        self._running = False
        self._threads = []
        self._session = requests.Session()

    def _set_scale(self, info):
        """Pick the RSSI scale from the gate's info, until the first connection
        fixes it (RotorHazard's levels are in that scale from then on)."""
        if not info or self._ever_connected:
            return
        raw_max = info.get("rawMax")
        if self.full_rssi and not raw_max:
            logger.warning("FPVGate firmware %s has no full-resolution RSSI; using 0-255", info.get("version", "?"))
        self.scale = (raw_max + 1) // (RSSI_MAX + 1) if self.full_rssi and raw_max else 1
        for node in self._nodes:
            node.max_rssi_value = (RSSI_MAX + 1) * self.scale

    def _to_gate(self, level):
        return max(0, min(RSSI_MAX, round(int(level) / self.scale)))

    @property
    def nodes(self):
        return self._nodes

    @nodes.setter
    def nodes(self, value):
        pass

    @property
    def ready(self):
        return self.connected and self._offset is not None

    #
    # Lifecycle
    #

    def start(self):
        if self._running or not self.address:
            return None
        self._running = True
        self._threads = [gevent.spawn(self._stream_loop), gevent.spawn(self._clock_loop)]
        # Outlives reconnects: killing it mid-handoff would lose that pass.
        if self._pass_thread is None or self._pass_thread.dead:
            self._pass_thread = gevent.spawn(self._pass_loop)
        return True

    def stop(self):
        self._running = False
        for thread in self._threads:
            thread.kill(block=False)
        self._threads = []
        self._set_connected(False)

    def restart(self, address=None):
        self.stop()
        if address is not None:
            self.address = address
        self._clock.clear()
        self._offset = None
        return self.start()

    def _set_connected(self, connected):
        if connected != self.connected:
            self.connected = connected
            if not connected:
                # Don't leave the last readings on screen as if they were live.
                for node in self._nodes:
                    node.current_rssi = 0
                    node.crossing_flag = False
            logger.info("FPVGate at %s %s", self.address, "connected" if connected else "disconnected")
            if self.status_callback:
                self.status_callback()

    #
    # Clock
    #

    def _sample_clock(self):
        t0 = monotonic()
        response = self._session.get(f"http://{self.address}/api/rh/clock", timeout=HTTP_TIMEOUT_S)
        t1 = monotonic()
        response.raise_for_status()
        gate_s = response.json()["us"] / 1e6
        self._clock.append((t1 - t0, gate_s - (t0 + t1) / 2))
        rtt, offset = min(self._clock)
        self._offset = offset
        self.sync_ms = rtt * 500  # worst case error is half the round trip

    def _clock_loop(self):
        while self._running:
            try:
                if len(self._clock) < 4:   # settle quickly after (re)connecting
                    self._sample_clock()
                    gevent.sleep(0.2)
                    continue
                self._sample_clock()
            except Exception as exc:
                logger.debug("FPVGate clock poll failed: %s", exc)
            gevent.sleep(CLOCK_POLL_S)

    def server_time(self, gate_us):
        """A gate timestamp on RotorHazard's monotonic clock (seconds)."""
        return gate_us / 1e6 - self._offset

    #
    # Event stream
    #

    def _stream_loop(self):
        while self._running:
            try:
                info = get_info(self.address)
                if not info:
                    raise ConnectionError("no answer from /api/rh/info")
                self._apply_info(info)
                # The gate only serves event streams to clients that accept one.
                path = "/api/rh/events" if info.get("api", 1) >= 2 else "/events"
                with self._session.get(f"http://{self.address}{path}", stream=True,
                                       headers={"Accept": "text/event-stream"},
                                       timeout=STREAM_TIMEOUT_S) as response:
                    response.raise_for_status()
                    self._set_connected(True)
                    event, data = None, []
                    # chunk_size=1: the default 512 waits for ~7 events and
                    # delivers them in bursts about a second apart.
                    for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                        if not self._running:
                            break
                        if line is None:
                            continue
                        if line == "":
                            if event and data:
                                self._handle_event(event, "\n".join(data))
                            event, data = None, []
                        elif line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].lstrip())
            except Exception as exc:
                logger.debug("FPVGate stream ended: %s", exc)
            self._set_connected(False)
            if self._running:
                gevent.sleep(RECONNECT_S)

    def _apply_info(self, info):
        self.info = info
        self._set_scale(info)
        scale = self.scale
        for node, slot in zip(self._nodes, info.get("nodes", [])):
            if node.rh_frequency:
                self._push(node, frequency=node.frequency)
            else:
                node.frequency = slot.get("frequency", 0)
            enter, exit = slot.get("enter", 0) * scale, slot.get("exit", 0) * scale
            if node.rh_levels:
                self._push(node, enter=self._to_gate(node.enter_at_level), exit=self._to_gate(node.exit_at_level))
            elif not self._ever_connected or (enter, exit) != (node.enter_at_level, node.exit_at_level):
                # Adopt the gate's levels and have RotorHazard store them in
                # its profile and show them.
                node.enter_at_level = enter
                node.exit_at_level = exit
                if callable(self.new_enter_or_exit_at_callback):
                    gevent.spawn(self.new_enter_or_exit_at_callback, node, True)
                    gevent.spawn(self.new_enter_or_exit_at_callback, node, False)
        if not self._ever_connected:
            self._ever_connected = True
            if callable(self.frequencies_callback):
                gevent.spawn(self.frequencies_callback, self._nodes)
        if len(info.get("nodes", [])) != len(self._nodes):
            logger.warning("FPVGate reports %d nodes but RotorHazard set up %d; restart RotorHazard to match",
                           len(info.get("nodes", [])), len(self._nodes))

    def _handle_event(self, event, data):
        if event == "rhPass":
            self._on_pass(json.loads(data))
        elif event == "rhRssi":
            self._on_rssi(json.loads(data))

    def _on_pass(self, msg):
        index = msg.get("node", -1)
        if not 0 <= index < len(self._nodes) or self._offset is None:
            return
        node = self._nodes[index]
        pass_s = msg["us"] / 1e6
        last = self._last_pass_s.get(index)
        if last is not None and 0 <= pass_s - last < PASS_FRAGMENT_S:
            logger.debug("FPVGate node %d: pass %.3f s after the last one ignored", index + 1, pass_s - last)
            return
        self._last_pass_s[index] = pass_s
        peak = msg.get("rawPeak", 0) if self.scale > 1 else msg.get("peak", 0)
        if peak:
            node.pass_peak_rssi = peak
            node.node_peak_rssi = max(peak, node.node_peak_rssi)
        try:
            self._passes.put_nowait((node, self.server_time(msg["us"]), peak or None))
        except gevent.queue.Full:
            self.passes_dropped += 1
            self._report_overload()
            return
        self.pass_backlog_peak = max(self.pass_backlog_peak, self._passes.qsize())

    def _pass_loop(self):
        """Hand passes to RotorHazard, in the order the gate sent them."""
        while True:
            node, timestamp, peak = self._passes.get()
            if not callable(self.pass_record_callback):
                continue
            try:
                self.pass_record_callback(node, timestamp, BaseHardwareInterface.LAP_SOURCE_REALTIME, peak=peak)
            except Exception:
                logger.exception("FPVGate: RotorHazard failed to record a pass for node %d", node.local_index + 1)

    def _report_overload(self):
        now = monotonic()
        if self._overload_logged_s is not None and now - self._overload_logged_s < OVERLOAD_LOG_S:
            return
        logger.warning("FPVGate: RotorHazard is not keeping up with the gate's passes; %d dropped so far. "
                       "Their RSSI is still recorded: recalculate laps on the Marshal page, and check the "
                       "Enter/Exit levels.", self.passes_dropped)
        self._overload_logged_s = now
        if self.status_callback:
            gevent.spawn(self.status_callback)

    def _on_rssi(self, msg):
        # Timed by the gate when it says when, so a slow read doesn't shift
        # the Marshal trace against the passes.
        readtime = self.server_time(msg["us"]) if "us" in msg and self._offset is not None else monotonic()
        inside = msg.get("in", 0)
        values = msg.get("raw", []) if self.scale > 1 else msg.get("rssi", [])
        for index, rssi in enumerate(values):
            if index >= len(self._nodes):
                break
            node = self._nodes[index]
            node.current_rssi = rssi
            node.node_peak_rssi = max(rssi, node.node_peak_rssi)
            node.node_nadir_rssi = min(rssi, node.node_nadir_rssi) if node.node_nadir_rssi else rssi
            history = PeakNadirHistory(node.index)
            history.peakRssi = rssi
            self.process_history(node, readtime, history)
            crossing = bool(inside >> index & 1)
            if crossing != node.crossing_flag:
                node.crossing_flag = crossing
                if callable(self.node_crossing_callback):
                    gevent.spawn(self.node_crossing_callback, node)

    #
    # Tuning (RotorHazard -> gate)
    #

    def _push(self, node, **values):
        if not self.address:
            return None
        try:
            response = self._session.post(f"http://{self.address}/api/rh/node",
                                          json=dict(node=node.local_index, **values), timeout=HTTP_TIMEOUT_S)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.debug("FPVGate node %d update failed: %s", node.local_index + 1, exc)
            return None

    # Until the gate has been reached once, settings from RotorHazard are its
    # profile being loaded at startup, not a choice: the gate's own frequencies
    # and levels win, and RotorHazard's profile is updated to them on connect.
    def set_frequency(self, node_index, frequency, band=None, channel=None):
        node = self._nodes[node_index]
        if not self._ever_connected:
            return None
        node.rh_frequency = True
        node.debug_pass_count = 0
        node.frequency = frequency
        stored = self._push(node, frequency=frequency)
        if stored is not None and stored.get("frequency") != frequency:
            logger.warning("FPVGate can't tune node %d to %s MHz (range %s-%s); it is off",
                           node_index + 1, frequency, (self.info or {}).get("minMhz"), (self.info or {}).get("maxMhz"))
            node.frequency = stored.get("frequency", 0)
        return None

    def transmit_enter_at_level(self, node, level):
        return level

    # A level of 0 is RotorHazard having no value yet (a new profile sets
    # every node to 0 at startup): keep the gate's, which RotorHazard then
    # reads back into the profile.
    def set_enter_at_level(self, node_index, level):
        node = self._nodes[node_index]
        if not self._ever_connected or not level or int(level) <= 0:
            return node.enter_at_level
        node.rh_levels = True
        node.enter_at_level = int(level)
        self._push_levels(node)
        return node.enter_at_level

    def transmit_exit_at_level(self, node, level):
        return level

    def set_exit_at_level(self, node_index, level):
        node = self._nodes[node_index]
        if not self._ever_connected or not level or int(level) <= 0:
            return node.exit_at_level
        node.rh_levels = True
        node.exit_at_level = int(level)
        self._push_levels(node)
        return node.exit_at_level

    def _push_levels(self, node):
        """Send a node's levels to the gate and keep the levels it stored.
        Full-resolution levels land on the gate's 0..255 steps (4 apart)."""
        stored = self._push(node, enter=self._to_gate(node.enter_at_level), exit=self._to_gate(node.exit_at_level))
        if stored:
            node.enter_at_level = stored.get("enter", self._to_gate(node.enter_at_level)) * self.scale
            node.exit_at_level = stored.get("exit", self._to_gate(node.exit_at_level)) * self.scale

    #
    # Not applicable to FPVGate
    #

    def force_end_crossing(self, node_index):
        pass

    def jump_to_bootloader(self):
        pass

    def send_status_message(self, msgTypeVal, msgDataVal):
        return False

    def send_shutdown_button_state(self, stateVal):
        return False

    def send_shutdown_started_message(self):
        return False

    def send_server_idle_message(self):
        return False

    def get_fwupd_serial_name(self):
        return None

    def close_fwupd_serial_port(self):
        pass

    def get_info_node_obj(self):
        return None

    def inc_intf_read_block_count(self):
        pass

    def inc_intf_read_error_count(self):
        pass

    def inc_intf_write_block_count(self):
        pass

    def inc_intf_write_error_count(self):
        pass

    def get_intf_total_error_count(self):
        return 0

    def set_intf_error_report_percent_limit(self, percentVal):
        pass

    def get_intf_error_report_str(self, forceFlag=False):
        return None
