import argparse
import math
import os
import queue
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
MAX_GATEWAY_DATAGRAMS_PER_LOOP = 32
# 仅用于面板显示，不修改接收缓存或回传协议。
FLOAT_PRECISION = dict.fromkeys(
    ("surge", "sway", "heave", "roll", "pitch", "yaw", "yaw_sp", "rate_sp",
     "temp_c", "humidity_rh", "voltage_v", "current_a", "power_w",
     "pressure_mbar", "surface_mbar", "roll_deg", "pitch_deg", "yaw_deg"), 2
) | dict.fromkeys(
    ("lx", "ly", "rx", "ry", "l2", "r2", "depth_m",
     "ax_mps2", "ay_mps2", "az_mps2", "gx_dps", "gy_dps", "gz_dps"), 3
)
TEXT_WIDTHS = {"mode": 9, "action": 34, "dpad": 10, "stab_fault": 11}
INTEGER_WIDTHS = {
    "uptime_ms": 10, "pkt_seq": 10, "pkt_age_ms": 5,
    "timeout_count": 10, "rx_rejected": 10, "buttons": 10,
    "ready": 1, "limit": 4, "teensy_connected": 1, "teensy_age_ms": 10,
}
MOTOR_FIELDS = tuple(f"m{index}" for index in range(1, 9))
PANEL_COLUMNS = 79


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


def decode_gateway_line(data: bytes) -> str:
    if not data or b"\r" in data or b"\n" in data:
        raise ValueError("gateway datagram must contain one line")
    line = data.decode("ascii").strip()
    if not line:
        raise ValueError("gateway datagram is empty")
    return line


def read_available_gateway_lines(
    udp_socket: socket.socket,
    max_datagrams: int = MAX_GATEWAY_DATAGRAMS_PER_LOOP,
) -> list[str]:
    lines: list[str] = []
    for _ in range(max_datagrams):
        try:
            data, _ = udp_socket.recvfrom(4096)
        except BlockingIOError:
            break
        except socket.timeout:
            break

        try:
            lines.append(decode_gateway_line(data))
        except (UnicodeDecodeError, ValueError) as exc:
            lines.append(f"INFO{{gateway_line_error={exc}}}")

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


def format_panel_value(tag: str, key: str, value: str | None) -> str:
    if key in TEXT_WIDTHS:
        width = TEXT_WIDTHS[key]
        text = value or "---"
        return (text if len(text) <= width else "OVF").ljust(width)

    precision = 2 if tag == "MIX" and key in MOTOR_FIELDS else FLOAT_PRECISION.get(key)
    width = 9 if precision is not None else INTEGER_WIDTHS.get(key)
    if tag == "MIX" and key in MOTOR_FIELDS:
        width = 8
    if key.endswith("_alive"):
        width = 1
    if tag == "PWM" and key in MOTOR_FIELDS:
        width = 4
    if width is None:
        return value or "---"

    text = "---"[:width]
    if value:
        try:
            if precision is not None:
                number = float(value)
                text = f"{number:+.{precision}f}" if math.isfinite(number) else "ERR"
            elif tag == "PWM" and key == "limit":
                text = f"{int(value.removesuffix('%'))}%"
            else:
                text = str(int(value))
        except ValueError:
            text = "ERR"
    if len(text) > width:
        text = "OVF" if width >= 3 else "!" * width
    return text.rjust(width)


def parse_panel_fields(line: str) -> dict[str, str]:
    _, separator, body = line.partition("{")
    if not separator or not body.endswith("}"):
        return {}
    return {
        key: value
        for key, separator, value in (token.partition("=") for token in body[:-1].split())
        if separator
    }


def build_panel_lines(
    panel_state: dict[str, str],
    send_hz: float,
    render_hz: float,
    title: str = "DS5 -> Teensy demo",
) -> list[str]:
    fields = {tag: parse_panel_fields(line) for tag, line in panel_state.items()}

    def value(tag: str, key: str) -> str:
        return format_panel_value(tag, key, fields.get(tag, {}).get(key)).strip()

    def cell(tag: str, key: str, label: str = "") -> str:
        return f"{label or key:<11} {value(tag, key):>11}"

    def row(label: str, *cells: str) -> str:
        return f"{label:<7} " + " ".join(cells)

    online = {"1": "ON", "0": "OFF", "-": "---", "!": "ERR"}
    # 普通数据三列，电机使用共用表头；所有常规行最多 79 字符。
    lines = [
        f"{title} | send={send_hz:.0f}Hz | render={render_hz:.0f}Hz",
        "-" * PANEL_COLUMNS,
        row("SYS", cell("SYS", "uptime_ms"), cell("SYS", "pkt_seq"),
            cell("SYS", "pkt_age_ms", "age_ms")),
        row("", cell("SYS", "timeout_count", "timeouts"), cell("SYS", "rx_rejected"),
            cell("SYS", "stab_fault", "fault")),
        row("ONLINE", *(
            f"{sensor:<5} {online.get(value('SYS', key), '---'):>5}"
            for sensor, key in (
                ("PKT", "pkt_alive"), ("SHT40", "sht40_alive"),
                ("PM08", "pm08_alive"), ("DEPTH", "depth_alive"), ("IMU", "imu_alive")
            )
        )),
        row("INPUT", cell("DS5", "buttons"), cell("DS5", "dpad"), cell("CTRL", "mode")),
        row("STICKS", cell("DS5", "lx"), cell("DS5", "ly"), cell("DS5", "rx")),
        row("", cell("DS5", "ry"), cell("DS5", "l2"), cell("DS5", "r2")),
        row("ACTION", value("CTRL", "action")),
        row("CTRL", cell("CTRL", "surge"), cell("CTRL", "sway"),
            cell("CTRL", "heave")),
        row("", cell("CTRL", "roll"), cell("CTRL", "pitch"), cell("CTRL", "yaw")),
        row("TARGET", cell("CTRL", "yaw_sp", "yawSP deg"),
            cell("CTRL", "rate_sp", "rateSP d/s"), cell("PWM", "limit", "PWM limit")),
        row("MOTOR", *(f"{key.upper():>8}" for key in MOTOR_FIELDS)),
        row("MIX", *(f"{value('MIX', key):>8}" for key in MOTOR_FIELDS)),
        row("PWM us", *(f"{value('PWM', key):>8}" for key in MOTOR_FIELDS)),
        row("SHT40", cell("SHT40", "temp_c", "temp C"),
            cell("SHT40", "humidity_rh", "humidity %")),
        row("PM08", cell("PM08", "voltage_v", "voltage V"),
            cell("PM08", "current_a", "current A"), cell("PM08", "power_w", "power W")),
        row("", cell("PM08", "temp_c", "temp C")),
        row("DEPTH", cell("DEPTH", "depth_m", "depth m"),
            cell("DEPTH", "pressure_mbar", "press mbar"), cell("DEPTH", "temp_c", "temp C")),
        row("", cell("DEPTH", "surface_mbar", "surface mb")),
        row("IMU", cell("IMU", "temp_c", "temp C")),
        row("ACC", cell("IMU", "ax_mps2", "ax m/s2"),
            cell("IMU", "ay_mps2", "ay m/s2"), cell("IMU", "az_mps2", "az m/s2")),
        row("GYR", cell("IMU", "gx_dps", "gx deg/s"),
            cell("IMU", "gy_dps", "gy deg/s"), cell("IMU", "gz_dps", "gz deg/s")),
        row("ANG", cell("IMU", "roll_deg", "roll deg"),
            cell("IMU", "pitch_deg", "pitch deg"), cell("IMU", "yaw_deg", "yaw deg")),
    ]

    if "GATEWAY" in panel_state:
        lines.append(row("GATEWAY", cell("GATEWAY", "teensy_connected", "connected"),
                         cell("GATEWAY", "teensy_age_ms", "age_ms")))
    if "INFO" in panel_state:
        info = panel_state["INFO"]
        lines.append(info if len(info) <= PANEL_COLUMNS else info[:PANEL_COLUMNS - 3] + "...")
    return lines


def render_panel(lines: list[str]) -> None:
    # 从原位覆盖并清除行尾，不整屏清空；末行不换行，避免满屏时滚动。
    sys.stdout.write("\x1b[H" + "\n".join(line + "\x1b[K" for line in lines) + "\x1b[J")
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
        print(f"\n{exc}")
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
        print(f"\n{exc}")
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
                    encoded = build_packet_line(sequence, joystick_snapshot).encode("ascii")
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
        print(f"\n{exc}")
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
