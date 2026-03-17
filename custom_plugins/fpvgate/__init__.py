"""
FPVGate RotorHazard Integration Plugin
=======================================
Bidirectional integration between FPVGate IR lap timers and RotorHazard.

FPVGate -> RH  (lap recording)
    FPVGate firmware POSTs to POST /fpvgate/lap with {"node": <seat>}.
    The plugin records the crossing as a lap via rhapi.race.lap_add().
    FPVGate's IP is auto-detected from incoming lap POSTs, or can be set
    explicitly in the FPVGate panel on the RH Settings page.

RH -> FPVGate  (race control)
    The plugin listens to RH race lifecycle events and POSTs to FPVGate:
      RACE_STAGE  -> /timer/clearLaps  + /timer/countdown  (arm + pre-race beeps)
      RACE_START  -> /timer/start                          (timer starts)
      RACE_FINISH -> /timer/stop                           (time-limited race ends)
      RACE_STOP   -> /timer/stop                           (manual stop)
      RACE_ABORT  -> /timer/stop                           (aborted during staging)
      LAPS_CLEAR  -> /timer/clearLaps                      (manual lap clear)

Installation:
    Copy the 'fpvgate' directory into your RotorHazard data 'plugins/' folder.
    (e.g. ~/rh-data/plugins/fpvgate/ on Windows, ~/rh-data/plugins/fpvgate/ on Pi)
    Restart the RotorHazard server.
"""

import logging
import urllib.request
from time import monotonic

import gevent
from flask import request, jsonify
from FlaskAppObj import APP
from RHUI import UIField, UIFieldType
from RHRace import RaceStatus
from eventmanager import Evt

logger = logging.getLogger(__name__)

PLUGIN_VERSION = "1.1.0"
FPVGATE_PORT = 80
FPVGATE_HTTP_TIMEOUT = 2.0  # seconds - event handlers are async so this is safe

# Auto-detected IP of the most recently connected FPVGate.
# Overridden by the 'fpvgate_host_ip' RH setting if configured.
_fpvgate_ip = None

# Stored rhapi reference so event handlers can read the configured IP.
_rhapi = None

OPT_HOST_IP = "fpvgate_host_ip"


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def initialize(rhapi):
    global _rhapi
    _rhapi = rhapi
    logger.info("FPVGate plugin v%s initialized", PLUGIN_VERSION)

    # Settings panel on the RH Settings page
    rhapi.ui.register_panel("fpvgate", "FPVGate", "settings", order=0)
    rhapi.fields.register_option(
        UIField(
            name=OPT_HOST_IP,
            label="FPVGate IP Address",
            field_type=UIFieldType.TEXT,
            value="",
            desc=(
                "IP address of FPVGate (e.g. 192.168.0.158). "
                "Leave blank to use auto-detection from incoming lap posts."
            ),
            placeholder="e.g. 192.168.0.158",
        ),
        panel="fpvgate",
    )

    # FPVGate -> RH: clock sync endpoint
    # FPVGate GETs this periodically to compute its clock offset against RH's
    # monotonic clock.  The response is {"monotonic_ms": <int>} where the value
    # is Python's time.monotonic() * 1000 -- the same time base that
    # rhapi.race.start_time_internal uses.
    @APP.route("/fpvgate/time", methods=["GET"])
    def fpvgate_time():
        return jsonify({"monotonic_ms": int(monotonic() * 1000)}), 200

    # FPVGate -> RH: lap recording endpoint
    @APP.route("/fpvgate/lap", methods=["POST"])
    def fpvgate_record_lap():
        global _fpvgate_ip
        detected = request.remote_addr
        # Only update auto-detected IP if no static IP is configured
        configured = (rhapi.db.option(OPT_HOST_IP) or "").strip()
        if not configured:
            _fpvgate_ip = detected
            logger.debug("[FPVGate] IP auto-detected: %s", _fpvgate_ip)
        else:
            logger.debug("[FPVGate] Lap from %s (using configured IP %s)", detected, configured)

        # Parse request data while still in the Flask request context
        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

        try:
            seat_index = int(data.get("node", 0))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid node value"}), 400

        if rhapi.race.status != RaceStatus.RACING:
            logger.info(
                "[FPVGate] Lap for seat %d ignored - race not running (status=%s)",
                seat_index, rhapi.race.status,
            )
            return jsonify({"status": "ignored", "reason": "race not running"}), 200

        # Resolve timestamp now, before leaving request context.
        # Prefer timestampMs (absolute RH monotonic ms, sent when FPVGate has
        # a synced clock offset).  Fall back to raceTimeMs + race_start for
        # firmware that has not yet performed a clock sync.
        timestamp_ms = data.get("timestampMs")
        race_time_ms = data.get("raceTimeMs")
        race_start   = rhapi.race.start_time_internal  # absolute monotonic seconds
        if timestamp_ms is not None:
            try:
                timestamp_abs = int(timestamp_ms) / 1000.0
            except (TypeError, ValueError):
                timestamp_abs = monotonic()
        elif race_time_ms is not None:
            try:
                timestamp_abs = race_start + int(race_time_ms) / 1000.0
            except (TypeError, ValueError):
                timestamp_abs = monotonic()
        else:
            timestamp_abs = monotonic()

        if timestamp_abs < race_start:
            logger.warning(
                "[FPVGate] Timestamp before race start for seat %d - ignoring",
                seat_index,
            )
            return jsonify({"status": "ignored", "reason": "timestamp before race start"}), 200

        # Spawn lap recording outside the Flask request context so that
        # add_lap()'s internal APP.app_context().push() doesn't conflict.
        gevent.spawn(_record_lap, rhapi, seat_index, timestamp_abs)
        elapsed_ms = int((timestamp_abs - race_start) * 1000)
        return jsonify({"status": "ok", "seat": seat_index, "timestamp_ms": elapsed_ms}), 200

    # RH -> FPVGate: race lifecycle events (priority=200 = async, non-blocking)
    rhapi.events.on(Evt.RACE_STAGE,  _on_race_stage,  name="fpvgate_stage",  priority=200)
    rhapi.events.on(Evt.RACE_START,  _on_race_start,  name="fpvgate_start",  priority=200)
    rhapi.events.on(Evt.RACE_FINISH, _on_race_stop,   name="fpvgate_finish", priority=200)
    rhapi.events.on(Evt.RACE_STOP,   _on_race_stop,   name="fpvgate_stop",   priority=200)
    rhapi.events.on(Evt.RACE_ABORT,  _on_race_stop,   name="fpvgate_abort",  priority=200)
    rhapi.events.on(Evt.LAPS_CLEAR,  _on_laps_clear,  name="fpvgate_clear",  priority=200)


# ---------------------------------------------------------------------------
# RH -> FPVGate: outbound race control
# ---------------------------------------------------------------------------

def _get_fpvgate_ip():
    """Return the FPVGate IP: configured static IP first, then auto-detected."""
    if _rhapi:
        configured = (_rhapi.db.option(OPT_HOST_IP) or "").strip()
        if configured:
            return configured
    return _fpvgate_ip


def _post_to_fpvgate(path):
    """Fire-and-forget HTTP POST to FPVGate (static or auto-detected IP)."""
    ip = _get_fpvgate_ip()
    if not ip:
        logger.debug("[FPVGate] No IP known yet, skipping POST %s", path)
        return
    url = f"http://{ip}:{FPVGATE_PORT}{path}"
    try:
        req = urllib.request.Request(
            url,
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=FPVGATE_HTTP_TIMEOUT) as resp:
            logger.info("[FPVGate] POST %s -> HTTP %d", path, resp.status)
    except Exception as exc:
        logger.warning("[FPVGate] POST %s failed: %s", path, exc)


def _on_race_stage(args):
    _post_to_fpvgate("/timer/clearLaps")
    _post_to_fpvgate("/timer/countdown")


def _on_race_start(args):
    _post_to_fpvgate("/timer/start")


def _on_race_stop(args):
    _post_to_fpvgate("/timer/stop")


def _on_laps_clear(args):
    _post_to_fpvgate("/timer/clearLaps")


# ---------------------------------------------------------------------------
# FPVGate -> RH: inbound lap recording
# ---------------------------------------------------------------------------

def _record_lap(rhapi, seat_index, timestamp_abs):
    """Record a lap in a greenlet, outside any Flask request context.

    add_lap() internally calls APP.app_context().push(). Running it here
    (in a spawned greenlet with no existing context) avoids the
    'Popped wrong app context' AssertionError that occurs when the same
    call is made directly from within a Flask route handler.

    timestamp_abs is an absolute monotonic time (seconds since boot),
    as expected by RHRace.add_lap().
    """
    try:
        rhapi.race.lap_add(seat_index, timestamp_abs)
        logger.info(
            "[FPVGate] Lap recorded: seat=%d, abs_ts=%.3f",
            seat_index, timestamp_abs,
        )
    except Exception:
        logger.exception("[FPVGate] Error recording lap for seat %d", seat_index)
