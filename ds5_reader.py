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


class Ds5Reader:
    def __init__(self, deadzone: float = DEFAULT_DEADZONE, joystick_index: int = 0):
        self.deadzone = deadzone
        self.joystick_index = joystick_index
        self.joystick: pygame.joystick.Joystick | None = None

    def open(self) -> str:
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
