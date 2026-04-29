# Controller

## 当前定位

这个目录负责电脑侧控制链路，主要包括：

- `DS5` 读取
- `PKT` 文本总包桥接
- `Teensy` 回显监视

当前这里已经从 `ROV_Teensy41` 中独立出来，并使用 `uv` 管理 Python 环境与依赖。
当前默认主链已经收紧为：`DS5 -> Controller -> Teensy`。

## 目录结构

```text
Controller/
├─ main/
│  └─ bridge_ds5_hi13r4.py
├─ DS5/
│  └─ ds5_reader.py
├─ Sensor/
│  ├─ hipnuc_parser.py
│  ├─ imu_reader.py
│  └─ read_hi13r4.py
├─ protocol_bridge.py
├─ controller_monitor.py
├─ pyproject.toml
├─ uv.lock
└─ README.md
```

其中 `Sensor/` 目录当前作为历史 IMU 调试工具保留，不属于默认运行主链。

## 依赖管理

首次进入项目时执行：

```powershell
cd D:\Code\ROV_code\Controller
uv sync
```

这会根据 `pyproject.toml` 和 `uv.lock` 自动创建 `.venv` 并安装依赖。

## 常用运行命令

### 1. 运行 DS5 手柄桥接

默认端口约定：

- `COM8 = Teensy`

```powershell
cd D:\Code\ROV_code\Controller
uv run python main\bridge_ds5_hi13r4.py --teensy-port COM8 --view normal
```

如果需要查看 `MIX{}`、`PWM{}` 和 `DS5{}` 的详细调试信息，可切换到调试模式：

```powershell
cd D:\Code\ROV_code\Controller
uv run python main\bridge_ds5_hi13r4.py --teensy-port COM8 --view debug
```

### 2. 单独验证历史 IMU 工具

```powershell
cd D:\Code\ROV_code\Controller
uv run python Sensor\read_hi13r4.py --port COM9
```

## 当前模块职责

- `DS5/ds5_reader.py`：读取手柄并做标准化处理
- `protocol_bridge.py`：将手柄输入打包成 `PKT` 总包并发送给 `Teensy`
- `controller_monitor.py`：按 `normal / debug` 模式显示和记录 `Teensy` 回显
  - `normal`：`CTRL{}` + `SYS{}`
  - `debug`：`CTRL{}` + `MIX{}` + `PWM{}` + `SYS{}` + `DS5{}`
- `main/bridge_ds5_hi13r4.py`：负责把默认 DS5 桥接主链串起来运行
- `Sensor/imu_reader.py`、`Sensor/hipnuc_parser.py`、`Sensor/read_hi13r4.py`：历史 `HI13R4` 调试工具，当前默认主链不使用
