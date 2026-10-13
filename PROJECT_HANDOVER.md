# ROV Controller 项目交接

> 最近源码核对：2026-10-13
>
> 通信基线：串口与 Gateway 模式共用同一套 `PKT`/标签文本

本文只记录当前可用状态，不记录逐次开发流水。行为变化时直接修改对应章节，历史过程以 Git 为准。

## 1. 项目定位

本仓库是轻量 Python 调试上位机，负责：

- 读取 DualSense 手柄；
- 将控制状态发送给 Teensy 或 Jetson Gateway；
- 缓存并显示最新的下位机回显；
- 检查 Windows 和 Linux 下的 DS5 原始映射。

当前链路：

```text
串口调试：
DS5 -> Controller -> USB/PKT -> Teensy

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
- 三列固定网格，数值右对齐；IMU按温度、加速度、角速度、角度四行显示，MIX和PWM共用M1-M8表头，缺失和异常值不挤动其他列。
- `--ds5-debug` 原始轴、按键、十字键和打包结果检查。
- 可配置控制发送频率、面板刷新频率、串口和 Gateway 地址。
- 可选文本日志。
- Windows 串口打开超时和串口写入超时保护。

面板读取的原始标签：

```text
CTRL SYS DS5 MIX PWM SHT40 PM08 DEPTH IMU
```

Gateway模式额外显示GATEWAY连接状态行；INFO和无标签信息显示在末行，超出79字符的提示以省略号截短，原始日志保留全文。

面板只在绘制时格式化原始标签缓存，串口/Gateway收发、PKT内容、原始日志和默认50Hz频率不变。ACC/GYR/ANG是IMU的屏幕分组，不是新增回传标签；无需重新烧录Teensy。

常规面板固定24行，每行最多79字符，终端可视区域至少80列、24行；GATEWAY和INFO各额外占一行。`render_panel()`原位覆盖、清除行尾及剩余旧内容，不整屏清空，末行不换行，避免恰好占满屏幕时滚动。

已知浮点字段保留固件原有2/3位精度并显示正负号。普通字段按统一网格右对齐，电机每个数值占8字符。缺失数据为`---`，无效数值为`ERR`，超出该字段数值范围的显示槽宽为`OVF`。ONLINE行把alive映射为ON/OFF，缺失为`---`；这只表示固件最后回传的状态，不是Controller新增掉线检测。IMU尚未收到数据时仍占四行。

固定布局在`build_panel_lines()`中列出需要显示的字段；新增标签或字段需在那里补充布局及数值格式，不自动追加未知字段。原始缓存和日志始终保留完整接收行。

### 已有实机结论

- Windows 串口直连链路可用于 Teensy 控制和传感器回显。
- Gateway UDP 模式使用 Windows 本地临时端口，并按最后一个合法来源接收回传。
- Jetson 上曾使用 Homebrew Python 3.13、`/dev/input/js0` 和 Teensy 稳定设备路径完成链路测试。
- Controller 的串口打开或写入超时曾由 Teensy 的 PM08/CAN 路径失去响应触发；上位机超时通常是下位机或 USB 状态的症状，不代表控制数值超限。

### 仍需确认

- 当前最新固件全部传感器标签经 Gateway 的完整联调。
- Linux 上十字键、面键、L3/R3 和扳机轴的最终映射。
- 更换手柄、系统或 SDL 版本后的轴编号一致性。

### 未实现

- 正式产品 UI、任务控制、参数配置和数据可视化。
- 串口掉线后的自动重连。
- 多 Gateway 控制端仲裁。
- 带真实 Gateway、Teensy 和 DS5 的自动化集成测试。
- 对持续串口输入洪泛的显式处理预算；Gateway UDP 每轮已限制为 32 个数据报。

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
| `ds5_teensy_demo.py` | 参数解析、串口/UDP 通信、状态缓存、固定面板和日志 |
| `test_ds5_teensy_demo.py` | raw Gateway协议、接收预算、面板列宽与缓存/日志保留测试 |
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

串口循环：

```text
读取现有串口数据 -> 更新标签缓存 -> 读取DS5
-> 到期时发送PKT -> 到期时重绘面板 -> sleep 1ms
```

Gateway 循环结构相同，但使用非阻塞 UDP socket，每轮最多处理 32 个数据报。Windows 上 socket 必须先绑定本地临时端口，Gateway 才能把标签行回传到正确源端口。

串口打开单独使用一个 daemon worker，是因为 Windows 驱动层的 `serial.Serial()` 曾无法及时响应 `Ctrl+C`。主线程每 50ms 检查结果并在 3 秒后返回错误。除这个打开保护外，不应为当前程序增加后台线程。

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

Teensy回显通过TAG前缀进入最新原始行缓存。标签内部字段只在绘制时解析用于对齐，不参与控制判断；接收缓存和日志不补空格、不改数值。增加需要固定格式显示的字段时扩展面板字段规则。

### Gateway raw UDP

- 目标为 `192.168.10.2:14550`；
- 一个 UDP 数据报携带一条完整 ASCII 文本记录，不带 `\r` 或 `\n`；
- Controller 直接发送和串口模式相同的 `PKT`；
- Gateway 逐行回传 Teensy 标签，并低频生成 `GATEWAY{teensy_connected=... teensy_age_ms=...}`；
- Controller按`{`前面的标签名缓存最新原始行，字段只用于面板排版，不参与控制或网关路由。

Gateway 当前按最后一个合法 PC 地址回传文本，因此 Python Controller 和 C# 上位机不能同时控制同一个 Gateway。

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

当前9项单元测试覆盖raw Gateway协议、列宽/精度/字段位置、79字符/24行边界、IMU缺失/异常/超宽值、ANSI清尾、缓存与日志原样保留、PKT内容和默认频率。unittest、compileall、--help已通过。

用用户截图中的可见数据生成终端预览并查看；临时终端模拟检查80x24、80x30、160x30窗口，反复切换长短动作、缺失数据和Gateway/INFO时无旧字符残留。模拟工具仅用于此次验证，未加入项目依赖。完整面板生成最佳批次均值约0.067ms/次，上一版约0.049ms/次；不包含真实终端绘制、串口和UDP延迟，也不能替代实机验证。

## 8. 已知问题与排查

| 现象 | 首先检查 |
| --- | --- |
| 未检测到 DS5 | 手柄连接、`--joystick-index`、Linux `/dev/input` 权限和 SDL 设备 |
| 打开 COM 超时 | 端口号、串口占用、USB 枚举、Teensy 主循环是否仍响应 |
| 串口写入超时 | Teensy 是否被 CAN/I2C/串口传感器路径拖住，不先放大超时 |
| 字段一直为 `---` | Teensy固件是否输出该字段、Gateway是否收到并回传该行 |
| 数值停住但仍显示 | 面板保存最后值，结合ONLINE行判断传感器alive，并检查SYS时间是否仍增长 |
| 面板换行或滚动 | 终端可视区域至少80列、24行；GATEWAY和INFO各需增加一行 |
| 数值显示为ERR/OVF/! | 无效数值或超出显示槽宽；查原始日志，不把占位符当成0 |
| Gateway 无回包 | Jetson 地址/端口、防火墙、Gateway 是否运行及 UDP 源端口绑定 |
| Linux 扳机静止为 0.5 | SDL 轴初值或映射不同，先用 `--ds5-debug` 记录 raw 值 |

当前串口读取会处理当时全部 `in_waiting` 数据；UDP 每轮最多接收 32 个数据报。若实测串口持续积压，再为串口路径增加明确预算，不提前搭建额外线程或队列框架。

启用 `--log-file` 后每行都会写入并 flush，只用于短期诊断，不建议长期高频记录。

`pyproject.toml` 仍标记为 `0.2.0`，仓库已有 `V0.2.1` Git 标签。当前没有发布 Python 包，行为版本以 Git 提交为准；只有建立正式发布流程时再统一版本来源。

## 9. 维护重点

1. 保持本仓库是可快速运行的调试工具，不复制 C# 上位机功能。
2. 协议变更时同步核对 Teensy、Gateway 和 C# 项目。
3. 新增标签或字段时扩展`build_panel_lines()`和数值格式，不把显示解析用于控制或协议转换。
4. 完成 Linux DS5 映射确认和最新 Gateway 全标签联调。
5. 始终区分静态检查、软件联调和实机验证。
6. 使用英文 commit message，并保持一次提交只处理一类修改。
