"""
FPVGate RotorHazard Plugin
==========================
Adds an FPVGate lap timer to RotorHazard as timing hardware: one node for
RX5808 builds, eight for ESP32-C5 multi-pilot builds. RotorHazard sets each
node's frequency and enter/exit levels, shows its live RSSI, and gets every
pass with the gate's own timestamp. See interface.py for the protocol.

Firmware without plugin-2 support (before 1.9.0) still works the version 1
way: it POSTs laps to /fpvgate/lap and syncs its clock from /fpvgate/time.
"""

import json
import logging
from time import monotonic

import gevent
import requests
from flask import Blueprint, jsonify, request

from eventmanager import Evt
from RHRace import RaceStatus
from RHUI import UIField, UIFieldSelectOption, UIFieldType

from .interface import FPVGateInterface, FPVGateSplitInterface, get_info

logger = logging.getLogger(__name__)

PLUGIN_VERSION = "2.2.0"
SECTION = "FPVGate"
PANEL = "fpvgate"
DEFAULT_NODES = 8
HTTP_TIMEOUT_S = 2.0

# For labelling the gate's frequencies in RotorHazard's profile.
BANDS = {
    "R": [5658, 5695, 5732, 5769, 5806, 5843, 5880, 5917],
    "F": [5740, 5760, 5780, 5800, 5820, 5840, 5860, 5880],
    "A": [5865, 5845, 5825, 5805, 5785, 5765, 5745, 5725],
    "B": [5733, 5752, 5771, 5790, 5809, 5828, 5847, 5866],
    "E": [5705, 5685, 5665, 5645, 5885, 5905, 5925, 5945],
    "L": [5362, 5399, 5436, 5473, 5510, 5547, 5584, 5621],
}

def split_list(text):
    return [item.strip() for item in (text or "").replace(";", ",").split(",") if item.strip()]


def band_channel(frequency):
    for band, channels in BANDS.items():
        if frequency in channels:
            return band, channels.index(frequency) + 1
    return None, None


class FPVGateProvider:
    def __init__(self, rhapi):
        self._rhapi = rhapi
        # Version 1 firmware: the gate's address learned from its lap POSTs.
        self._legacy_address = None
        # Set at startup if RotorHazard didn't keep the gate's nodes.
        self._nodes_dropped = False

        rhapi.config.register_section(SECTION)

        # Ask the gate how many nodes it has before registering the fields:
        # once node_count is registered with persistent_restart, every write
        # to it raises RotorHazard's "restart to apply" banner.
        address = self._address()
        node_count = rhapi.config.get(SECTION, "node_count", as_int=True) or DEFAULT_NODES
        info = get_info(address, timeout=3.0) if address else None
        if info and info.get("nodes") and len(info["nodes"]) != node_count:
            node_count = len(info["nodes"])
            rhapi.config.set(SECTION, "node_count", node_count)

        rhapi.ui.register_panel(PANEL, "FPVGate", "settings")
        rhapi.fields.register_option(
            UIField(
                name="address",
                label="FPVGate Address",
                field_type=UIFieldType.TEXT,
                value="",
                desc="IP address or hostname of the gate, e.g. 192.168.4.1. Restart RotorHazard after changing it.",
                placeholder="192.168.4.1",
                persistent_section=SECTION,
                persistent_restart=True,
            ),
            panel=PANEL,
        )
        rhapi.fields.register_option(
            UIField(
                name="node_count",
                label="Nodes",
                field_type=UIFieldType.NUMBER,
                value=DEFAULT_NODES,
                desc="Used when the gate can't be reached at startup. Set automatically from the gate.",
                html_attributes={"min": 1, "max": 8},
                persistent_section=SECTION,
                persistent_restart=True,
            ),
            panel=PANEL,
        )
        rhapi.fields.register_option(
            UIField(
                name="mirror_race",
                label="Run races on the gate too",
                field_type=UIFieldType.CHECKBOX,
                value="1",
                desc="Start, stop and clear the gate's own race with RotorHazard's, for its display, LEDs and speaker.",
                persistent_section=SECTION,
            ),
            panel=PANEL,
        )
        rhapi.fields.register_option(
            UIField(
                name="full_rssi",
                label="Full-resolution RSSI",
                field_type=UIFieldType.CHECKBOX,
                value="0",
                desc="Show a C5 gate's RSSI as 0-1023 instead of 0-255, for finer graphs. EnterAt/ExitAt "
                     "switch to the same scale and snap to steps of 4. Restart RotorHazard after changing it.",
                persistent_section=SECTION,
                persistent_restart=True,
            ),
            panel=PANEL,
        )

        rhapi.fields.register_option(
            UIField(
                name="split_gates",
                label="Split Gates",
                field_type=UIFieldType.TEXT,
                value="",
                desc="Addresses of FPVGates part way round the course, in the order pilots pass them, separated "
                     "by commas. Each gate's passes become split times. Set each split gate's Enter/Exit levels "
                     "on the gate itself. Needs RotorHazard with split timers for plugins (RHAPI 1.10). "
                     "Restart RotorHazard after changing it.",
                placeholder="192.168.4.2, 192.168.4.3",
                persistent_section=SECTION,
                persistent_restart=True,
            ),
            panel=PANEL,
        )
        rhapi.fields.register_option(
            UIField(
                name="first_split",
                label="First Split Number",
                field_type=UIFieldType.NUMBER,
                value=1,
                desc="The split number of the first split gate. Leave at 1 unless RotorHazard secondary timers "
                     "give the first splits; then set it to the number after theirs. Restart RotorHazard after "
                     "changing it.",
                html_attributes={"min": 1, "max": 16},
                persistent_section=SECTION,
                persistent_restart=True,
            ),
            panel=PANEL,
        )
        rhapi.fields.register_option(
            UIField(
                name="split_distances",
                label="Split Distances",
                field_type=UIFieldType.TEXT,
                value="",
                desc="Optional: the distance to each split gate from the gate before it, separated by commas, "
                     "for split speeds. A speed is the distance divided by the seconds taken: metres give m/s, "
                     "metres x 3.6 give km/h. Restart RotorHazard after changing it.",
                placeholder="40, 55",
                persistent_section=SECTION,
                persistent_restart=True,
            ),
            panel=PANEL,
        )
        rhapi.fields.register_option(
            UIField(
                name="split_callout",
                label="Split Callouts",
                field_type=UIFieldType.SELECT,
                value="auto",
                desc="What is announced at each split. Automatic: the speed when a distance is set, else the time.",
                options=[UIFieldSelectOption("auto", "Automatic"), UIFieldSelectOption("time", "Time"),
                         UIFieldSelectOption("speed", "Speed"), UIFieldSelectOption("both", "Time and speed"),
                         UIFieldSelectOption("nameOnly", "Pilot name only"), UIFieldSelectOption("none", "Nothing")],
                persistent_section=SECTION,
                persistent_restart=True,
            ),
            panel=PANEL,
        )

        self.interface = FPVGateInterface(address, node_count if address else 0, self._update_status, info,
                                          self._adopt_frequencies, full_rssi=self._option("full_rssi"),
                                          frequency_changed_callback=self._follow_frequency)
        rhapi.interface.add(self.interface)
        self.splits = []
        self._splits_unsupported = False
        self._add_split_gates(split_list(rhapi.config.get(SECTION, "split_gates")))
        if address:
            logger.info("FPVGate: %d nodes at %s (%s at startup)", len(self.interface.nodes), address,
                        "reached" if info else "not reached")
        else:
            logger.warning("FPVGate: no address set, so no nodes; set it in Settings and restart RotorHazard")

        rhapi.events.on(Evt.STARTUP, self._startup)
        rhapi.events.on(Evt.SHUTDOWN, self._shutdown)
        rhapi.events.on(Evt.RACE_STAGE, self._race_stage, priority=200)
        rhapi.events.on(Evt.RACE_START, self._race_start, priority=200)
        rhapi.events.on(Evt.RACE_FINISH, self._race_stop, priority=200)
        rhapi.events.on(Evt.RACE_STOP, self._race_stop, priority=200)
        rhapi.events.on(Evt.RACE_ABORT, self._race_stop, priority=200)
        rhapi.events.on(Evt.LAPS_CLEAR, self._laps_clear, priority=200)

        rhapi.ui.blueprint_add(self._legacy_blueprint())

    def _address(self):
        return (self._rhapi.config.get(SECTION, "address") or "").strip()

    #
    # UI
    #

    def _startup(self, _args):
        self._rhapi.ui.register_quickbutton(PANEL, "fpvgate-reconnect", "Reconnect", self._reconnect)
        self._check_nodes_kept()
        # RotorHazard starts the finish gate as its timing hardware; the split
        # gates are the plugin's own.
        for split in self.splits:
            split.start()
        self._update_status()

    def _check_nodes_kept(self):
        """RotorHazard 4.5.1-dev builds before the fix for plugin interfaces
        drop interfaces that plugins add while loading, so the gate connects
        but RotorHazard has none of its nodes. Say so instead of failing
        silently."""
        seats = {id(seat) for seat in self._rhapi.interface.seats}
        nodes = self.interface.nodes
        self._nodes_dropped = bool(nodes) and not any(id(node) in seats for node in nodes)
        if self._nodes_dropped:
            logger.warning("FPVGate: RotorHazard dropped the gate's %d nodes after the plugin added them. "
                           "This RotorHazard build doesn't keep plugin timing hardware; use 4.5.0 or a newer "
                           "build.", len(nodes))
            self._rhapi.ui.message_alert("FPVGate: this RotorHazard build dropped the gate's nodes, so the gate "
                                         "can't be used for timing. Use RotorHazard 4.5.0 or a newer build.")

    def _shutdown(self, _args):
        self.interface.stop()
        for split in self.splits:
            split.stop()

    def _reconnect(self, _args=None):
        if not self._address():
            self._rhapi.ui.message_notify("Set the FPVGate address first, then restart RotorHazard.")
            return
        self._rhapi.ui.message_notify("Reconnecting to FPVGate...")
        self.interface.restart()
        for split in self.splits:
            split.restart()

    def _adopt_frequencies(self, nodes):
        """First connection: put the gate's frequencies into RotorHazard's
        current profile, so RotorHazard starts from how the gate is set up."""
        try:
            profile = self._rhapi.race.frequencyset
            freqs = json.loads(profile.frequencies)
            changed = False
            for node in nodes:
                index = node.index
                if index < 0:
                    continue
                for key in ("f", "b", "c"):
                    while len(freqs[key]) <= index:
                        freqs[key].append(None if key != "f" else 0)
                band, channel = band_channel(node.frequency)
                if (freqs["f"][index], freqs["b"][index], freqs["c"][index]) != (node.frequency, band, channel):
                    freqs["f"][index], freqs["b"][index], freqs["c"][index] = node.frequency, band, channel
                    changed = True
            if changed:
                self._rhapi.db.frequencyset_alter(profile.id, frequencies=freqs)
                logger.info("FPVGate: profile '%s' now uses the gate's frequencies %s", profile.name, freqs["f"])
        except Exception:
            logger.exception("FPVGate: could not copy the gate's frequencies into the profile")

    def _update_status(self):
        iface = self.interface
        if not self._address():
            text = "No address set. Enter the gate's address and restart RotorHazard."
        elif self._nodes_dropped:
            text = ("**RotorHazard dropped the gate's nodes.** This RotorHazard build doesn't keep timing "
                    "hardware added by plugins (a bug in some 4.5.1-dev builds). Use RotorHazard 4.5.0 or a "
                    "newer build.")
        elif iface.connected:
            info = iface.info or {}
            sync = f"{iface.sync_ms:.1f} ms" if iface.sync_ms is not None else "syncing"
            text = (f"**Connected** to {info.get('board', 'FPVGate')} at {iface.address}, "
                    f"firmware {info.get('version', '?')}, {len(iface.nodes)} nodes, "
                    f"RSSI 0-{256 * iface.scale - 1}, clock within {sync}.")
            if iface.passes_dropped:
                text += (f"\n\n**{iface.passes_dropped} passes dropped**: RotorHazard couldn't keep up with "
                         f"them. Their RSSI was recorded, so recalculate laps on the Marshal page, and check "
                         f"the Enter/Exit levels aren't so low that noise counts as passes.")
        elif self._legacy_address:
            text = (f"Receiving laps from version 1 firmware at {self._legacy_address}. "
                    f"Update the gate to 1.9.0 or later for every pilot, live RSSI and tuning from RotorHazard.")
        else:
            text = f"**Not connected** to {iface.address}. Check the gate is on and on this network."
        for split in self.splits:
            if split.connected:
                sync = f"{split.sync_ms:.1f} ms" if split.sync_ms is not None else "syncing"
                text += (f"\n\nSplit {split.split_id + 1}: **connected** to {split.address}, "
                         f"{len(split.nodes)} nodes, clock within {sync}.")
            else:
                text += f"\n\nSplit {split.split_id + 1}: **not connected** to {split.address}."
        if self._splits_unsupported:
            text += ("\n\n**Split gates need a newer RotorHazard**, with split timers for plugins (RHAPI 1.10). "
                     "Lap timing works as before.")
        self._rhapi.ui.register_markdown(PANEL, "fpvgate_status", text)
        self._rhapi.ui.broadcast_ui("settings", replace_panels=False)

    #
    # RotorHazard -> gate race control
    #

    def _gate(self):
        if self.interface.connected:
            return self.interface.address
        return self._address() or self._legacy_address

    def _post(self, path):
        # Split gates in the background: one that is off mustn't hold up
        # RotorHazard's race start.
        for split in self.splits:
            gevent.spawn(self._post_to, split.address, path)
        self._post_to(self._gate(), path)

    def _post_to(self, address, path):
        if not address:
            return
        try:
            response = requests.post(f"http://{address}{path}", json={}, timeout=HTTP_TIMEOUT_S)
            logger.debug("FPVGate POST %s to %s -> HTTP %d", path, address, response.status_code)
        except Exception as exc:
            logger.warning("FPVGate POST %s to %s failed: %s", path, address, exc)

    def _option(self, name):
        value = self._rhapi.config.get(SECTION, name)
        return str(value) not in ("0", "False", "false", "")

    def _mirror(self):
        return self._option("mirror_race")

    def _race_stage(self, _args):
        if self._mirror():
            self._post("/timer/clearLaps")
            self._post("/timer/countdown")

    def _race_start(self, _args):
        if self._mirror():
            self._post("/timer/start")

    def _race_stop(self, _args):
        if self._mirror():
            self._post("/timer/stop")

    def _laps_clear(self, _args):
        if self._mirror():
            self._post("/timer/clearLaps")

    #
    # Split gates
    #

    def _follow_frequency(self, node):
        for split in self.splits:
            split.follow(node.local_index)

    def _add_split_gates(self, addresses):
        """A RotorHazard split timer for each split gate, numbered from First
        Split Number in course order. RotorHazard records their passes as
        splits by its own rules, as for its secondary timers."""
        if not addresses:
            return
        if not hasattr(self._rhapi.race, "split_add"):
            self._splits_unsupported = True
            logger.warning("FPVGate: split gates need RotorHazard with split timers for plugins (RHAPI 1.10); "
                           "this one has RHAPI %s.%s", self._rhapi.API_VERSION_MAJOR, self._rhapi.API_VERSION_MINOR)
            return
        first = (self._rhapi.config.get(SECTION, "first_split", as_int=True) or 1) - 1
        distances = split_list(self._rhapi.config.get(SECTION, "split_distances"))
        callout = self._rhapi.config.get(SECTION, "split_callout")
        for i, address in enumerate(addresses):
            settings = {} if callout in (None, "auto") else {"callout": callout}
            if i < len(distances):
                try:
                    settings["distance"] = float(distances[i])
                except ValueError:
                    logger.warning("FPVGate: split distance '%s' isn't a number; no speed for split %d",
                                   distances[i], first + i + 1)
            if self._rhapi.race.split_timer_add(first + i, settings):
                self.splits.append(FPVGateSplitInterface(first + i, address, self.interface, self._record_split,
                                                         self._update_status))
            else:
                logger.warning("FPVGate: split gate %s not used: split %d is already taken", address, first + i + 1)
        if self.splits:
            logger.info("FPVGate: split gates %s",
                        ", ".join(f"{split.split_id + 1} at {split.address}" for split in self.splits))

    def _record_split(self, split, local_index, timestamp):
        # Node i of a split gate times the pilot on the finish gate's node i.
        finish_nodes = self.interface.nodes
        if local_index < len(finish_nodes) and finish_nodes[local_index].index >= 0:
            self._rhapi.race.split_add(finish_nodes[local_index].index, split.split_id, timestamp)

    #
    # Version 1 firmware: laps POSTed by the gate
    #

    def _legacy_blueprint(self):
        bp = Blueprint("fpvgate_legacy", __name__)
        rhapi = self._rhapi

        # The gate syncs its clock to time.monotonic(), the clock that
        # rhapi.race.start_time_internal uses.
        @bp.route("/fpvgate/time", methods=["GET"])
        def fpvgate_time():
            return jsonify({"monotonic_ms": int(monotonic() * 1000)}), 200

        @bp.route("/fpvgate/lap", methods=["POST"])
        def fpvgate_lap():
            if self._legacy_address != request.remote_addr:
                self._legacy_address = request.remote_addr
                gevent.spawn(self._update_status)
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400
            try:
                seat = int(data.get("node", 0))
            except (TypeError, ValueError):
                return jsonify({"status": "error", "message": "Invalid node value"}), 400
            if rhapi.race.status != RaceStatus.RACING:
                return jsonify({"status": "ignored", "reason": "race not running"}), 200

            race_start = rhapi.race.start_time_internal
            try:
                if data.get("timestampMs") is not None:
                    timestamp = int(data["timestampMs"]) / 1000.0
                elif data.get("raceTimeMs") is not None:
                    timestamp = race_start + int(data["raceTimeMs"]) / 1000.0
                else:
                    timestamp = monotonic()
            except (TypeError, ValueError):
                timestamp = monotonic()
            if timestamp < race_start:
                return jsonify({"status": "ignored", "reason": "timestamp before race start"}), 200

            # lap_add() pushes its own app context; run it outside this request's.
            gevent.spawn(self._legacy_lap, seat, timestamp)
            return jsonify({"status": "ok", "seat": seat, "timestamp_ms": int((timestamp - race_start) * 1000)}), 200

        return bp

    def _legacy_lap(self, seat, timestamp):
        try:
            self._rhapi.race.lap_add(seat, timestamp)
        except Exception:
            logger.exception("FPVGate: error recording lap for seat %d", seat)


def initialize(rhapi):
    logger.info("FPVGate plugin %s", PLUGIN_VERSION)
    FPVGateProvider(rhapi)
