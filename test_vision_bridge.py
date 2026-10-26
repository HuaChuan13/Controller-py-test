"""Deterministic control gates plus real localhost UDP; no device writes."""
import json
import socket
import unittest
from unittest.mock import patch

from ds5_teensy_demo import build_argument_parser
from vision_bridge import VisionBridge


def observation(now, lock=1, frame=1, status="tracking", error=.25, selection=10.03):
    return dict(schema="coral.observation.v1", coordinate_frame="rectified_left", session="a" * 32,
                observation_seq=frame + 1, capture_seq=frame, lock_id=lock,
                capture_monotonic_s=now - .01, result_monotonic_s=now, valid_for_ms=200,
                selection_monotonic_s=selection, valid=status == "tracking", status=status,
                target=dict(class_id=0, confidence=.8), smoothed_error_x=error,
                track_memory=dict(last_seen_monotonic_s=now - .05, remaining_ms=350))


def acknowledge(bridge, now, seq, state=0, mode=1, token=None, frame=0):
    token = bridge.token if token is None else token
    bridge.telemetry([f"VIS{{session={bridge.session} token={token} frame={frame} state={state} mode={mode} pkt={seq} slew=-2.100}}"], now)


class VisionTests(unittest.TestCase):
    def setUp(self):
        clock_patch = patch("vision_bridge.time.monotonic", return_value=10.)
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        args = build_argument_parser().parse_args(["--vision", "--vision-port", "0"])
        with patch("vision_bridge.time.monotonic", return_value=10.):
            self.b = VisionBridge(args)
        self.addCleanup(self.b.close)
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sender.bind(("127.0.0.1", 0))
        self.sender.settimeout(.2)
        self.addCleanup(self.sender.close)
        self.source = self.sender.getsockname()
        self.b.suffix(1, 10.)
        acknowledge(self.b, 10.01, 1)
        self.b.observe(observation(10.02, lock=0, frame=0, status="idle", selection=None), self.source, 10.02)

    def start(self):
        self.b.observe(observation(10.04), self.source, 10.04)
        self.assertTrue(self.b.active)
        suffix = self.b.suffix(2, 10.04)
        self.assertTrue(suffix.endswith("1 -2.1000"))
        acknowledge(self.b, 10.05, 2, state=1, frame=1)

    def test_udp_click_wait_recover_cancel_and_feedback(self):
        p = observation(10.04)
        self.sender.sendto(json.dumps(p).encode(), self.b.socket.getsockname())
        self.b.receive(10.04, 0)
        self.assertTrue(self.b.active)
        self.b.suffix(2, 10.04)
        acknowledge(self.b, 10.05, 2, state=1, frame=1)
        self.b.reply(10.06)
        result = json.loads(self.sender.recv(4096))
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual(result["accepted_slew_dps"], -2.1)
        self.b.observe(observation(10.08, frame=2, status="waiting"), self.source, 10.08)
        self.assertTrue(self.b.active)
        self.assertEqual((self.b.request_state, self.b.slew), (2, 0))
        self.b.observe(observation(10.12, frame=3), self.source, 10.12)
        self.assertEqual((self.b.token, self.b.request_state), (1, 1))
        self.b.observe(observation(10.13, frame=4, status="cancelled", selection=None), self.source, 10.13)
        self.assertFalse(self.b.active)
        self.b.suffix(3, 10.14)
        self.b.telemetry([f"VIS{{session={self.b.session} token={self.b.token} frame=3 state=0 mode=1 pkt=3 slew=0 yaw_sp=12 yaw_deg=12 lead_limit=15}}"], 10.15)
        self.b.reply(10.16)
        stopped = json.loads(self.sender.recv(4096))
        self.assertEqual((stopped["requested_slew_dps"], stopped["accepted_slew_dps"]), (0, 0))
        self.assertEqual(stopped["lead_limit_deg"], 15)

    def test_manual_rejects_cached_selection_but_new_click_works(self):
        self.start()
        self.b.receive(10.06, 1 << 1)
        self.assertFalse(self.b.active)
        self.b.suffix(3, 10.07)
        acknowledge(self.b, 10.08, 3)
        self.b.observe(observation(10.09, frame=2), self.source, 10.09)
        self.assertFalse(self.b.active)
        self.b.observe(observation(10.12, lock=2, frame=3, selection=10.11), self.source, 10.12)
        self.assertTrue(self.b.active)
        self.assertEqual(self.b.token, 2)

    def test_manual_mode_click_does_not_arm_later(self):
        acknowledge(self.b, 10.025, 1, mode=0)
        self.b.observe(observation(10.04), self.source, 10.04)
        self.assertFalse(self.b.active)
        self.b.suffix(2, 10.05)
        acknowledge(self.b, 10.06, 2, mode=1)
        self.b.observe(observation(10.07, frame=2), self.source, 10.07)
        self.assertFalse(self.b.active)

    def test_repeated_frame_never_renews_deadline(self):
        self.start()
        p = observation(10.18)
        p.update(observation_seq=10, capture_monotonic_s=10.03)
        self.b.observe(p, self.source, 10.18)
        self.b.suffix(3, 10.18)
        acknowledge(self.b, 10.19, 3, state=1, frame=1)
        self.b.suffix(4, 10.24)
        self.assertFalse(self.b.active)
        self.b.observe(observation(10.25, frame=20), self.source, 10.25)
        self.assertFalse(self.b.active)

    def test_bad_data_order_and_hysteresis(self):
        self.start()
        self.b.observe(observation(10.08, frame=2, error=.03), self.source, 10.08)
        self.assertEqual(self.b.request_state, 3)
        self.b.observe(observation(10.1, frame=3, error=.05), self.source, 10.1)
        self.assertEqual(self.b.request_state, 3)
        self.b.observe(observation(10.12, frame=4, error=.07), self.source, 10.12)
        self.assertEqual(self.b.request_state, 1)
        self.b.observe(observation(10.08, frame=2, error=-1), self.source, 10.13)
        self.assertGreater(self.b.packet["smoothed_error_x"], 0)
        for value in (float("nan"), float("inf"), True, "0", 3):
            with self.assertRaises(ValueError):
                self.b.observe(observation(10.14, frame=5, error=value), self.source, 10.14)

    def test_expired_feedback_restart_and_firmware_rejection(self):
        self.start()
        self.b.suffix(3, 10.06)
        acknowledge(self.b, 10.07, 3, state=4)
        self.assertFalse(self.b.active)
        self.b.telemetry(["SYS{ready=1}"], 10.08)
        self.assertEqual(self.b.token, 0)
        self.assertFalse(self.b.sent)
        self.assertTrue(self.b.suffix(4, 10.09).endswith("0 0 0 0 0.0000"))
        acknowledge(self.b, 10.4, 4, mode=1)
        self.assertIsNone(self.b.ready_since)

    def test_queued_idle_or_cancel_does_not_block_a_later_click(self):
        self.b.observe(observation(10.025, lock=0, frame=1, status="idle", selection=None), self.source, 10.04)
        self.b.observe(observation(10.035, frame=2, selection=10.03), self.source, 10.04)
        self.assertTrue(self.b.active, "idle and click in same receive batch")
        self.b.observe(observation(10.05, frame=3, status="cancelled", selection=None), self.source, 10.07)
        self.b.observe(observation(10.065, lock=2, frame=4, selection=10.06), self.source, 10.07)
        self.assertTrue(self.b.active, "cancel and new click in same receive batch")
        self.b.observe(observation(10.08, lock=2, frame=5, status="cancelled", selection=None), self.source, 10.09)
        self.b.observe(observation(10.08, lock=3, frame=6, selection=10.08), self.source, 10.09)
        self.assertTrue(self.b.active, "same clock tick must use producer ordering")
        self.b.stop("EXPIRED", 10.1)
        self.b.observe(observation(10.1, lock=4, frame=7, selection=10.1), self.source, 10.11)
        self.assertFalse(self.b.active, "local fault barrier must remain strict")

    def test_datagram_created_during_receive_is_not_future_data(self):
        self.clock.return_value = 10.045
        self.sender.sendto(json.dumps(observation(10.044)).encode(), self.b.socket.getsockname())
        self.b.receive(10.04, 0)
        self.assertTrue(self.b.active)

    def test_failure_reason_survives_later_idle_observations(self):
        self.start()
        self.b.stop("EXPIRED", 10.1)
        self.b.observe(observation(10.12, frame=2, status="stale", selection=None), self.source, 10.12)
        self.assertEqual(self.b.state, "EXPIRED")


if __name__ == "__main__":
    unittest.main()
