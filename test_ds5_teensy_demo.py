import io
import unittest
from contextlib import redirect_stdout

from ds5_teensy_demo import (
    FLOAT_PRECISION,
    build_argument_parser,
    build_packet_line,
    build_panel_lines,
    decode_gateway_line,
    format_panel_value,
    parse_panel_fields,
    read_available_gateway_lines,
    render_panel,
    update_panel_state,
)
from ds5_reader import JoystickSnapshot


# 截图中可见的数据；截图未提供的字段留空，不虚构读数。
SCREENSHOT_STATE = {
    "SYS": "SYS{uptime_ms=6644660 pkt_alive=1 sht40_alive=1 pm08_alive=0 depth_alive=0 imu_alive=1 pkt_seq=1640 pkt_age_ms=5 timeout_count=7}",
    "DS5": "DS5{buttons=0 dpad=NONE lx=0 ly=0 rx=0 ry=-0.082 l2=0 r2=0}",
    "CTRL": "CTRL{mode=STABILIZE action=IDLE surge=0 sway=0 heave=0 roll=0 pitch=0 yaw=-0.01}",
    "MIX": "MIX{m1=0.01 m2=0.01 m3=0.01 m4=-0.01 m5=0 m6=0 m7=0 m8=0}",
    "PWM": "PWM{limit=70% m1=1503 m2=1503 m3=1503 m4=1497 m5=1500 m6=1500 m7=1500 m8=1500}",
    "SHT40": "SHT40{temp_c=26.07 humidity_rh=52.54}",
    "PM08": "PM08{voltage_v=17.94 current_a=-0.18 power_w=-3.27 temp_c=31.60}",
    "DEPTH": "DEPTH{depth_m=0 pressure_mbar=0 surface_mbar=1013 temp_c=0}",
    "IMU": "IMU{temp_c=26 ax_mps2=-0.061 ay_mps2=0.038 az_mps2=9.783 gx_dps=0.003 gy_dps=-0.021 gz_dps=-0.029 roll_deg=0.38 pitch_deg=0.25 yaw_deg=0.84}",
}


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
        self.assertEqual(format_panel_value("MIX", "m1", "-1"), "   -1.00")

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
        baseline = build_panel_lines({"IMU": frames[0]}, 50, 50)[-4:]
        for frame in frames:
            rows = build_panel_lines({"IMU": frame}, 50, 50)[-4:]
            self.assertEqual(len(rows), 4)
            for row, expected in zip(rows, baseline):
                self.assertEqual(len(row), len(expected))
            for row, labels in zip(rows[1:], (
                ("ax m/s2", "ay m/s2", "az m/s2"),
                ("gx deg/s", "gy deg/s", "gz deg/s"),
                ("roll deg", "pitch deg", "yaw deg"),
            )):
                self.assertEqual([row.index(label) for label in labels], [8, 32, 56])

    def test_invalid_and_overflow_values_do_not_move_columns(self) -> None:
        for value, marker in ((None, "---"), ("nan", "ERR"), ("inf", "ERR"),
                              ("broken", "ERR"), ("1e30", "OVF")):
            text = format_panel_value("IMU", "gz_dps", value)
            self.assertEqual(len(text), 9)
            self.assertEqual(text.strip(), marker)
        self.assertEqual(format_panel_value("SYS", "pkt_alive", "123"), "!")
        self.assertEqual(format_panel_value("SYS", "future_field", "original"), "original")

    def test_panel_is_bounded_and_keeps_grid_and_motor_columns(self) -> None:
        extreme = dict(SCREENSHOT_STATE)
        extreme["SYS"] = "SYS{uptime_ms=4294967295 pkt_seq=4294967295 pkt_age_ms=500 timeout_count=4294967295 rx_rejected=4294967295 stab_fault=CONTROL_GAP}"
        extreme["DS5"] = "DS5{buttons=4294967295 dpad=DOWN_RIGHT lx=-1 ly=1 rx=-1 ry=1 l2=1 r2=1}"
        extreme["CTRL"] = "CTRL{mode=MANUAL action=FWD+BACK+LEFT+RIGHT+UP+DOWN+CW+CCW surge=-1 sway=1 heave=-1 roll=1 pitch=-1 yaw=1 yaw_sp=-179.99 rate_sp=-30}"
        for state in ({}, SCREENSHOT_STATE, extreme):
            rows = build_panel_lines(state, 50, 50)
            self.assertEqual(len(rows), 24)
            self.assertLessEqual(max(map(len, rows)), 79)
            for index in (2, 3, 5, 6, 7, 9, 10, 11, 16, 18, 21, 22, 23):
                self.assertEqual(len(rows[index]), 79)
            for index in (12, 13, 14):
                self.assertEqual(len(rows[index]), 79)
                for motor in range(8):
                    slot = rows[index][8 + 9 * motor:16 + 9 * motor].strip()
                    self.assertTrue(slot)
            self.assertEqual([rows[12].index(f"M{motor}") for motor in range(1, 9)],
                             [14 + 9 * motor for motor in range(8)])
            self.assertEqual(rows[2].index("pkt_seq"), 32)
            self.assertEqual(rows[11].index("PWM limit"), 56)

    def test_parser_and_renderer_keep_raw_data_and_clear_old_tail(self) -> None:
        self.assertEqual(parse_panel_fields("IMU{temp_c=26 gx_dps=-0.021}"),
                         {"temp_c": "26", "gx_dps": "-0.021"})
        self.assertEqual(parse_panel_fields("IMU{partial"), {})
        state = dict(SCREENSHOT_STATE)
        state["INFO"] = "INFO{" + "x" * 200 + "}"
        rows = build_panel_lines(state, 50, 50)
        self.assertEqual(len(rows[-1]), 79)
        self.assertTrue(rows[-1].endswith("..."))
        output = io.StringIO()
        with redirect_stdout(output):
            render_panel(rows)
        text = output.getvalue()
        self.assertTrue(text.startswith("\x1b[H"))
        self.assertTrue(text.endswith("\x1b[J"))
        self.assertNotIn("\x1b[2J", text)
        self.assertEqual(text.count("\x1b[K"), len(rows))
        self.assertEqual(text.count("\n"), len(rows) - 1)

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
        self.assertEqual(len(panel), 25)  # 24行固定面板，另加INFO行
        self.assertEqual(build_packet_line(186, JoystickSnapshot(buttons=2048)),
                         "PKT 186 0.000 0.000 0.000 0.000 0.000 0.000 2048")
        args = build_argument_parser().parse_args([])
        self.assertEqual((args.send_hz, args.render_hz), (50, 50))


if __name__ == "__main__":
    unittest.main()
