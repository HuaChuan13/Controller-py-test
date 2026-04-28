from dataclasses import dataclass

import pygame


DEFAULT_DEADZONE = 0.08
HAT_UP_BIT = 11
HAT_DOWN_BIT = 12
HAT_LEFT_BIT = 13
HAT_RIGHT_BIT = 14


@dataclass
class JoystickSnapshot:
    # 统一保存当前一帧 DS5 输入，后续桥接和控制映射都只依赖这份结构化数据。
    lx: float = 0.0
    ly: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    l2: float = 0.0
    r2: float = 0.0
    buttons: int = 0


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def apply_deadzone(value: float, deadzone: float) -> float:
    return 0.0 if abs(value) < deadzone else value


def buttons_bitmask(js: pygame.joystick.Joystick) -> int:
    mask = 0
    for index in range(min(js.get_numbuttons(), 32)):
        if js.get_button(index):
            mask |= 1 << index

    if js.get_numhats() > 0:
        hat_x, hat_y = js.get_hat(0)
        if hat_y > 0:
            mask |= 1 << HAT_UP_BIT
        if hat_y < 0:
            mask |= 1 << HAT_DOWN_BIT
        if hat_x < 0:
            mask |= 1 << HAT_LEFT_BIT
        if hat_x > 0:
            mask |= 1 << HAT_RIGHT_BIT
    return mask


class Ds5Reader:
    def __init__(self, deadzone: float = DEFAULT_DEADZONE, joystick_index: int = 0):
        self.deadzone = deadzone
        self.joystick_index = joystick_index
        self.joystick: pygame.joystick.Joystick | None = None

    def open(self) -> str:
        # 这里统一完成 pygame 和手柄初始化，避免入口脚本分散处理底层细节。
        pygame.init()
        pygame.joystick.init()

        if pygame.joystick.get_count() <= self.joystick_index:
            raise RuntimeError("未检测到 DS5 手柄")

        self.joystick = pygame.joystick.Joystick(self.joystick_index)
        self.joystick.init()
        return self.joystick.get_name()

    def read(self) -> JoystickSnapshot:
        if self.joystick is None:
            raise RuntimeError("DS5 reader has not been opened")

        pygame.event.pump()

        # 当前按 Windows 下 DS5 的轴顺序读取。
        # 如果后续换手柄或驱动，这里是主要调整点。
        lx = self.joystick.get_axis(0) if self.joystick.get_numaxes() > 0 else 0.0
        ly = self.joystick.get_axis(1) if self.joystick.get_numaxes() > 1 else 0.0
        rx = self.joystick.get_axis(2) if self.joystick.get_numaxes() > 2 else 0.0
        ry = self.joystick.get_axis(3) if self.joystick.get_numaxes() > 3 else 0.0
        l2 = self.joystick.get_axis(4) if self.joystick.get_numaxes() > 4 else -1.0
        r2 = self.joystick.get_axis(5) if self.joystick.get_numaxes() > 5 else -1.0

        # 前推为正，当前把纵向摇杆统一做一次方向修正。
        ly = -ly
        ry = -ry

        # 读取阶段就完成死区、限幅和扳机归一化，
        # 后续模块直接使用标准化后的控制量。
        return JoystickSnapshot(
            lx=clamp(apply_deadzone(lx, self.deadzone), -1.0, 1.0),
            ly=clamp(apply_deadzone(ly, self.deadzone), -1.0, 1.0),
            rx=clamp(apply_deadzone(rx, self.deadzone), -1.0, 1.0),
            ry=clamp(apply_deadzone(ry, self.deadzone), -1.0, 1.0),
            l2=clamp((l2 + 1.0) * 0.5, 0.0, 1.0),
            r2=clamp((r2 + 1.0) * 0.5, 0.0, 1.0),
            buttons=buttons_bitmask(self.joystick),
        )

    def close(self) -> None:
        # Controller 结束时显式关闭 pygame，避免下次启动残留状态。
        pygame.joystick.quit()
        pygame.quit()
