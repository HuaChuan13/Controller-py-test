# Controller

ROV 的 Python 调试上位机，用于读取 DualSense 手柄、发送控制数据并查看下位机回显。正式产品上位机位于 C# 项目，本仓库只保留链路联调所需功能。

当前支持两条链路：

```text
串口直连：
DS5 -> Controller -> USB/PKT -> Teensy

网关联调：
DS5 -> Controller -> UDP/JSON -> Jetson rov_gateway -> USB/PKT -> Teensy
```

## 环境

- Python 3.11 或更高版本
- [uv](https://docs.astral.sh/uv/)
- `pygame`
- `pyserial`

安装依赖：

```powershell
cd D:\Code\ROV_code\Controller
uv sync
```

## 串口直连

默认以 `50Hz` 发送控制数据并以 `50Hz` 刷新状态面板：

```powershell
uv run python ds5_teensy_demo.py --mode serial --teensy-port COM8
```

`COM8` 需要按实际 Teensy 端口修改。面板固定显示：

```text
CTRL{} SYS{} DS5{} MIX{} PWM{} SHT40{} PM08{} DEPTH{} IMU{}
```

降低面板刷新频率不会改变控制发送频率：

```powershell
uv run python ds5_teensy_demo.py --mode serial --teensy-port COM8 --render-hz 10
```

## Gateway 模式

先在 Jetson 上启动 `rov_gateway`，再在 PC 上运行：

```powershell
uv run python ds5_teensy_demo.py --mode gateway --gateway-host 192.168.10.2 --gateway-port 14550
```

Gateway 模式不打开 PC 上的 Teensy 串口。状态面板会额外显示 `GATEWAY{}`。

同一时间不要让本程序和 C# 上位机同时控制同一个 Gateway。当前 Gateway 按最后一个合法 PC 地址回传数据，多客户端会互相抢占控制权。

## DS5 映射检查

只检查手柄原始轴、按键、十字键和打包结果，不打开串口或 Gateway：

```powershell
uv run python ds5_teensy_demo.py --ds5-debug
```

Linux 上可显式指定 SDL 输入设备：

```bash
uv run python ds5_teensy_demo.py \
  --ds5-debug \
  --sdl-joystick-device /dev/input/js0
```

## 常用参数

| 参数 | 默认值 | 作用 |
| --- | ---: | --- |
| `--mode` | `serial` | 选择 `serial` 或 `gateway` |
| `--teensy-port` | `COM8` | Teensy 串口 |
| `--gateway-host` | `192.168.10.2` | Jetson 地址 |
| `--gateway-port` | `14550` | Gateway UDP 端口 |
| `--send-hz` | `50` | 控制数据发送频率 |
| `--render-hz` | `50` | 状态面板刷新频率 |
| `--deadzone` | `0.08` | 摇杆死区 |
| `--log-file` | 空 | 将收到的回显追加到文件 |

查看全部参数：

```powershell
uv run python ds5_teensy_demo.py --help
```

项目状态、协议、Linux 环境和排障信息见 [PROJECT_HANDOVER.md](PROJECT_HANDOVER.md)。
