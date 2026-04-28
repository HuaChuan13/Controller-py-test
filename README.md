# Controller

## 当前定位

这个目录负责电脑侧控制链路，主要包括：

- `DS5` 读取
- `HI13R4` 读取
- `PKT` 文本总包桥接
- `Teensy` 回显监视

当前这里已经从 `ROV_Teensy41` 中独立出来，并使用 `uv` 管理 Python 环境与依赖。

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

## 依赖管理

首次进入项目时执行：

```powershell
cd D:\Code\ROV_code\Controller
uv sync
```

这会根据 `pyproject.toml` 和 `uv.lock` 自动创建 `.venv` 并安装依赖。

## 常用运行命令

### 1. 运行 DS5 + HI13R4 桥接

默认端口约定：

- `COM8 = Teensy`
- `COM9 = HI13R4`

```powershell
cd D:\Code\ROV_code\Controller
uv run python main\bridge_ds5_hi13r4.py --teensy-port COM8 --imu-port COM9 --view normal
```

如果需要查看 `MIX{}`、`PWM{}`、`DS5{}` 和 `IMU{}` 的详细调试信息，可切换到调试模式：

```powershell
cd D:\Code\ROV_code\Controller
uv run python main\bridge_ds5_hi13r4.py --teensy-port COM8 --imu-port COM9 --view debug
```

### 2. 单独验证 IMU

```powershell
cd D:\Code\ROV_code\Controller
uv run python Sensor\read_hi13r4.py --port COM9
```

## 当前模块职责

- `DS5/ds5_reader.py`：读取手柄并做标准化处理
- `Sensor/imu_reader.py`：读取 `HI13R4` 并输出姿态快照
- `protocol_bridge.py`：将输入打包成 `PKT` 总包并发送给 `Teensy`
- `controller_monitor.py`：按 `normal / debug` 模式显示和记录 `Teensy` 回显
  - `normal`：`CTRL{}` + `SYS{}`
  - `debug`：`CTRL{}` + `MIX{}` + `PWM{}` + `SYS{}` + `DS5{}` + `IMU{}`
- `main/bridge_ds5_hi13r4.py`：负责把这些模块串起来运行
