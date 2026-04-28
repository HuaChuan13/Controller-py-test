import argparse
from pathlib import Path
import sys
import time

"""
该脚本的作用是通过窜口单独读取IMU的数据
当桥接链路出问题时，可以先用它确认 COM9 上的 HI13R4 是否正常输出。
"""
CONTROLLER_ROOT = Path(__file__).resolve().parent.parent
if str(CONTROLLER_ROOT) not in sys.path:
    sys.path.insert(0, str(CONTROLLER_ROOT))

from Sensor.imu_reader import DEFAULT_BAUDRATE, DEFAULT_IMU_PORT, ImuReader


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read and parse HI13R4 data from serial port"
    )
    parser.add_argument("--port", default=DEFAULT_IMU_PORT)
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()

    imu_reader = ImuReader(args.port, args.baudrate)
    imu_reader.open()

    print(f"Reading HI13R4 from {args.port} @ {args.baudrate}")

    try:
        while True:
            snapshot = imu_reader.poll()
            if snapshot is None:
                time.sleep(0.01)
                continue

            # 单独联调时只打印最关心的姿态角、角速度和时间戳。
            print(
                "roll={roll:.3f} pitch={pitch:.3f} yaw={yaw:.3f} "
                "gx={gx:.3f} gy={gy:.3f} gz={gz:.3f} ts={ts}".format(
                    roll=snapshot.roll,
                    pitch=snapshot.pitch,
                    yaw=snapshot.yaw,
                    gx=snapshot.gx,
                    gy=snapshot.gy,
                    gz=snapshot.gz,
                    ts=snapshot.timestamp_ms,
                )
            )
    except KeyboardInterrupt:
        print("\nStop reading HI13R4")
        return 0
    finally:
        imu_reader.close()


if __name__ == "__main__":
    raise SystemExit(main())
