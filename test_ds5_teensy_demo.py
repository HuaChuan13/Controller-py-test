import io
import socket
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ds5_teensy_demo import (
    DISPLAY_TAGS,
    VofaYawSender,
    build_argument_parser,
    build_panel_lines,
    build_vofa_yaw_frame,
    decode_gateway_line,
    main,
    read_available_gateway_lines,
    run_serial_demo,
    update_panel_state,
)


class FakeSocket:
    def __init__(self, payloads: list[bytes]) -> None:
        self.payloads = list(payloads)

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        del size
        if not self.payloads:
            raise BlockingIOError
        return self.payloads.pop(0), ("192.168.10.2", 14550)


class GatewayLineTests(unittest.TestCase):
    def test_decodes_single_ascii_line(self) -> None:
        self.assertEqual(decode_gateway_line(b"SYS{ready=1}"), "SYS{ready=1}")

    def test_rejects_multiline_payload(self) -> None:
        with self.assertRaises(ValueError):
            decode_gateway_line(b"SYS{ready=1}\nPWM{m1=1500}")

    def test_receive_budget_is_bounded(self) -> None:
        sock = FakeSocket([b"SYS{a=1}", b"SYS{a=2}"])
        lines = read_available_gateway_lines(sock, max_datagrams=1)  # type: ignore[arg-type]
        self.assertEqual(lines, ["SYS{a=1}"])


class ImuPanelTests(unittest.TestCase):
    def test_only_imu_display_changes_and_raw_data_stays_unchanged(self) -> None:
        received = [f"{tag}{{raw=+12.345 unknown=unchanged}}" for tag in DISPLAY_TAGS]
        received[-1] = "IMU{temp_c=26 ax_mps2=-0.066 yaw_deg=0.84}"
        received += ["GATEWAY{teensy_connected=1}", "INFO{unchanged}"]
        state: dict[str, str] = {}
        log = io.StringIO()
        update_panel_state(received, state, log)
        original = dict(state)
        rows = build_panel_lines(state, 50, 50)
        self.assertEqual(rows[2:10], received[:8])
        self.assertEqual(rows[-2:], received[-2:])
        self.assertEqual(state, original)
        self.assertEqual(log.getvalue(), "".join(f"[Teensy] {line}\n" for line in received))

    def test_imu_columns_stay_fixed_for_changed_missing_and_invalid_values(self) -> None:
        frames = (
            "IMU{waiting}",
            "IMU{temp_c=26 ax_mps2=-0.066 ay_mps2=0.043 az_mps2=9.781 "
            "gx_dps=-0.003 gy_dps=-0.020 gz_dps=-0.012 "
            "roll_deg=0.38 pitch_deg=0.25 yaw_deg=0.84}",
            "IMU{temp_c=-5 ax_mps2=100 ay_mps2=-12 az_mps2=0 "
            "gx_dps=999 gy_dps=-999 gz_dps=0 "
            "roll_deg=100 pitch_deg=-90 yaw_deg=-179.99}",
            "IMU{temp_c=nan ax_mps2=broken gx_dps=inf yaw_deg=1e30}",
        )
        for frame in frames:
            rows = build_panel_lines({"IMU": frame}, 50, 50)[-4:]
            self.assertEqual([len(row) for row in rows], [24, 64, 64, 64])
            self.assertEqual([row[:3] for row in rows], ["IMU", "ACC", "GYR", "ANG"])
            for row in rows[1:]:
                self.assertEqual([index for index, char in enumerate(row) if char == "="],
                                 [13, 33, 53])
        self.assertIn("---", build_panel_lines({}, 50, 50)[-1])
        rows = build_panel_lines({"IMU": frames[-1]}, 50, 50)[-4:]
        self.assertIn("ERR", rows[0])
        self.assertIn("ERR", rows[1])
        self.assertIn("ERR", rows[2])
        self.assertIn("OVF", rows[3])


class VofaTests(unittest.TestCase):
    lines = [
        "CTRL{mode=STABILIZE action=FWD+CW yaw=-0.08 yaw_sp=110.09 rate_sp=-4.00}",
        "SYS{pkt_alive=1 imu_alive=1}",
        "IMU{temp_c=31 yaw_deg=112.09 gz_dps=-0.031}",
    ]
    frame = b"yaw:110.09,112.09,-4,-0.031\n"

    def test_four_channels_and_invalid_snapshots(self) -> None:
        snapshot = dict(zip(("CTRL", "SYS", "IMU"), self.lines))
        self.assertEqual(build_vofa_yaw_frame(snapshot), self.frame)
        bad_lines = {
            "CTRL": (
                self.lines[0].replace("STABILIZE", "MANUAL"),
                "CTRL{mode=STABILIZE rate_sp=1}",
                self.lines[0][:-1],
                "CTRL{mode=STABILIZE yaw_sp=nan rate_sp=1}",
            ),
            "SYS": ("SYS{imu_alive=0}", "SYS{ready=1}"),
            "IMU": (
                "IMU{yaw_deg=1}",
                "IMU{yaw_deg=1 gz_dps=inf}",
                "IMU{yaw_deg=1 gz_dps=bad}",
                "IMU{yaw_deg=1 gz_dps=2 broken}",
            ),
        }
        for tag, lines in bad_lines.items():
            for line in lines:
                with self.subTest(line=line):
                    self.assertIsNone(build_vofa_yaw_frame({**snapshot, tag: line}))
            self.assertIsNone(build_vofa_yaw_frame({k: v for k, v in snapshot.items() if k != tag}))

    def test_split_snapshot_does_not_mix_old_values(self) -> None:
        with patch("ds5_teensy_demo.socket.socket") as factory:
            sender = VofaYawSender(1347)
        sock = factory.return_value
        sock.setblocking.assert_called_once_with(False)
        sender.update(self.lines[:2], 0.0)
        sock.sendto.assert_not_called()
        sender.update(["DS5{buttons=0}", self.lines[2]], 0.01)
        sock.sendto.assert_called_once_with(self.frame, ("127.0.0.1", 1347))
        sender.update([self.lines[2]], 0.04)
        sender.update([self.lines[0], self.lines[2]], 0.06)
        self.assertEqual(sock.sendto.call_count, 1)
        sender.close()
        sock.close.assert_called_once()

    def test_rate_limit_keeps_only_latest_frame_and_never_replays(self) -> None:
        with patch("ds5_teensy_demo.socket.socket") as factory:
            sender = VofaYawSender(1347)
        sock = factory.return_value
        sender.update(self.lines, 0.0)
        sender.update(self.lines, 0.005)
        latest = self.lines[:-1] + ["IMU{yaw_deg=113 gz_dps=2}"]
        sender.update(latest, 0.01)
        self.assertEqual(sock.sendto.call_count, 1)
        sender.update([], 0.021)
        self.assertEqual(sock.sendto.call_count, 2)
        self.assertEqual(sock.sendto.call_args.args[0], b"yaw:110.09,113,-4,2\n")
        sender.update([], 1.0)
        self.assertEqual(sock.sendto.call_count, 2)

    def test_pending_frame_is_dropped_on_reset_invalid_data_or_expiry(self) -> None:
        for replacement, now in (
            (["SYS{ready=1}"], 0.03),
            (["CTRL{mode=MANUAL}"], 0.03),
            (self.lines[:-1] + ["IMU{yaw_deg=nan gz_dps=0}"], 0.03),
            ([], 0.2),
        ):
            with self.subTest(replacement=replacement):
                with patch("ds5_teensy_demo.socket.socket") as factory:
                    sender = VofaYawSender(1347)
                sender.update(self.lines, 0.0)
                sender.update(self.lines, 0.005)
                sender.update(replacement, now)
                sender.update([], 1.0)
                self.assertEqual(factory.return_value.sendto.call_count, 1)

    def test_udp_loopback_receives_firewater_frame(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(1.0)
            sender = VofaYawSender(receiver.getsockname()[1])
            try:
                sender.update(self.lines, 0.0)
                payload, _ = receiver.recvfrom(1024)
                self.assertEqual(payload, self.frame)
                self.assertEqual(len(payload.split(b":")[1].split(b",")), 4)
            finally:
                sender.close()

    def test_cli_defaults_and_invalid_options_before_hardware_access(self) -> None:
        args = build_argument_parser().parse_args([])
        self.assertFalse(args.vofa)
        self.assertEqual(args.vofa_port, 1347)
        for options in (
            ["--vofa-port", "0"],
            ["--vofa-port", "65536"],
            ["--vofa", "--mode", "gateway"],
            ["--vofa", "--ds5-debug"],
        ):
            with self.subTest(options=options), patch("ds5_teensy_demo.sys.argv", ["demo"] + options):
                with patch("ds5_teensy_demo.Ds5Reader") as reader, redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        main()
                    self.assertEqual(error.exception.code, 2)
                    reader.assert_not_called()

    def test_udp_failure_and_disabled_forwarding_preserve_control_packets(self) -> None:
        for enabled, socket_error in ((False, False), (True, False), (True, True)):
            with self.subTest(enabled=enabled, socket_error=socket_error):
                options = ["--vofa"] if enabled else []
                args = build_argument_parser().parse_args(options)
                reader = Mock()
                reader.read.return_value = SimpleNamespace(
                    lx=0.1, ly=-0.2, rx=0.3, ry=-0.4, l2=0.5, r2=0.6, buttons=8
                )
                port = Mock()
                with (
                    patch("ds5_teensy_demo.open_serial_port", return_value=port),
                    patch("ds5_teensy_demo.read_available_lines", side_effect=[
                        (self.lines, ""), (self.lines, ""), KeyboardInterrupt
                    ]),
                    patch("ds5_teensy_demo.time.time", side_effect=[1.0, 1.021, 1.042]),
                    patch("ds5_teensy_demo.time.monotonic", side_effect=[0.0, 0.021]),
                    patch("ds5_teensy_demo.time.sleep"),
                    patch("ds5_teensy_demo.render_panel"),
                    patch("ds5_teensy_demo.socket.socket") as factory,
                    redirect_stdout(io.StringIO()),
                ):
                    factory.return_value.sendto.side_effect = BlockingIOError
                    if socket_error:
                        factory.side_effect = OSError("UDP unavailable")
                    self.assertEqual(run_serial_demo(args, reader, None), 0)
                self.assertEqual([call.args[0] for call in port.write.call_args_list], [
                    b"PKT 1 0.100 -0.200 0.300 -0.400 0.500 0.600 8\n",
                    b"PKT 2 0.100 -0.200 0.300 -0.400 0.500 0.600 8\n",
                ])
                port.close.assert_called_once()
                if enabled and not socket_error:
                    self.assertEqual(factory.return_value.sendto.call_count, 2)
                    factory.return_value.close.assert_called_once()
                elif socket_error:
                    factory.assert_called_once()
                    factory.return_value.sendto.assert_not_called()
                else:
                    factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
