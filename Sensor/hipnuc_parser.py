import struct

# HI13R4 的上位机解析工具。
# 这一层只关心“如何把原始二进制帧拆成 Python 字典”，
# 不参与任何控制映射和串口桥接逻辑。
CHSYNC1 = 0x5A
CHSYNC2 = 0xA5
CH_HDR_SIZE = 6
GRAVITY = 9.80665
R2D = 57.2957795130823

HI81_GYR_SCALE = 0.001 * R2D
HI81_ACC_SCALE = 0.0048828
HI81_MAG_SCALE = 0.030517

FRAME_TAG_HI91 = 0x91
FRAME_TAG_HI81 = 0x81
FRAME_TAG_HI83 = 0x83

HI83_BMAP_ACC_B = 1 << 0
HI83_BMAP_GYR_B = 1 << 1
HI83_BMAP_MAG_B = 1 << 2
HI83_BMAP_RPY = 1 << 3
HI83_BMAP_QUAT = 1 << 4
HI83_BMAP_SYSTEM_TIME = 1 << 5
HI83_BMAP_UTC = 1 << 6
HI83_BMAP_AIR_PRESSURE = 1 << 7
HI83_BMAP_TEMPERATURE = 1 << 8
HI83_BMAP_INCLINATION = 1 << 9
HI83_BMAP_HSS = 1 << 10
HI83_BMAP_HSS_FRQ = 1 << 11
HI83_BMAP_VEL_ENU = 1 << 12
HI83_BMAP_ACC_ENU = 1 << 13
HI83_BMAP_INS_LON_LAT_MSL = 1 << 14
HI83_BMAP_GNSS_QUALITY_NV = 1 << 15
HI83_BMAP_OD_SPEED = 1 << 16
HI83_BMAP_UNDULATION = 1 << 17
HI83_BMAP_DIFF_AGE = 1 << 18
HI83_BMAP_NODE_ID = 1 << 19
HI83_BMAP_GNSS_LON_LAT_MSL = 1 << 30
HI83_BMAP_GNSS_VEL = 1 << 31


class HipnucParser:
    def __init__(self) -> None:
        self.buffer = bytearray()

    @staticmethod
    def crc16_update(crc: int, data: bytes) -> int:
        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                temp = crc << 1
                if crc & 0x8000:
                    temp ^= 0x1021
                crc = temp
        return crc & 0xFFFF

    def _parse_hi91(self, payload: bytes, offset: int):
        # HI91 是信息最完整的姿态帧，包含姿态角、陀螺仪、加速度和四元数。
        packet = {
            "type": "HI91",
            "temperature": struct.unpack_from("<b", payload, offset + 3)[0],
            "air_pressure": struct.unpack_from("<f", payload, offset + 4)[0],
            "system_time": struct.unpack_from("<I", payload, offset + 8)[0],
            "acc": [
                x * GRAVITY for x in struct.unpack_from("<3f", payload, offset + 12)
            ],
            "gyr": list(struct.unpack_from("<3f", payload, offset + 24)),
            "mag": list(struct.unpack_from("<3f", payload, offset + 36)),
            "roll": struct.unpack_from("<f", payload, offset + 48)[0],
            "pitch": struct.unpack_from("<f", payload, offset + 52)[0],
            "yaw": struct.unpack_from("<f", payload, offset + 56)[0],
            "quat": list(struct.unpack_from("<4f", payload, offset + 60)),
        }
        return packet, offset + 76

    def _parse_hi81(self, payload: bytes, offset: int):
        # HI81 是更紧凑的一种输出格式，姿态角和原始 IMU 数据都需要按固定比例缩放。
        packet = {
            "type": "HI81",
            "system_time": struct.unpack_from("<I", payload, offset + 6)[0],
            "gyr": [
                value * HI81_GYR_SCALE
                for value in struct.unpack_from("<3h", payload, offset + 12)
            ],
            "acc": [
                value * HI81_ACC_SCALE
                for value in struct.unpack_from("<3h", payload, offset + 18)
            ],
            "mag": [
                value * HI81_MAG_SCALE
                for value in struct.unpack_from("<3h", payload, offset + 24)
            ],
            "roll": struct.unpack_from("<h", payload, offset + 42)[0] * 0.01,
            "pitch": struct.unpack_from("<h", payload, offset + 44)[0] * 0.01,
            "yaw": struct.unpack_from("<H", payload, offset + 46)[0] * 0.01,
        }
        return packet, offset + 104

    def _parse_hi83(self, payload: bytes, offset: int):
        # HI83 按位图决定当前帧里实际包含哪些字段，因此需要动态解析。
        bitmap = struct.unpack_from("<I", payload, offset + 4)[0]
        idx = offset + 8
        packet = {
            "type": "HI83",
            "data_bitmap": bitmap,
        }

        if bitmap & HI83_BMAP_ACC_B:
            packet["acc"] = list(struct.unpack_from("<3f", payload, idx))
            idx += 12
        if bitmap & HI83_BMAP_GYR_B:
            packet["gyr"] = list(struct.unpack_from("<3f", payload, idx))
            idx += 12
        if bitmap & HI83_BMAP_MAG_B:
            packet["mag"] = list(struct.unpack_from("<3f", payload, idx))
            idx += 12
        if bitmap & HI83_BMAP_RPY:
            rpy = struct.unpack_from("<3f", payload, idx)
            packet["roll"] = rpy[0]
            packet["pitch"] = rpy[1]
            packet["yaw"] = rpy[2]
            idx += 12
        if bitmap & HI83_BMAP_QUAT:
            packet["quat"] = list(struct.unpack_from("<4f", payload, idx))
            idx += 16
        if bitmap & HI83_BMAP_SYSTEM_TIME:
            packet["system_time_us"] = struct.unpack_from("<Q", payload, idx)[0]
            idx += 8
        if bitmap & HI83_BMAP_UTC:
            idx += 8
        if bitmap & HI83_BMAP_AIR_PRESSURE:
            idx += 4
        if bitmap & HI83_BMAP_TEMPERATURE:
            idx += 4
        if bitmap & HI83_BMAP_INCLINATION:
            idx += 12
        if bitmap & HI83_BMAP_HSS:
            idx += 12
        if bitmap & HI83_BMAP_HSS_FRQ:
            idx += 12
        if bitmap & HI83_BMAP_VEL_ENU:
            idx += 12
        if bitmap & HI83_BMAP_ACC_ENU:
            idx += 12
        if bitmap & HI83_BMAP_INS_LON_LAT_MSL:
            idx += 24
        if bitmap & HI83_BMAP_GNSS_QUALITY_NV:
            idx += 4
        if bitmap & HI83_BMAP_OD_SPEED:
            idx += 4
        if bitmap & HI83_BMAP_UNDULATION:
            idx += 4
        if bitmap & HI83_BMAP_DIFF_AGE:
            idx += 4
        if bitmap & HI83_BMAP_NODE_ID:
            idx += 4
        if bitmap & HI83_BMAP_GNSS_LON_LAT_MSL:
            idx += 24
        if bitmap & HI83_BMAP_GNSS_VEL:
            idx += 12

        return packet, idx

    def parse(self, incoming: bytes):
        # 串口数据可能分包到达，这里先持续累积，再按完整帧解析。
        self.buffer += incoming
        packets = []

        while len(self.buffer) >= CH_HDR_SIZE:
            if self.buffer[0] != CHSYNC1 or self.buffer[1] != CHSYNC2:
                del self.buffer[0]
                continue

            frame_length = struct.unpack_from("<H", self.buffer, 2)[0]
            total_length = CH_HDR_SIZE + frame_length
            if len(self.buffer) < total_length:
                break

            frame = bytes(self.buffer[:total_length])
            del self.buffer[:total_length]

            crc_received = struct.unpack_from("<H", frame, 4)[0]
            crc_calculated = self.crc16_update(0, frame[:4] + frame[6:])
            if crc_received != crc_calculated:
                continue

            payload = frame[CH_HDR_SIZE:]
            offset = 0
            while offset < len(payload):
                # 一个物理帧中可能连续带多个逻辑数据块，这里逐块拆开。
                item_type = payload[offset]
                if item_type == FRAME_TAG_HI91:
                    packet, offset = self._parse_hi91(payload, offset)
                    packets.append(packet)
                elif item_type == FRAME_TAG_HI81:
                    packet, offset = self._parse_hi81(payload, offset)
                    packets.append(packet)
                elif item_type == FRAME_TAG_HI83:
                    packet, offset = self._parse_hi83(payload, offset)
                    packets.append(packet)
                else:
                    offset += 1

        return packets
