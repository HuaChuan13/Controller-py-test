import serial

from DS5.ds5_reader import JoystickSnapshot


DEFAULT_TEENSY_PORT = "COM8"
DEFAULT_BAUDRATE = 115200


class ProtocolBridge:
    # 这层只负责“Controller 和 Teensy 之间怎么传”，
    # 不关心手柄和 IMU 各自是怎么读出来的。
    def __init__(self, port: str = DEFAULT_TEENSY_PORT, baudrate: int = DEFAULT_BAUDRATE):
        self.serial = serial.Serial(port, baudrate, timeout=0.001)
        self.sequence = 0
        self._read_buffer = ""

    def send_line(self, line: str) -> None:
        self.serial.write((line + "\n").encode("utf-8"))

    def send_packet(self, joystick: JoystickSnapshot) -> int:
        self.sequence += 1
        self.send_line(self.build_packet_line(self.sequence, joystick))
        return self.sequence

    @staticmethod
    def build_packet_line(sequence: int, joystick: JoystickSnapshot) -> str:
        # 当前临时协议只服务于 DS5 手动控制链路，因此先压成最小手柄总包。
        return (
            "PKT "
            f"{sequence} "
            f"{joystick.lx:.3f} "
            f"{joystick.ly:.3f} "
            f"{joystick.rx:.3f} "
            f"{joystick.ry:.3f} "
            f"{joystick.l2:.3f} "
            f"{joystick.r2:.3f} "
            f"{joystick.buttons}"
        )

    def read_available_lines(self) -> list[str]:
        # Teensy 回传同样按文本行处理，方便监视模块直接显示。
        if not self.serial.in_waiting:
            return []

        data = self.serial.read(self.serial.in_waiting).decode(
            "utf-8", errors="replace"
        )
        self._read_buffer += data

        lines: list[str] = []
        while "\n" in self._read_buffer:
            line, self._read_buffer = self._read_buffer.split("\n", 1)
            stripped = line.strip()
            if stripped:
                lines.append(stripped)

        return lines

    def close(self) -> None:
        self.serial.close()
