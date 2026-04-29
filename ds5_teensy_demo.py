import argparse
import time
from typing import TextIO

import serial

from ds5_reader import DEFAULT_DEADZONE, Ds5Reader, JoystickSnapshot


DEFAULT_TEENSY_PORT = "COM8"
DEFAULT_BAUDRATE = 115200
DEFAULT_SEND_HZ = 50


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read DS5 input, send PKT to Teensy, and print Teensy telemetry"
    )
    parser.add_argument("--teensy-port", default=DEFAULT_TEENSY_PORT)
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument("--send-hz", type=float, default=DEFAULT_SEND_HZ)
    parser.add_argument("--deadzone", type=float, default=DEFAULT_DEADZONE)
    parser.add_argument("--view", choices=["normal", "debug"], default="normal")
    parser.add_argument("--log-file", default="")
    return parser


def build_packet_line(sequence: int, joystick: JoystickSnapshot) -> str:
    # 当前临时协议只服务于 DS5 手动控制 demo，因此只发送手柄总包。
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


def read_available_lines(
    serial_port: serial.Serial, read_buffer: str
) -> tuple[list[str], str]:
    if not serial_port.in_waiting:
        return [], read_buffer

    data = serial_port.read(serial_port.in_waiting).decode("utf-8", errors="replace")
    read_buffer += data

    lines: list[str] = []
    while "\n" in read_buffer:
        line, read_buffer = read_buffer.split("\n", 1)
        stripped = line.strip()
        if stripped:
            lines.append(stripped)

    return lines, read_buffer


def should_display_teensy_line(line: str, view_mode: str) -> bool:
    # 默认模式只保留最关键的控制摘要和系统状态，方便盯主链。
    if view_mode == "debug":
        return True

    if line.startswith("CTRL{") or line.startswith("SYS{"):
        return True

    if line.startswith("MIX{") or line.startswith("PWM{") or line.startswith("DS5{"):
        return False

    return True


def emit_teensy_lines(
    lines: list[str], view_mode: str, log_file: TextIO | None
) -> None:
    for line in lines:
        text = f"[Teensy] {line}"
        if log_file is not None:
            log_file.write(text + "\n")
            log_file.flush()

        if should_display_teensy_line(line, view_mode):
            print(text)


def main() -> int:
    args = build_argument_parser().parse_args()

    ds5_reader = Ds5Reader(deadzone=args.deadzone)
    serial_port = None
    log_file = open(args.log_file, "a", encoding="utf-8") if args.log_file else None
    sequence = 0
    read_buffer = ""
    last_send = 0.0

    try:
        joystick_name = ds5_reader.open()
        print(f"Joystick: {joystick_name}")

        serial_port = serial.Serial(args.teensy_port, args.baudrate, timeout=0.001)
        print(f"Connecting Teensy on {args.teensy_port}, wait for board ready...")
        time.sleep(2.0)

        while True:
            now = time.time()

            lines, read_buffer = read_available_lines(serial_port, read_buffer)
            emit_teensy_lines(lines, args.view, log_file)

            joystick_snapshot = ds5_reader.read()

            if now - last_send >= 1.0 / args.send_hz:
                sequence += 1
                serial_port.write(
                    (build_packet_line(sequence, joystick_snapshot) + "\n").encode(
                        "utf-8"
                    )
                )
                last_send = now

            time.sleep(0.001)
    except RuntimeError as exc:
        print(exc)
        return 1
    except KeyboardInterrupt:
        print("\nStop DS5 demo")
        return 0
    finally:
        if serial_port is not None:
            serial_port.close()
        if log_file is not None:
            log_file.close()
        ds5_reader.close()


if __name__ == "__main__":
    raise SystemExit(main())
