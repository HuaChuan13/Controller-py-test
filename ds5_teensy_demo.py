import argparse
import json
import os
import queue
import re
import socket
import sys
import threading
import time
from typing import TextIO

import serial

from ds5_reader import (
    DEFAULT_DEADZONE,
    Ds5Reader,
    JoystickInfo,
    JoystickRawSnapshot,
    JoystickSnapshot,
)


DEFAULT_TEENSY_PORT = "COM8"
DEFAULT_GATEWAY_HOST = "192.168.10.2"
DEFAULT_GATEWAY_PORT = 14550
DEFAULT_BAUDRATE = 115200
DEFAULT_SEND_HZ = 50  # 手柄信息发送频率
DEFAULT_RENDER_HZ = 50  # 界面刷新频率
DEFAULT_SERIAL_OPEN_TIMEOUT_S = 3.0
DEFAULT_SERIAL_WRITE_TIMEOUT_S = 0.5
DISPLAY_TAGS = [
    "CTRL",
    "SYS",
    "DS5",
    "MIX",
    "PWM",
    "SHT40",
    "PM08",
    "DEPTH",
    "IMU",
]


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read DS5 input and send control packets to Teensy or rov_gateway"
    )
    parser.add_argument("--mode", choices=["serial", "gateway"], default="serial")
    parser.add_argument("--teensy-port", default=DEFAULT_TEENSY_PORT)
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument(
        "--serial-open-timeout",
        type=float,
        default=DEFAULT_SERIAL_OPEN_TIMEOUT_S,
    )
    parser.add_argument(
        "--serial-write-timeout",
        type=float,
        default=DEFAULT_SERIAL_WRITE_TIMEOUT_S,
    )
    parser.add_argument("--gateway-host", default=DEFAULT_GATEWAY_HOST)
    parser.add_argument("--gateway-port", type=int, default=DEFAULT_GATEWAY_PORT)
    parser.add_argument("--send-hz", type=float, default=DEFAULT_SEND_HZ)
    parser.add_argument("--render-hz", type=float, default=DEFAULT_RENDER_HZ)
    parser.add_argument("--deadzone", type=float, default=DEFAULT_DEADZONE)
    parser.add_argument("--joystick-index", type=int, default=0)
    parser.add_argument(
        "--sdl-joystick-device",
        default="",
        help="Linux SDL joystick device override, for example /dev/input/js0",
    )
    parser.add_argument(
        "--ds5-debug",
        action="store_true",
        help="Only show raw DS5 axes/buttons/hats and packed snapshot; no serial",
    )
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


def build_control_message(
    sequence: int, joystick: JoystickSnapshot
) -> dict[str, object]:
    return {
        "type": "control",
        "seq": sequence,
        "timestamp_ms": int(time.time() * 1000),
        "lx": joystick.lx,
        "ly": joystick.ly,
        "rx": joystick.rx,
        "ry": joystick.ry,
        "l2": joystick.l2,
        "r2": joystick.r2,
        "buttons": joystick.buttons,
    }


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


def open_serial_port(
    port: str,
    baudrate: int,
    read_timeout: float,
    write_timeout: float,
    open_timeout: float,
) -> serial.Serial:
    result_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            serial_port = serial.Serial(
                port,
                baudrate,
                timeout=read_timeout,
                write_timeout=write_timeout,
            )
        except Exception as exc:
            result_queue.put(("error", exc))
            return

        result_queue.put(("ok", serial_port))

    thread = threading.Thread(target=worker, name="serial-open", daemon=True)
    thread.start()
    deadline = time.monotonic() + max(0.1, open_timeout)

    while True:
        try:
            status, payload = result_queue.get(timeout=0.05)
        except queue.Empty:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Opening serial {port} timed out after {open_timeout:.1f}s"
                )
            continue

        if status == "ok":
            return payload  # type: ignore[return-value]

        if isinstance(payload, Exception):
            raise RuntimeError(f"Failed to open serial {port}: {payload}") from payload

        raise RuntimeError(f"Failed to open serial {port}: {payload}")


def format_teensy_port(port: object) -> object:
    if not isinstance(port, str) or not port:
        return port
    if port == "dry-run":
        return port

    normalized = port.replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1]
    serial_match = re.search(r"(\d{6,})", basename)
    if "Teensy" in basename and serial_match is not None:
        return f"Teensy:{serial_match.group(1)}"
    if basename.startswith(("ttyACM", "ttyUSB", "COM")):
        return basename
    if len(basename) <= 24:
        return basename
    return f"...{basename[-20:]}"


def build_gateway_status_line(message: dict[str, object]) -> str:
    counts = message.get("counts")
    if not isinstance(counts, dict):
        counts = {}
    teensy_port = format_teensy_port(message.get("teensy_port"))

    return (
        "GATEWAY{"
        f"pc_alive={message.get('pc_alive')} "
        f"teensy_connected={message.get('teensy_connected')} "
        f"teensy={teensy_port} "
        f"pc_age_ms={message.get('pc_last_rx_age_ms')} "
        f"udp_rx={counts.get('udp_rx')} "
        f"teensy_tx={counts.get('teensy_tx')} "
        f"teensy_rx={counts.get('teensy_rx')}"
        "}"
    )


def decode_gateway_telemetry(data: bytes) -> list[str]:
    message = json.loads(data.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("telemetry JSON root is not an object")
    if message.get("type") != "telemetry":
        raise ValueError(f"unsupported telemetry type: {message.get('type')!r}")

    lines = [build_gateway_status_line(message)]
    latest = message.get("latest")
    if isinstance(latest, dict):
        for value in latest.values():
            if isinstance(value, str):
                lines.append(value)

    return lines


def read_available_gateway_lines(udp_socket: socket.socket) -> list[str]:
    lines: list[str] = []
    while True:
        try:
            data, _ = udp_socket.recvfrom(65535)
        except BlockingIOError:
            break
        except socket.timeout:
            break

        try:
            lines.extend(decode_gateway_telemetry(data))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            lines.append(f"INFO{{gateway_telemetry_error={exc}}}")

    return lines


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
    panel_state: dict[str, str],
    send_hz: float,
    render_hz: float,
    title: str = "DS5 -> Teensy demo",
) -> list[str]:
    lines = [
        f"{title} | send={send_hz:.0f}Hz | render={render_hz:.0f}Hz",
        "-" * 72,
    ]

    for tag in DISPLAY_TAGS:
        lines.append(panel_state.get(tag, f"{tag}{{waiting}}"))

    if "GATEWAY" in panel_state:
        lines.append(panel_state["GATEWAY"])

    if "INFO" in panel_state:
        lines.append(panel_state["INFO"])

    return lines


def render_panel(lines: list[str]) -> None:
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()


def format_pressed_buttons(raw: JoystickRawSnapshot) -> str:
    pressed = [
        f"{index}(bit={1 << index})"
        for index, value in enumerate(raw.buttons[:32])
        if value
    ]
    return ", ".join(pressed) if pressed else "none"


def format_axis_values(raw: JoystickRawSnapshot) -> str:
    if not raw.axes:
        return "none"
    return " ".join(f"{index}:{value:+.3f}" for index, value in enumerate(raw.axes))


def format_button_values(raw: JoystickRawSnapshot) -> str:
    if not raw.buttons:
        return "none"
    return " ".join(f"{index}:{value}" for index, value in enumerate(raw.buttons))


def format_hat_values(raw: JoystickRawSnapshot) -> str:
    if not raw.hats:
        return "none"
    return " ".join(
        f"{index}:({hat_x:+d},{hat_y:+d})"
        for index, (hat_x, hat_y) in enumerate(raw.hats)
    )


def build_ds5_debug_lines(
    info: JoystickInfo,
    raw: JoystickRawSnapshot,
    snapshot: JoystickSnapshot,
    joystick_index: int,
    deadzone: float,
) -> list[str]:
    sdl_device = os.environ.get("SDL_JOYSTICK_DEVICE", "<unset>")
    return [
        f"DS5 mapping debug | joystick={joystick_index} | deadzone={deadzone:.2f}",
        f"name={info.name}",
        f"guid={info.guid}",
        f"counts axes={info.axes} buttons={info.buttons} hats={info.hats}",
        f"SDL_JOYSTICK_DEVICE={sdl_device}",
        "-" * 72,
        "Press one control at a time. Ctrl+C to stop.",
        f"axes: {format_axis_values(raw)}",
        f"buttons: {format_button_values(raw)}",
        f"pressed: {format_pressed_buttons(raw)}",
        f"hats: {format_hat_values(raw)}",
        (
            "packed: "
            f"lx={snapshot.lx:+.3f} ly={snapshot.ly:+.3f} "
            f"rx={snapshot.rx:+.3f} ry={snapshot.ry:+.3f} "
            f"l2={snapshot.l2:.3f} r2={snapshot.r2:.3f} "
            f"buttons={snapshot.buttons}"
        ),
    ]


def run_ds5_debug(args: argparse.Namespace) -> int:
    ds5_reader = Ds5Reader(
        deadzone=args.deadzone,
        joystick_index=args.joystick_index,
    )
    try:
        ds5_reader.open()
        info = ds5_reader.info()
        while True:
            raw = ds5_reader.read_raw()
            snapshot = ds5_reader.read()
            render_panel(
                build_ds5_debug_lines(
                    info,
                    raw,
                    snapshot,
                    args.joystick_index,
                    args.deadzone,
                )
            )
            time.sleep(1.0 / args.render_hz)
    except RuntimeError as exc:
        print(exc)
        return 1
    except KeyboardInterrupt:
        print("\nStop DS5 debug")
        return 0
    finally:
        ds5_reader.close()


def run_serial_demo(
    args: argparse.Namespace,
    ds5_reader: Ds5Reader,
    log_file: TextIO | None,
) -> int:
    serial_port: serial.Serial | None = None
    sequence = 0
    read_buffer = ""
    last_send = 0.0
    last_render = 0.0
    panel_state: dict[str, str] = {}

    try:
        joystick_name = ds5_reader.open()
        print(f"Joystick: {joystick_name}")

        print(f"Opening Teensy serial {args.teensy_port}...", flush=True)
        serial_port = open_serial_port(
            args.teensy_port,
            args.baudrate,
            read_timeout=0.001,
            write_timeout=args.serial_write_timeout,
            open_timeout=args.serial_open_timeout,
        )
        print(f"Connecting Teensy on {args.teensy_port}, wait for board ready...")
        time.sleep(2.0)

        while True:
            now = time.time()

            lines, read_buffer = read_available_lines(serial_port, read_buffer)
            update_panel_state(lines, panel_state, log_file)

            joystick_snapshot = ds5_reader.read()

            if now - last_send >= 1.0 / args.send_hz:
                sequence += 1
                try:
                    serial_port.write(
                        (build_packet_line(sequence, joystick_snapshot) + "\n").encode(
                            "utf-8"
                        )
                    )
                except serial.SerialTimeoutException as exc:
                    raise RuntimeError(
                        f"Serial write to {args.teensy_port} timed out"
                    ) from exc
                last_send = now

            if now - last_render >= 1.0 / args.render_hz:
                render_panel(
                    build_panel_lines(
                        panel_state,
                        args.send_hz,
                        args.render_hz,
                        title="DS5 -> Teensy demo",
                    )
                )
                last_render = now

            time.sleep(0.001)
    except (RuntimeError, serial.SerialException) as exc:
        print(exc)
        return 1
    except KeyboardInterrupt:
        print("\nStop DS5 demo")
        return 0
    finally:
        if serial_port is not None:
            serial_port.close()


def run_gateway_demo(
    args: argparse.Namespace,
    ds5_reader: Ds5Reader,
    log_file: TextIO | None,
) -> int:
    target = (args.gateway_host, args.gateway_port)
    sequence = 0
    last_send = 0.0
    last_render = 0.0
    panel_state: dict[str, str] = {}

    try:
        joystick_name = ds5_reader.open()
        print(f"Joystick: {joystick_name}")
        print(f"Connecting rov_gateway on {args.gateway_host}:{args.gateway_port}...")

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
            # Windows 上未绑定的 UDP socket 不能先 recvfrom；绑定 0 表示让系统分配本地临时端口。
            # rov_gateway 会把 telemetry 回发到这个源端口。
            udp_socket.bind(("", 0))
            udp_socket.setblocking(False)

            while True:
                now = time.time()

                lines = read_available_gateway_lines(udp_socket)
                update_panel_state(lines, panel_state, log_file)

                joystick_snapshot = ds5_reader.read()

                if now - last_send >= 1.0 / args.send_hz:
                    sequence += 1
                    payload = build_control_message(sequence, joystick_snapshot)
                    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                    udp_socket.sendto(encoded, target)
                    last_send = now

                if now - last_render >= 1.0 / args.render_hz:
                    render_panel(
                        build_panel_lines(
                            panel_state,
                            args.send_hz,
                            args.render_hz,
                            title="DS5 -> rov_gateway demo",
                        )
                    )
                    last_render = now

                time.sleep(0.001)
    except RuntimeError as exc:
        print(exc)
        return 1
    except KeyboardInterrupt:
        print("\nStop DS5 gateway demo")
        return 0


def main() -> int:
    args = build_argument_parser().parse_args()

    if args.sdl_joystick_device:
        os.environ["SDL_JOYSTICK_DEVICE"] = args.sdl_joystick_device

    enable_ansi_console()

    if args.ds5_debug:
        return run_ds5_debug(args)

    ds5_reader = Ds5Reader(
        deadzone=args.deadzone,
        joystick_index=args.joystick_index,
    )
    log_file = open(args.log_file, "a", encoding="utf-8") if args.log_file else None

    try:
        if args.mode == "gateway":
            return run_gateway_demo(args, ds5_reader, log_file)

        return run_serial_demo(args, ds5_reader, log_file)

    finally:
        if log_file is not None:
            log_file.close()
        ds5_reader.close()


if __name__ == "__main__":
    raise SystemExit(main())
