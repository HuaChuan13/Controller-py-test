import argparse
from pathlib import Path
import sys
import time

# 入口脚本位于子目录中，启动时先把 Controller 根目录加入导入路径，
# 这样 DS5、Sensor 和桥接模块都可以按统一方式导入。
CONTROLLER_ROOT = Path(__file__).resolve().parent.parent
if str(CONTROLLER_ROOT) not in sys.path:
    sys.path.insert(0, str(CONTROLLER_ROOT))

DEFAULT_SEND_HZ = 30
DEFAULT_BAUDRATE = 115200


def build_argument_parser(
    *,
    default_teensy_port: str,
    default_baudrate: int,
    default_deadzone: float,
) -> argparse.ArgumentParser:
    # 当前入口脚本只负责“把几个模块串起来跑”，
    # 所以命令行参数也只保留联调阶段需要的最小集合。
    parser = argparse.ArgumentParser(
        description="Bridge DS5 data to Teensy serial"
    )
    parser.add_argument("--teensy-port", default=default_teensy_port)
    parser.add_argument("--baudrate", type=int, default=default_baudrate)
    parser.add_argument("--send-hz", type=float, default=DEFAULT_SEND_HZ)
    parser.add_argument("--deadzone", type=float, default=default_deadzone)
    parser.add_argument("--view", choices=["normal", "debug"], default="normal")
    parser.add_argument("--log-file", default="")
    return parser


def main() -> int:
    from controller_monitor import ControllerMonitor
    from DS5.ds5_reader import DEFAULT_DEADZONE, Ds5Reader
    from protocol_bridge import DEFAULT_BAUDRATE, DEFAULT_TEENSY_PORT, ProtocolBridge

    args = build_argument_parser(
        default_teensy_port=DEFAULT_TEENSY_PORT,
        default_baudrate=DEFAULT_BAUDRATE,
        default_deadzone=DEFAULT_DEADZONE,
    ).parse_args()

    # 当前过渡版 Controller 只保留 DS5 手动桥接主链：
    # DS5 读取、协议桥接、终端监视。
    ds5_reader = Ds5Reader(deadzone=args.deadzone)
    bridge = None
    monitor = ControllerMonitor(view_mode=args.view, log_path=args.log_file or None)
    last_send = 0.0

    try:
        joystick_name = ds5_reader.open()
        print(f"Joystick: {joystick_name}")

        bridge = ProtocolBridge(args.teensy_port, args.baudrate)
        print(f"Connecting Teensy on {args.teensy_port}, wait for board ready...")
        time.sleep(2.0)

        while True:
            now = time.time()

            # 先回读 Teensy 输出，保证联调时能直接看到下位机接收情况。
            monitor.emit_teensy_lines(bridge.read_available_lines())

            joystick_snapshot = ds5_reader.read()

            # 当前协议仍然是文本总包，因此按固定发送频率推送到 Teensy。
            if now - last_send >= 1.0 / args.send_hz:
                bridge.send_packet(joystick_snapshot)
                last_send = now

            time.sleep(0.001)
    except RuntimeError as exc:
        print(exc)
        return 1
    except KeyboardInterrupt:
        print("\nStop DS5 bridge")
        return 0
    finally:
        # 无论是正常退出还是中断退出，都统一释放串口和 pygame 资源。
        if bridge is not None:
            bridge.close()
        monitor.close()
        ds5_reader.close()


if __name__ == "__main__":
    raise SystemExit(main())
