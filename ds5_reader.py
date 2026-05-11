import os
from dataclasses import dataclass

import pygame


DEFAULT_DEADZONE = 0.08
DPAD_UP_BIT = 11
DPAD_DOWN_BIT = 12
DPAD_LEFT_BIT = 13
DPAD_RIGHT_BIT = 14


@dataclass
class JoystickSnapshot:
    lx: float = 0.0
    ly: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    l2: float = 0.0
    r2: float = 0.0
    buttons: int = 0


@dataclass
class JoystickInfo:
    name: str
    guid: str
    axes: int
    buttons: int
    hats: int


@dataclass
class JoystickRawSnapshot:
    axes: tuple[float, ...]
    buttons: tuple[int, ...]
    hats: tuple[tuple[int, int], ...]


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
            mask |= 1 << DPAD_UP_BIT
        if hat_y < 0:
            mask |= 1 << DPAD_DOWN_BIT
        if hat_x < 0:
            mask |= 1 << DPAD_LEFT_BIT
        if hat_x > 0:
            mask |= 1 << DPAD_RIGHT_BIT
    return mask


def build_open_error(joystick_index: int, joystick_count: int) -> str:
    message = f"未检测到 DS5 手柄: requested index={joystick_index}, count={joystick_count}"
    if os.name == "posix":
        message += (
            "\nLinux 提示: 如果系统能看到 /dev/input/js0，但 pygame count=0，"
            "可以先设置 SDL_JOYSTICK_DEVICE=/dev/input/js0；"
            "长期方案是处理 /dev/input/event* 的 input 组或 udev 权限。"
        )
    return message


class Ds5Reader:
    def __init__(self, deadzone: float = DEFAULT_DEADZONE, joystick_index: int = 0):
        self.deadzone = deadzone
        self.joystick_index = joystick_index
        self.joystick: pygame.joystick.Joystick | None = None

    def open(self) -> str:
        pygame.init()
        pygame.joystick.init()

        joystick_count = pygame.joystick.get_count()
        if joystick_count <= self.joystick_index:
            raise RuntimeError(build_open_error(self.joystick_index, joystick_count))

        self.joystick = pygame.joystick.Joystick(self.joystick_index)
        self.joystick.init()
        return self.joystick.get_name()

    def info(self) -> JoystickInfo:
        if self.joystick is None:
            raise RuntimeError("DS5 reader has not been opened")

        return JoystickInfo(
            name=self.joystick.get_name(),
            guid=self.joystick.get_guid(),
            axes=self.joystick.get_numaxes(),
            buttons=self.joystick.get_numbuttons(),
            hats=self.joystick.get_numhats(),
        )

    def read_raw(self) -> JoystickRawSnapshot:
        if self.joystick is None:
            raise RuntimeError("DS5 reader has not been opened")

        pygame.event.pump()
        return JoystickRawSnapshot(
            axes=tuple(
                self.joystick.get_axis(index)
                for index in range(self.joystick.get_numaxes())
            ),
            buttons=tuple(
                self.joystick.get_button(index)
                for index in range(self.joystick.get_numbuttons())
            ),
            hats=tuple(
                self.joystick.get_hat(index)
                for index in range(self.joystick.get_numhats())
            ),
        )

    def read(self) -> JoystickSnapshot:
        if self.joystick is None:
            raise RuntimeError("DS5 reader has not been opened")

        pygame.event.pump()

        lx = self.joystick.get_axis(0) if self.joystick.get_numaxes() > 0 else 0.0
        ly = self.joystick.get_axis(1) if self.joystick.get_numaxes() > 1 else 0.0
        rx = self.joystick.get_axis(2) if self.joystick.get_numaxes() > 2 else 0.0
        ry = self.joystick.get_axis(3) if self.joystick.get_numaxes() > 3 else 0.0
        l2 = self.joystick.get_axis(4) if self.joystick.get_numaxes() > 4 else -1.0
        r2 = self.joystick.get_axis(5) if self.joystick.get_numaxes() > 5 else -1.0

        ly = -ly
        ry = -ry

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
        pygame.joystick.quit()
        pygame.quit()
