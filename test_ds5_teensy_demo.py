import io
import unittest

from ds5_teensy_demo import (
    DISPLAY_TAGS,
    build_panel_lines,
    decode_gateway_line,
    read_available_gateway_lines,
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


if __name__ == "__main__":
    unittest.main()
