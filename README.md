# Controller

## 当前定位

当前 `Controller` 只保留一个最小上位机 demo，用来完成三件事：

- 读取 `DS5`
- 将手柄状态打包成 `PKT` 发送给 `Teensy`
- 固定面板显示 `Teensy` 回传的调试信息

当前主链是：

`DS5 -> Controller -> PKT -> Teensy`

## 目录结构

```text
Controller/
├─ ds5_reader.py
├─ ds5_teensy_demo.py
├─ pyproject.toml
├─ uv.lock
└─ README.md
```

## 依赖管理

首次进入项目时执行：

```powershell
cd D:\Code\ROV_code\Controller
uv sync
```

工控机 Linux 端已验证路径：

```bash
ssh nvidia@192.168.10.2
cd ~/Code/controller
eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
uv sync --python /home/linuxbrew/.linuxbrew/bin/python3.13
```

注意：远端 Homebrew 默认 `python3` 可能是 3.14，`pygame==2.6.1` 会尝试源码编译并因为缺少 `gcc-12` 失败。当前测试用 Homebrew Python 3.13 可以直接安装 wheel。

## 运行方式

默认端口约定：

- `COM8 = Teensy`

普通模式只显示 `CTRL{}` 和 `SYS{}`：

```powershell
cd D:\Code\ROV_code\Controller
uv run python ds5_teensy_demo.py --teensy-port COM8 --view normal
```

调试模式显示 `CTRL{}`、`MIX{}`、`PWM{}`、`SYS{}`、`DS5{}`：

```powershell
cd D:\Code\ROV_code\Controller
uv run python ds5_teensy_demo.py --teensy-port COM8 --view debug
```

终端默认不会继续刷屏，而是按固定频率重绘一个状态面板。需要调整面板刷新频率时，可追加：

```powershell
uv run python ds5_teensy_demo.py --teensy-port COM8 --view debug --render-hz 10
```

## 工控机 Linux 运行

当前工控机已验证：

- Controller 目录：`~/Code/controller`
- Teensy 稳定串口：`/dev/serial/by-id/usb-Teensyduino_USB_Serial_13173040-if00`
- Teensy 当前也可用：`/dev/ttyACM0`
- DS5 当前输入设备：`/dev/input/js0`

运行前先加载 Homebrew 环境，并指定 SDL 读取 `js0`：

```bash
cd ~/Code/controller
eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
export SDL_JOYSTICK_DEVICE=/dev/input/js0

uv run python ds5_teensy_demo.py \
  --teensy-port /dev/serial/by-id/usb-Teensyduino_USB_Serial_13173040-if00 \
  --view debug
```

如果稳定串口路径不存在，可以临时改用：

```bash
uv run python ds5_teensy_demo.py --teensy-port /dev/ttyACM0 --view debug
```

当前远端实测输出包含 `CTRL{}`、`SYS{}`、`DS5{}`、`MIX{}`、`PWM{}`，说明 `DS5 -> Controller -> PKT -> Teensy` 链路已跑通。

### DS5 映射自检

映射自检只读取手柄，不打开串口：

```bash
cd ~/Code/controller
eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"

uv run python ds5_teensy_demo.py \
  --ds5-debug \
  --sdl-joystick-device /dev/input/js0
```

自检面板会显示：

- `axes`：每个轴的 raw 值
- `buttons`：每个 button index 的 raw 值
- `pressed`：当前按下的 button index 和对应 bit 值
- `hats`：十字键 hat 值
- `packed`：当前 `ds5_reader.py` 打包后的 `JoystickSnapshot`

现场验证时每次只按一个控制量，重点记录十字键、面键、`L3`、`R3`、`L2`、`R2` 对应的 raw 变化。

### Linux 权限与输入设备

串口权限当前正常，`nvidia` 用户已在 `dialout` 组。若换机器后串口打不开，可检查：

```bash
ls -l /dev/ttyACM* /dev/serial/by-id/*
id
```

DS5 在系统里能看到 `event` 和 `js` 设备，但 SSH 环境下 `pygame` 默认可能因为 `/dev/input/event*` 权限不足而显示“未检测到 DS5 手柄”。当前可行方式是显式指定：

```bash
export SDL_JOYSTICK_DEVICE=/dev/input/js0
```

若要长期不依赖这个环境变量，可以再处理 `input` 组或 udev 权限。

### 当前 Linux 映射待确认

当前静止状态下 raw axes 为 `[0, 0, 0, 0, 0, 0]`，因此现有 Windows 归一化逻辑会把 `L2/R2` 显示为 `0.500`。十字键、面键、`R3`、`L3`、扳机轴仍需要在 Linux 上逐项按键确认后再最小修改 `ds5_reader.py`。

## 当前模块职责

- `ds5_reader.py`：读取 `DS5`，整理摇杆、扳机和按键位图
- `ds5_teensy_demo.py`：发送 `PKT`、缓存最新回显并固定面板刷新显示
