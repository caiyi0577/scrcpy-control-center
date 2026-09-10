from __future__ import annotations

import os
import ctypes
import importlib.util
import re
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path


_QT_DLL_DIRECTORY_HANDLES = []
_QT_LIBRARY_DIRS: list[Path] = []


def prepare_qt_dll_paths() -> None:
    locations: list[Path] = [
        Path(sys.prefix) / "Lib" / "site-packages" / "PySide6",
        Path(sys.prefix) / "Lib" / "site-packages" / "shiboken6",
    ]
    for module_name in ("PySide6", "shiboken6"):
        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, ModuleNotFoundError, ValueError):
            spec = None
        if spec and spec.submodule_search_locations:
            locations.extend(Path(item) for item in spec.submodule_search_locations)

    unique_locations = []
    seen = set()
    for location in locations:
        location = location.resolve()
        if location in seen or not location.is_dir():
            continue
        seen.add(location)
        unique_locations.append(location)
        try:
            _QT_DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(location)))
        except (AttributeError, OSError):
            pass
    if unique_locations:
        _QT_LIBRARY_DIRS.extend(unique_locations)
        os.environ["PATH"] = os.pathsep.join(
            [str(location) for location in unique_locations] + [os.environ.get("PATH", "")]
        )


prepare_qt_dll_paths()


def preload_qt_core_dlls() -> None:
    if getattr(sys, "frozen", False):
        internal_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        search_dirs = [internal_dir, internal_dir / "PySide6"] + _QT_LIBRARY_DIRS
    else:
        search_dirs = _QT_LIBRARY_DIRS

    dll_names = (
        "MSVCP140.dll",
        "MSVCP140_1.dll",
        "MSVCP140_2.dll",
        "VCRUNTIME140.dll",
        "VCRUNTIME140_1.dll",
        "concrt140.dll",
        "msvcp140_codecvt_ids.dll",
        "Qt6Core.dll",
    )
    loaded = set()
    for directory in search_dirs:
        for dll_name in dll_names:
            dll_path = directory / dll_name
            if not dll_path.is_file() or dll_path in loaded:
                continue
            try:
                ctypes.WinDLL(str(dll_path))
                loaded.add(dll_path)
            except OSError:
                pass


preload_qt_core_dlls()

try:
    import win32gui
except ImportError:  # pragma: no cover - optional until the dependency is installed
    win32gui = None
try:
    import win32process
except ImportError:  # pragma: no cover - optional until the dependency is installed
    win32process = None

from PySide6.QtCore import QProcess, QSettings, QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QCompleter,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from screen_power import ScreenPowerController, physical_screen_on


APP_NAME = "Scrcpy Control Center"
SCRCPY_PACKAGE_ID = "Genymobile.scrcpy"


def local_app_data() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    return Path(value) if value else Path.home() / "AppData" / "Local"


def discover_scrcpy() -> Path | None:
    # Prefer a real system installation first. This makes the GUI follow the
    # version the user installed or upgraded with WinGet. The bundled copy is
    # only a fallback for friends who do not have scrcpy installed.
    path_value = shutil.which("scrcpy")
    if path_value:
        candidate = Path(path_value)
        if candidate.is_file():
            return candidate

    app_data = local_app_data()
    links_candidate = app_data / "Microsoft" / "WinGet" / "Links" / "scrcpy.exe"
    if links_candidate.is_file():
        return links_candidate

    package_root = app_data / "Microsoft" / "WinGet" / "Packages"
    try:
        package_candidates = sorted(
            package_root.glob("Genymobile.scrcpy_*/*/scrcpy.exe"),
            reverse=True,
        )
    except OSError:
        package_candidates = []
    for candidate in package_candidates:
        if candidate.is_file():
            return candidate

    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        internal_dir = Path(getattr(sys, "_MEIPASS", app_dir))
        # A single-file build extracts the bundled official scrcpy release to
        # PyInstaller's temporary directory while this launcher is running.
        fallback_candidates = [
            # Also support placing a scrcpy folder beside the launcher.
            app_dir / "scrcpy" / "scrcpy.exe",
            app_dir / "scrcpy-win64-v4.1" / "scrcpy.exe",
            internal_dir / "scrcpy" / "scrcpy.exe",
        ]
    else:
        # A shared portable package keeps the official scrcpy release beside
        # the launcher, so a friend can use it without installing scrcpy.
        app_dir = Path(__file__).resolve().parent
        fallback_candidates = [
            app_dir / "scrcpy" / "scrcpy.exe",
            app_dir / "scrcpy-win64-v4.1" / "scrcpy.exe",
        ]

    for candidate in fallback_candidates:
        if candidate.is_file():
            return candidate
    return None


def discover_adb(scrcpy_path: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if scrcpy_path:
        candidates.append(scrcpy_path.parent / "adb.exe")
    path_value = shutil.which("adb")
    if path_value:
        candidates.append(Path(path_value))
    candidates.append(Path(r"C:\Android\adb.exe"))

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def parse_scrcpy_version(output: str) -> str:
    match = re.search(r"scrcpy\s+(\d+(?:\.\d+)+)", output, re.IGNORECASE)
    return match.group(1) if match else "未知"


def winget_reports_no_upgrade(output: str) -> bool:
    normalized = output.casefold()
    return any(
        marker in normalized
        for marker in (
            "no applicable upgrade",
            "no upgrade found",
            "没有适用的升级",
            "无可用升级",
            "没有可用的升级",
            "找不到可用的升级",
            "没有可用的较新的包版本",
            "配置的源中没有可用的较新的包版本",
            "已是最新",
        )
    )


class FloatingToolbar(QWidget):
    action_requested = Signal(str)

    def __init__(self, window_title: str, window_handle_provider=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.window_title = window_title
        self.window_handle_provider = window_handle_provider
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("floatingToolbar")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(6)

        buttons = [
            ("‹", "返回", "back"),
            ("⌂", "主页", "home"),
            ("▤", "最近任务", "recent"),
            ("☼", "开启手机屏幕", "screen_on"),
            ("●", "关闭手机屏幕", "screen_off"),
            ("↻", "重新连接", "restart"),
        ]
        self.screen_buttons = []
        for text, tooltip, action in buttons:
            button = QPushButton(text)
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.setFixedSize(42, 40)
            button.clicked.connect(lambda _checked=False, name=action: self.action_requested.emit(name))
            layout.addWidget(button)
            if action in {"screen_on", "screen_off"}:
                self.screen_buttons.append(button)

        self.setStyleSheet(
            """
            #floatingToolbar {
                background: rgba(25, 31, 43, 235);
                border: 1px solid rgba(255, 255, 255, 35);
                border-radius: 13px;
            }
            #floatingToolbar QPushButton {
                color: #f6f8fc;
                background: rgba(255, 255, 255, 16);
                border: 1px solid rgba(255, 255, 255, 22);
                border-radius: 9px;
                font-size: 18px;
            }
            #floatingToolbar QPushButton:hover {
                background: #4977ef;
            }
            """
        )

    def track_scrcpy_window(self) -> None:
        if not win32gui:
            return
        if self.window_handle_provider:
            hwnd = self.window_handle_provider()
        else:
            hwnd = win32gui.FindWindow(None, self.window_title)
        if not hwnd or not win32gui.IsWindow(hwnd):
            return
        left, top, right, _bottom = win32gui.GetWindowRect(hwnd)
        self.adjustSize()
        x = right + 8
        y = top + 48

        # Keep the toolbar visible when the scrcpy window is near a screen
        # edge. This also avoids making it look detached after a window move.
        screen = self.screen()
        if screen:
            available = screen.availableGeometry()
            if x + self.width() > available.right() + 1:
                x = left - self.width() - 8
            if y + self.height() > available.bottom() + 1:
                y = available.bottom() - self.height()
            x = max(available.left(), x)
            y = max(available.top(), y)
        self.move(x, y)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(820, 620)

        self.settings = QSettings("OpenAI", "ScrcpyControlCenter")
        self._loading_settings = True
        self.scrcpy_process: QProcess | None = None
        self.extra_scrcpy_processes: list[QProcess] = []
        self.multi_window_index = 0
        self.multi_apps: list[tuple[str, str]] = []
        self.multi_apps_serial = ""
        self._multi_app_lookup_busy = False
        self._pending_multi_open = False
        self.active_serial = ""
        self._shortcut_busy = False
        self.one_shot_processes: list[QProcess] = []
        self.toolbar: FloatingToolbar | None = None
        self.screen_control = ScreenPowerController(self)
        self.screen_control.busy_changed.connect(self.update_screen_toggle_button)
        self.screen_control.finished.connect(self.on_screen_power_finished)
        self.startup_remaining = 0
        self.startup_total = 0
        self.toolbar_timer = QTimer(self)
        self.toolbar_timer.timeout.connect(self.track_toolbar)
        self.device_refresh_timer = QTimer(self)
        self.device_refresh_timer.setInterval(2500)
        self.device_refresh_timer.timeout.connect(self.refresh_devices)

        self.scrcpy_path_source = self.settings.value("scrcpy_path_source", "auto", type=str)
        if self.scrcpy_path_source not in {"auto", "manual"}:
            self.scrcpy_path_source = "auto"
        self.saved_scrcpy_path = self.path_from_setting("scrcpy_path")
        self.scrcpy_path = self.saved_scrcpy_path if self.scrcpy_path_source == "manual" else None
        self.adb_path = self.path_from_setting("adb_path")

        self.build_ui()
        self.load_settings()
        self._loading_settings = False
        self.apply_styles()
        self.startup_progress.setValue(0)
        self.startup_progress.setFormat("正在准备启动… %p%")
        QTimer.singleShot(0, self.initialize_runtime)

    def path_from_setting(self, key: str) -> Path | None:
        value = self.settings.value(key, "", type=str)
        path = Path(value) if value else None
        return path if path and path.is_file() else None

    def build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root = QWidget()
        root.setMinimumSize(0, 0)
        root.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(12, 10, 12, 12)
        main_layout.setSpacing(8)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("scrcpy 控制中心")
        title.setObjectName("title")
        subtitle = QLabel("无线连接、启动参数与运行中控制")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        self.status_label = QLabel("准备就绪")
        self.status_label.setObjectName("status")
        header.addWidget(self.status_label)
        self.startup_progress = QProgressBar()
        self.startup_progress.setRange(0, 100)
        self.startup_progress.setValue(0)
        self.startup_progress.setTextVisible(True)
        self.startup_progress.setFormat("正在准备启动… %p%")
        self.startup_progress.setFixedWidth(170)
        self.startup_progress.setToolTip("启动器正在后台检测 scrcpy、adb 和设备")
        header.addWidget(self.startup_progress)
        main_layout.addLayout(header)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        top_row.addWidget(self.build_device_group(), 1)
        top_row.addWidget(self.build_update_group(), 1)
        main_layout.addLayout(top_row)

        settings_row = QHBoxLayout()
        settings_row.setSpacing(8)
        settings_row.addWidget(self.build_start_group(), 1)
        settings_row.addWidget(self.build_video_audio_group(), 1)
        main_layout.addLayout(settings_row)

        bottom_settings_row = QHBoxLayout()
        bottom_settings_row.setSpacing(8)
        bottom_settings_row.addWidget(self.build_window_group(), 1)
        bottom_settings_row.addWidget(self.build_wireless_group(), 1)
        main_layout.addLayout(bottom_settings_row)

        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setMinimumHeight(42)
        self.log_view.setMaximumHeight(72)
        self.log_view.setPlaceholderText("这里会显示 adb、scrcpy 和更新操作的结果。")
        log_layout.addWidget(self.log_view)
        main_layout.addWidget(log_group, 1)

        shortcut_group = QGroupBox("常用快捷指令")
        shortcut_layout = QVBoxLayout(shortcut_group)
        self.shortcut_list = QLabel(
            "MOD 默认是左 Alt（也支持左 Win）：\n"
            "Alt+O / Shift+Alt+O　关闭 / 开启手机屏幕\n"
            "Alt+H / 鼠标中键　主页　　Alt+B / Alt+Backspace　返回\n"
            "Alt+S　最近任务　　　　Alt+F / F11　全屏\n"
            "Alt+← / Alt+→　向左 / 向右旋转画面\n"
            "Alt+Shift+R　重置视频采集　　Alt+W　去除黑边\n"
            "Alt+K　打开 UHID 键盘设置；启用键盘控制需在启动前勾选 -K"
        )
        self.shortcut_list.setObjectName("shortcutList")
        self.shortcut_list.setWordWrap(True)
        shortcut_layout.addWidget(self.shortcut_list)
        main_layout.addWidget(shortcut_group)

        footer = QHBoxLayout()
        self.path_label = QLabel()
        self.path_label.setObjectName("pathLabel")
        self.path_label.setMinimumWidth(0)
        self.path_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        footer.addWidget(self.path_label, 1)
        self.refresh_button = QPushButton("刷新设备")
        self.refresh_button.clicked.connect(self.refresh_devices)
        footer.addWidget(self.refresh_button)
        self.choose_scrcpy_button = QPushButton("选择 scrcpy")
        self.choose_scrcpy_button.clicked.connect(self.choose_scrcpy)
        footer.addWidget(self.choose_scrcpy_button)
        self.start_button = QPushButton("启动 scrcpy")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.start_scrcpy)
        footer.addWidget(self.start_button)
        self.multi_open_button = QPushButton("新增独立窗口")
        self.multi_open_button.setObjectName("multiOpenButton")
        self.multi_open_button.setToolTip("使用 scrcpy --new-display 创建一个与主窗口独立的 Android 虚拟显示")
        self.multi_open_button.clicked.connect(self.open_multi_scrcpy)
        footer.addWidget(self.multi_open_button)
        main_layout.addLayout(footer)

        self.update_screen_toggle_button()
        self.update_path_label()

    def build_device_group(self) -> QGroupBox:
        group = QGroupBox("设备")
        layout = QVBoxLayout(group)
        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(self.on_device_changed)
        layout.addWidget(self.device_combo)
        self.device_info = QLabel("等待 adb 设备")
        self.device_info.setObjectName("muted")
        layout.addWidget(self.device_info)
        self.auto_reconnect = QCheckBox("设备出现后自动刷新并保持选择")
        layout.addWidget(self.auto_reconnect)
        return group

    def build_update_group(self) -> QGroupBox:
        group = QGroupBox("scrcpy 版本")
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self.version_label = QLabel("当前版本：检查中")
        self.version_label.setObjectName("version")
        row.addWidget(self.version_label, 1)
        self.check_update_button = QPushButton("检查更新")
        self.check_update_button.clicked.connect(self.check_update)
        row.addWidget(self.check_update_button)
        self.update_button = QPushButton("一键更新")
        self.update_button.clicked.connect(self.update_scrcpy)
        row.addWidget(self.update_button)
        layout.addLayout(row)
        note = QLabel("通过 WinGet 更新官方 Genymobile.scrcpy 包")
        note.setObjectName("muted")
        layout.addWidget(note)
        return group

    def build_start_group(self) -> QGroupBox:
        group = QGroupBox("启动前与手机状态")
        layout = QVBoxLayout(group)
        self.keep_awake = QCheckBox("保持手机唤醒")
        self.turn_screen_off = QCheckBox("启动时关闭手机屏幕")
        self.screen_on_start = QCheckBox("启动时唤醒手机")
        self.power_off_on_close = QCheckBox("关闭 scrcpy 时关闭手机屏幕")
        self.show_touches = QCheckBox("显示手机实体触摸点")
        self.uhid_keyboard = QCheckBox("启用键盘控制（scrcpy -K / UHID）")
        self.uhid_keyboard.setToolTip("启动 scrcpy 时加入 -K，使用 UHID 物理键盘模式；首次使用需在手机上配置物理键盘布局。")
        self.show_toolbar = QCheckBox("显示按键悬浮窗")
        self.show_toolbar.setToolTip("启动 scrcpy 后显示返回、主页、最近任务和屏幕开关按钮；取消勾选即可关闭。")
        for checkbox in (
            self.keep_awake,
            self.turn_screen_off,
            self.screen_on_start,
            self.power_off_on_close,
            self.show_touches,
            self.uhid_keyboard,
            self.show_toolbar,
        ):
            checkbox.stateChanged.connect(self.save_settings)
            layout.addWidget(checkbox)
        self.show_toolbar.stateChanged.connect(self.on_toolbar_setting_changed)
        return group

    def build_video_audio_group(self) -> QGroupBox:
        group = QGroupBox("画面与声音")
        layout = QFormLayout(group)
        self.max_size = QComboBox()
        for label, value in (
            ("设备原始分辨率", 0),
            ("480p（省资源）", 480),
            ("720p（常用）", 720),
            ("1080p（推荐）", 1080),
            ("1440p（高画质）", 1440),
            ("2160p（4K）", 2160),
        ):
            self.max_size.addItem(label, value)
        self.max_size.setMinimumWidth(155)
        self.max_size.currentIndexChanged.connect(self.save_settings)
        layout.addRow("分辨率上限", self.max_size)

        self.max_fps = QComboBox()
        for label, value in (
            ("不限制", 0),
            ("30 FPS（省资源）", 30),
            ("60 FPS（常用）", 60),
            ("90 FPS", 90),
            ("120 FPS（高刷）", 120),
            ("144 FPS", 144),
            ("240 FPS", 240),
        ):
            self.max_fps.addItem(label, value)
        self.max_fps.setMinimumWidth(155)
        self.max_fps.currentIndexChanged.connect(self.save_settings)
        layout.addRow("帧率上限", self.max_fps)

        self.video_bitrate = QLineEdit()
        self.video_bitrate.setPlaceholderText("例如 8M")
        self.video_bitrate.textChanged.connect(self.save_settings)
        layout.addRow("视频码率", self.video_bitrate)

        self.codec_combo = QComboBox()
        self.codec_combo.addItem("H.264", "h264")
        self.codec_combo.addItem("H.265", "h265")
        self.codec_combo.addItem("AV1", "av1")
        self.codec_combo.currentIndexChanged.connect(self.save_settings)
        layout.addRow("视频编码", self.codec_combo)

        self.no_audio = QCheckBox("关闭音频")
        self.no_audio.stateChanged.connect(self.save_settings)
        layout.addRow("音频", self.no_audio)

        self.clipboard_sync = QCheckBox("同步剪贴板")
        self.clipboard_sync.stateChanged.connect(self.save_settings)
        layout.addRow("剪贴板", self.clipboard_sync)
        return group

    def build_window_group(self) -> QGroupBox:
        group = QGroupBox("窗口与显示变换")
        layout = QFormLayout(group)
        self.orientation_combo = QComboBox()
        orientations = [
            ("自动", ""),
            ("0°", "0"),
            ("90°", "90"),
            ("180°", "180"),
            ("270°", "270"),
            ("左右镜像", "flip0"),
            ("上下镜像", "flip180"),
            ("左右镜像 + 90°", "flip90"),
            ("左右镜像 + 270°", "flip270"),
        ]
        for label, value in orientations:
            self.orientation_combo.addItem(label, value)
        self.orientation_combo.currentIndexChanged.connect(self.save_settings)
        layout.addRow("显示变换", self.orientation_combo)

        self.fullscreen = QCheckBox("全屏启动")
        self.always_on_top = QCheckBox("窗口置顶")
        self.borderless = QCheckBox("无边框窗口")
        self.remember_window = QCheckBox("记住窗口位置和大小")
        for checkbox in (self.fullscreen, self.always_on_top, self.borderless, self.remember_window):
            checkbox.stateChanged.connect(self.save_settings)
            layout.addRow(checkbox)

        self.window_title_edit = QLineEdit("scrcpy")
        self.window_title_edit.textChanged.connect(self.save_settings)
        layout.addRow("窗口标题", self.window_title_edit)
        app_row = QHBoxLayout()
        app_row.setSpacing(6)
        self.multi_app_combo = QComboBox()
        self.multi_app_combo.setEditable(True)
        self.multi_app_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.multi_app_combo.setPlaceholderText("先读取应用，再输入中文名称搜索")
        self.multi_app_combo.setToolTip("先点击“读取应用”，再输入手机上显示的中文名称；也支持直接填写 Android 包名。")
        self.multi_app_combo.setMinimumWidth(175)
        self.multi_app_combo.currentTextChanged.connect(self.save_settings)
        self.multi_app_completer = QCompleter(self.multi_app_combo.model(), self.multi_app_combo)
        self.multi_app_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.multi_app_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.multi_app_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.multi_app_combo.setCompleter(self.multi_app_completer)
        app_row.addWidget(self.multi_app_combo, 1)
        self.load_apps_button = QPushButton("读取应用")
        self.load_apps_button.setToolTip("通过 scrcpy --list-apps 读取手机中可以启动的应用名称")
        self.load_apps_button.clicked.connect(self.load_multi_apps)
        app_row.addWidget(self.load_apps_button)
        layout.addRow("独立窗口 App", app_row)
        self.multi_app_status = QLabel("尚未读取应用列表")
        self.multi_app_status.setObjectName("muted")
        layout.addRow("应用检索", self.multi_app_status)
        return group

    def build_wireless_group(self) -> QGroupBox:
        group = QGroupBox("无线 ADB")
        outer = QVBoxLayout(group)

        connect_row = QHBoxLayout()
        self.wifi_address = QLineEdit()
        self.wifi_address.setPlaceholderText("设备 IP，例如 192.168.1.20")
        self.wifi_port = QSpinBox()
        self.wifi_port.setRange(1, 65535)
        self.wifi_port.setValue(5555)
        self.wifi_port.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.wifi_port.setFixedWidth(70)
        self.wifi_port.setToolTip("Wi‑Fi ADB 端口，默认 5555")
        self.wifi_port.valueChanged.connect(self.save_settings)
        self.connect_wifi_button = QPushButton("连接 Wi‑Fi ADB")
        self.connect_wifi_button.clicked.connect(self.connect_wifi)
        connect_row.addWidget(self.wifi_address, 1)
        connect_row.addWidget(self.wifi_port)
        connect_row.addWidget(self.connect_wifi_button)
        outer.addLayout(connect_row)

        pair_row = QHBoxLayout()
        self.pair_address = QLineEdit()
        self.pair_address.setPlaceholderText("Android 11+ 配对地址:端口")
        self.pair_code = QLineEdit()
        self.pair_code.setPlaceholderText("配对码")
        self.pair_button = QPushButton("配对")
        self.pair_button.clicked.connect(self.pair_wifi)
        self.usb_to_wifi_button = QPushButton("USB 一键切换 Wi‑Fi")
        self.usb_to_wifi_button.clicked.connect(self.switch_usb_to_wifi)
        pair_row.addWidget(self.pair_address, 1)
        pair_row.addWidget(self.pair_code)
        pair_row.addWidget(self.pair_button)
        pair_row.addWidget(self.usb_to_wifi_button)
        outer.addLayout(pair_row)

        note = QLabel("手机与电脑需在同一网络；Android 11+ 可使用无线调试配对。")
        note.setObjectName("muted")
        outer.addWidget(note)
        for field in (self.wifi_address, self.pair_address, self.pair_code):
            field.textChanged.connect(self.save_settings)
        return group

    def initialize_runtime(self) -> None:
        self.set_status("正在初始化…")
        self.startup_progress.setValue(10)
        self.startup_progress.setFormat("正在检测运行环境… %p%")
        QTimer.singleShot(0, self.detect_runtime)

    def detect_runtime(self) -> None:
        if self.scrcpy_path_source == "manual":
            if not self.scrcpy_path:
                self.scrcpy_path = discover_scrcpy() or self.saved_scrcpy_path
                if self.scrcpy_path and self.scrcpy_path != self.saved_scrcpy_path:
                    self.scrcpy_path_source = "auto"
        else:
            # Re-scan every launch so a newly installed or WinGet-updated
            # system scrcpy is selected instead of an older saved fallback.
            self.scrcpy_path = discover_scrcpy() or self.saved_scrcpy_path
        if not self.adb_path:
            self.adb_path = discover_adb(self.scrcpy_path)
        self.update_path_label()
        self.save_settings()

        tasks = int(bool(self.adb_path)) + int(bool(self.scrcpy_path))
        self.startup_total = tasks
        self.startup_remaining = tasks
        if self.adb_path:
            self.refresh_devices(self.startup_task_finished)
        if self.scrcpy_path:
            self.read_scrcpy_version(self.startup_task_finished)
        if not tasks:
            self.startup_progress.setValue(100)
            self.startup_progress.setFormat("未找到运行环境")
            self.set_status("未找到 scrcpy 或 adb", False)
        else:
            self.startup_progress.setValue(40)
            self.startup_progress.setFormat("正在检测设备和版本… %p%")
        self.device_refresh_timer.start()

    def startup_task_finished(self) -> None:
        if self.startup_remaining <= 0:
            return
        self.startup_remaining -= 1
        completed = self.startup_total - self.startup_remaining
        progress = 40 + int(60 * completed / max(1, self.startup_total))
        self.startup_progress.setValue(progress)
        if self.startup_remaining == 0:
            self.startup_progress.setFormat("启动完成 %p%")
            self.set_status("准备就绪")

    def apply_styles(self) -> None:
        arrow_icon = (Path(__file__).resolve().parent / "assets" / "chevron-down.svg").as_posix()
        self.setStyleSheet(
            """
            QMainWindow { background: #10141b; }
            QWidget { color: #edf3fb; font-size: 12px; }
            QGroupBox {
                border: 1px solid #303b4d;
                border-radius: 10px;
                margin-top: 10px;
                padding: 8px 8px 7px;
                background: #171c25;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #a9b7cc;
            }
            QLabel#title { font-size: 19px; font-weight: 600; color: #f4f7fc; }
            QLabel#subtitle, QLabel#muted, QLabel#pathLabel { color: #92a0b5; }
            QLabel#shortcutList { color: #b7c4d8; padding: 2px 4px 1px; line-height: 1.35; }
            QLabel#status { color: #64d39b; padding: 5px 9px; background: #1b3a2d; border-radius: 7px; }
            QLabel#version { font-size: 14px; font-weight: 600; }
            QComboBox, QLineEdit, QSpinBox, QPlainTextEdit {
                min-height: 28px;
                border: 1px solid #344156;
                border-radius: 7px;
                padding: 4px 10px;
                background: #222b38;
                color: #edf3fb;
                selection-background-color: #426fe4;
            }
            QComboBox {
                min-height: 30px;
                padding: 0 38px 0 12px;
            }
            QComboBox:hover, QComboBox:focus {
                border-color: #5d83e8;
                background: #273247;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 29px;
                border-left: 1px solid #344156;
                background: #1d2532;
                border-top-right-radius: 7px;
                border-bottom-right-radius: 7px;
            }
            QComboBox::drop-down:hover { background: #304365; }
            QComboBox::down-arrow {
                image: url("__ARROW_ICON__");
                width: 12px;
                height: 8px;
            }
            QComboBox QAbstractItemView {
                min-width: 190px;
                padding: 5px;
                border: 1px solid #4a5e80;
                border-radius: 8px;
                background: #192231;
                color: #edf3fb;
                outline: none;
                selection-background-color: #426fe4;
                selection-color: #ffffff;
            }
            QComboBox QAbstractItemView::item {
                min-height: 26px;
                padding: 4px 8px;
                border-radius: 5px;
            }
            QComboBox QAbstractItemView::item:hover { background: #2d3c59; }
            QPlainTextEdit { font-family: Consolas, monospace; }
            QProgressBar {
                min-height: 18px;
                border: 1px solid #344156;
                border-radius: 7px;
                background: #1b2432;
                color: #dce7fb;
                text-align: center;
            }
            QProgressBar::chunk {
                border-radius: 6px;
                background: #426fe4;
            }
            QCheckBox { spacing: 6px; min-height: 22px; }
            QPushButton {
                min-height: 28px;
                padding: 4px 11px;
                border: 1px solid #3b4a62;
                border-radius: 7px;
                background: #222b38;
                color: #edf3fb;
            }
            QPushButton:hover { background: #2d3b52; }
            QPushButton#primaryButton { background: #426fe4; border-color: #426fe4; font-weight: 600; padding: 5px 20px; }
            QPushButton#primaryButton:hover { background: #557ff0; }
            """.replace("__ARROW_ICON__", arrow_icon)
        )

    def save_settings(self, *_args) -> None:
        if self._loading_settings:
            return
        self.settings.setValue("scrcpy_path", str(self.scrcpy_path) if self.scrcpy_path else "")
        self.settings.setValue("scrcpy_path_source", self.scrcpy_path_source)
        self.settings.setValue("adb_path", str(self.adb_path) if self.adb_path else "")
        self.settings.setValue("auto_reconnect", self.auto_reconnect.isChecked())
        self.settings.setValue("keep_awake", self.keep_awake.isChecked())
        self.settings.setValue("turn_screen_off", self.turn_screen_off.isChecked())
        self.settings.setValue("screen_on_start", self.screen_on_start.isChecked())
        self.settings.setValue("power_off_on_close", self.power_off_on_close.isChecked())
        self.settings.setValue("show_touches", self.show_touches.isChecked())
        self.settings.setValue("uhid_keyboard", self.uhid_keyboard.isChecked())
        self.settings.setValue("show_toolbar", self.show_toolbar.isChecked())
        self.settings.setValue("max_size", self.max_size.currentData())
        self.settings.setValue("max_fps", self.max_fps.currentData())
        self.settings.setValue("video_bitrate", self.video_bitrate.text())
        self.settings.setValue("codec", self.codec_combo.currentData())
        self.settings.setValue("no_audio", self.no_audio.isChecked())
        self.settings.setValue("clipboard_sync", self.clipboard_sync.isChecked())
        self.settings.setValue("orientation", self.orientation_combo.currentData())
        self.settings.setValue("fullscreen", self.fullscreen.isChecked())
        self.settings.setValue("always_on_top", self.always_on_top.isChecked())
        self.settings.setValue("borderless", self.borderless.isChecked())
        self.settings.setValue("remember_window", self.remember_window.isChecked())
        self.settings.setValue("window_title", self.window_title_edit.text())
        self.settings.setValue("multi_app", self.multi_app_combo.currentText())
        self.settings.setValue("wifi_address", self.wifi_address.text())
        self.settings.setValue("wifi_port", self.wifi_port.value())
        self.settings.setValue("pair_address", self.pair_address.text())
        self.settings.setValue("pair_code", self.pair_code.text())
        if self.remember_window.isChecked():
            self.settings.setValue("window_geometry", self.saveGeometry())
        self.settings.sync()

    def load_settings(self) -> None:
        self.settings.remove("secure_mode")
        def get_bool(key: str, default: bool) -> bool:
            return self.settings.value(key, default, type=bool)

        self.auto_reconnect.setChecked(get_bool("auto_reconnect", True))
        self.keep_awake.setChecked(get_bool("keep_awake", True))
        self.turn_screen_off.setChecked(get_bool("turn_screen_off", False))
        self.screen_on_start.setChecked(get_bool("screen_on_start", True))
        self.power_off_on_close.setChecked(get_bool("power_off_on_close", False))
        self.show_touches.setChecked(get_bool("show_touches", False))
        self.uhid_keyboard.setChecked(get_bool("uhid_keyboard", False))
        self.show_toolbar.setChecked(get_bool("show_toolbar", True))
        max_size = self.settings.value("max_size", 0, type=int)
        max_size_index = self.max_size.findData(max_size)
        self.max_size.setCurrentIndex(max(0, max_size_index))
        max_fps = self.settings.value("max_fps", 60, type=int)
        max_fps_index = self.max_fps.findData(max_fps)
        self.max_fps.setCurrentIndex(max(0, max_fps_index))
        self.video_bitrate.setText(self.settings.value("video_bitrate", "8M", type=str))
        codec = self.settings.value("codec", "h264", type=str)
        codec_index = self.codec_combo.findData(codec)
        self.codec_combo.setCurrentIndex(max(0, codec_index))
        self.no_audio.setChecked(get_bool("no_audio", False))
        self.clipboard_sync.setChecked(get_bool("clipboard_sync", True))
        orientation = self.settings.value("orientation", "", type=str)
        orientation_index = self.orientation_combo.findData(orientation)
        self.orientation_combo.setCurrentIndex(max(0, orientation_index))
        self.fullscreen.setChecked(get_bool("fullscreen", False))
        self.always_on_top.setChecked(get_bool("always_on_top", False))
        self.borderless.setChecked(get_bool("borderless", False))
        self.remember_window.setChecked(get_bool("remember_window", True))
        self.window_title_edit.setText(self.settings.value("window_title", "scrcpy", type=str))
        self.multi_app_combo.setEditText(self.settings.value("multi_app", "", type=str))
        self.wifi_address.setText(self.settings.value("wifi_address", "", type=str))
        self.wifi_port.setValue(self.settings.value("wifi_port", 5555, type=int))
        self.pair_address.setText(self.settings.value("pair_address", "", type=str))
        self.pair_code.setText(self.settings.value("pair_code", "", type=str))
        layout_version = self.settings.value("layout_version", 0, type=int)
        if layout_version < 2:
            self.settings.remove("window_geometry")
            self.settings.setValue("layout_version", 2)
            self.resize(820, 620)
        elif self.remember_window.isChecked():
            geometry = self.settings.value("window_geometry")
            if geometry:
                self.restoreGeometry(geometry)
        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            if self.width() > int(available.width() * 0.9) or self.height() > int(available.height() * 0.9):
                self.resize(min(820, available.width() - 40), min(620, available.height() - 80))

    def update_path_label(self) -> None:
        scrcpy_text = str(self.scrcpy_path) if self.scrcpy_path else "未找到 scrcpy"
        adb_text = str(self.adb_path) if self.adb_path else "未找到 adb"
        def compact(path_text: str) -> str:
            path = Path(path_text)
            if not path.is_absolute():
                return path_text
            return path.name

        self.path_label.setText(f"scrcpy: {compact(scrcpy_text)}   |   adb: {compact(adb_text)}")
        self.path_label.setToolTip(f"scrcpy: {scrcpy_text}\nadb: {adb_text}")
        self.update_multi_open_button()

    def log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def set_status(self, message: str, good: bool = True) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            "color: #64d39b; padding: 8px 12px; background: #1b3a2d; border-radius: 8px;"
            if good
            else "color: #ffbe7b; padding: 8px 12px; background: #49301f; border-radius: 8px;"
        )

    def selected_serial(self) -> str:
        return self.device_combo.currentData() or ""

    def adb_command(self, args: list[str], callback=None) -> None:
        if not self.adb_path:
            self.set_status("未找到 adb", False)
            self.log("错误：未找到 adb.exe")
            return
        self.run_one_shot(self.adb_path, args, callback)

    def run_one_shot(self, program: Path | str, args: list[str], callback=None) -> None:
        process = QProcess(self)
        process.setProgram(str(program))
        process.setArguments(args)
        if isinstance(program, Path):
            process.setWorkingDirectory(str(program.parent))
        self.one_shot_processes.append(process)

        def finished(_exit_code: int, _exit_status) -> None:
            stdout = bytes(process.readAllStandardOutput()).decode(errors="replace")
            stderr = bytes(process.readAllStandardError()).decode(errors="replace")
            output = (stdout + stderr).strip()
            if output:
                self.log(output)
            if callback:
                callback(_exit_code, output)
            if process in self.one_shot_processes:
                self.one_shot_processes.remove(process)
            process.deleteLater()

        process.finished.connect(finished)
        process.errorOccurred.connect(lambda _error: self.log(f"进程错误：{process.errorString()}"))
        process.start()

    def refresh_devices(self, finished_callback=None) -> None:
        if not self.adb_path:
            self.device_info.setText("未找到 adb.exe")
            if finished_callback:
                finished_callback()
            return
        previous = self.selected_serial()

        def done(_exit_code: int, output: str) -> None:
            devices = []
            for line in output.splitlines():
                line = line.strip()
                if not line or line.startswith("List of devices"):
                    continue
                parts = line.split()
                if len(parts) < 2 or parts[1] not in {"device", "unauthorized", "offline"}:
                    continue
                serial = parts[0]
                state = parts[1]
                model_match = re.search(r"model:([^\s]+)", line)
                model = model_match.group(1).replace("_", " ") if model_match else serial
                connection = "Wi‑Fi" if ":" in serial else "USB"
                devices.append((serial, state, model, connection))

            self.device_combo.blockSignals(True)
            self.device_combo.clear()
            for serial, state, model, connection in devices:
                label = f"{model} · {connection} · {state}"
                self.device_combo.addItem(label, serial)
            if previous:
                index = self.device_combo.findData(previous)
                if index >= 0:
                    self.device_combo.setCurrentIndex(index)
            if self.device_combo.count() and self.device_combo.currentIndex() < 0:
                self.device_combo.setCurrentIndex(0)
            self.device_combo.blockSignals(False)
            self.on_device_changed()
            if finished_callback:
                finished_callback()

        self.adb_command(["devices", "-l"], done)

    def on_device_changed(self, *_args) -> None:
        serial = self.selected_serial()
        if serial:
            self.device_info.setText(f"序列号：{serial}")
            self.usb_to_wifi_button.setEnabled(":" not in serial)
        else:
            self.device_info.setText("等待 adb 设备")
            self.usb_to_wifi_button.setEnabled(False)
        if serial != self.multi_apps_serial:
            self.clear_multi_app_list()
            self.multi_apps_serial = serial
        self.save_settings()
        self.update_screen_toggle_button()
        self.update_multi_open_button()

    def clear_multi_app_list(self) -> None:
        """Discard the cached app mapping when the selected device changes."""
        if not hasattr(self, "multi_app_combo"):
            return
        self.multi_apps.clear()
        self.multi_apps_serial = ""
        current_text = self.multi_app_combo.currentText()
        self.multi_app_combo.blockSignals(True)
        self.multi_app_combo.clear()
        self.multi_app_combo.setEditText(current_text)
        self.multi_app_combo.blockSignals(False)
        self.multi_app_status.setText("尚未读取应用列表")

    @staticmethod
    def parse_scrcpy_apps(output: str) -> list[tuple[str, str]]:
        """Parse the human-readable app table emitted by scrcpy --list-apps."""
        package_pattern = r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+"
        apps: list[tuple[str, str]] = []
        pending_name = ""
        for line in output.splitlines():
            text = re.sub(r"^\s*(?:DEBUG|INFO|WARN|ERROR):\s*", "", line.rstrip(), flags=re.IGNORECASE)
            marked = re.match(r"^\s*[*-]\s+(.*)$", text)
            if marked:
                pending_name = marked.group(1).strip()
                match = re.search(rf"(?P<package>{package_pattern})\s*$", pending_name)
                if match:
                    package = match.group("package")
                    name = pending_name[: match.start()].strip()
                    if name:
                        apps.append((name, package))
                        pending_name = ""
                continue

            if not pending_name:
                continue
            match = re.search(rf"(?P<package>{package_pattern})\s*$", text.strip())
            if not match:
                continue
            package = match.group("package")
            name = pending_name.strip()
            if name:
                apps.append((name, package))
            pending_name = ""

        unique: list[tuple[str, str]] = []
        seen_packages: set[str] = set()
        for name, package in apps:
            if package in seen_packages:
                continue
            seen_packages.add(package)
            unique.append((name, package))
        return unique

    def load_multi_apps(self, after_loaded=None) -> None:
        if self._multi_app_lookup_busy:
            return
        if not self.scrcpy_path:
            self.set_status("未找到 scrcpy，无法读取应用列表。", False)
            return
        serial = self.selected_serial()
        if not serial:
            self.set_status("请先选择一台 adb 设备。", False)
            return

        self._multi_app_lookup_busy = True
        self.load_apps_button.setEnabled(False)
        self.multi_app_status.setText("正在读取手机应用……")
        self.set_status("正在读取手机应用列表……")

        def done(exit_code: int, output: str) -> None:
            self._multi_app_lookup_busy = False
            self.load_apps_button.setEnabled(True)
            if exit_code != 0:
                self.multi_app_status.setText("读取失败，请查看日志")
                self.set_status("读取应用列表失败，请查看日志。", False)
            else:
                self.multi_apps = self.parse_scrcpy_apps(output)
                current_text = self.multi_app_combo.currentText()
                self.multi_app_combo.blockSignals(True)
                self.multi_app_combo.clear()
                for name, package in self.multi_apps:
                    index = self.multi_app_combo.count()
                    self.multi_app_combo.addItem(name, package)
                    self.multi_app_combo.setItemData(index, package, Qt.ItemDataRole.ToolTipRole)
                self.multi_app_combo.setEditText(current_text)
                self.multi_app_combo.blockSignals(False)
                self.multi_app_status.setText(f"已读取 {len(self.multi_apps)} 个可启动应用，可输入中文搜索")
                self.set_status("应用列表读取完成")
            if after_loaded:
                after_loaded()
            if self._pending_multi_open:
                self._pending_multi_open = False
                if self.multi_apps:
                    self.open_multi_scrcpy()
                else:
                    self.set_status("没有读取到可启动应用，未创建独立窗口。", False)

        self.run_one_shot(self.scrcpy_path, ["--serial", serial, "--list-apps"], done)

    def read_scrcpy_version(self, finished_callback=None) -> None:
        if not self.scrcpy_path:
            self.version_label.setText("当前版本：未找到")
            if finished_callback:
                finished_callback()
            return

        def done(_exit_code: int, output: str) -> None:
            version = parse_scrcpy_version(output)
            self.version_label.setText(f"当前版本：{version}")
            if finished_callback:
                finished_callback()

        self.run_one_shot(self.scrcpy_path, ["--version"], done)

    def choose_scrcpy(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "选择 scrcpy.exe", "", "scrcpy.exe (scrcpy.exe)")
        if not path:
            return
        self.scrcpy_path = Path(path)
        self.scrcpy_path_source = "manual"
        self.adb_path = discover_adb(self.scrcpy_path) or self.adb_path
        self.update_path_label()
        self.save_settings()
        self.read_scrcpy_version()

    def find_winget(self) -> Path | None:
        path_value = shutil.which("winget")
        candidates = [Path(path_value)] if path_value else []
        candidates.append(local_app_data() / "Microsoft" / "WindowsApps" / "winget.exe")
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def check_update(self) -> None:
        winget = self.find_winget()
        if not winget:
            self.set_status("未找到 WinGet", False)
            self.log("错误：未找到 winget.exe，无法检查更新。")
            return
        self.log("正在检查 scrcpy 更新……")
        self.run_one_shot(
            winget,
            ["upgrade", "--id", SCRCPY_PACKAGE_ID, "--exact", "--source", "winget", "--accept-source-agreements"],
            self.on_update_check_finished,
        )

    def on_update_check_finished(self, exit_code: int, output: str) -> None:
        if winget_reports_no_upgrade(output):
            self.set_status("已是最新版本")
            self.log("WinGet 未发现可用的 scrcpy 更新。")
        elif exit_code != 0:
            self.log(f"WinGet 检查更新失败，返回代码：{exit_code}")
            self.set_status("检查更新失败，请查看日志", False)
        else:
            self.set_status("发现可用更新，请点击一键更新")

    def update_scrcpy(self) -> None:
        if self.scrcpy_process and self.scrcpy_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, "请先停止 scrcpy", "更新前需要先关闭正在运行的 scrcpy 窗口。")
            return
        winget = self.find_winget()
        if not winget:
            self.set_status("未找到 WinGet", False)
            self.log("错误：未找到 winget.exe，无法更新。")
            return
        self.log("正在通过 WinGet 更新 scrcpy……")
        self.run_one_shot(
            winget,
            [
                "upgrade",
                "--id",
                SCRCPY_PACKAGE_ID,
                "--exact",
                "--source",
                "winget",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
            self.on_update_finished,
        )

    def on_update_finished(self, exit_code: int, _output: str) -> None:
        if exit_code != 0:
            self.log(f"WinGet 更新失败，返回代码：{exit_code}")
            self.set_status("更新失败，请查看日志", False)
            return
        if self.scrcpy_path_source != "manual":
            self.scrcpy_path = discover_scrcpy() or self.scrcpy_path
        self.adb_path = discover_adb(self.scrcpy_path) or self.adb_path
        self.update_path_label()
        self.save_settings()
        self.set_status("更新操作完成")
        self.read_scrcpy_version()

    def build_scrcpy_args(
        self,
        title_override: str | None = None,
        new_display: bool = False,
        start_app: str = "",
    ) -> list[str]:
        serial = self.selected_serial()
        args: list[str] = []
        if serial:
            args.extend(["--serial", serial])
        if new_display:
            args.extend(["--new-display", "--display-ime-policy=local"])
            if start_app:
                # The default virtual-display launcher is known to render a
                # broken UI on some devices. When an app is selected, mirror
                # only that app and let the app own the virtual display.
                args.append("--no-vd-system-decorations")
        max_size = int(self.max_size.currentData() or 0)
        if max_size > 0:
            args.extend(["--max-size", str(max_size)])
        max_fps = int(self.max_fps.currentData() or 0)
        if max_fps > 0:
            args.extend(["--max-fps", str(max_fps)])
        if self.video_bitrate.text().strip():
            args.extend(["--video-bit-rate", self.video_bitrate.text().strip()])
        codec = self.codec_combo.currentData()
        if codec:
            args.extend(["--video-codec", codec])
        if self.no_audio.isChecked():
            args.append("--no-audio")
        if not self.clipboard_sync.isChecked():
            args.append("--no-clipboard-autosync")
        if self.keep_awake.isChecked():
            args.append("--stay-awake")
        if not new_display:
            if self.turn_screen_off.isChecked():
                args.append("--turn-screen-off")
            if not self.screen_on_start.isChecked():
                args.append("--no-power-on")
            if self.power_off_on_close.isChecked():
                args.append("--power-off-on-close")
            if self.show_touches.isChecked():
                args.append("--show-touches")
        if self.uhid_keyboard.isChecked():
            args.append("-K")
        orientation = self.orientation_combo.currentData()
        if orientation:
            args.extend(["--orientation", orientation])
        if self.fullscreen.isChecked():
            args.append("--fullscreen")
        if self.always_on_top.isChecked():
            args.append("--always-on-top")
        if self.borderless.isChecked():
            args.append("--window-borderless")
        title = title_override or self.window_title_edit.text().strip() or "scrcpy"
        args.extend(["--window-title", title])
        if start_app:
            args.extend(["--start-app", start_app])
        return args

    def start_scrcpy(self) -> None:
        if not self.scrcpy_path:
            QMessageBox.warning(self, "找不到 scrcpy", "请选择 scrcpy.exe 的位置。")
            return
        if self.scrcpy_process and self.scrcpy_process.state() != QProcess.ProcessState.NotRunning:
            self.stop_scrcpy()
            return
        self.save_settings()
        self.active_serial = self.selected_serial()

        title = self.window_title_edit.text().strip() or "scrcpy"
        args = self.build_scrcpy_args()
        self.scrcpy_process = QProcess(self)
        self.scrcpy_process.setProgram(str(self.scrcpy_path))
        self.scrcpy_process.setArguments(args)
        self.scrcpy_process.setWorkingDirectory(str(self.scrcpy_path.parent))
        self.scrcpy_process.readyReadStandardOutput.connect(
            lambda: self.log(bytes(self.scrcpy_process.readAllStandardOutput()).decode(errors="replace").strip())
        )
        self.scrcpy_process.readyReadStandardError.connect(
            lambda: self.log(bytes(self.scrcpy_process.readAllStandardError()).decode(errors="replace").strip())
        )
        self.scrcpy_process.started.connect(lambda: self.on_scrcpy_started(title, args))
        self.scrcpy_process.finished.connect(self.on_scrcpy_finished)
        self.scrcpy_process.errorOccurred.connect(lambda _error: self.log(f"scrcpy 启动错误：{self.scrcpy_process.errorString()}"))
        self.scrcpy_process.start()
        self.start_button.setText("停止 scrcpy")
        self.update_screen_toggle_button()
        self.set_status("正在启动 scrcpy……")

    def update_multi_open_button(self) -> None:
        if not hasattr(self, "multi_open_button"):
            return
        available = bool(self.scrcpy_path and self.adb_path and self.selected_serial())
        self.multi_open_button.setEnabled(available)
        count = len(self.extra_scrcpy_processes)
        self.multi_open_button.setText(f"新增独立窗口（{count}）" if count else "新增独立窗口")

    def open_multi_scrcpy(self) -> None:
        if not self.scrcpy_path:
            self.set_status("未找到 scrcpy，无法创建独立窗口。", False)
            return
        serial = self.selected_serial()
        if not serial:
            self.set_status("请先选择一台 adb 设备。", False)
            return

        raw_app = self.multi_app_combo.currentText().strip()
        if not raw_app:
            QMessageBox.warning(
                self,
                "请选择独立窗口应用",
                "请先点击“读取应用”，再选择或输入一个手机应用名称。\n\n"
                "这样可以避免打开机型自带的虚拟桌面启动器。",
            )
            return

        if not self.is_explicit_multi_app(raw_app) and not self.multi_apps:
            self._pending_multi_open = True
            self.load_multi_apps()
            return

        start_app = self.resolve_multi_app(raw_app)
        if not start_app:
            QMessageBox.warning(
                self,
                "没有找到应用",
                f"没有在手机应用列表中找到“{raw_app}”。\n\n"
                "请先点击“读取应用”，然后从下拉列表选择，或检查中文名称。",
            )
            self.multi_app_status.setText(f"未找到：{raw_app}")
            return

        self.save_settings()
        self.multi_window_index += 1
        base_title = self.window_title_edit.text().strip() or "scrcpy"
        title = f"{base_title} · 独立窗口 {self.multi_window_index}"
        args = self.build_scrcpy_args(title_override=title, new_display=True, start_app=start_app)
        process = QProcess(self)
        process.setProgram(str(self.scrcpy_path))
        process.setArguments(args)
        process.setWorkingDirectory(str(self.scrcpy_path.parent))
        process.readyReadStandardOutput.connect(
            lambda p=process: self.log(bytes(p.readAllStandardOutput()).decode(errors="replace").strip())
        )
        process.readyReadStandardError.connect(
            lambda p=process: self.log(bytes(p.readAllStandardError()).decode(errors="replace").strip())
        )
        process.started.connect(lambda p=process, t=title: self.on_multi_scrcpy_started(p, t))
        process.finished.connect(
            lambda exit_code, _status, p=process, t=title: self.on_multi_scrcpy_finished(p, t, exit_code)
        )
        process.errorOccurred.connect(
            lambda _error, p=process, t=title: self.log(f"{t} 启动错误：{p.errorString()}")
        )
        self.extra_scrcpy_processes.append(process)
        self.update_multi_open_button()
        process.start()
        self.set_status(f"正在创建 {title}……")
        self.log("启动独立窗口参数：scrcpy " + " ".join(args))
        if raw_app and start_app.startswith("?"):
            self.log(f"正在按应用名称检索“{raw_app}”，scrcpy 可能需要几秒钟找到它。")

    @staticmethod
    def is_explicit_multi_app(value: str) -> bool:
        text = value.strip()
        if text.startswith(("?", "+?")):
            return True
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", text))

    def resolve_multi_app(self, value: str) -> str:
        """Resolve a selected label or typed Chinese name to a package name."""
        text = value.strip()
        if self.is_explicit_multi_app(text):
            return self.normalize_multi_app(text)

        exact = [(name, package) for name, package in self.multi_apps if name.casefold() == text.casefold()]
        if len(exact) == 1:
            return exact[0][1]
        prefix = [(name, package) for name, package in self.multi_apps if name.casefold().startswith(text.casefold())]
        if len(prefix) == 1:
            return prefix[0][1]
        if len(prefix) > 1:
            self.log("应用名称匹配到多个结果：" + "、".join(name for name, _package in prefix[:8]))
        return ""

    @staticmethod
    def normalize_multi_app(value: str) -> str:
        """Accept a visible app name while preserving explicit package syntax."""
        text = value.strip()
        if not text:
            return ""
        if text.startswith("+?"):
            return text
        if text.startswith("?"):
            return text
        if text.startswith("+"):
            package_or_name = text[1:]
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", package_or_name):
                return text
            return "+?" + package_or_name
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", text):
            return text
        return "?" + text

    def on_multi_scrcpy_started(self, _process: QProcess, title: str) -> None:
        self.set_status(f"{title} 已启动")
        self.log(f"{title} 使用独立 Android 虚拟显示，窗口之间互不共享画面。")

    def on_multi_scrcpy_finished(self, process: QProcess, title: str, exit_code: int) -> None:
        if process in self.extra_scrcpy_processes:
            self.extra_scrcpy_processes.remove(process)
        self.update_multi_open_button()
        self.log(f"{title} 已退出（{exit_code}）")
        process.deleteLater()
        if not self.scrcpy_process or self.scrcpy_process.state() == QProcess.ProcessState.NotRunning:
            self.set_status(f"{title} 已退出", exit_code == 0)

    def create_toolbar(self, title: str) -> None:
        if self.toolbar:
            return
        self.toolbar = FloatingToolbar(title, self.scrcpy_window_handle)
        self.toolbar.action_requested.connect(self.handle_toolbar_action)
        self.toolbar.show()
        self.toolbar.track_scrcpy_window()
        self.update_screen_toggle_button()
        self.toolbar_timer.start(250)

    def close_toolbar(self) -> None:
        self.toolbar_timer.stop()
        if self.toolbar:
            self.toolbar.close()
            self.toolbar.deleteLater()
            self.toolbar = None
        self.update_screen_toggle_button()

    def on_toolbar_setting_changed(self, *_args) -> None:
        if getattr(self, "_loading_settings", False):
            return
        if not self.show_toolbar.isChecked():
            self.close_toolbar()
            return
        running = bool(self.scrcpy_process and self.scrcpy_process.state() != QProcess.ProcessState.NotRunning)
        if running:
            title = self.window_title_edit.text().strip() or "scrcpy"
            self.create_toolbar(title)

    def on_scrcpy_started(self, title: str, args: list[str]) -> None:
        self.log("启动参数：scrcpy " + " ".join(args))
        self.set_status("scrcpy 正在运行")
        if self.show_toolbar.isChecked():
            self.create_toolbar(title)
        else:
            self.close_toolbar()

    def on_scrcpy_finished(self, exit_code: int, _status) -> None:
        self.screen_control.cancel()
        self.active_serial = ""
        self.close_toolbar()
        self.start_button.setText("启动 scrcpy")
        self.update_screen_toggle_button()
        self.set_status(f"scrcpy 已退出（{exit_code}）", exit_code == 0)

    def stop_scrcpy(self) -> None:
        if self.scrcpy_process and self.scrcpy_process.state() != QProcess.ProcessState.NotRunning:
            self.scrcpy_process.terminate()
            if not self.scrcpy_process.waitForFinished(1500):
                self.scrcpy_process.kill()
        self.start_button.setText("启动 scrcpy")

    def stop_extra_scrcpy(self) -> None:
        for process in list(self.extra_scrcpy_processes):
            if process.state() != QProcess.ProcessState.NotRunning:
                process.terminate()
                if not process.waitForFinished(1200):
                    process.kill()
                    process.waitForFinished(300)
            process.deleteLater()
        self.extra_scrcpy_processes.clear()

    def track_toolbar(self) -> None:
        if self.toolbar:
            self.toolbar.track_scrcpy_window()

    def send_keyevent(self, key: str) -> None:
        serial = self.active_serial or self.selected_serial()
        args = []
        if serial:
            args.extend(["-s", serial])
        args.extend(["shell", "input", "keyevent", key])
        self.adb_command(args)

    def update_screen_toggle_button(self) -> None:
        running = bool(self.scrcpy_process and self.scrcpy_process.state() != QProcess.ProcessState.NotRunning)
        enabled = running and not self._shortcut_busy
        if hasattr(self, "screen_toggle_button"):
            self.screen_toggle_button.setText("正在发送快捷键…" if self._shortcut_busy else "切换屏幕")
            self.screen_toggle_button.setToolTip(
                "正在发送 scrcpy 官方快捷键…" if self._shortcut_busy else "按当前真实状态发送 Alt+O 或 Shift+Alt+O"
            )
            self.screen_toggle_button.setEnabled(enabled)
        if self.toolbar:
            for button in self.toolbar.screen_buttons:
                button.setEnabled(enabled)

    def request_screen_power(self, turn_on: bool | None = None) -> None:
        self.screen_control.request(self.adb_path, self.active_serial or self.selected_serial(), turn_on)

    def on_screen_power_finished(self, success: bool, _actual, message: str) -> None:
        self.log(message)
        self.set_status(message, success)

    def toggle_screen_button(self) -> None:
        self.request_scrcpy_screen_shortcut()

    def scrcpy_window_handle(self) -> int:
        if not win32gui or not win32process or not self.scrcpy_process:
            return 0
        title = self.window_title_edit.text().strip() or "scrcpy"
        process_id = int(self.scrcpy_process.processId() or 0)
        title_matches: list[int] = []
        process_matches: list[int] = []

        def visit(hwnd: int, _extra) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            window_title = win32gui.GetWindowText(hwnd)
            try:
                owner_pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            except Exception:
                return True
            if window_title == title:
                title_matches.append(hwnd)
            if process_id and owner_pid == process_id:
                process_matches.append(hwnd)
            return True

        try:
            win32gui.EnumWindows(visit, None)
        except Exception:
            return 0
        if title_matches:
            for hwnd in title_matches:
                try:
                    owner_pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                except Exception:
                    owner_pid = 0
                if not process_id or owner_pid == process_id:
                    return hwnd
            return title_matches[0]
        if process_matches:
            return process_matches[0]
        try:
            return win32gui.FindWindow(None, title)
        except Exception:
            return 0

    def post_scrcpy_window_shortcut(self, hwnd: int, key: str, shift: bool = False) -> bool:
        """Deliver the shortcut through scrcpy's own window message queue.

        This is used when Windows refuses to change the foreground window for
        a process started from another desktop or terminal. SDL still receives
        these keyboard messages through its normal Windows event pump.
        """
        virtual_keys = {"o": 0x4F, "k": 0x4B}
        if key not in virtual_keys or sys.platform != "win32":
            return False
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.PostMessageW.restype = wintypes.BOOL
        map_virtual_key = user32.MapVirtualKeyW
        map_virtual_key.argtypes = (wintypes.UINT, wintypes.UINT)
        map_virtual_key.restype = wintypes.UINT

        vk_alt = 0xA4
        vk_shift = 0xA0
        keys = [vk_alt] if not shift else [vk_shift, vk_alt]
        keys.append(virtual_keys[key])
        messages: list[tuple[int, int, int]] = []
        for index, virtual_key in enumerate(keys):
            message = 0x0104 if virtual_key == vk_alt else 0x0100
            scan_code = map_virtual_key(virtual_key, 0)
            lparam = 1 | (int(scan_code) << 16)
            messages.append((message, virtual_key, lparam))
        for message, virtual_key, lparam in reversed(messages):
            up_message = 0x0105 if message == 0x0104 else 0x0101
            messages.append((up_message, virtual_key, lparam | (1 << 30) | (1 << 31)))

        posted = 0
        for message, virtual_key, lparam in messages:
            if user32.PostMessageW(hwnd, message, virtual_key, lparam):
                posted += 1
            time.sleep(0.015)
        return posted == len(messages)

    def send_scrcpy_window_shortcut(self, key: str, shift: bool = False) -> bool:
        hwnd = self.scrcpy_window_handle()
        if not hwnd or not win32gui or sys.platform != "win32":
            return False

        class KeybdInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        class InputUnion(ctypes.Union):
            _fields_ = [("ki", KeybdInput), ("padding", ctypes.c_ubyte * 32)]

        class Input(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", InputUnion)]

        virtual_keys = {"o": 0x4F, "k": 0x4B}
        if key not in virtual_keys:
            return False
        keys = [0xA4]
        if shift:
            keys.append(0xA0)
        keys.append(virtual_keys[key])
        keyboard_inputs: list[Input] = []
        for virtual_key in keys:
            keyboard_inputs.append(Input(1, InputUnion(ki=KeybdInput(virtual_key, 0, 0, 0, 0))))
        for virtual_key in reversed(keys):
            keyboard_inputs.append(Input(1, InputUnion(ki=KeybdInput(virtual_key, 0, 0x0002, 0, 0))))
        input_array = (Input * len(keyboard_inputs))(*keyboard_inputs)
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, 9)
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
            user32.SetForegroundWindow.restype = wintypes.BOOL
            user32.BringWindowToTop.argtypes = (wintypes.HWND,)
            user32.BringWindowToTop.restype = wintypes.BOOL
            target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
            foreground = user32.GetForegroundWindow()
            foreground_thread = (
                win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
            )
            attached = False
            if foreground_thread and target_thread and foreground_thread != target_thread:
                user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
                user32.AttachThreadInput.restype = wintypes.BOOL
                attached = bool(user32.AttachThreadInput(foreground_thread, target_thread, True))
            try:
                user32.BringWindowToTop(hwnd)
                foreground_set = bool(user32.SetForegroundWindow(hwnd))
            finally:
                if attached:
                    user32.AttachThreadInput(foreground_thread, target_thread, False)
            time.sleep(0.08)
            if user32.GetForegroundWindow() != hwnd:
                self.log(
                    f"scrcpy 窗口未成为前台窗口（SetForegroundWindow={foreground_set}），仍尝试发送快捷键。"
                )
                if self.post_scrcpy_window_shortcut(hwnd, key, shift):
                    self.log("已通过 scrcpy 窗口消息队列发送快捷键。")
                    return True
            user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
            user32.SendInput.restype = wintypes.UINT
            sent = user32.SendInput(len(input_array), input_array, ctypes.sizeof(Input))
            if sent == len(input_array):
                return True

            # Some Windows desktop configurations reject SendInput while the
            # target window is changing foreground. The legacy API uses the
            # same normal keyboard path and is a reliable fallback here.
            self.log(f"SendInput 仅发送了 {sent}/{len(input_array)} 个按键事件，改用兼容发送方式。")
            user32.keybd_event.argtypes = (wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p)
            user32.keybd_event.restype = None
            for virtual_key in keys:
                user32.keybd_event(virtual_key, 0, 0, None)
            for virtual_key in reversed(keys):
                user32.keybd_event(virtual_key, 0, 0x0002, None)
            return True
        except Exception as error:
            self.log(f"发送 scrcpy 快捷键失败：{error}")
            return False

    def read_screen_state(self, callback) -> None:
        serial = self.active_serial or self.selected_serial()
        if not self.adb_path or not serial:
            callback(None)
            return
        args = ["-s", serial, "shell", "dumpsys", "display"]
        self.run_one_shot(
            self.adb_path,
            args,
            lambda code, output: callback(physical_screen_on(output) if code == 0 else None),
        )

    def finish_scrcpy_shortcut(self, success: bool, message: str) -> None:
        self._shortcut_busy = False
        self.update_screen_toggle_button()
        self.log(message)
        self.set_status(message, success)

    def verify_scrcpy_screen_shortcut(self, target: bool, attempt: int = 0) -> None:
        QTimer.singleShot(
            350,
            lambda: self.read_screen_state(
                lambda actual: self.on_scrcpy_screen_state_checked(target, attempt, actual)
            ),
        )

    def on_scrcpy_screen_state_checked(self, target: bool, attempt: int, actual: bool | None) -> None:
        if actual is target:
            self.finish_scrcpy_shortcut(True, "已确认 scrcpy 快捷键生效：手机屏幕" + ("亮起。" if target else "熄灭。"))
        elif attempt < 1 and self.send_scrcpy_window_shortcut("o", shift=target):
            self.verify_scrcpy_screen_shortcut(target, attempt + 1)
        else:
            self.finish_scrcpy_shortcut(False, "已发送 scrcpy 快捷键，但未确认屏幕状态改变；请确认 scrcpy 窗口已启动并可接收按键。")

    def request_scrcpy_screen_shortcut(self, turn_on: bool | None = None) -> None:
        if self._shortcut_busy:
            return
        running = bool(self.scrcpy_process and self.scrcpy_process.state() != QProcess.ProcessState.NotRunning)
        if not running:
            self.set_status("请先启动 scrcpy，再使用屏幕快捷键。", False)
            return
        self._shortcut_busy = True
        self.update_screen_toggle_button()

        def known_state(current: bool | None) -> None:
            if current is None:
                self.finish_scrcpy_shortcut(False, "无法读取手机屏幕状态，未发送快捷键。")
                return
            target = not current if turn_on is None else turn_on
            if not self.send_scrcpy_window_shortcut("o", shift=target):
                self.finish_scrcpy_shortcut(False, "找不到可接收快捷键的 scrcpy 窗口。")
                return
            self.verify_scrcpy_screen_shortcut(target)

        self.read_screen_state(known_state)

    def handle_toolbar_action(self, action: str) -> None:
        key_map = {
            "back": "KEYCODE_BACK",
            "home": "KEYCODE_HOME",
            "recent": "KEYCODE_APP_SWITCH",
        }
        if action in {"screen_on", "screen_off"}:
            self.request_scrcpy_screen_shortcut(action == "screen_on")
        elif action in key_map:
            self.send_keyevent(key_map[action])
        elif action == "restart":
            self.stop_scrcpy()
            QTimer.singleShot(700, self.start_scrcpy)

    def connect_wifi(self) -> None:
        host = self.wifi_address.text().strip()
        if not host:
            QMessageBox.warning(self, "需要设备地址", "请输入手机 IP 地址。")
            return
        endpoint = f"{host}:{self.wifi_port.value()}"
        self.adb_command(["connect", endpoint], lambda _code, _output: self.refresh_devices())

    def pair_wifi(self) -> None:
        address = self.pair_address.text().strip()
        code = self.pair_code.text().strip()
        if not address or not code:
            QMessageBox.warning(self, "需要配对信息", "请输入 Android 11+ 无线调试的配对地址和配对码。")
            return
        self.adb_command(["pair", address, code], lambda _code, _output: self.refresh_devices())

    def switch_usb_to_wifi(self) -> None:
        serial = self.selected_serial()
        if not serial or ":" in serial:
            QMessageBox.warning(self, "需要 USB 设备", "请选择一台 USB 连接的设备。")
            return

        def got_route(_code: int, output: str) -> None:
            match = re.search(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)", output)
            if not match:
                match = re.search(r"\b(192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[0-1])\.\d+\.\d+)\b", output)
            if not match:
                QMessageBox.warning(self, "无法获取 IP", "没有从设备获取到 Wi‑Fi IP，请手动输入。")
                return
            host = match.group(1)

            def connected(_tcp_code: int, _tcp_output: str) -> None:
                self.adb_command(["connect", f"{host}:{self.wifi_port.value()}"], lambda _c, _o: self.refresh_devices())

            self.adb_command(["-s", serial, "tcpip", str(self.wifi_port.value())], connected)

        self.adb_command(["-s", serial, "shell", "ip", "route"], got_route)

    def stop_one_shot_processes(self) -> None:
        for process in list(self.one_shot_processes):
            process.blockSignals(True)
            if process.state() != QProcess.ProcessState.NotRunning:
                process.terminate()
                if not process.waitForFinished(300):
                    process.kill()
                    process.waitForFinished(300)
            process.deleteLater()
        self.one_shot_processes.clear()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.screen_control.cancel()
        self.save_settings()
        self.stop_extra_scrcpy()
        self.stop_scrcpy()
        self.stop_one_shot_processes()
        event.accept()


def main() -> None:
    app = QApplication([])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("OpenAI")
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()
    if "--check-startup" in sys.argv:
        # Packaging regression check: an idle process can be an error dialog.
        # Verify the real main window and changed controls, then exit cleanly.
        def check_startup() -> None:
            ready = window.isVisible() and hasattr(window, "screen_control") and not hasattr(window, "secure_mode")
            window.close()
            app.exit(0 if ready else 1)
        QTimer.singleShot(1500, check_startup)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
