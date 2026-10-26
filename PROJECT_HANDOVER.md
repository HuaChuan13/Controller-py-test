# ROV Controller 项目交接

> 最近源码核对：2026-10-26
>
> 通信基线：串口与 Gateway 模式共用同一套 `PKT`/标签文本

本文只记录当前可用状态，不记录逐次开发流水。行为变化时直接修改对应章节，历史过程以 Git 为准。

## 本地视觉辅助（支持岸上视觉项目的 USB/RTP 输入）

网络接入开发分支为 `codex/network-video`，保留 `codex/local-visual-yaw`。视频由 Jetson 直接推送到 PC 视觉项目，Controller 仍独占 PC→Teensy USB；观测和串口协议不变。Windows Python 3.11 的 `time.monotonic()` 实测分辨率15.625ms，原20ms发送门限常形成31ms间隔；发送及面板调度改用 `perf_counter()`，观测有效期/回执仍使用原单调时钟，200ms期限和20ms串口预算不变。Gateway模式的调度也使用高精度单调时钟，避免系统时钟调整影响周期。不补发积压的历史控制包。

2026-10-26网络实机无动力联调：真实RTP双目/YOLO/异步深度，测试脚本调用同一选择入口并使用本模块VisionBridge，真实COM3/IMU；三次选择、取消、暂停推流0.6秒后撤销且不自动恢复、重新选择、彻底停止推流均通过。最终1866份活动VIS、发送48.08Hz，只有注入故障导致超时，PWM全部1500；结束另读遥测确认MANUAL/LIMIT0。协议/VOFA接口和固件未改，未进行物理手柄或有动力测试。详见视觉项目 `coral_runs/network_bench/verified_result.json`。

开发分支 `codex/local-visual-yaw`。`--vision`仅支持串口模式，新增 `vision_bridge.py` 负责本机观测与启用仲裁，唯一入口/串口仍为ds5_teensy_demo.py，无新增线程。每轮UDP最多16包、单包8192字节，只保留最新观测；64条已发PKT时间用于检查回执，未建立历史观测队列。

- 接收 `coral.observation.v1`，需有 `selection_monotonic_s`；新鲜VIS证明已自稳后发生的新点击才能启用。启动前、手动模式或接管之前的点击不延后执行。重复帧不得更换源时间、延长有效期。
- 平滑偏差经比例、死区、限幅生成目标航向变化速度；进入居中0.04、退出0.06，均相对半幅宽度。实际参数见README；不使用XYZ，不再叠加平滑。
- Circle/Square（bits1/2）及R1（bit10）撤销当前授权；两个旋转键同时按下也撤销。原DS5映射与平移/升降行为不改。
- 原PKT后追加 `V1 <session> <token> <frame> <age_ms> <state> <slew_dps>`；session为Controller随机非零uint32，token为每次接受新点击递增的uint32，frame为原源帧编号。state为0关闭/1修正/2等待/3居中；只有1可有非零slew。初始先发OFF/token0建立会话。
- 源有效期最多200ms，并预留20ms串口传输预算（含向上取整的年龄）。同一观测可以按手柄周期重发，但frame不递增、年龄继续增长；不能靠手柄包在线保活视觉。
- 短暂等待保持授权但停止修正，最长至最后接受检测后0.4秒；恢复需视觉确认。真丢失、过期、取消、模式退出或反馈断链后须重新点击。固件拒绝/故障状态也撤销本地授权。
- `VIS{session token frame state mode pkt slew yaw_sp yaw_deg lead_limit}` 为配套固件50Hz独立小帧。只有匹配本Controller会话且echo的PKT发送年龄小于200ms才采用；不会把串口积压的旧遥测当作新鲜模式。确认须匹配本次token和请求状态，slew取固件实际回执。
- 同一socket向视觉源端口回复 `coral.control.v1`，最多20Hz，含视觉会话/锁定ID、源帧、状态、请求/接受值、结果时间及截止时间。过期UI只显示未确认，不把黄色框当作启用证据。
- 固件ready或回执超时后建立新OFF会话，旧点击不自动恢复；串口掉线仍退出，不增加自动重连。错误UDP不阻塞手柄发送，停止程序由固件独立超时退出。

验证：新增视觉单元检查与原Controller测试通过，跨项目真实UDP→PKT→编译后的固件模块→回执通过。2026-10-23烧录后COM3/真实IMU无动力13秒验证启用、WAIT恢复、居中、取消、手动接管、断帧、模式重入；收到649份VIS回执。真实USB相机240帧另测收到617份有效控制回执，检测17.90FPS、深度9.21Hz，八路PWM均1500。观测/按键场景由测试脚本生成，真实相机测试也由脚本调用选择入口；没有验证有动力运动、物理手柄操作或水下稳定性。记录在视觉项目 `coral_runs/control_link/`，结束保留MANUAL/LIMIT0。

修复接收批次中时钟早于新消息的问题：逐包重新采样接收时间，并在发包前刷新时间。取消/丢失与新点击按观测序号和递增锁定 ID 排序，不将处理旧消息的时刻作为后续点击门槛；同一时钟刻度内的取消和重选有效。模式/超时/手动接管的本地时间门槛仍保留；旧会话首次带缓存选择不启用。退出原因保留到明确新操作，避免被后续无目标帧覆盖。

检查文件 `test_vision_bridge.py` 由 `uv run python -m unittest -q` 一并执行；增加粗粒度协议时钟下的50Hz调度回归，现29项通过。VOFA继续复用PID曲线与调参，不添加启用按钮；网络视频接收由视觉项目负责。


2026-10-23 后续修复已烧录验证：视觉领先量15°、变化速度上限仍5°/s；VIS增加yaw_sp/yaw_deg/lead_limit，单帧224字节有界缓冲。Controller修复接收批次时间和取消/重选排序，生产端同一时钟刻度的操作按序号/锁定ID判定，超时或人工接管仍要求新点击。真实Teensy无动力连续12次取消重选均收到活动回执，最大领先角15°；真实VOFA UDP桥接收到1777条曲线记录，手动同值参数设置OK、自稳拒绝，参数未改变。详细记录位于视觉项目 coral_runs/control_link/revision_2/。28项Controller检查、固件主机回归与正式构建通过；真实相机持续600帧收到1337份活动回执，无超时撤销。测试脚本调用选择入口，未做物理鼠标/手柄操作或有动力测试。

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
Teensy -> Controller -> UDP/FireWater :1347 -> 本机 VOFA（可选）
VOFA -> UDP :1346 -> Controller -> USB/CMD PID YAW -> Teensy

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
- 复用同一非阻塞UDP socket在本机1346接收yaw四类增益命令，Teensy仅允许MANUAL改参；PARAM回执进入面板和VOFA文字区，不增加曲线通道。
- 串口每轮最多读取4096字节，换行前文本最多4096字符（固件为ASCII）。完整行超长时整行丢弃；半行超长后持续丢弃到换行，避免把坏行尾部当作新记录。

固定面板标签：

```text
SYS DS5 CTRL VIS MIX PWM SHT40 PM08 DEPTH IMU PID PARAM
```

Gateway 模式额外显示 `GATEWAY`；无标签信息使用 `INFO` 显示。

PID尚未收到时显示`PID{waiting}`，之后保存最后一条。切回手动或反馈故障后固件停止PID输出，面板不会清零，需结合实际模式和反馈状态判断有效性；不加`--vofa`也可显示收到的PID行。此显示调整不改变Teensy固件、串口数据和VOFA发送路径。

PARAM在PID之后显示最新事件回执；未收到时为`PARAM{waiting}`，不按面板频率重发。`SYS{ready=1}`清除旧PARAM缓存，因为固件重启会恢复默认增益。滑块只是拟发送值，必须检查新回执的status和四个实际参数。

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
- Gateway 到 VOFA 转发、其余三个闭环轴的通道和调参、参数持久化。
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
| Controller命令监听 | `127.0.0.1:1346`，只允许配置的VOFA本地端口发来命令，每轮最多1个数据报 |
| 串口单次接收预算 / 行文本上限 | 4096字节 / 4096字符（ASCII时等同字节数） |

串口循环：

```text
读取现有串口数据 -> 更新标签缓存 -> 读取DS5
-> 到期时优先发送PKT -> 可选转发VOFA、接收1条调参命令并串行写CMD
-> 到期时重绘面板 -> sleep 1ms
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

- 仅串口模式通过 `--vofa` 开启；`--vofa-port` 默认1347，`--vofa-command-port` 默认1346，均须为1~65535且不能相同。不能与Gateway或`--ds5-debug`组合。
- Controller 独占 Teensy 串口，VOFA 只监听本机 UDP，不打开 COM8。
- 使用 [FireWater](https://www.vofa.plus/docs/learning/dataengines/firewater/)：一个数据报一行 CSV，以 `\n` 结束。例如 `yaw:110.09,112.09,-4,-0.031\n`。
- 数据源改为一条完整记录：`PID{yaw_sp=110.09 yaw_deg=112.09 rate_sp=-4.00 gz_dps=-0.031}`。四通道顺序为yaw_sp、yaw_deg、rate_sp、gz_dps，前两个单位deg，后两个deg/s。需要配套新版固件，旧版CTRL/SYS/IMU拼帧路径已删除。
- 固件只在有效偏航自稳的新样本上输出PID，手动或反馈故障时停止。Controller不再依赖50Hz SYS进行二次模式门控；字段缺失、格式错误、NaN/Inf时丢弃，不补零，也不增加alive通道。
- 串口半行由接收缓冲拼接，完整PID行逐条转发；同一批接收到4个样本，就尝试发送4个数据报，不按面板50Hz限速，不覆盖或重放缓存，没有额外队列或线程。
- 约200Hz来自固件200Hz新IMU样本，不是电脑定时重复发送。当前四通道记录无采样时间戳，USB积压可能产生批量到达，PC不能严格识别样本年龄；精确延迟分析需再扩展协议。
- UDP 非阻塞发送，失败只丢绘图帧，不重试、不阻断手柄发包。socket 创建失败时显示 `INFO{vofa=disabled ...}`，继续原有串口控制。
- PID行进入日志和最新值缓存，面板50Hz显示最新一条，VOFA仍逐样本转发约200Hz；绘制面板不会重新发送PID。PKT格式、手柄映射、滤波和PID运算不变。

### VOFA 调参命令

复用`VofaBridge`同一个UDP socket：绑定127.0.0.1:1346，只接受配置的VOFA源端口（默认1347）。每轮最多取一个不超过128字节的数据报，只允许一条ASCII命令：

```text
CMD PID YAW ANGLE_KP <value>
CMD PID YAW RATE_KP <value>
CMD PID YAW RATE_KI <value>
CMD PID YAW RATE_KD <value>
```

Controller校验长度、单行、白名单和有限非负数，规范化后补一个LF，与PKT在同一线程写COM8；PKT先发。无线程、历史命令队列或自动重试，不允许透传MODE/LIMIT等命令。UDP/格式错误只显示INFO，串口写超时仍终止控制会话。

固件再次校验，且current/requested都必须MANUAL。四个上限只在Teensy Config.h的kYawAngleKpMax/kYawRateKpMax/kYawRateKiMax/kYawRateKdMax维护，改动后重新编译烧录、匹配VOFA滑块，不需要改Controller。成功返回`PARAM{status=OK angle_kp=... rate_kp=... rate_ki=... rate_kd=...}`；模式拒绝返回MODE_REJECTED和未变的实际值。超过固件上限只增加rx_rejected，不产生新PARAM，旧回执不代表本次成功。控制器不根据50Hz面板缓存代替固件判定模式；软件上限不代表已整定的水下安全参数。

PARAM作为文字原样转发至VOFA，不进入FireWater四条曲线。固件只在事件后回传，USB忙时保留一份最新结果，批量操作可合并为最终四值；UDP可能丢失，未收到新回执不能宣称生效，可手动重复同一设定确认。运行参数在RAM，重启恢复默认值。VOFA的Abc模板、滑块配置及逐步操作见README，暂不增加GET、持久化、自动回填滑块或运行中改参。

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

原18项测试覆盖raw Gateway、IMU排版、缓存日志原样保留、PID四通道、串口坏行与恢复，以及双向UDP、调参白名单/有限非负数、旧上限以上值转发、源端口/数据报预算、PKT优先交错写入、PARAM缓存/重启失效和写超时。混合200个PID样本与50份完整快照及200帧UDP回环仍通过，UDP发送/创建失败不终止手柄发包。unittest、compileall和--help通过；以上是软件检查，不是实机200Hz时序测量，VOFA界面、DS5、Teensy和Gateway仍需实机验证。

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
| VOFA 参数不变 | 远程1346/本地1347、Abc模板是否发送实际值和换行、是否MANUAL、INFO/PARAM状态；不能以滑块数字代替固件确认 |
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
