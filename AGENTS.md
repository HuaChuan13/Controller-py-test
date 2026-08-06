# 项目级开发规则

## 开始之前

- 本仓库只负责 ROV 的 Python 调试上位机，不承担正式 C# 上位机功能。
- 修改前先读 `PROJECT_HANDOVER.md` 和 `README.md`。
- 文档与源码冲突时，以当前源码和最近一次实机结果为准，并同步修正文档。
- 先检查 `git status`，保留用户已有的未提交改动。
- 修改 Teensy `PKT`、Gateway raw UDP、DS5 映射或控制频率前，先核对对应项目的当前协议。

## 实现原则

- 优先选择最简单、直接且满足联调需求的实现。
- 保持 `ds5_teensy_demo.py` 为唯一运行入口，手柄读取集中在 `ds5_reader.py`。
- 串口和 Gateway 模式共享 DS5 数据模型、状态缓存和固定回显面板。
- 不恢复普通/调试视图分支；当前只保留完整回显面板。
- `--ds5-debug` 只负责原始手柄映射检查，不连接 Teensy 或 Gateway。
- 不为正式 C# 上位机的未来功能提前增加界面框架、配置层、插件系统或通用传输抽象。
- 修改范围只覆盖当前任务，不顺手重构无关代码。

## 通信与运行约束

- 50Hz 控制发送循环不得被串口读取、UDP 接收、日志或界面刷新长期阻塞。
- 串口读取使用短超时，UDP socket 保持非阻塞且每轮接收有数据报数量预算。
- 保留 Windows 串口打开超时保护和串口写入超时保护。
- 不通过单纯增大超时时间掩盖 Teensy、USB、CAN 或传感器路径卡死。
- 不在高频循环加入大量逐包输出；状态面板只显示最新缓存。
- 新增回显标签时，同步更新固定标签列表和文档。
- 同时兼顾 Windows 调试环境和 Jetson Linux/Homebrew 环境，不猜测端口、设备权限或 DS5 轴映射。

## 验证

无硬件修改至少执行：

```powershell
cd D:\Code\ROV_code\Controller
uv run python -m unittest -q
uv run python -m compileall -q ds5_reader.py ds5_teensy_demo.py test_ds5_teensy_demo.py
uv run python ds5_teensy_demo.py --help
```

涉及 DS5 时执行映射检查：

```powershell
uv run python ds5_teensy_demo.py --ds5-debug
```

涉及通信时按改动范围验证串口模式、Gateway 模式或两者。结束任务前：

1. 检查 `git diff` 和 `git status`。
2. 明确说明已完成的软件检查和仍需实机确认的内容。
3. 不把命令行启动成功描述为完整链路已通过。

## 文档与 Git

- `README.md` 只保留安装、常用命令和入口说明。
- 当前状态、协议、环境、已知问题和排障结论维护在 `PROJECT_HANDOVER.md`。
- 本项目规模较小，不新增 `doc/` 目录或拆分更多交接文档。
- 只有行为、接口、运行方式、环境或已知问题变化时才更新交接文档。
- 完成一组相关修改后及时提交，commit message 统一使用英文。
- 一个 commit 只处理一类逻辑修改。
- 不提交日志、虚拟环境、缓存、密钥、构建产物和本机生成文件。
