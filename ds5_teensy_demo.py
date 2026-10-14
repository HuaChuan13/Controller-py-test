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
DEFAULT_VOFA_PORT = 1347
VOFA_SEND_PERIOD_S = 0.02  # 最多 50Hz，不随面板刷新频率变化
VOFA_FRAME_TIMEOUT_S = 0.1  # 丢弃在电脑上停留过久的待发数据
VOFA_YAW_FIELDS = (
    ("CTRL", "yaw_sp"),   # 期望偏航角，单位 deg
    ("IMU", "yaw_deg"),   # 实际偏航角，单位 deg
    ("CTRL", "rate_sp"),  # 期望偏航角速度，单位 deg/s
    ("IMU", "gz_dps"),    # 实际偏航角速度，单位 deg/s
)
# DISPLAY_TAGS = [
#     "CTRL",
#     "SYS",
#     "DS5",
#     "MIX",
#     "PWM",
#     "SHT40",
#     "PM08",
#     "DEPTH",
#     "IMU",
# ]
DISPLAY_TAGS = [
    "SYS",
    "DS5",
    "CTRL",
    "MIX",
    "PWM",
    "SHT40",
    "PM08",
    "DEPTH",
    "IMU",
]
IMU_ROWS = (
    ("IMU", ("temp_c",), 2),
    ("ACC", ("ax_mps2", "ay_mps2", "az_mps2"), 3),
    ("GYR", ("gx_dps", "gy_dps", "gz_dps"), 3),
    ("ANG", ("roll_deg", "pitch_deg", "yaw_deg"), 2),
)


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
    parser.add_argument(
        "--vofa",
        action="store_true",
        help="Forward yaw telemetry to VOFA via local UDP (serial mode only)",
    )
    parser.add_argument(
        "--vofa-port", type=int, default=DEFAULT_VOFA_PORT, help="VOFA UDP listening port"
    )
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


def build_vofa_yaw_frame(snapshot: dict[str, str]) -> bytes | None:
    try:
        fields = {}
        for tag in ("CTRL", "SYS", "IMU"):
            line = snapshot[tag]
            if not line.startswith(tag + "{") or not line.endswith("}"):
                return None
            fields[tag] = dict(token.split("=", 1) for token in line[len(tag) + 1 : -1].split())
        if fields["CTRL"].get("mode") != "STABILIZE" or fields["SYS"].get("imu_alive") != "1":
            return None
        values = [float(fields[tag][key]) for tag, key in VOFA_YAW_FIELDS]
    except (KeyError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return ("yaw:" + ",".join(f"{value:.9g}" for value in values) + "\n").encode("ascii")


class VofaYawSender:
    def __init__(self, port: int) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.target = ("127.0.0.1", port)
        self.snapshot: dict[str, str] = {}
        self.pending: tuple[float, bytes] | None = None
        self.last_send = -math.inf

    def update(self, lines: list[str], now: float) -> None:
        # ponytail: 当前固件快照以 CTRL 开始、IMU 结束，顺序变化时需同步调整。
        for line in lines:
            tag = extract_tag(line)
            if tag == "CTRL":
                self.snapshot = {"CTRL": line}
                self.pending = None
            elif line == "SYS{ready=1}":
                self.snapshot.clear()
                self.pending = None
            elif tag in ("SYS", "IMU") and self.snapshot:
                self.snapshot[tag] = line
                if tag == "IMU":
                    frame = build_vofa_yaw_frame(self.snapshot)
                    self.pending = (now, frame) if frame is not None else None
                    self.snapshot.clear()

        if self.pending is None or now - self.last_send < VOFA_SEND_PERIOD_S:
            return
        received_at, frame = self.pending
        self.pending = None
        if now - received_at > VOFA_FRAME_TIMEOUT_S:
            return
        self.last_send = now
        try:
            self.socket.sendto(frame, self.target)
        except OSError:
            pass  # 绘图数据发送失败时丢帧，不重试，不中断手柄控制

    def close(self) -> None:
        self.socket.close()


def format_imu_lines(line: str) -> list[str]:
    # 仅排版IMU，接收缓存和日志保持原样。
    fields = {}
    if line.startswith("IMU{") and line.endswith("}"):
        fields = dict(token.split("=", 1) for token in line[4:-1].split() if "=" in token)

    def cell(key: str, precision: int) -> str:
        text = "---"
        if key in fields:
            try:
                number = float(fields[key])
                text = f"{number:+.{precision}f}" if math.isfinite(number) else "ERR"
            except ValueError:
                text = "ERR"
        if len(text) > 9:
            text = "OVF"
        return f"{key:<9}={text:>9}"

    return [
        f"{label}{{" + " ".join(cell(key, precision) for key in keys) + "}"
        for label, keys, precision in IMU_ROWS
    ]


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
        line = panel_state.get(tag, f"{tag}{{waiting}}")
        if tag == "IMU":
            lines.extend(format_imu_lines(line))
        else:
            lines.append(line)

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
    vofa: VofaYawSender | None = None
    sequence = 0
    read_buffer = ""
    last_send = 0.0
    last_render = 0.0
    panel_state: dict[str, str] = {}

    try:
        if args.vofa:
            try:
                vofa = VofaYawSender(args.vofa_port)
            except OSError as exc:
                panel_state["INFO"] = f"INFO{{vofa=disabled error={exc}}}"
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

            if vofa is not None:
                vofa.update(lines, time.monotonic())

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
        if vofa is not None:
            vofa.close()
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
        print(exc)
        return 1
    except KeyboardInterrupt:
        print("\nStop DS5 gateway demo")
        return 0


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    if not 1 <= args.vofa_port <= 65535:
        parser.error("--vofa-port must be between 1 and 65535")
    if args.vofa and (args.mode != "serial" or args.ds5_debug):
        parser.error("--vofa requires serial mode without --ds5-debug")

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
