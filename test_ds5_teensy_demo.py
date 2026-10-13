import unittest

from ds5_teensy_demo import decode_gateway_line, read_available_gateway_lines


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


if __name__ == "__main__":
    unittest.main()
