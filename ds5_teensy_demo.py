import argparse
import os
import sys
import time
from typing import TextIO

import serial

from ds5_reader import DEFAULT_DEADZONE, Ds5Reader, JoystickSnapshot


DEFAULT_TEENSY_PORT = "COM8"
DEFAULT_BAUDRATE = 115200
DEFAULT_SEND_HZ = 50  # 手柄信息发送频率
DEFAULT_RENDER_HZ = 50  # 界面刷新频率
DISPLAY_TAGS = {
    "normal": ["CTRL", "SYS"],
    "debug": ["CTRL", "SYS", "DS5", "MIX", "PWM"],
}


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read DS5 input, send PKT to Teensy, and print Teensy telemetry"
    )
    parser.add_argument("--teensy-port", default=DEFAULT_TEENSY_PORT)
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument("--send-hz", type=float, default=DEFAULT_SEND_HZ)
    parser.add_argument("--render-hz", type=float, default=DEFAULT_RENDER_HZ)
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


def enable_ansi_console() -> None:
    if os.name != "nt":
        return

    import ctypes

    handle = ctypes.windll.kernel32.GetStdHandle(-11)
    mode = ctypes.c_uint()
    if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)


def extract_tag(line: str) -> str | None:
    brace_index = line.find("{")
    if brace_index <= 0:
        return None

    return line[:brace_index]


def update_panel_state(
    lines: list[str], panel_state: dict[str, str], log_file: TextIO | None
) -> None:
    for line in lines:
        text = f"[Teensy] {line}"
        if log_file is not None:
            log_file.write(text + "\n")
            log_file.flush()

        tag = extract_tag(line)
        if tag is not None:
            panel_state[tag] = line
        else:
            panel_state["INFO"] = line


def build_panel_lines(
    panel_state: dict[str, str], view_mode: str, send_hz: float, render_hz: float
) -> list[str]:
    lines = [
        f"DS5 -> Teensy demo | view={view_mode} | send={send_hz:.0f}Hz | render={render_hz:.0f}Hz",
        "-" * 72,
    ]

    for tag in DISPLAY_TAGS[view_mode]:
        lines.append(panel_state.get(tag, f"{tag}{{waiting}}"))

    if view_mode == "debug" and "INFO" in panel_state:
        lines.append(panel_state["INFO"])

    return lines


def render_panel(lines: list[str]) -> None:
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()


def main() -> int:
    args = build_argument_parser().parse_args()

    enable_ansi_console()

    ds5_reader = Ds5Reader(deadzone=args.deadzone)
    serial_port = None
    log_file = open(args.log_file, "a", encoding="utf-8") if args.log_file else None
    sequence = 0
    read_buffer = ""
    last_send = 0.0
    last_render = 0.0
    panel_state: dict[str, str] = {}

    try:
        joystick_name = ds5_reader.open()
        print(f"Joystick: {joystick_name}")

        serial_port = serial.Serial(args.teensy_port, args.baudrate, timeout=0.001)
        print(f"Connecting Teensy on {args.teensy_port}, wait for board ready...")
        time.sleep(2.0)

        while True:
            now = time.time()

            lines, read_buffer = read_available_lines(serial_port, read_buffer)
            update_panel_state(lines, panel_state, log_file)

            joystick_snapshot = ds5_reader.read()

            if now - last_send >= 1.0 / args.send_hz:
                sequence += 1
                serial_port.write(
                    (build_packet_line(sequence, joystick_snapshot) + "\n").encode(
                        "utf-8"
                    )
                )
                last_send = now

            if now - last_render >= 1.0 / args.render_hz:
                render_panel(
                    build_panel_lines(
                        panel_state, args.view, args.send_hz, args.render_hz
                    )
                )
                last_render = now

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
