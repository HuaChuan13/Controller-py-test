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
MAX_SERIAL_READ_BYTES = 4096  # 限制每轮接收量，避免积压处理拖住手柄发包
DEFAULT_VOFA_PORT = 1347
DEFAULT_VOFA_COMMAND_PORT = 1346
MAX_VOFA_COMMAND_BYTES = 128
VOFA_YAW_GAINS = ("ANGLE_KP", "RATE_KP", "RATE_KI", "RATE_KD")
VOFA_YAW_FIELDS = (
    "yaw_sp",   # 期望偏航角，单位 deg
    "yaw_deg",  # 实际偏航角，单位 deg
    "rate_sp",  # 期望偏航角速度，单位 deg/s
    "gz_dps",   # 实际偏航角速度，单位 deg/s
)
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
    "PID",
    "PARAM",
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
        help="Bridge yaw telemetry and manual gain commands with local VOFA (serial mode only)",
    )
    parser.add_argument(
        "--vofa-port", type=int, default=DEFAULT_VOFA_PORT, help="VOFA UDP listening port"
    )
    parser.add_argument(
        "--vofa-command-port", type=int, default=DEFAULT_VOFA_COMMAND_PORT,
        help="Local UDP port receiving VOFA yaw gain commands",
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
    serial_port: serial.Serial, read_buffer: str | None
) -> tuple[list[str], str | None]:
    if not serial_port.in_waiting:
        return [], read_buffer

    data = serial_port.read(min(serial_port.in_waiting, MAX_SERIAL_READ_BYTES)).decode(
        "utf-8", errors="replace"
    )
    # None 表示超长行尚未结束，必须丢弃到换行后才能重新接收。
    if read_buffer is None:
        _, newline, data = data.partition("\n")
        if not newline:
            return [], None
        read_buffer = ""
    read_buffer += data

    lines: list[str] = []
    while "\n" in read_buffer:
        line, read_buffer = read_buffer.split("\n", 1)
        if len(line) > MAX_SERIAL_READ_BYTES:
            continue
        stripped = line.strip()
        if stripped:
            lines.append(stripped)

    # 固件为 ASCII；正常单行小于 1024 字节，超长后不再保留正文。
    if len(read_buffer) > MAX_SERIAL_READ_BYTES:
        read_buffer = None
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
        if line == "SYS{ready=1}":
            panel_state.pop("PARAM", None)  # 固件重启恢复默认值，不能沿用旧参数确认
        text = f"[Teensy] {line}"
        if log_file is not None:
            log_file.write(text + "\n")
            log_file.flush()

        tag = extract_tag(line)
        if tag is not None:
            panel_state[tag] = line
        else:
            panel_state["INFO"] = line


def build_vofa_yaw_frame(line: str) -> bytes | None:
    if not line.startswith("PID{") or not line.endswith("}"):
        return None
    try:
        fields = dict(token.split("=", 1) for token in line[4:-1].split())
        values = [float(fields[key]) for key in VOFA_YAW_FIELDS]
    except (KeyError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return ("yaw:" + ",".join(f"{value:.9g}" for value in values) + "\n").encode("ascii")


def normalize_vofa_command(data: bytes) -> bytes:
    if not data or len(data) > MAX_VOFA_COMMAND_BYTES:
        raise ValueError("command must contain 1..128 bytes")
    text = data.decode("ascii").strip()
    fields = text.split()
    if "\n" in text or "\r" in text or len(fields) != 5 or fields[:3] != ["CMD", "PID", "YAW"]:
        raise ValueError("expected one CMD PID YAW <gain> <value> line")
    gain = fields[3]
    value = float(fields[4])
    # 可调上限只由 Teensy Config.h 维护，避免 PC 与固件范围不一致。
    if gain not in VOFA_YAW_GAINS or not math.isfinite(value) or value < 0:
        raise ValueError("unknown yaw gain or value is not finite and nonnegative")
    return f"CMD PID YAW {gain} {value:.9g}\n".encode("ascii")


class VofaBridge:
    def __init__(self, port: int, command_port: int = DEFAULT_VOFA_COMMAND_PORT) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.bind(("127.0.0.1", command_port))
            self.socket.setblocking(False)
        except OSError:
            self.socket.close()
            raise
        self.target = ("127.0.0.1", port)

    def read_command(self) -> bytes | None:
        # 每轮只读一个数据报；只接收已配置的本机 VOFA 端口，不开放任意命令透传。
        try:
            data, source = self.socket.recvfrom(MAX_VOFA_COMMAND_BYTES + 1)
        except BlockingIOError:
            return None
        if source != self.target:
            return None
        return normalize_vofa_command(data)

    def update(self, lines: list[str]) -> None:
        # 每个完整 PID 样本转发一次，不按面板频率限速或合并样本。
        # ponytail: 四通道帧不含时间戳，严格识别 USB 积压时再扩展采样时间。
        for line in lines:
            frame = build_vofa_yaw_frame(line)
            if line.startswith("PARAM{") and line.endswith("}"):
                try:
                    frame = (line + "\n").encode("ascii")
                except UnicodeEncodeError:
                    continue
            if frame is None:
                continue
            try:
                self.socket.sendto(frame, self.target)
            except OSError:
                pass  # 绘图发送失败只丢当前帧，不重试，不中断手柄控制

    def close(self) -> None:
        self.socket.close()


def write_serial_data(serial_port: serial.Serial, data: bytes, port_name: str) -> None:
    try:
        serial_port.write(data)
    except serial.SerialTimeoutException as exc:
        raise RuntimeError(f"Serial write to {port_name} timed out") from exc


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
    vofa: VofaBridge | None = None
    sequence = 0
    read_buffer: str | None = ""
    last_send = 0.0
    last_render = 0.0
    panel_state: dict[str, str] = {}

    try:
        if args.vofa:
            try:
                vofa = VofaBridge(args.vofa_port, args.vofa_command_port)
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
            now = time.monotonic()

            lines, read_buffer = read_available_lines(serial_port, read_buffer)
            update_panel_state(lines, panel_state, log_file)

            joystick_snapshot = ds5_reader.read()

            if now - last_send >= 1.0 / args.send_hz:
                sequence += 1
                write_serial_data(
                    serial_port, (build_packet_line(sequence, joystick_snapshot) + "\n").encode("ascii"),
                    args.teensy_port,
                )
                last_send = now

            if vofa is not None:
                vofa.update(lines)
                try:
                    command = vofa.read_command()
                except (ValueError, OSError) as exc:
                    panel_state["INFO"] = f"INFO{{vofa_command=rejected error={exc}}}"
                else:
                    if command is not None:
                        write_serial_data(serial_port, command, args.teensy_port)

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
    if not 1 <= args.vofa_command_port <= 65535:
        parser.error("--vofa-command-port must be between 1 and 65535")
    if args.vofa and args.vofa_command_port == args.vofa_port:
        parser.error("VOFA receive and command ports must differ")
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
