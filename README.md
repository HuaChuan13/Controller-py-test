# Controller

ROV 的 Python 调试上位机，用于读取 DualSense 手柄、发送控制数据并查看下位机回显。正式产品上位机位于 C# 项目，本仓库只保留链路联调所需功能。

当前支持两条链路：

```text
串口直连：
DS5 -> Controller -> USB/PKT -> Teensy

网关联调：
DS5 -> Controller -> UDP/PKT -> Jetson rov_gateway -> USB/PKT -> Teensy
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
SYS{} DS5{} CTRL{} MIX{} PWM{} SHT40{} PM08{} DEPTH{} IMU{} PID{} PARAM{}
```

只有IMU在显示端拆成温度、加速度（ACC）、角速度（GYR）、角度（ANG）四行，三轴数值使用固定列宽；其余标签保持原始文本。ACC/GYR/ANG不是新增回传标签，原始日志和50Hz频率不变，无需重新烧录Teensy。

降低面板刷新频率不会改变控制发送频率：

```powershell
uv run python ds5_teensy_demo.py --mode serial --teensy-port COM8 --render-hz 10
```

### VOFA 偏航曲线

在串口模式增加 `--vofa`，将偏航闭环的四个数值转发到本机 VOFA：

```powershell
uv run python ds5_teensy_demo.py --mode serial --teensy-port COM8 --send-hz 200 --vofa
```

VOFA 选择 **UDP**，本地监听地址设为 `127.0.0.1`（或 `0.0.0.0`），监听端口设为 **1347**，协议选择 [FireWater](https://www.vofa.plus/docs/learning/dataengines/firewater/)。VOFA 不要打开 COM8，串口仍由 Controller 独占。

| 通道 | 数值 | 单位 |
| --- | --- | --- |
| 0 | 期望 yaw 角：`PID.yaw_sp` | deg |
| 1 | 实际 yaw 角：`PID.yaw_deg` | deg |
| 2 | 期望角速度：`PID.rate_sp` | deg/s |
| 3 | 实际角速度：`PID.gz_dps` | deg/s |

建议用两个波形图：通道 0、1 放在角度图，通道 2、3 放在角速度图。按 R1 请求自稳，确认 Controller 显示 `mode=STABILIZE` 且 `imu_alive=1` 后才会有曲线；手动模式、掉线或字段无效时停止转发，不补零、不重复旧数据。

需要烧录支持独立 `PID{}` 小帧的新版 Teensy 固件。固件每完成一个有效的新偏航样本就输出一条 PID 记录，200Hz IMU 对应约 200 组/秒；Controller 逐条转发，不从 50Hz 面板缓存拼帧，也不把同一批的多个样本合并成一个。旧版仅输出 `CTRL/IMU` 的固件不再提供 VOFA 数据。

上面的命令将手柄发包设为目标 200Hz；完整遥测和终端面板仍是 50Hz，VOFA 数据接收与面板刷新独立。默认不加 `--send-hz` 仍按 50Hz 发手柄，避免改变其他使用方式。USB/PC 延迟可能造成批量到达或丢帧，实际帧率需实机确认，不能把曲线点数当成精确采样时间。

面板在IMU四行（IMU/ACC/GYR/ANG）之后显示最新一条 `PID{}`，仍按50Hz刷新，不影响约200Hz的VOFA转发，也不增加Teensy串口数据。尚未收到时显示 `PID{waiting}`；手动或故障停止发送后保留最后值，需结合实际模式和反馈状态判断有效性。这项显示调整不需要重新烧录已支持PID小帧的固件。

更换端口时使用 `--vofa-port 1348`，VOFA 的监听端口也改为同一个值。不加 `--vofa` 时不会创建 VOFA socket，但仍显示收到的PID行。

### VOFA 手动改参

需要烧录支持 `CMD PID YAW` 的新版固件；上面的 `--vofa` 命令同时启用曲线和调参，不新增运行入口。

VOFA 仍选择 UDP/FireWater，设置为：

| 配置项 | 值 |
| --- | --- |
| 远程 IP | `127.0.0.1` |
| 远程端口（Controller 接收命令） | `1346` |
| 本地端口（VOFA 接收曲线与回执） | `1347` |

命令设置保持 **Abc / Str** 文本模式，发送内容不要只填 `Kp`。四个命令分别填下面的模板，末尾的 `\r\n` 也要输入，不加引号；VOFA 会将其转成实际行尾：

| 名称 | 发送内容 | 滑块范围（固件默认配置） | 初始值 |
| --- | --- | --- | --- |
| yaw外环Kp | `CMD PID YAW ANGLE_KP %.6f\r\n` | `0~10` | `2` |
| yaw内环Kp | `CMD PID YAW RATE_KP %.6f\r\n` | `0~1` | `0.02` |
| yaw内环Ki | `CMD PID YAW RATE_KI %.6f\r\n` | `0~1` | `0` |
| yaw内环Kd | `CMD PID YAW RATE_KD %.6f\r\n` | `0~0.1` | `0` |

占位符接收绑定控件的浮点值，见[官方命令/参数说明](https://www.vofa.plus/docs/learning/start/data_cmd_parameter/)。先使用单次发送，不开启连续发送。上下限只是防误输入的软件边界，不是水下安全增益或推荐整定范围；先断开推进器动力验收链路。

四个上限只在Teensy项目的`lib/core/Config.h`维护：`kYawAngleKpMax`、`kYawRateKpMax`、`kYawRateKiMax`、`kYawRateKdMax`。按需修改、重新编译烧录，并调整VOFA滑块范围即可；Controller不再维护同一组上限，只检查白名单、单行和有限非负数。

调参流程：先确认 Controller 显示 **MANUAL**，修改滑块并发送；例如实际发出的文本应为 `CMD PID YAW RATE_KP 0.030000` 加换行，而不是原样的 `%f`。Controller 只接收这四类参数命令，不会转发 VOFA 发来的 MODE、LIMIT 或其他任意串口文本。

Teensy 返回后，完整面板在 `PID{}` 后显示最新一条：

```text
PARAM{status=OK angle_kp=2.000000 rate_kp=0.030000 rate_ki=0.000000 rate_kd=0.000000}
```

`status=OK` 且四个实际值符合预期才算修改成功；`MODE_REJECTED` 表示处于自稳或已请求自稳，参数未改。非法格式、未知参数、负数或非有限数由Controller显示`INFO{vofa_command=rejected ...}`。超过Config.h上限的命令会转发，但固件只增加`SYS{rx_rejected}`，不产生新PARAM；旧PARAM缓存不能作为本次修改成功的确认。

参数不作为第五条曲线：回执只随调参事件发送，面板重复显示缓存，不重复发包。USB 暂时不可写时固件保留一份最新待发结果；VOFA UDP 回执不保证送达，未看到新回执可手动重发相同设定确认，不自动重试。固件启动的 `SYS{ready=1}` 会清除 Controller 的旧 PARAM 缓存。

确认后再按 R1 进入自稳测试。运行参数仅保存在 RAM，重启恢复 `Config.h` 默认值；不修改原有 200Hz 控制/PWM、推力上限、平滑或电机方向。调参目前不支持 Gateway 模式。

端口有冲突时可使用 `--vofa-command-port` 修改 Controller 命令监听端口，同时修改 VOFA 远程端口；`--vofa-port` 修改的是 VOFA 本地端口，两者不能相同。

## Gateway 模式

先在 Jetson 上启动 `rov_gateway`，再在 PC 上运行：

```powershell
uv run python ds5_teensy_demo.py --mode gateway --gateway-host 192.168.10.2 --gateway-port 14550
```

Gateway 模式直接发送与串口模式相同的 `PKT` 文本，并接收 Gateway 原样转发的 Teensy 标签。它不打开 PC 上的 Teensy 串口，状态面板会额外显示 `GATEWAY{}`。

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
| `--vofa` | 关闭 | 串口模式下转发偏航数据并接收四类调参命令 |
| `--vofa-port` | `1347` | 本机 VOFA 的 UDP 监听端口 |
| `--vofa-command-port` | `1346` | Controller 的本机 UDP 命令监听端口 |

查看全部参数：

```powershell
uv run python ds5_teensy_demo.py --help
```

项目状态、协议、Linux 环境和排障信息见 [PROJECT_HANDOVER.md](PROJECT_HANDOVER.md)。
