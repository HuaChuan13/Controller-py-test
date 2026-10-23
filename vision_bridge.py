"""Local coral observations -> optional PKT suffix. No serial ownership or worker threads."""

from collections import deque
import json
import math
import secrets
import socket
import time


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("invalid vision number")
    return value


def integer(value, low=0):
    if type(value) is not int or not low <= value <= 0xffffffff:
        raise ValueError("invalid vision integer")
    return value


class VisionBridge:
    def __init__(self, args):
        self.args = args
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.bind(("127.0.0.1", args.vision_port))
            self.socket.setblocking(False)
        except OSError:
            self.socket.close()
            raise
        self.session = secrets.randbelow(0xffffffff) + 1
        self.token = 0
        self.sent = deque(maxlen=64)
        self.started = self.barrier = time.monotonic()
        self.ready_since = None
        self.ack = None
        self.ack_until = 0.
        self.packet = None
        self.source = None
        self.seen_selection = None
        self.active = False
        self.centered = False
        self.state = "WAIT_LINK"
        self.last_result = -1.
        self.last_observation = -1
        self.vision_session = None
        self.request_state, self.slew = 0, 0.
        self.last_reply = 0.
        self.sent_slew, self.sent_at = None, 0.

    def stop(self, reason, now):
        self.active = False
        self.centered = False
        self.request_state, self.slew = 0, 0.
        # Producer events are already ordered by observation_seq/lock_id. On Windows,
        # cancel and re-click can share a clock tick; only local faults need a time barrier.
        if reason not in ("CANCELLED", "TARGET_LOST", "NEW_VISION_SESSION"):
            self.barrier = max(self.barrier, now)
        self.state = reason

    def telemetry(self, lines, now):
        for line in lines:
            if line == "SYS{ready=1}":
                self.stop("RESTARTED", now)
                self.session, self.token = secrets.randbelow(0xffffffff) + 1, 0
                self.sent.clear()
                self.ack, self.ack_until, self.ready_since = None, 0., None
            if not line.startswith("VIS{") or not line.endswith("}"):
                continue
            try:
                fields = dict(item.split("=", 1) for item in line[4:-1].split())
                ack = {key: integer(int(fields[key])) for key in ("session", "token", "frame", "state", "mode", "pkt")}
                ack["slew"] = number(float(fields["slew"]), -5, 5)
                for key, low, high in (("yaw_sp", -180, 180), ("yaw_deg", -180, 180), ("lead_limit", .1, 180)):
                    ack[key] = number(float(fields[key]), low, high) if key in fields else None
                if ack["session"] != self.session or ack["state"] > 6 or ack["mode"] > 1:
                    continue
                sent_at = next((stamp for seq, stamp in reversed(self.sent) if seq == ack["pkt"]), None)
                if sent_at is None or not 0 <= now - sent_at < .2 or sent_at + .2 < self.ack_until:
                    continue
            except (ValueError, KeyError, TypeError):
                continue
            self.ack, self.ack_until = ack, sent_at + .2
            if ack["mode"] != 1:
                self.ready_since = None
                self.stop("OBSERVE_ONLY", now)
            elif self.ready_since is None:
                self.ready_since = now
                if not self.active and self.state in ("OBSERVE_ONLY", "WAIT_LINK", "NEW_VISION_SESSION"):
                    self.state = "CLICK_AGAIN"
            if self.active and ack["token"] == self.token and ack["state"] in (0, 4, 5, 6):
                self.stop({0: "CLICK_AGAIN", 4: "MANUAL_OVERRIDE", 5: "EXPIRED", 6: "MODE_REJECTED"}[ack["state"]], now)

    def receive(self, now, buttons):
        if now >= self.ack_until:
            if self.ack is not None:
                self.session, self.token = secrets.randbelow(0xffffffff) + 1, 0
                self.sent.clear()
                self.ack = None
            self.ready_since = None
            self.stop("WAIT_LINK", now)
        # Match actual firmware mapping: Circle/Square (bits 1/2), including both pressed.
        if buttons & ((1 << 1) | (1 << 2) | (1 << 10)):
            self.stop("MANUAL_OVERRIDE", now)
            self.ready_since = None
        for _ in range(16):
            try:
                data, source = self.socket.recvfrom(8193)
            except BlockingIOError:
                break
            except OSError:
                self.stop("UDP_ERROR", now)
                break
            if source[0] != "127.0.0.1" or len(data) > 8192:
                continue
            # The datagram may have been produced after this loop's original timestamp.
            now = max(now, time.monotonic())
            try:
                self.observe(json.loads(data), source, now)
            except (ValueError, KeyError, TypeError, UnicodeDecodeError, OverflowError):
                self.stop("INVALID_DATA", now)
        self.refresh(now)

    def observe(self, p, source, now):
        if p["schema"] != "coral.observation.v1" or p["coordinate_frame"] != "rectified_left":
            raise ValueError("schema")
        session = p["session"]
        if type(session) is not str or len(session) != 32 or any(c not in "0123456789abcdef" for c in session):
            raise ValueError("session")
        result = number(p["result_monotonic_s"], 0, now)
        seq, lock = integer(p["observation_seq"], 1), integer(p["lock_id"])
        if result < self.last_result or (session == self.vision_session and seq <= self.last_observation):
            return
        stamp = number(p["capture_monotonic_s"], 0, result)
        frame = integer(p["capture_seq"])
        lifetime = min(200., number(p["valid_for_ms"], 1, 10000)) / 1000
        if type(p["valid"]) is not bool or p["status"] not in (
                "idle", "tracking", "waiting", "lost", "stale", "ambiguous", "cancelled", "no_target", "stopped"):
            raise ValueError("state")
        if p["valid"] != (p["status"] == "tracking"):
            raise ValueError("valid/state mismatch")
        if self.packet and session == self.vision_session:
            if lock < self.packet["lock_id"]:
                return
            if frame < self.packet["capture_seq"]:
                return
            if frame == self.packet["capture_seq"] and stamp != self.packet["capture_monotonic_s"]:
                raise ValueError("repeated frame timestamp changed")
        if p["valid"]:
            number(p["smoothed_error_x"], -2, 2)
            integer(p["target"]["class_id"])
            number(p["target"]["confidence"], 0, 1)
        selection = p.get("selection_monotonic_s")
        if selection is not None:
            number(selection, 0, result)
        new_session = session != self.vision_session
        if new_session:
            self.stop("NEW_VISION_SESSION", result)
            # New session is allowed only through a subsequent fresh click, never its cached selection.
        self.vision_session, self.last_observation, self.last_result = session, seq, result
        self.packet, self.source = p, source
        p["deadline"] = stamp + lifetime
        key = (session, lock)
        if key != self.seen_selection:
            self.seen_selection = key
            self.active = False
            if (not new_session and p["valid"] and selection is not None and self.ready_since is not None
                    and self.barrier < selection <= now and self.ready_since <= selection
                    and now - selection < .2 and now < p["deadline"]):
                if self.token == 0xffffffff:
                    raise ValueError("restart Controller before token rollover")
                self.token += 1
                self.active = True
                self.centered = abs(p["smoothed_error_x"]) <= self.args.vision_deadband
                self.state = "AWAITING_TEENSY"
            else:
                self.state = "CLICK_AGAIN" if self.ready_since is not None else "OBSERVE_ONLY"
        if p["status"] == "cancelled" or self.active and p["status"] not in ("tracking", "waiting"):
            # A queued cancellation must not invalidate a click made later at the producer.
            self.stop("CANCELLED" if p["status"] == "cancelled" else "TARGET_LOST", result)
        if p["status"] == "waiting":
            last_seen = number(p["track_memory"]["last_seen_monotonic_s"], 0, stamp)
            remaining = number(p["track_memory"]["remaining_ms"], 0, 10000) / 1000
            p["deadline"] = min(p["deadline"], last_seen + .4, result + remaining)
        self.refresh(now)

    def refresh(self, now):
        if self.active and (now >= self.ack_until or not self.packet or now >= self.packet["deadline"]):
            self.stop("EXPIRED", now)
        self.request_state, self.slew = 0, 0.
        if not self.active:
            return
        if self.packet["status"] == "waiting":
            self.request_state, self.state = 2, "WAITING"
            return
        error = self.packet["smoothed_error_x"]
        if self.centered:
            self.centered = abs(error) <= self.args.vision_exit_deadband
        else:
            self.centered = abs(error) <= self.args.vision_deadband
        self.request_state = 3 if self.centered else 1
        if not self.centered:
            self.slew = self.args.vision_sign * math.copysign(
                min(self.args.vision_limit, self.args.vision_gain * max(0., abs(error) - self.args.vision_deadband)), error)
        self.state = "AWAITING_TEENSY"

    def suffix(self, sequence, now):
        self.refresh(now)
        # Reserve 20ms for local serial transit; firmware still owns an independent deadline.
        remaining = 0 if not self.active else math.floor(1000 * (self.packet["deadline"] - now)) - 20
        if self.active and remaining <= 0:
            self.stop("EXPIRED", now)
        age = max(0, 200 - remaining) if self.active else 0
        frame = self.packet["capture_seq"] if self.active else 0
        self.sent.append((sequence, now))
        self.sent_slew, self.sent_at = self.slew, now
        return f" V1 {self.session} {self.token} {frame} {age} {self.request_state} {self.slew:.4f}"

    def reply(self, now):
        if not self.source or not self.packet or now - self.last_reply < .05:
            return
        self.last_reply = now
        ack = self.ack
        fresh = ack is not None and now < self.ack_until
        confirmed = bool(self.active and ack and now < self.ack_until and ack["token"] == self.token
                         and ack["state"] == self.request_state and ack["frame"] <= self.packet["capture_seq"])
        status = {1: "ACTIVE", 2: "WAITING", 3: "CENTERED"}.get(self.request_state) if confirmed else self.state
        result = dict(schema="coral.control.v1", session=self.packet["session"], lock_id=self.packet["lock_id"],
                      capture_seq=self.packet["capture_seq"], status=status, confirmed=confirmed,
                      requested_slew_dps=self.sent_slew if 0 <= now - self.sent_at < .2 else None,
                      accepted_slew_dps=ack["slew"] if confirmed else
                          (0. if fresh and not self.active and ack["token"] == self.token
                           and ack["state"] in (0, 4, 5, 6) and ack["slew"] == 0 else None),
                      mode=("STABILIZE" if ack["mode"] == 1 else "MANUAL") if fresh else None,
                      yaw_target_deg=ack.get("yaw_sp") if fresh and ack["mode"] == 1 else None,
                      yaw_actual_deg=ack.get("yaw_deg") if fresh else None,
                      lead_limit_deg=ack.get("lead_limit") if fresh else None,
                      result_monotonic_s=now, valid_until_monotonic_s=min(now + .2, self.ack_until) if confirmed else now + .2)
        try:
            self.socket.sendto(json.dumps(result, allow_nan=False).encode(), self.source)
        except OSError:
            pass

    def close(self):
        self.socket.close()
