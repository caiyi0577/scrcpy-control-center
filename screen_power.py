"""Verified physical display power control, without foreground key injection."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal


def power_commands(help_text: str) -> tuple[str, str] | None:
    commands = set(re.findall(r"^\s+(power-[a-z-]+)\s+DISPLAY_ID", help_text, re.MULTILINE))
    # Android/vendor versions differ: some expose power-on, others power-reset.
    if "power-off" not in commands:
        return None
    for on_command in ("power-reset", "power-on"):
        if on_command in commands:
            return "power-off", on_command
    return None


def physical_screen_on(output: str) -> bool | None:
    # Logical display / power-controller state can stay ON while the physical
    # panel is OFF. Only read the default INTERNAL DisplayDeviceInfo block.
    devices = []
    for block in re.split(r"(?m)^\s*DisplayDeviceInfo\{", output)[1:]:
        info = block.splitlines()[0]
        if not re.search(r"\btype INTERNAL\b", info):
            continue
        state = re.search(r"(?m)^\s+mCommittedState=(\w+)\s*$", block)
        if state is None:
            state = re.search(r"(?m)^\s+mState=(\w+)\s*$", block)
        if state:
            devices.append(("DEFAULT_DISPLAY" in info, state.group(1)))
    defaults = [state for is_default, state in devices if is_default]
    states = defaults or [state for _, state in devices]
    if len(states) != 1:
        return None
    if states[0] == "ON":
        return True
    if states[0] in {"OFF", "DOZE", "DOZE_SUSPEND"}:
        return False
    return None


class ScreenPowerController(QObject):
    busy_changed = Signal(bool)
    finished = Signal(bool, object, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.busy = False
        self.process: QProcess | None = None
        self.serial = ""
        self.adb_path = ""
        self.generation = 0

    def request(self, adb_path: Path | None, serial: str, turn_on: bool | None = None) -> None:
        if self.busy:
            return
        if not adb_path or not serial:
            self.finished.emit(False, None, "请先选择一台已连接的手机。")
            return
        self.generation += 1
        self.serial = serial
        self.adb_path = str(adb_path)
        self.busy = True
        self.busy_changed.emit(True)

        def capabilities(code: int, output: str) -> None:
            # Android ShellCommand may return -1 (adb exit 255) for "help"
            # even when the complete, valid command list was printed.
            commands = power_commands(output) if code in {0, 255} else None
            if code != 0 and not commands:
                self._finish(False, None, "读取设备能力失败：" + (output or str(code))[:240])
                return
            if not commands:
                self._finish(False, None, "此设备不支持直接亮灭屏。请在 scrcpy 窗口使用 Alt+O / Shift+Alt+O。")
                return

            def current_state(state_code: int, dump: str) -> None:
                current = physical_screen_on(dump) if state_code == 0 else None
                if current is None:
                    self._finish(False, None, "无法读取手机实际屏幕状态，未执行切换。")
                    return
                target = not current if turn_on is None else turn_on
                if target == current:
                    self._finish(True, current, "手机屏幕已经亮起。" if current else "手机屏幕已经熄灭。")
                    return
                command = commands[1] if target else commands[0]

                def changed(exit_code: int, result: str) -> None:
                    if exit_code != 0 or re.search(r"error|exception|unknown command", result, re.I):
                        self._finish(False, None, "手机拒绝了屏幕操作：" + (result or str(exit_code))[:240])
                        return
                    if target:
                        # power-reset removes the override; WAKEUP also handles
                        # a phone that went to sleep meanwhile. Never use SLEEP:
                        # it locks/suspends Android instead of just the panel.
                        self._run(["input", "keyevent", "KEYCODE_WAKEUP"], lambda _c, _o: self._verify(target))
                    else:
                        self._verify(target)

                self._run(["cmd", "display", command, "0"], changed)

            self._run(["dumpsys", "display"], current_state)

        self._run(["cmd", "display", "help"], capabilities)

    def _verify(self, target: bool, attempt: int = 0) -> None:
        generation = self.generation

        def read_state() -> None:
            if generation != self.generation or not self.busy:
                return

            def checked(code: int, output: str) -> None:
                actual = physical_screen_on(output) if code == 0 else None
                if actual is target:
                    self._finish(True, actual, "已确认手机屏幕亮起。" if target else "已确认手机屏幕熄灭（系统继续运行）。")
                elif attempt < 3:
                    self._verify(target, attempt + 1)
                else:
                    self._finish(False, actual, "命令已发送，但未确认屏幕达到目标状态。请查看手机。")

            self._run(["dumpsys", "display"], checked)

        QTimer.singleShot(200, read_state)

    def _run(self, args: list[str], callback) -> None:
        process = QProcess(self)
        self.process = process
        generation = self.generation
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        timeout = QTimer(process)
        timeout.setSingleShot(True)
        done = False

        def complete(code: int, override: str | None = None) -> None:
            nonlocal done
            if done:
                return
            done = True
            timeout.stop()
            output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
            if self.process is process:
                self.process = None
            process.deleteLater()
            if generation == self.generation:
                callback(code, override if override is not None else output)

        def expired() -> None:
            process.kill()
            complete(-1, "设备响应超时")

        process.finished.connect(lambda code, _status: complete(code))
        process.errorOccurred.connect(lambda error: complete(-1, process.errorString())
                                     if error == QProcess.ProcessError.FailedToStart else None)
        timeout.timeout.connect(expired)
        process.start(self.adb_path, ["-s", self.serial, "shell", *args])
        timeout.start(5000)

    def _finish(self, success: bool, actual: bool | None, message: str) -> None:
        self.busy = False
        self.busy_changed.emit(False)
        self.finished.emit(success, actual, message)

    def cancel(self) -> None:
        self.generation += 1
        if self.process is not None:
            process = self.process
            self.process = None
            process.blockSignals(True)
            process.kill()
            process.waitForFinished(500)
            process.deleteLater()
        self.busy = False
        self.busy_changed.emit(False)
