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

## 当前模块职责

- `ds5_reader.py`：读取 `DS5`，整理摇杆、扳机和按键位图
- `ds5_teensy_demo.py`：发送 `PKT`、缓存最新回显并固定面板刷新显示
