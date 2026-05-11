import pygame
import time

pygame.init()
pygame.joystick.init()

count = pygame.joystick.get_count()

if count == 0:
    print("没有检测到手柄")
else:
    joy = pygame.joystick.Joystick(0)

    print("手柄名称:", joy.get_name())
    print("GUID:", joy.get_guid())

    while True:
        pygame.event.pump()  # 需要定期刷新事件队列
        power = joy.get_power_level()
        print("电量状态:", power)
        time.sleep(1)