class ControllerMonitor:
    # 监视模块是给“人”看的，不属于控制链核心。
    # 即使关闭它，DS5 -> IMU -> ProtocolBridge -> Teensy 的链路仍然成立。
    def __init__(self, view_mode: str = "normal", log_path: str | None = None):
        self.view_mode = view_mode
        self.log_file = open(log_path, "a", encoding="utf-8") if log_path else None

    def emit_teensy_lines(self, lines: list[str]) -> None:
        for line in lines:
            text = f"[Teensy] {line}"
            self._log(text)
            if self._should_display_teensy_line(line):
                print(text)

    def close(self) -> None:
        if self.log_file is not None:
            self.log_file.close()

    def _should_display_teensy_line(self, line: str) -> bool:
        # 默认模式只显示控制摘要和系统状态；调试模式显示全部分类输出。
        if self.view_mode == "debug":
            return True

        if line.startswith("CTRL{") or line.startswith("SYS{"):
            return True

        if line.startswith("MIX{") or line.startswith("PWM{") or line.startswith("DS5{") or line.startswith("IMU{"):
            return False

        return True

    def _log(self, text: str) -> None:
        if self.log_file is not None:
            self.log_file.write(text + "\n")
            self.log_file.flush()
