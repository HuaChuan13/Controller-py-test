from dataclasses import dataclass
from typing import Optional

import serial

from Sensor.hipnuc_parser import HipnucParser


DEFAULT_IMU_PORT = "COM9"
DEFAULT_BAUDRATE = 115200


@dataclass
class ImuSnapshot:
    # 统一保存当前最新的姿态和角速度，桥接层只读取这份快照，不关心底层帧格式。
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    gx: float = 0.0
    gy: float = 0.0
    gz: float = 0.0
    timestamp_ms: int = 0
    valid: bool = False


class ImuReader:
    def __init__(self, port: str = DEFAULT_IMU_PORT, baudrate: int = DEFAULT_BAUDRATE):
        self.port = port
        self.baudrate = baudrate
        self.parser = HipnucParser()
        self.latest = ImuSnapshot()
        self.serial: serial.Serial | None = None

    def open(self) -> None:
        # HI13R4 当前通过串口输出姿态数据，打开串口后主动发送 LOG ENABLE。
        self.serial = serial.Serial(self.port, self.baudrate, timeout=0.001)
        self.serial.write(b"LOG ENABLE\r\n")

    def poll(self) -> Optional[ImuSnapshot]:
        if self.serial is None:
            raise RuntimeError("IMU reader has not been opened")

        if not self.serial.in_waiting:
            return None

        # 底层解析统一交给 HipnucParser，本模块只负责筛选当前控制链需要的字段。
        data = self.serial.read(self.serial.in_waiting)
        packets = self.parser.parse(data)
        for packet in packets:
            if packet.get("type") not in ("HI91", "HI81", "HI83"):
                continue
            if not all(key in packet for key in ("roll", "pitch", "yaw")):
                continue

            gyro = packet.get("gyr", [0.0, 0.0, 0.0])
            if "system_time" in packet:
                timestamp = int(packet["system_time"])
            elif "system_time_us" in packet:
                timestamp = int(packet["system_time_us"] // 1000)
            else:
                timestamp = 0
            self.latest = ImuSnapshot(
                roll=float(packet["roll"]),
                pitch=float(packet["pitch"]),
                yaw=float(packet["yaw"]),
                gx=float(gyro[0]),
                gy=float(gyro[1]),
                gz=float(gyro[2]),
                timestamp_ms=timestamp,
                valid=True,
            )

        return self.latest if self.latest.valid else None

    def close(self) -> None:
        if self.serial is not None:
            self.serial.close()
