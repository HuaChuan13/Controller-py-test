import io
import select
import socket
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

import serial

from ds5_teensy_demo import (
    DISPLAY_TAGS,
    VofaBridge,
    build_argument_parser,
    build_panel_lines,
    build_vofa_yaw_frame,
    decode_gateway_line,
    main,
    normalize_vofa_command,
    read_available_gateway_lines,
    read_available_lines,
    run_serial_demo,
    update_panel_state,
    write_serial_data,
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
        imu_index = DISPLAY_TAGS.index("IMU")
        received[imu_index] = "IMU{temp_c=26 ax_mps2=-0.066 yaw_deg=0.84}"
        received += ["GATEWAY{teensy_connected=1}", "INFO{unchanged}"]
        state: dict[str, str] = {}
        log = io.StringIO()
        update_panel_state(received, state, log)
        original = dict(state)
        rows = build_panel_lines(state, 50, 50)
        self.assertEqual(rows[2:2 + imu_index], received[:imu_index])
        self.assertEqual(rows[2 + imu_index + 4], state["PID"])
        self.assertEqual(rows[-2:], received[-2:])
        self.assertEqual(state, original)
        self.assertEqual(log.getvalue(), "".join(f"[Teensy] {line}\n" for line in received))

    def test_imu_columns_stay_fixed_for_changed_missing_and_invalid_values(self) -> None:
        imu_index = 2 + DISPLAY_TAGS.index("IMU")
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
            rows = build_panel_lines({"IMU": frame}, 50, 50)[imu_index:imu_index + 4]
            self.assertEqual([len(row) for row in rows], [24, 64, 64, 64])
            self.assertEqual([row[:3] for row in rows], ["IMU", "ACC", "GYR", "ANG"])
            for row in rows[1:]:
                self.assertEqual([index for index, char in enumerate(row) if char == "="],
                                 [13, 33, 53])
        self.assertIn("---", build_panel_lines({}, 50, 50)[imu_index + 3])
        rows = build_panel_lines({"IMU": frames[-1]}, 50, 50)[imu_index:imu_index + 4]
        self.assertIn("ERR", rows[0])
        self.assertIn("ERR", rows[1])
        self.assertIn("ERR", rows[2])
        self.assertIn("OVF", rows[3])


class VofaTests(unittest.TestCase):
    line = "PID{yaw_sp=110.09 yaw_deg=112.09 rate_sp=-4.00 gz_dps=-0.031}"
    frame = b"yaw:110.09,112.09,-4,-0.031\n"

    def test_four_channels_and_invalid_lines(self) -> None:
        self.assertEqual(build_vofa_yaw_frame(self.line), self.frame)
        for line in (
            "CTRL{mode=STABILIZE yaw_sp=110 rate_sp=-4}",
            "SYS{imu_alive=1}", "IMU{yaw_deg=112 gz_dps=0}",
            self.line[:-1], self.line.replace("yaw_sp=110.09 ", ""),
            self.line.replace("110.09", "nan"), self.line.replace("-4.00", "inf"),
            self.line.replace("112.09", "broken"), self.line.replace("}", " broken}"),
        ):
            with self.subTest(line=line):
                self.assertIsNone(build_vofa_yaw_frame(line))

    def test_split_serial_line_is_forwarded_only_when_complete(self) -> None:
        with patch("ds5_teensy_demo.socket.socket") as factory:
            sender = VofaBridge(1347)
        sock = factory.return_value
        sock.bind.assert_called_once_with(("127.0.0.1", 1346))
        sock.setblocking.assert_called_once_with(False)
        port = Mock(in_waiting=100)
        port.read.side_effect = [self.line[:20].encode(), (self.line[20:] + "\r\n").encode()]
        lines, buffer = read_available_lines(port, "")
        sender.update(lines)
        sock.sendto.assert_not_called()
        lines, buffer = read_available_lines(port, buffer)
        self.assertEqual(buffer, "")
        sender.update(lines)
        sock.sendto.assert_called_once_with(self.frame, ("127.0.0.1", 1347))
        sender.update([])
        self.assertEqual(sock.sendto.call_count, 1)
        sender.close()
        sock.close.assert_called_once()

    def test_200_pid_samples_survive_50_full_snapshots_without_coalescing(self) -> None:
        rows = []
        for i in range(200):
            rows.append(self.line.replace("112.09", str(i)))
            if i % 4 == 0:
                rows.extend(f"{tag}{{sample={i}}}" for tag in DISPLAY_TAGS if tag not in ("PID", "PARAM"))
        stream = io.BytesIO(("\n".join(rows) + "\n").encode())
        port = Mock()
        port.read.side_effect = stream.read
        panel = {}
        buffer = ""
        with patch("ds5_teensy_demo.socket.socket") as factory:
            sender = VofaBridge(1347)
        while stream.tell() < len(stream.getvalue()):
            port.in_waiting = len(stream.getvalue()) - stream.tell()
            lines, buffer = read_available_lines(port, buffer)
            update_panel_state(lines, panel, None)
            sender.update(lines)
        frames = [call.args[0] for call in factory.return_value.sendto.call_args_list]
        self.assertEqual(frames, [f"yaw:110.09,{i},-4,-0.031\n".encode() for i in range(200)])
        self.assertEqual(panel["SYS"], "SYS{sample=196}")
        self.assertTrue(all(call.args[0] <= 4096 for call in port.read.call_args_list))
        latest = self.line.replace("112.09", "199")
        self.assertEqual(build_panel_lines(panel, 200, 50)[-2], latest)
        self.assertEqual(build_panel_lines(panel, 200, 50)[-1], "PARAM{waiting}")
        self.assertIn("PID{waiting}", build_panel_lines({}, 200, 50))
        update_panel_state([], panel, None)
        self.assertIn(latest, build_panel_lines(panel, 200, 50))
        sender.update([])
        self.assertEqual(factory.return_value.sendto.call_count, 200)

    def test_serial_budget_bounds_unterminated_input(self) -> None:
        port = Mock(in_waiting=8192)
        port.read.return_value = b"x" * 4096
        lines, buffer = read_available_lines(port, "")
        self.assertEqual(lines, [])
        self.assertEqual(len(buffer), 4096)
        lines, buffer = read_available_lines(port, buffer)
        self.assertEqual((lines, buffer), ([], None))
        port.in_waiting = 0
        self.assertEqual(read_available_lines(port, buffer), ([], None))
        port.in_waiting = 4096
        port.read.return_value = self.line.encode()
        lines, buffer = read_available_lines(port, buffer)
        self.assertEqual((lines, buffer), ([], None))
        port.read.return_value = ("\n" + self.line + "\n").encode()
        lines, buffer = read_available_lines(port, buffer)
        self.assertEqual((lines, buffer), ([self.line], ""))

    def test_complete_serial_lines_obey_length_limit(self) -> None:
        for length in (4095, 4096, 4097, 5042):
            with self.subTest(length=length):
                oversized = self.line[:-1] + " " * (length - len(self.line)) + "}"
                stream = io.BytesIO((oversized + "\r\n" + self.line + "\r\n").encode())
                port = Mock()
                port.read.side_effect = stream.read
                buffer = ""
                received = []
                while stream.tell() < len(stream.getvalue()):
                    port.in_waiting = len(stream.getvalue()) - stream.tell()
                    rows, buffer = read_available_lines(port, buffer)
                    received.extend(rows)
                # CR before LF counts toward the raw line length limit.
                expected = [oversized, self.line] if length + 1 <= 4096 else [self.line]
                self.assertEqual(received, expected)
                self.assertEqual(buffer, "")

    def test_overlong_record_tail_never_reaches_panel_log_or_vofa(self) -> None:
        valid = self.line.replace("112.09", "113")
        stream = io.BytesIO(b"x" * 8192 + (self.line + "\r\n" + valid + "\r\n").encode())
        port = Mock()
        port.read.side_effect = stream.read
        buffer = ""
        panel = {}
        log = io.StringIO()
        with patch("ds5_teensy_demo.socket.socket") as factory:
            sender = VofaBridge(1347)
        while stream.tell() < len(stream.getvalue()):
            port.in_waiting = len(stream.getvalue()) - stream.tell()
            rows, buffer = read_available_lines(port, buffer)
            update_panel_state(rows, panel, log)
            sender.update(rows)
        self.assertEqual(panel, {"PID": valid})
        self.assertEqual(log.getvalue(), f"[Teensy] {valid}\n")
        factory.return_value.sendto.assert_called_once_with(
            build_vofa_yaw_frame(valid), ("127.0.0.1", 1347)
        )
        self.assertEqual(buffer, "")
        sender.close()

    def test_udp_loopback_receives_all_four_samples_per_batch(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(1.0)
            sender = VofaBridge(receiver.getsockname()[1], command_port=0)
            try:
                for batch in range(50):
                    lines = [self.line.replace("112.09", str(batch * 4 + i)) for i in range(4)]
                    sender.update(lines)
                    for line in lines:
                        payload, _ = receiver.recvfrom(1024)
                        self.assertEqual(payload, build_vofa_yaw_frame(line))
                        self.assertEqual(len(payload.split(b":")[1].split(b",")), 4)
            finally:
                sender.close()

    def test_yaw_command_whitelist_ranges_and_line_framing(self) -> None:
        for gain, maximum in (("ANGLE_KP", 10), ("RATE_KP", 1), ("RATE_KI", 1), ("RATE_KD", 0.1)):
            for value in (0, maximum):
                command = f"CMD PID YAW {gain} {value}\r\n".encode()
                self.assertEqual(normalize_vofa_command(command), f"CMD PID YAW {gain} {value:.9g}\n".encode())
            for value in ("-1", "nan", "inf", "%f", "1.1e99", str(maximum + 1)):
                with self.subTest(gain=gain, value=value), self.assertRaises(ValueError):
                    normalize_vofa_command(f"CMD PID YAW {gain} {value}\n".encode())
        for command in (
            b"", b"x" * 129, b"\xff", b"CMD MODE STABILIZE\n", b"CMD LIMIT 100\n",
            b"CMD PID YAW UNKNOWN 1\n", b"CMD PID YAW RATE_KP 0.02 junk\n",
            b"CMD PID YAW RATE_KP 0.02\nCMD LIMIT 100\n",
            b"CMD PID YAW RATE_KP 0.02\\r\\n",
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                normalize_vofa_command(command)

    def test_vofa_peer_filter_one_datagram_budget_and_bind_failure(self) -> None:
        with patch("ds5_teensy_demo.socket.socket") as factory:
            bridge = VofaBridge(1347)
        sock = factory.return_value
        sock.recvfrom.side_effect = [
            (b"CMD PID YAW RATE_KP 0.03\n", ("127.0.0.1", 1348)),
            (b"CMD PID YAW RATE_KP 0.03\n", ("127.0.0.1", 1347)),
            BlockingIOError,
        ]
        self.assertIsNone(bridge.read_command())
        self.assertEqual(sock.recvfrom.call_count, 1)
        self.assertEqual(bridge.read_command(), b"CMD PID YAW RATE_KP 0.03\n")
        self.assertIsNone(bridge.read_command())
        sock.recvfrom.assert_called_with(129)
        bridge.close()
        with patch("ds5_teensy_demo.socket.socket") as factory:
            factory.return_value.bind.side_effect = OSError("port occupied")
            with self.assertRaises(OSError):
                VofaBridge(1347)
            factory.return_value.close.assert_called_once()

    def test_bidirectional_udp_and_cached_param_after_pid(self) -> None:
        param = "PARAM{status=OK angle_kp=2.000000 rate_kp=0.030000 rate_ki=0.000000 rate_kd=0.000000}"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as vofa:
            vofa.bind(("127.0.0.1", 0))
            vofa.settimeout(1.0)
            bridge = VofaBridge(vofa.getsockname()[1], command_port=0)
            try:
                vofa.sendto(b"CMD PID YAW RATE_KP 0.030000\r\n", bridge.socket.getsockname())
                self.assertTrue(select.select([bridge.socket], [], [], 1.0)[0])
                self.assertEqual(bridge.read_command(), b"CMD PID YAW RATE_KP 0.03\n")
                bridge.update([self.line, param, "PARAM{broken", "PARAM{bad=\ufffd}"])
                self.assertEqual(vofa.recvfrom(1024)[0], self.frame)
                self.assertEqual(vofa.recvfrom(1024)[0], (param + "\n").encode())
                self.assertFalse(select.select([vofa], [], [], 0.01)[0])
            finally:
                bridge.close()
        panel = {}
        log = io.StringIO()
        update_panel_state([self.line, param], panel, log)
        self.assertEqual(build_panel_lines(panel, 200, 50)[-2:], [self.line, param])
        update_panel_state([], panel, log)
        self.assertEqual(log.getvalue().count("PARAM{"), 1)
        self.assertEqual(build_panel_lines(panel, 200, 50)[-1], param)
        update_panel_state(["SYS{ready=1}"], panel, None)
        self.assertEqual(build_panel_lines(panel, 200, 50)[-1], "PARAM{waiting}")

    def test_commands_share_serial_after_control_packets_and_bad_command_is_rejected(self) -> None:
        args = build_argument_parser().parse_args(["--send-hz", "200", "--vofa"])
        reader = Mock()
        reader.read.return_value = SimpleNamespace(lx=0, ly=0, rx=0, ry=0, l2=0, r2=0, buttons=0)
        port = Mock()
        with (
            patch("ds5_teensy_demo.open_serial_port", return_value=port),
            patch("ds5_teensy_demo.read_available_lines", side_effect=[([], "")] * 3 + [KeyboardInterrupt]),
            patch("ds5_teensy_demo.time.monotonic", side_effect=[1.0, 1.0051, 1.0211, 1.03]),
            patch("ds5_teensy_demo.time.sleep"),
            patch("ds5_teensy_demo.render_panel") as render,
            patch("ds5_teensy_demo.socket.socket") as factory,
            redirect_stdout(io.StringIO()),
        ):
            factory.return_value.recvfrom.side_effect = [
                (b"CMD PID YAW RATE_KP 0.03\r\n", ("127.0.0.1", 1347)),
                (b"CMD MODE STABILIZE\n", ("127.0.0.1", 1347)),
                (b"CMD PID YAW RATE_KI 0.01\r\n", ("127.0.0.1", 1347)),
            ]
            self.assertEqual(run_serial_demo(args, reader, None), 0)
        sent = [call.args[0] for call in port.write.call_args_list]
        self.assertEqual([data.split()[0] for data in sent], [b"PKT", b"CMD", b"PKT", b"PKT", b"CMD"])
        self.assertEqual(sent[1], b"CMD PID YAW RATE_KP 0.03\n")
        self.assertEqual(sent[-1], b"CMD PID YAW RATE_KI 0.01\n")
        self.assertIn("vofa_command=rejected", " ".join(render.call_args.args[0]))
        port.close.assert_called_once()
        factory.return_value.close.assert_called_once()
        port.write.side_effect = serial.SerialTimeoutException()
        with self.assertRaisesRegex(RuntimeError, "Serial write to COM8 timed out"):
            write_serial_data(port, b"CMD PID YAW RATE_KP 0.03\n", "COM8")

    def test_cli_defaults_and_invalid_options_before_hardware_access(self) -> None:
        args = build_argument_parser().parse_args([])
        self.assertFalse(args.vofa)
        self.assertEqual(args.vofa_port, 1347)
        self.assertEqual(args.vofa_command_port, 1346)
        for options in (
            ["--vofa-port", "0"],
            ["--vofa-port", "65536"],
            ["--vofa-command-port", "0"],
            ["--vofa-command-port", "65536"],
            ["--vofa", "--vofa-command-port", "1347"],
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
                options = ["--send-hz", "200"] + (["--vofa"] if enabled else [])
                args = build_argument_parser().parse_args(options)
                reader = Mock()
                reader.read.return_value = SimpleNamespace(
                    lx=0.1, ly=-0.2, rx=0.3, ry=-0.4, l2=0.5, r2=0.6, buttons=8
                )
                port = Mock()
                with (
                    patch("ds5_teensy_demo.open_serial_port", return_value=port),
                    patch("ds5_teensy_demo.read_available_lines", side_effect=[
                        ([self.line] * 4, ""), ([self.line] * 4, ""), KeyboardInterrupt
                    ]),
                    patch("ds5_teensy_demo.time.monotonic", side_effect=[1.0, 1.0051, 1.0102]),
                    patch("ds5_teensy_demo.time.sleep"),
                    patch("ds5_teensy_demo.render_panel") as render,
                    patch("ds5_teensy_demo.socket.socket") as factory,
                    redirect_stdout(io.StringIO()),
                ):
                    factory.return_value.sendto.side_effect = BlockingIOError
                    factory.return_value.recvfrom.side_effect = BlockingIOError
                    if socket_error:
                        factory.side_effect = OSError("UDP unavailable")
                    self.assertEqual(run_serial_demo(args, reader, None), 0)
                self.assertEqual([call.args[0] for call in port.write.call_args_list], [
                    b"PKT 1 0.100 -0.200 0.300 -0.400 0.500 0.600 8\n",
                    b"PKT 2 0.100 -0.200 0.300 -0.400 0.500 0.600 8\n",
                ])
                port.close.assert_called_once()
                self.assertEqual(render.call_count, 1)
                if enabled and not socket_error:
                    self.assertEqual(factory.return_value.sendto.call_count, 8)
                    factory.return_value.close.assert_called_once()
                elif socket_error:
                    factory.assert_called_once()
                    factory.return_value.sendto.assert_not_called()
                else:
                    factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
