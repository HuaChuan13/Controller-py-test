import io
import unittest

from ds5_teensy_demo import (
    FLOAT_PRECISION,
    build_argument_parser,
    build_packet_line,
    build_panel_lines,
    decode_gateway_line,
    format_panel_lines,
    format_panel_value,
    read_available_gateway_lines,
    update_panel_state,
)
from ds5_reader import JoystickSnapshot


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


class PanelTests(unittest.TestCase):
    def test_numeric_width_and_precision_are_stable(self) -> None:
        for key, precision in FLOAT_PRECISION.items():
            for value in ("0", "-0.01", "9.9", "112.09", "999.999", "-999.999"):
                text = format_panel_value("IMU", key, value)
                self.assertEqual(len(text), 9)
                self.assertEqual(len(text.rsplit(".", 1)[1]), precision)
                self.assertIn(text.strip()[0], "+-")
        for value in ("0", "12", "4294967295"):
            self.assertEqual(len(format_panel_value("SYS", "uptime_ms", value)), 10)
        for value in ("0%", "50%", "100%"):
            self.assertEqual(len(format_panel_value("PWM", "limit", value)), 4)
        self.assertEqual(format_panel_value("PWM", "m1", "1500"), "1500")
        self.assertEqual(format_panel_value("MIX", "m1", "-1"), "    -1.00")

    def test_imu_rows_keep_positions_even_without_data(self) -> None:
        frames = (
            "IMU{waiting}",
            "IMU{temp_c=31 ax_mps2=0.066 ay_mps2=0.027 az_mps2=9.764 "
            "gx_dps=0.010 gy_dps=-0.049 gz_dps=-0.031 roll_deg=-0.38 "
            "pitch_deg=0.13 yaw_deg=112.09}",
            "IMU{temp_c=-1 ax_mps2=-12 ay_mps2=-0.1 az_mps2=0 "
            "gx_dps=-999.999 gy_dps=100 gz_dps=0 roll_deg=100 "
            "pitch_deg=-90 yaw_deg=-179.99}",
            "IMU{temp_c=nan ax_mps2=invalid yaw_deg=1e30}",
        )
        baseline = format_panel_lines("IMU", frames[0])
        for frame in frames:
            rows = format_panel_lines("IMU", frame)
            self.assertEqual(len(rows), 4)
            for row, expected in zip(rows, baseline):
                self.assertEqual(len(row), len(expected))
                self.assertEqual(
                    [index for index, char in enumerate(row) if char == "="],
                    [index for index, char in enumerate(expected) if char == "="],
                )

    def test_invalid_and_overflow_values_do_not_move_columns(self) -> None:
        for value, marker in ((None, "---"), ("nan", "ERR"), ("inf", "ERR"),
                              ("broken", "ERR"), ("1e30", "OVF")):
            text = format_panel_value("IMU", "gz_dps", value)
            self.assertEqual(len(text), 9)
            self.assertEqual(text.strip(), marker)
        self.assertEqual(format_panel_value("SYS", "pkt_alive", "123"), "!")
        self.assertEqual(format_panel_value("SYS", "future_field", "original"), "original")

    def test_variable_text_and_numbers_keep_next_field_position(self) -> None:
        samples = {
            "CTRL": ("mode=MANUAL action=IDLE yaw=0", "mode=STABILIZE action=FWD+CW yaw=-1"),
            "DS5": ("buttons=0 dpad=NONE lx=0", "buttons=4294967295 dpad=DOWN_RIGHT lx=-1"),
            "SYS": ("uptime_ms=0 stab_fault=NONE pkt_age_ms=1",
                    "uptime_ms=4294967295 stab_fault=CONTROL_GAP pkt_age_ms=500"),
            "MIX": ("m1=0 m2=1", "m1=-1 m2=-0.25"),
            "PWM": ("limit=0% m1=1500", "limit=100% m1=1000"),
            "SHT40": ("temp_c=0 humidity_rh=0", "temp_c=-20 humidity_rh=100"),
            "PM08": ("voltage_v=0 current_a=0 power_w=0 temp_c=0",
                     "voltage_v=24.59 current_a=-100 power_w=-2459 temp_c=32.85"),
            "DEPTH": ("depth_m=0 pressure_mbar=0 surface_mbar=0 temp_c=0",
                      "depth_m=-0.043 pressure_mbar=1008.8 surface_mbar=1013 temp_c=26.01"),
            "GATEWAY": ("teensy_connected=1 teensy_age_ms=1",
                        "teensy_connected=0 teensy_age_ms=-1"),
        }
        for tag, bodies in samples.items():
            rows = [format_panel_lines(tag, f"{tag}{{{body}}}")[0] for body in bodies]
            self.assertEqual(len(rows[0]), len(rows[1]))
            self.assertEqual(
                [index for index, char in enumerate(rows[0]) if char == "="],
                [index for index, char in enumerate(rows[1]) if char == "="],
            )

    def test_rendering_preserves_raw_cache_logs_packets_and_frequencies(self) -> None:
        raw = "IMU{temp_c=31.0 gx_dps=-0.01 yaw_deg=112.09}"
        state: dict[str, str] = {}
        log = io.StringIO()
        update_panel_state([raw, "INFO{unchanged}"], state, log)
        original = dict(state)
        panel = build_panel_lines(state, 50, 50)
        self.assertEqual(state, original)
        self.assertEqual(log.getvalue(), f"[Teensy] {raw}\n[Teensy] INFO{{unchanged}}\n")
        self.assertEqual(panel[-1], "INFO{unchanged}")
        self.assertEqual(len(panel), 15)  # 标题2行、其他标签8行、IMU4行、INFO1行
        self.assertEqual(build_packet_line(186, JoystickSnapshot(buttons=2048)),
                         "PKT 186 0.000 0.000 0.000 0.000 0.000 0.000 2048")
        args = build_argument_parser().parse_args([])
        self.assertEqual((args.send_hz, args.render_hz), (50, 50))


if __name__ == "__main__":
    unittest.main()
