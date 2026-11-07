"""
高级深色风格设置弹窗 (SettingsDialog)。

包含：
1. 开机自启：现代化圆角滑动开关 (ToggleSwitch)，开启/关闭时立即同步 Windows 注册表并显示状态反馈。
2. 唤起方式与快捷键：
   - 交互式多模态录制框 (HotkeyEditWidget)：支持录制组合键（如 Alt+V）、双击按键（如 双击 Alt / 双击 Ctrl）、
     长按或双击鼠标按键（如 长按鼠标中键 / 长按鼠标侧键1 / 长按鼠标侧键2 / 双击鼠标中键）。
   - 一键快捷预设胶囊栏：方便直接点选常用高级触发手势。
   - 触发响应时间（毫秒）：支持通过滑块与数字框精确设置 `50 ~ 5000 ms`（默认 `300 ms`）。
3. 合并复制行为：
   - 多选合并分隔符下拉框（换行符 / 空行 / 空格 / 直接拼接）
   - 合并复制后自动隐藏悬浮窗（勾选框）
4. 底部「保存」与「取消」按钮。
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QPoint, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import autostart, win_input
from core.config_manager import DEFAULT_HOTKEY, DEFAULT_TRIGGER_TIMEOUT_MS, ConfigManager
from core.hotkey_manager import (
    PRESET_TRIGGERS,
    HotkeyManager,
    format_qt_key_event,
    format_qt_single_key_name,
)
from .icons import make_tool_icon
from .theme import (
    FONT_CSS,
    SHADOW_MARGIN,
    WINDOW_RADIUS,
    Palette,
    css_rgba,
    paint_soft_shadow,
    rgba,
    ui_font,
)

SEPARATOR_OPTIONS: list[tuple[str, str]] = [
    ("换行符 (\\n)", "\n"),
    ("空行 (\\n\\n)", "\n\n"),
    ("空格 ( )", " "),
    ("直接拼接", ""),
]

_MOUSE_BUTTON_NAMES: dict[Qt.MouseButton, str] = {
    Qt.MouseButton.MiddleButton: "鼠标中键",
    Qt.MouseButton.BackButton: "鼠标侧键1",
    Qt.MouseButton.ForwardButton: "鼠标侧键2",
    Qt.MouseButton.RightButton: "鼠标右键",
}


class ToggleSwitch(QWidget):
    """现代化圆角滑动开关组件 (Toggle Switch)。"""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked = bool(checked)
        self._hovered = False
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def isChecked(self) -> bool:  # noqa: N802
        return self._checked

    def setChecked(self, checked: bool, emit_signal: bool = False) -> None:  # noqa: N802
        new_val = bool(checked)
        if self._checked != new_val:
            self._checked = new_val
            self.update()
            if emit_signal:
                self.toggled.emit(self._checked)

    def enterEvent(self, event) -> None:  # noqa: ANN001
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked, emit_signal=True)
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)

        if self._checked:
            track_color = QColor(Palette.ACCENT_HI if self._hovered else Palette.ACCENT)
            thumb_color = QColor(Palette.ON_ACCENT)
            thumb_x = self.width() - 20.0
        else:
            track_color = QColor("#3a3d47" if self._hovered else "#2d3039")
            thumb_color = QColor(Palette.TEXT)
            thumb_x = 4.0

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(rect, 11.0, 11.0)

        painter.setBrush(thumb_color)
        painter.drawEllipse(QRectF(thumb_x, 4.0, 16.0, 16.0))
        painter.end()


class HotkeyEditWidget(QFrame):
    """
    多模态快捷键与手势录制框：
    - 点击后进入录制状态并显示 "请按下快捷键..."；
    - 支持直接录制：
      1. 键盘组合键（如 Alt+V、Ctrl+Shift+X）
      2. 双击单键/修饰键（如快速按两下 Alt -> "双击 Alt"，按两下 Ctrl -> "双击 Ctrl"）
      3. 长按单键/修饰键（按住 Alt 达到响应毫秒数 -> "长按 Alt"）
      4. 长按或双击鼠标中键/侧键（在录制框内按住鼠标中键/侧键1/侧键2 -> "长按鼠标中键" / "长按鼠标侧键1" / "双击鼠标中键"）
    """

    hotkey_changed = Signal(str)
    recording_state_changed = Signal(bool)

    def __init__(
        self,
        hotkey: str = DEFAULT_HOTKEY,
        trigger_timeout_ms: int = DEFAULT_TRIGGER_TIMEOUT_MS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._hotkey = hotkey or DEFAULT_HOTKEY
        self._trigger_timeout_ms = max(50, min(5000, int(trigger_timeout_ms)))
        self._recording = False

        # 单键双击 / 长按录制辅助状态
        self._last_single_key: str | None = None
        self._last_single_key_mono: float = 0.0
        self._pending_hold_target: str | None = None
        self._last_mouse_btn_name: str | None = None
        self._last_mouse_click_mono: float = 0.0

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._on_hold_timeout)

        self._mouse_single_timer = QTimer(self)
        self._mouse_single_timer.setSingleShot(True)
        self._mouse_single_timer.timeout.connect(self._on_mouse_single_Fallback)

        self.setObjectName("HotkeyEditWidget")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        self._label = QLabel(self._hotkey, self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._label)

        self._refresh_style()

    @property
    def hotkey(self) -> str:
        return self._hotkey

    @property
    def is_recording(self) -> bool:
        return self._recording

    def set_trigger_timeout_ms(self, timeout_ms: int) -> None:
        self._trigger_timeout_ms = max(50, min(5000, int(timeout_ms)))

    def set_hotkey(self, hotkey: str) -> None:
        self._hold_timer.stop()
        self._mouse_single_timer.stop()
        self._pending_hold_target = None
        val = (hotkey or DEFAULT_HOTKEY).strip()
        self._hotkey = val
        self._recording = False
        self.recording_state_changed.emit(False)
        self._refresh_style()
        self.hotkey_changed.emit(self._hotkey)

    def start_recording(self) -> None:
        self._hold_timer.stop()
        self._mouse_single_timer.stop()
        self._last_single_key = None
        self._last_single_key_mono = 0.0
        self._pending_hold_target = None
        self._last_mouse_btn_name = None
        self._last_mouse_click_mono = 0.0
        self._recording = True
        self.recording_state_changed.emit(True)
        self._refresh_style()
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def stop_recording(self) -> None:
        self._hold_timer.stop()
        self._mouse_single_timer.stop()
        self._pending_hold_target = None
        if self._recording:
            self._recording = False
            self.recording_state_changed.emit(False)
            self._refresh_style()

    def _on_hold_timeout(self) -> None:
        if not self._recording or not self._pending_hold_target:
            return
        target = self._pending_hold_target
        self._pending_hold_target = None
        if target.startswith("鼠标"):
            self.set_hotkey(f"长按{target}")
        else:
            self.set_hotkey(f"长按 {target}")

    def _on_mouse_single_Fallback(self) -> None:
        if not self._recording or not self._last_mouse_btn_name:
            return
        btn_name = self._last_mouse_btn_name
        self._last_mouse_btn_name = None
        self.set_hotkey(f"长按{btn_name}")

    def _refresh_style(self) -> None:
        if self._recording:
            self._label.setText("请按下快捷键...")
            self._label.setStyleSheet(
                f"color: {Palette.ACCENT}; font-family: {FONT_CSS}; font-size: 13px; font-weight: 600; background: transparent; border: none;"
            )
            self.setStyleSheet(
                f"""
                QFrame#HotkeyEditWidget {{
                    background-color: #161c28;
                    border: 1.5px solid {Palette.ACCENT};
                    border-radius: 8px;
                }}
                """
            )
        else:
            self._label.setText(self._hotkey)
            self._label.setStyleSheet(
                f"color: {Palette.TEXT}; font-family: {FONT_CSS}; font-size: 13px; font-weight: 600; background: transparent; border: none;"
            )
            self.setStyleSheet(
                f"""
                QFrame#HotkeyEditWidget {{
                    background-color: {Palette.WINDOW};
                    border: 1px solid {Palette.BORDER};
                    border-radius: 8px;
                }}
                QFrame#HotkeyEditWidget:hover {{
                    border: 1px solid {Palette.BORDER_STRONG};
                    background-color: #181a20;
                }}
                """
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        btn = event.button()
        if not self._recording:
            if btn == Qt.MouseButton.LeftButton:
                self.start_recording()
                event.accept()
                return
            super().mousePressEvent(event)
            return

        # 录制状态下：支持鼠标中键、侧键1、侧键2、右键的长按或双击录制
        if btn in _MOUSE_BUTTON_NAMES:
            btn_name = _MOUSE_BUTTON_NAMES[btn]
            now = time.monotonic()
            window_sec = max(self._trigger_timeout_ms / 1000.0, 0.42)
            if self._last_mouse_btn_name == btn_name and (now - self._last_mouse_click_mono) <= window_sec:
                self._mouse_single_timer.stop()
                self._hold_timer.stop()
                self._last_mouse_btn_name = None
                self.set_hotkey(f"双击{btn_name}")
                event.accept()
                return

            self._last_mouse_btn_name = btn_name
            self._last_mouse_click_mono = now
            self._pending_hold_target = btn_name
            self._hold_timer.start(self._trigger_timeout_ms)
            event.accept()
            return

        if btn == Qt.MouseButton.LeftButton:
            self.start_recording()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._recording and event.button() in _MOUSE_BUTTON_NAMES:
            if self._hold_timer.isActive():
                self._hold_timer.stop()
                self._pending_hold_target = None
                self._mouse_single_timer.start(max(240, min(420, self._trigger_timeout_ms)))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self.stop_recording()
        super().focusOutEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._recording:
            super().keyPressEvent(event)
            return

        if event.isAutoRepeat():
            event.accept()
            return

        if event.key() == Qt.Key.Key_Escape and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.stop_recording()
            event.accept()
            return

        # 1. 标准组合键（如 Ctrl+Shift+K、Alt+V、F11）直接完成录制
        formatted = format_qt_key_event(event)
        if formatted:
            self.set_hotkey(formatted)
            event.accept()
            return

        # 2. 单个修饰键或单键（如 Alt / Ctrl / Shift / Win / Space）：检测双击或长按
        single_name = format_qt_single_key_name(int(event.key()))
        if single_name:
            now = time.monotonic()
            window_sec = max(self._trigger_timeout_ms / 1000.0, 0.45)
            if self._last_single_key == single_name and (now - self._last_single_key_mono) <= window_sec:
                self._hold_timer.stop()
                self._pending_hold_target = None
                self._last_single_key = None
                self.set_hotkey(f"双击 {single_name}")
                event.accept()
                return

            self._last_single_key = single_name
            self._last_single_key_mono = now
            self._pending_hold_target = single_name
            self._hold_timer.start(max(350, self._trigger_timeout_ms))
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if self._recording and not event.isAutoRepeat():
            if self._hold_timer.isActive():
                self._hold_timer.stop()
            self._pending_hold_target = None
            event.accept()
            return
        super().keyReleaseEvent(event)


class SettingsDialog(QDialog):
    """高级深色圆角设置弹窗。"""

    settings_saved = Signal(dict)

    def __init__(
        self,
        config_manager: ConfigManager,
        hotkey_manager: HotkeyManager | None = None,
        autostart_setter: Callable[[bool], bool] = autostart.set_autostart,
        autostart_checker: Callable[[], bool] = autostart.is_autostart_enabled,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config_manager
        self.hotkey_manager = hotkey_manager
        self._set_autostart = autostart_setter
        self._check_autostart = autostart_checker
        self._drag_pos: QPoint | None = None
        self._preset_buttons: dict[str, QPushButton] = {}

        self.setWindowTitle("设置")
        self.setModal(True)
        self.setFixedSize(460, 496)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._setup_ui()
        self._load_current_values()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)

        self._container = QFrame(self)
        self._container.setObjectName("SettingsContainer")
        self._container.setStyleSheet(
            f"""
            QFrame#SettingsContainer {{
                background-color: {Palette.ELEVATED};
                border: 1px solid {Palette.BORDER};
                border-radius: {int(WINDOW_RADIUS)}px;
                color: {Palette.TEXT};
                font-family: {FONT_CSS};
            }}
            QLabel {{
                color: {Palette.TEXT};
                font-family: {FONT_CSS};
                font-size: 13px;
                background: transparent;
            }}
            QComboBox {{
                background-color: {Palette.WINDOW};
                color: {Palette.TEXT};
                border: 1px solid {Palette.BORDER};
                border-radius: 7px;
                padding: 4px 10px;
                font-family: {FONT_CSS};
                font-size: 12px;
                min-height: 24px;
            }}
            QSpinBox {{
                background-color: {Palette.WINDOW};
                color: {Palette.TEXT};
                border: 1px solid {Palette.BORDER};
                border-radius: 7px;
                padding: 3px 8px;
                font-family: {FONT_CSS};
                font-size: 12px;
                font-weight: 600;
                min-height: 24px;
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                width: 0px;
                height: 0px;
                border: none;
            }}
            QComboBox:hover, QSpinBox:hover {{
                border: 1px solid {Palette.BORDER_STRONG};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 22px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {Palette.ELEVATED};
                color: {Palette.TEXT};
                border: 1px solid {Palette.BORDER_STRONG};
                selection-background-color: {css_rgba(Palette.ACCENT, 0.20)};
                selection-color: {Palette.ACCENT};
            }}
            QCheckBox {{
                color: {Palette.TEXT};
                font-size: 13px;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid {Palette.BORDER_STRONG};
                background-color: {Palette.WINDOW};
            }}
            QCheckBox::indicator:checked {{
                background-color: {Palette.ACCENT};
                border-color: {Palette.ACCENT};
            }}
            QSlider::groove:horizontal {{
                height: 5px;
                background: #2b2e37;
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {Palette.ACCENT};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {Palette.TEXT};
                border: 2px solid {Palette.ACCENT};
                width: 12px;
                height: 12px;
                margin: -5px 0;
                border-radius: 7px;
            }}
            """
        )
        outer.addWidget(self._container)

        layout = QVBoxLayout(self._container)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(12)

        # 标题栏
        header = QHBoxLayout()
        header.setSpacing(8)
        gear_lbl = QLabel(self._container)
        gear_lbl.setFixedSize(18, 18)
        gear_lbl.setPixmap(make_tool_icon("settings", Palette.ACCENT, 18).pixmap(18, 18))
        title_lbl = QLabel("偏好设置", self._container)
        title_lbl.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {Palette.TEXT};")

        close_btn = QPushButton("×", self._container)
        close_btn.setFixedSize(26, 26)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"""
            QPushButton {{
                color: {Palette.TEXT_2};
                background: transparent;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                color: {Palette.TEXT};
                background-color: {css_rgba('#ffffff', 0.08)};
            }}
            """
        )
        close_btn.clicked.connect(self.reject)
        header.addWidget(gear_lbl)
        header.addWidget(title_lbl)
        header.addStretch(1)
        header.addWidget(close_btn)
        layout.addLayout(header)

        divider = QFrame(self._container)
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {Palette.HAIRLINE};")
        layout.addWidget(divider)

        # 1. 开机自启
        autostart_box = QVBoxLayout()
        autostart_box.setSpacing(3)
        autostart_row = QHBoxLayout()
        autostart_lbl = QLabel("开机自动启动", self._container)
        self.autostart_switch = ToggleSwitch(False, self._container)
        self.autostart_switch.toggled.connect(self._on_autostart_toggled)
        autostart_row.addWidget(autostart_lbl)
        autostart_row.addStretch(1)
        autostart_row.addWidget(self.autostart_switch)

        self.autostart_status_label = QLabel("", self._container)
        self.autostart_status_label.setStyleSheet(f"color: {Palette.TEXT_3}; font-size: 11px;")
        autostart_box.addLayout(autostart_row)
        autostart_box.addWidget(self.autostart_status_label)
        layout.addLayout(autostart_box)

        # 2. 唤起快捷键与鼠标/键盘手势
        hotkey_box = QVBoxLayout()
        hotkey_box.setSpacing(6)
        hotkey_title = QLabel("全局唤起方式（点击输入框可录制组合键、双击Alt、长按鼠标中键/侧键）", self._container)
        hotkey_title.setStyleSheet(f"color: {Palette.TEXT_2}; font-size: 12px;")

        hotkey_row = QHBoxLayout()
        hotkey_row.setSpacing(8)
        self.hotkey_edit = HotkeyEditWidget(
            DEFAULT_HOTKEY,
            trigger_timeout_ms=self.config.trigger_timeout_ms,
            parent=self._container,
        )
        self.hotkey_edit.hotkey_changed.connect(self._sync_preset_highlight)
        if self.hotkey_manager is not None:
            self.hotkey_edit.recording_state_changed.connect(self.hotkey_manager.set_paused)

        self.btn_reset_hotkey = QPushButton("恢复默认", self._container)
        self.btn_reset_hotkey.setFixedSize(78, 36)
        self.btn_reset_hotkey.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_hotkey.setStyleSheet(
            f"""
            QPushButton {{
                background-color: #292c35;
                color: {Palette.TEXT};
                border: 1px solid {Palette.BORDER};
                border-radius: 8px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: #323641;
                border-color: {Palette.BORDER_STRONG};
            }}
            """
        )
        self.btn_reset_hotkey.clicked.connect(lambda: self.hotkey_edit.set_hotkey(DEFAULT_HOTKEY))

        hotkey_row.addWidget(self.hotkey_edit, 1)
        hotkey_row.addWidget(self.btn_reset_hotkey)
        hotkey_box.addWidget(hotkey_title)
        hotkey_box.addLayout(hotkey_row)

        # 快捷预设气泡行（支持一键选择 双击Alt / 长按鼠标中键 / 长按鼠标侧键1 / 长按鼠标侧键2 等）
        preset_wrap = QVBoxLayout()
        preset_wrap.setSpacing(5)
        preset_hint = QLabel("常用触发预设（点击即可选用）：", self._container)
        preset_hint.setStyleSheet(f"color: {Palette.TEXT_3}; font-size: 11px;")
        preset_wrap.addWidget(preset_hint)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        for idx, (label, spec_val) in enumerate(PRESET_TRIGGERS):
            chip = QPushButton(label, self._container)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFixedHeight(24)
            chip.clicked.connect(lambda _=False, s=spec_val: self.hotkey_edit.set_hotkey(s))
            self._preset_buttons[spec_val] = chip
            if idx < 4:
                row1.addWidget(chip)
            else:
                row2.addWidget(chip)
        row1.addStretch(1)
        row2.addStretch(1)
        preset_wrap.addLayout(row1)
        preset_wrap.addLayout(row2)
        hotkey_box.addLayout(preset_wrap)
        layout.addLayout(hotkey_box)

        # 3. 响应毫秒数设置 (trigger_timeout_ms)
        ms_box = QVBoxLayout()
        ms_box.setSpacing(4)
        ms_header = QHBoxLayout()
        ms_lbl = QLabel("触发响应时间（双击判定间隔 / 长按所需毫秒）", self._container)
        ms_lbl.setStyleSheet(f"color: {Palette.TEXT}; font-size: 12px;")
        ms_header.addWidget(ms_lbl)
        ms_header.addStretch(1)

        ms_ctrl_row = QHBoxLayout()
        ms_ctrl_row.setSpacing(10)
        self.trigger_ms_slider = QSlider(Qt.Orientation.Horizontal, self._container)
        self.trigger_ms_slider.setRange(80, 1500)
        self.trigger_ms_slider.setSingleStep(10)
        self.trigger_ms_slider.setPageStep(50)

        self.trigger_ms_spin = QSpinBox(self._container)
        self.trigger_ms_spin.setRange(50, 5000)
        self.trigger_ms_spin.setSingleStep(20)
        self.trigger_ms_spin.setSuffix(" ms")
        self.trigger_ms_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.trigger_ms_spin.setFixedSize(88, 30)

        self.trigger_ms_slider.valueChanged.connect(self._on_ms_slider_changed)
        self.trigger_ms_spin.valueChanged.connect(self._on_ms_spin_changed)

        ms_ctrl_row.addWidget(self.trigger_ms_slider, 1)
        ms_ctrl_row.addWidget(self.trigger_ms_spin)
        ms_box.addLayout(ms_header)
        ms_box.addLayout(ms_ctrl_row)
        layout.addLayout(ms_box)

        # 4. 合并复制行为
        sep_row = QHBoxLayout()
        sep_lbl = QLabel("多选合并分隔符", self._container)
        self.sep_combo = QComboBox(self._container)
        for text, val in SEPARATOR_OPTIONS:
            self.sep_combo.addItem(text, val)
        sep_row.addWidget(sep_lbl)
        sep_row.addStretch(1)
        sep_row.addWidget(self.sep_combo)
        layout.addLayout(sep_row)

        self.hide_after_copy_cb = QCheckBox("合并复制后自动隐藏悬浮窗", self._container)
        layout.addWidget(self.hide_after_copy_cb)

        self.error_label = QLabel("", self._container)
        self.error_label.setStyleSheet(f"color: {Palette.DANGER}; font-size: 12px;")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        layout.addStretch(1)

        # 5. 底部「保存」与「取消」按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch(1)

        self.btn_cancel = QPushButton("取消", self._container)
        self.btn_cancel.setFixedSize(88, 34)
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setStyleSheet(
            f"""
            QPushButton {{
                background-color: #292c35;
                color: {Palette.TEXT};
                border: 1px solid {Palette.BORDER};
                border-radius: 8px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: #323641;
            }}
            """
        )
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_save = QPushButton("保存", self._container)
        self.btn_save.setFixedSize(92, 34)
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {Palette.ACCENT};
                color: {Palette.ON_ACCENT};
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {Palette.ACCENT_HI};
            }}
            QPushButton:pressed {{
                background-color: {Palette.ACCENT_LO};
            }}
            """
        )
        self.btn_save.clicked.connect(self._on_save_clicked)

        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_save)
        layout.addLayout(btn_row)

    def _sync_preset_highlight(self, current_hotkey: str) -> None:
        norm = current_hotkey.strip()
        for spec_val, btn in self._preset_buttons.items():
            active = norm.lower() == spec_val.lower()
            if active:
                btn.setStyleSheet(
                    f"""
                    QPushButton {{
                        background-color: {css_rgba(Palette.ACCENT, 0.20)};
                        color: {Palette.ACCENT};
                        border: 1px solid {css_rgba(Palette.ACCENT, 0.60)};
                        border-radius: 12px;
                        padding: 0px 10px;
                        font-size: 11px;
                        font-weight: 600;
                    }}
                    """
                )
            else:
                btn.setStyleSheet(
                    f"""
                    QPushButton {{
                        background-color: #252830;
                        color: {Palette.TEXT_2};
                        border: 1px solid {css_rgba('#ffffff', 0.07)};
                        border-radius: 12px;
                        padding: 0px 10px;
                        font-size: 11px;
                    }}
                    QPushButton:hover {{
                        color: {Palette.TEXT};
                        background-color: #2e323c;
                    }}
                    """
                )

    def _on_ms_slider_changed(self, value: int) -> None:
        if self.trigger_ms_spin.value() != value:
            self.trigger_ms_spin.blockSignals(True)
            self.trigger_ms_spin.setValue(value)
            self.trigger_ms_spin.blockSignals(False)
        self.hotkey_edit.set_trigger_timeout_ms(value)

    def _on_ms_spin_changed(self, value: int) -> None:
        clamped = max(self.trigger_ms_slider.minimum(), min(self.trigger_ms_slider.maximum(), value))
        if self.trigger_ms_slider.value() != clamped:
            self.trigger_ms_slider.blockSignals(True)
            self.trigger_ms_slider.setValue(clamped)
            self.trigger_ms_slider.blockSignals(False)
        self.hotkey_edit.set_trigger_timeout_ms(value)

    def _load_current_values(self) -> None:
        real_autostart = self._check_autostart()
        self.autostart_switch.setChecked(real_autostart, emit_signal=False)
        self.autostart_status_label.setText(
            "✓ 已开启开机自启（注册表已生效）" if real_autostart else "当前未开启开机自启"
        )

        ms = self.config.trigger_timeout_ms
        self.trigger_ms_spin.setValue(ms)
        self._on_ms_spin_changed(ms)

        self.hotkey_edit.set_hotkey(self.config.hotkey)
        self._sync_preset_highlight(self.config.hotkey)

        current_sep = self.config.merge_separator
        idx = next((i for i, (_, v) in enumerate(SEPARATOR_OPTIONS) if v == current_sep), 0)
        self.sep_combo.setCurrentIndex(idx)

        self.hide_after_copy_cb.setChecked(self.config.hide_after_copy)

    def _on_autostart_toggled(self, checked: bool) -> None:
        ok = self._set_autostart(checked)
        if ok:
            self.config.set("auto_start", checked)
            if checked:
                self.autostart_status_label.setStyleSheet(f"color: {Palette.ACCENT}; font-size: 11px;")
                self.autostart_status_label.setText("✓ 已写入注册表，开机将自动静默启动")
            else:
                self.autostart_status_label.setStyleSheet(f"color: {Palette.TEXT_3}; font-size: 11px;")
                self.autostart_status_label.setText("已移除开机自启注册表项")
        else:
            self.autostart_switch.setChecked(not checked, emit_signal=False)
            self.autostart_status_label.setStyleSheet(f"color: {Palette.DANGER}; font-size: 11px;")
            self.autostart_status_label.setText("写入注册表失败，请检查权限")

    def _on_save_clicked(self) -> None:
        self.hotkey_edit.stop_recording()
        new_hotkey = self.hotkey_edit.hotkey
        trigger_ms = int(self.trigger_ms_spin.value())

        if self.hotkey_manager is not None:
            self.hotkey_manager.set_paused(False)
            ok, msg = self.hotkey_manager.register_hotkey(new_hotkey, trigger_timeout_ms=trigger_ms)
            if not ok:
                self.error_label.setText(msg)
                self.error_label.show()
                box = QMessageBox(
                    QMessageBox.Icon.Warning,
                    "快捷键或触发方式不可用",
                    msg,
                    QMessageBox.StandardButton.Ok,
                    self,
                )
                box.setWindowFlags(box.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
                box.exec()
                return
            new_hotkey = self.hotkey_manager.current_hotkey

        sep_val = str(self.sep_combo.currentData())
        hide_after = self.hide_after_copy_cb.isChecked()
        auto_start_val = self.autostart_switch.isChecked()

        self.config.update(
            {
                "hotkey": new_hotkey,
                "trigger_timeout_ms": trigger_ms,
                "auto_start": auto_start_val,
                "merge_separator": sep_val,
                "hide_after_copy": hide_after,
            }
        )
        self.settings_saved.emit(self.config.as_dict())
        self.accept()

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None and parent.isVisible():
            pg = parent.frameGeometry()
            x = pg.center().x() - self.width() // 2
            y = pg.center().y() - self.height() // 2
            self.move(x, y)
        else:
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                ag = screen.availableGeometry()
                self.move(ag.center().x() - self.width() // 2, ag.center().y() - self.height() // 2)

        self.raise_()
        self.activateWindow()
        try:
            win_input.activate_window(int(self.winId()))
        except Exception:  # noqa: BLE001
            pass

    def done(self, result: int) -> None:
        if self.hotkey_manager is not None:
            self.hotkey_manager.set_paused(False)
        super().done(result)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= SHADOW_MARGIN + 52:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_soft_shadow(painter, self._container.geometry(), radius=WINDOW_RADIUS, margin=SHADOW_MARGIN)
        painter.end()
