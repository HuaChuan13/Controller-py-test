# ROV Controller 项目交接

> 最近源码核对：2026-10-15
>
> 通信基线：串口与 Gateway 模式共用同一套 `PKT`/标签文本

本文只记录当前可用状态，不记录逐次开发流水。行为变化时直接修改对应章节，历史过程以 Git 为准。

## 1. 项目定位

本仓库是轻量 Python 调试上位机，负责：

- 读取 DualSense 手柄；
- 将控制状态发送给 Teensy 或 Jetson Gateway；
- 缓存并显示最新的下位机回显；
- 检查 Windows 和 Linux 下的 DS5 原始映射。
- 可选将串口偏航遥测转发给本机 VOFA 绘图。

当前链路：

```text
串口调试：
DS5 -> Controller -> USB/PKT -> Teensy
Teensy -> Controller -> UDP/FireWater :1347 -> 本机 VOFA（可选、只读）

三层联调：
DS5 -> Controller -> UDP/PKT :14550 -> Jetson rov_gateway
     -> USB/PKT -> Teensy
```

正式上位机位于 `ROV_Control_Station` C# 项目。该项目完成后，本仓库仍作为串口、Gateway 和手柄映射诊断工具保留，不与 C# 项目竞争产品功能。

## 2. 当前状态

### 已实现

- Windows/Linux DS5 读取、摇杆死区、扳机归一化和按键位图。
- `serial` 模式：通过 USB 串口发送 Teensy `PKT`。
- `gateway` 模式：通过 UDP 直接发送 `PKT`，并接收逐行回传的 Teensy 标签。
- 单一完整状态面板，不再提供普通/调试视图切换。
- IMU四行（IMU/ACC/GYR/ANG）之后显示最新`PID{}`原文，随面板50Hz刷新，与约200Hz VOFA逐帧转发独立，不增加USB或UDP数据。
- 仅IMU显示拆为温度、加速度（ACC）、角速度（GYR）、角度（ANG）四行并固定列宽，其余标签保持原始文本。
- `--ds5-debug` 原始轴、按键、十字键和打包结果检查。
- 可配置控制发送频率、面板刷新频率、串口和 Gateway 地址。
- 可选文本日志。
- Windows 串口打开超时和串口写入超时保护。
- 串口模式 `--vofa`：逐条转发独立 `PID{}` 四通道小帧，随固件有效新样本约 200Hz；完整回显和面板仍为 50Hz。
- 串口每轮最多读取4096字节，换行前文本最多4096字符（固件为ASCII）。完整行超长时整行丢弃；半行超长后持续丢弃到换行，避免把坏行尾部当作新记录。

固定面板标签：

```text
SYS DS5 CTRL MIX PWM SHT40 PM08 DEPTH IMU PID
```

Gateway 模式额外显示 `GATEWAY`；无标签信息使用 `INFO` 显示。

PID尚未收到时显示`PID{waiting}`，之后保存最后一条。切回手动或反馈故障后固件停止PID输出，面板不会清零，需结合实际模式和反馈状态判断有效性；不加`--vofa`也可显示收到的PID行。此显示调整不改变Teensy固件、串口数据和VOFA发送路径。

面板中的IMU仅在绘制时解析，温度/角度保留2位小数，加速度/角速度保留3位小数；每个数值占9字符，缺失为`---`、无效为`ERR`、超宽为`OVF`。三轴行固定64字符，未收到IMU时也保留四行占位。VOFA另从原始遥测解析所需字段，不读取面板格式化结果。

ACC/GYR/ANG只是显示分组，对应固件原有IMU标签；这项排版本身不改变缓存、日志或50Hz面板，也不需要固件修改。新增IMU字段需更新`IMU_ROWS`；其他原有标签继续显示完整字段。独立200Hz PID转发需要配套新版固件，见后面的VOFA接口。

### 已有实机结论

- Windows 串口直连链路可用于 Teensy 控制和传感器回显。
- Gateway UDP 模式使用 Windows 本地临时端口，并按最后一个合法来源接收回传。
- Jetson 上曾使用 Homebrew Python 3.13、`/dev/input/js0` 和 Teensy 稳定设备路径完成链路测试。
- Controller 的串口打开或写入超时曾由 Teensy 的 PM08/CAN 路径失去响应触发；上位机超时通常是下位机或 USB 状态的症状，不代表控制数值超限。

### 仍需确认

- 当前最新固件全部传感器标签经 Gateway 的完整联调。
- Linux 上十字键、面键、L3/R3 和扳机轴的最终映射。
- 更换手柄、系统或 SDL 版本后的轴编号一致性。
- VOFA 实际界面与 DS5、Teensy 串口同时运行时的曲线和持续控制；本次仅完成软件测试和 UDP 回环收包。

### 未实现

- 正式产品 UI、任务控制、参数配置和自建数据可视化界面；当前绘图复用外部 VOFA。
- VOFA 调参命令、Gateway 到 VOFA 转发、其余三个闭环轴的通道。
- 串口掉线后的自动重连。
- 多 Gateway 控制端仲裁。
- 带真实 Gateway、Teensy 和 DS5 的自动化集成测试。

## 3. 代码结构

```text
Controller/
├─ AGENTS.md
├─ PROJECT_HANDOVER.md
├─ README.md
├─ ds5_reader.py
├─ ds5_teensy_demo.py
├─ test_ds5_teensy_demo.py
├─ pyproject.toml
└─ uv.lock
```

| 文件 | 职责 |
| --- | --- |
| `ds5_reader.py` | 初始化 pygame，读取 DS5，归一化轴和打包按钮 |
| `ds5_teensy_demo.py` | 参数解析、串口/UDP 通信、状态缓存、固定面板、日志和可选 VOFA 偏航转发 |
| `test_ds5_teensy_demo.py` | Gateway、IMU 排版和 VOFA 转发的最小软件检查 |
| `pyproject.toml` | Python 版本与直接依赖 |
| `uv.lock` | 可复现依赖锁定 |

`ds5_teensy_demo.py` 是唯一运行入口。不要为当前规模拆分 transport、renderer 或 protocol 框架；只有文件职责明显失控时再做最小拆分。

## 4. 运行模型

默认参数：

| 项目 | 当前值 |
| --- | ---: |
| Python | `.python-version` 固定 3.11 |
| 串口 | `COM8`，115200 |
| Gateway | `192.168.10.2:14550` |
| 控制发送 | 50Hz |
| 面板刷新 | 50Hz |
| 摇杆死区 | 0.08 |
| 串口打开超时 | 3.0s |
| 串口写入超时 | 0.5s |
| 串口读取超时 | 1ms |
| 串口打开后等待 | 2.0s |
| VOFA（默认关闭） | 本机 `127.0.0.1:1347`，UDP/FireWater，随有效 PID 样本约 200Hz |
| 串口单次接收预算 / 行文本上限 | 4096字节 / 4096字符（ASCII时等同字节数） |

串口循环：

```text
读取现有串口数据 -> 更新标签缓存 -> 读取DS5
-> 到期时发送PKT -> 可选转发VOFA -> 到期时重绘面板 -> sleep 1ms
```

Gateway 循环结构相同，但使用非阻塞 UDP socket，每轮最多处理 32 个数据报。Windows 上 socket 必须先绑定本地临时端口，Gateway 才能把标签行回传到正确源端口。

串口打开单独使用一个 daemon worker，是因为 Windows 驱动层的 `serial.Serial()` 曾无法及时响应 `Ctrl+C`。主线程每 50ms 检查结果并在 3 秒后返回错误。除这个打开保护外，不应为当前程序增加后台线程。

串口循环使用单调时钟计时。`--send-hz 200` 可请求 200Hz 手柄发送，不改变默认50Hz、面板刷新和Gateway行为；Windows/Python不提供严格5ms调度保证。

## 5. 接口约定

### Teensy 串口

每个控制包占一行：

```text
PKT <seq> <lx> <ly> <rx> <ry> <l2> <r2> <buttons>
```

- `lx/ly/rx/ry` 范围为 `-1.0..1.0`；
- `ly/ry` 在 Controller 中反向；
- `l2/r2` 归一化为 `0.0..1.0`；
- 普通按钮按 pygame button index 写入低 32 位；
- 十字键固定使用 bit 11 至 bit 14；
- 每行以换行符结束。

Teensy 回显通过 `TAG{...}` 的标签前缀进入最新值缓存。除 IMU 排版和 VOFA 必需字段外，标签内部字段保持原样，新增普通字段通常不需要修改 Controller。

### Gateway raw UDP

- 目标为 `192.168.10.2:14550`；
- 一个 UDP 数据报携带一条完整 ASCII 文本记录，不带 `\r` 或 `\n`；
- Controller 直接发送和串口模式相同的 `PKT`；
- Gateway 逐行回传 Teensy 标签，并低频生成 `GATEWAY{teensy_connected=... teensy_age_ms=...}`；
- Controller 不解析标签内部业务字段，只按 `{` 前面的标签名缓存最新行。

Gateway 当前按最后一个合法 PC 地址回传文本，因此 Python Controller 和 C# 上位机不能同时控制同一个 Gateway。

### VOFA 本机 UDP

- 仅串口模式通过 `--vofa` 开启；`--vofa-port` 默认 1347。不能与 Gateway 或 `--ds5-debug` 组合。
- Controller 独占 Teensy 串口，VOFA 只监听本机 UDP，不打开 COM8。
- 使用 [FireWater](https://www.vofa.plus/docs/learning/dataengines/firewater/)：一个数据报一行 CSV，以 `\n` 结束。例如 `yaw:110.09,112.09,-4,-0.031\n`。
- 数据源改为一条完整记录：`PID{yaw_sp=110.09 yaw_deg=112.09 rate_sp=-4.00 gz_dps=-0.031}`。四通道顺序为yaw_sp、yaw_deg、rate_sp、gz_dps，前两个单位deg，后两个deg/s。需要配套新版固件，旧版CTRL/SYS/IMU拼帧路径已删除。
- 固件只在有效偏航自稳的新样本上输出PID，手动或反馈故障时停止。Controller不再依赖50Hz SYS进行二次模式门控；字段缺失、格式错误、NaN/Inf时丢弃，不补零，也不增加alive通道。
- 串口半行由接收缓冲拼接，完整PID行逐条转发；同一批接收到4个样本，就尝试发送4个数据报，不按面板50Hz限速，不覆盖或重放缓存，没有额外队列或线程。
- 约200Hz来自固件200Hz新IMU样本，不是电脑定时重复发送。当前四通道记录无采样时间戳，USB积压可能产生批量到达，PC不能严格识别样本年龄；精确延迟分析需再扩展协议。
- UDP 非阻塞发送，失败只丢绘图帧，不重试、不阻断手柄发包。socket 创建失败时显示 `INFO{vofa=disabled ...}`，继续原有串口控制。
- PID行进入日志和最新值缓存，面板50Hz显示最新一条，VOFA仍逐样本转发约200Hz；绘制面板不会重新发送PID。PKT格式、手柄映射、滤波和PID运算不变。

## 6. 环境与命令

Windows 安装：

```powershell
cd D:\Code\ROV_code\Controller
uv sync
```

串口模式：

```powershell
uv run python ds5_teensy_demo.py --mode serial --teensy-port COM8
```

串口模式转发 VOFA（接收端选择 UDP/FireWater、本地监听 1347）：

```powershell
uv run python ds5_teensy_demo.py --mode serial --teensy-port COM8 --send-hz 200 --vofa
```

Gateway 模式：

```powershell
uv run python ds5_teensy_demo.py --mode gateway --gateway-host 192.168.10.2 --gateway-port 14550
```

DS5 映射检查：

```powershell
uv run python ds5_teensy_demo.py --ds5-debug
```

Jetson 最后验证过的环境：

```bash
cd ~/Code/controller
eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
export SDL_JOYSTICK_DEVICE=/dev/input/js0

uv sync --python /home/linuxbrew/.linuxbrew/bin/python3.13
uv run python ds5_teensy_demo.py \
  --mode serial \
  --teensy-port /dev/serial/by-id/usb-Teensyduino_USB_Serial_13173040-if00
```

Homebrew 默认 Python 3.14 曾导致 `pygame==2.6.1` 尝试源码编译并缺少编译器；Python 3.13 可以安装对应 wheel。设备序列号和 `/dev/input/js0` 只代表最后一次实机，不是固定接口。

## 7. 验证

无硬件检查：

```powershell
uv run python -m unittest -q
uv run python -m compileall -q ds5_reader.py ds5_teensy_demo.py test_ds5_teensy_demo.py
uv run python ds5_teensy_demo.py --help
```

最小实机检查：

1. `--ds5-debug` 确认摇杆、扳机、十字键和按钮变化。
2. 串口模式确认控制包持续发送且固定面板全部出现。
3. 停止 Controller，确认 Teensy 按自身超时策略回到安全输出。
4. Gateway 模式确认 `GATEWAY{}` 和所有传感器标签更新。
5. 同一时间只运行一个串口占用者和一个 Gateway 控制端。
6. 烧录配套PID小帧固件，加 `--send-hz 200 --vofa`，按 R1 请求自稳；确认实际模式和IMU有效，VOFA四通道与PID字段一致，角度与角速度分别绘图。临时统计一段时间的PID记录和UDP接收数，确认接近200组/秒；不能仅看面板标题判断实际帧率。
7. 切回 MANUAL 或 IMU 掉线后应停止转发；恢复有效自稳后重新出现曲线。停止 VOFA 不应中断 Controller 手柄控制。

当前14项测试覆盖raw Gateway、IMU排版、缓存日志原样保留，以及PID四通道编码、坏行拒绝、串口分段拼接、4096字节接收预算和行文本上限。超长行尾部即使包含合法PID格式也不会进入面板、日志或VOFA；空读保留丢弃状态，遇到换行后恢复，完整超长行同样丢弃。混合200个不同PID样本与50份完整快照后保持全部200帧，面板显示最新PID，未收到时占位、无新数据保留最后值；真实UDP回环验证50批、每批4帧共200帧，UDP发送/创建失败仍继续原始PKT发送，面板维持独立周期。unittest、compileall和--help通过；以上是软件检查，不是实机200Hz时序测量，VOFA界面、DS5、Teensy和Gateway仍需实机验证。

## 8. 已知问题与排查

| 现象 | 首先检查 |
| --- | --- |
| 未检测到 DS5 | 手柄连接、`--joystick-index`、Linux `/dev/input` 权限和 SDL 设备 |
| 打开 COM 超时 | 端口号、串口占用、USB 枚举、Teensy 主循环是否仍响应 |
| 串口写入超时 | Teensy 是否被 CAN/I2C/串口传感器路径拖住，不先放大超时 |
| 标签停在 `waiting` | Teensy 固件是否输出该标签、Gateway 是否收到并回传该行 |
| 数值停住但仍显示 | 面板保存最后值，必须结合 `SYS{}` 中对应 `alive` 判断新鲜度 |
| Gateway 无回包 | Jetson 地址/端口、防火墙、Gateway 是否运行及 UDP 源端口绑定 |
| VOFA 没有曲线 | 是否加 `--vofa`、本地端口是否一致、协议是否FireWater、是否有效STABILIZE、新固件是否输出独立PID行；旧版CTRL/IMU不再转发 |
| VOFA 曲线停住 | MANUAL、IMU 掉线或没有新快照时主动停止转发，不代表手柄控制已停止 |
| Linux 扳机静止为 0.5 | SDL 轴初值或映射不同，先用 `--ds5-debug` 记录 raw 值 |

当前串口每轮最多读4096字节，完整行和未结束行均检查4096字符上限；超长半行持续丢弃到下一个换行，不把尾部重新作为记录。Gateway每轮最多接收32个数据报。预算只限制处理量，不保证持续洪泛下不丢数据，也不保证Windows调度周期；没有自动清空USB积压或无损历史队列。

启用 `--log-file` 后每行都会写入并 flush，只用于短期诊断，不建议长期高频记录。

`pyproject.toml` 仍标记为 `0.2.0`，仓库已有 `V0.2.1` Git 标签。当前没有发布 Python 包，行为版本以 Git 提交为准；只有建立正式发布流程时再统一版本来源。

## 9. 维护重点

1. 保持本仓库是可快速运行的调试工具，不复制 C# 上位机功能。
2. 协议变更时同步核对 Teensy、Gateway 和 C# 项目。
3. 新增传感器标签只扩展固定标签列表，不解析无业务需要的字段。
4. 完成 Linux DS5 映射确认和最新 Gateway 全标签联调。
5. 始终区分静态检查、软件联调和实机验证。
6. 使用英文 commit message，并保持一次提交只处理一类修改。
