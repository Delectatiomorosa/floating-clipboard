r"""
全局唤起快捷键与手势触发管理器 (HotkeyManager)。

支持两大类全局唤起方式：
1. 标准组合快捷键（如 `Alt+V`, `Ctrl+Shift+X`, `Ctrl+\``, `F1`~`F24`）：
   基于 Windows 原生 `user32.RegisterHotKey` / `UnregisterHotKey` + `QAbstractNativeEventFilter` 拦截 `WM_HOTKEY`。
2. 扩展按键与鼠标手势触发（支持自定义响应毫秒数 `trigger_timeout_ms`）：
   - 双击修饰键 / 单键：`双击 Alt`、`双击 Ctrl`、`双击 Shift`、`双击 Win`、`双击 Space` 等
   - 长按鼠标按键：`长按鼠标中键`、`长按鼠标侧键1`、`长按鼠标侧键2`、`长按鼠标右键`
   - 双击 / 单击鼠标特殊键：`双击鼠标中键`、`双击鼠标侧键1`、`双击鼠标侧键2`、`单击鼠标中键`、`单击鼠标侧键1`、`单击鼠标侧键2`
   - 长按键盘按键：`长按 Alt`、`长按 Ctrl`、`长按 Shift` 等
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import logging
import re
import sys
import time
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QCursor, QKeyEvent

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

HOTKEY_ID_DEFAULT = 0xA901
DEFAULT_TRIGGER_TIMEOUT_MS = 300

# Win32 Virtual-Key Codes
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_CAPITAL = 0x14
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5

_SPECIAL_VK_MAP: dict[str, int] = {
    "`": 0xC0,
    "~": 0xC0,
    "quoteleft": 0xC0,
    "backquote": 0xC0,
    "space": 0x20,
    "空格": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "ins": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pgup": 0x21,
    "pagedown": 0x22,
    "pgdn": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "-": 0xBD,
    "minus": 0xBD,
    "=": 0xBB,
    "plus": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    "\\": 0xDC,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
    "capslock": VK_CAPITAL,
    "caps": VK_CAPITAL,
}

_MODIFIER_TARGET_MAP: dict[str, tuple[tuple[int, ...], str]] = {
    "alt": ((VK_MENU, VK_LMENU, VK_RMENU), "Alt"),
    "lalt": ((VK_LMENU,), "Alt"),
    "ralt": ((VK_RMENU,), "Alt"),
    "menu": ((VK_MENU, VK_LMENU, VK_RMENU), "Alt"),
    "ctrl": ((VK_CONTROL, VK_LCONTROL, VK_RCONTROL), "Ctrl"),
    "control": ((VK_CONTROL, VK_LCONTROL, VK_RCONTROL), "Ctrl"),
    "lctrl": ((VK_LCONTROL,), "Ctrl"),
    "rctrl": ((VK_RCONTROL,), "Ctrl"),
    "shift": ((VK_SHIFT, VK_LSHIFT, VK_RSHIFT), "Shift"),
    "lshift": ((VK_LSHIFT,), "Shift"),
    "rshift": ((VK_RSHIFT,), "Shift"),
    "win": ((VK_LWIN, VK_RWIN), "Win"),
    "meta": ((VK_LWIN, VK_RWIN), "Win"),
    "windows": ((VK_LWIN, VK_RWIN), "Win"),
}

_MOUSE_TARGET_MAP: dict[str, tuple[tuple[int, ...], str]] = {
    "mousemiddle": ((VK_MBUTTON,), "鼠标中键"),
    "mbutton": ((VK_MBUTTON,), "鼠标中键"),
    "middle": ((VK_MBUTTON,), "鼠标中键"),
    "中键": ((VK_MBUTTON,), "鼠标中键"),
    "鼠标中键": ((VK_MBUTTON,), "鼠标中键"),
    "mousex1": ((VK_XBUTTON1,), "鼠标侧键1"),
    "xbutton1": ((VK_XBUTTON1,), "鼠标侧键1"),
    "mouseback": ((VK_XBUTTON1,), "鼠标侧键1"),
    "侧键1": ((VK_XBUTTON1,), "鼠标侧键1"),
    "侧键": ((VK_XBUTTON1,), "鼠标侧键1"),
    "鼠标侧键": ((VK_XBUTTON1,), "鼠标侧键1"),
    "鼠标侧键1": ((VK_XBUTTON1,), "鼠标侧键1"),
    "鼠标后退键": ((VK_XBUTTON1,), "鼠标侧键1"),
    "mousex2": ((VK_XBUTTON2,), "鼠标侧键2"),
    "xbutton2": ((VK_XBUTTON2,), "鼠标侧键2"),
    "mouseforward": ((VK_XBUTTON2,), "鼠标侧键2"),
    "侧键2": ((VK_XBUTTON2,), "鼠标侧键2"),
    "鼠标侧键2": ((VK_XBUTTON2,), "鼠标侧键2"),
    "鼠标前进键": ((VK_XBUTTON2,), "鼠标侧键2"),
    "mouseright": ((VK_RBUTTON,), "鼠标右键"),
    "rbutton": ((VK_RBUTTON,), "鼠标右键"),
    "右键": ((VK_RBUTTON,), "鼠标右键"),
    "鼠标右键": ((VK_RBUTTON,), "鼠标右键"),
    "mouseleft": ((VK_LBUTTON,), "鼠标左键"),
    "lbutton": ((VK_LBUTTON,), "鼠标左键"),
    "左键": ((VK_LBUTTON,), "鼠标左键"),
    "鼠标左键": ((VK_LBUTTON,), "鼠标左键"),
}

_QT_KEY_NAME_MAP: dict[int, str] = {
    int(Qt.Key.Key_QuoteLeft): "`",
    int(Qt.Key.Key_AsciiTilde): "`",
    int(Qt.Key.Key_Space): "Space",
    int(Qt.Key.Key_Tab): "Tab",
    int(Qt.Key.Key_Return): "Enter",
    int(Qt.Key.Key_Enter): "Enter",
    int(Qt.Key.Key_Backspace): "Backspace",
    int(Qt.Key.Key_Delete): "Delete",
    int(Qt.Key.Key_Insert): "Insert",
    int(Qt.Key.Key_Home): "Home",
    int(Qt.Key.Key_End): "End",
    int(Qt.Key.Key_PageUp): "PageUp",
    int(Qt.Key.Key_PageDown): "PageDown",
    int(Qt.Key.Key_Up): "Up",
    int(Qt.Key.Key_Down): "Down",
    int(Qt.Key.Key_Left): "Left",
    int(Qt.Key.Key_Right): "Right",
    int(Qt.Key.Key_Minus): "-",
    int(Qt.Key.Key_Equal): "=",
    int(Qt.Key.Key_BracketLeft): "[",
    int(Qt.Key.Key_BracketRight): "]",
    int(Qt.Key.Key_Backslash): "\\",
    int(Qt.Key.Key_Semicolon): ";",
    int(Qt.Key.Key_Apostrophe): "'",
    int(Qt.Key.Key_Comma): ",",
    int(Qt.Key.Key_Period): ".",
    int(Qt.Key.Key_Slash): "/",
    int(Qt.Key.Key_CapsLock): "CapsLock",
}

_MODIFIER_KEYS = {
    int(Qt.Key.Key_Control),
    int(Qt.Key.Key_Shift),
    int(Qt.Key.Key_Alt),
    int(Qt.Key.Key_Meta),
    int(Qt.Key.Key_AltGr),
}

_QT_MODIFIER_NAME_MAP: dict[int, str] = {
    int(Qt.Key.Key_Alt): "Alt",
    int(Qt.Key.Key_AltGr): "Alt",
    int(Qt.Key.Key_Control): "Ctrl",
    int(Qt.Key.Key_Shift): "Shift",
    int(Qt.Key.Key_Meta): "Win",
}

# 快捷预设列表（供设置界面一键选用）
PRESET_TRIGGERS: list[tuple[str, str]] = [
    ("Alt+V", "Alt+V"),
    ("双击 Alt", "双击 Alt"),
    ("双击 Ctrl", "双击 Ctrl"),
    ("长按鼠标中键", "长按鼠标中键"),
    ("长按鼠标侧键1", "长按鼠标侧键1"),
    ("长按鼠标侧键2", "长按鼠标侧键2"),
    ("双击鼠标中键", "双击鼠标中键"),
]


@dataclass(frozen=True)
class TriggerSpec:
    """统一的唤起触发器规格描述。"""

    mode: str                 # "hotkey" | "double_tap" | "long_press" | "single_click"
    modifiers: int            # Win32 MOD_* (仅 mode == "hotkey" 使用)
    vks: tuple[int, ...]      # 监听的虚拟键码集合（任一按下即视为目标按下）
    display: str              # 规范化展示文本，如 "Alt+V"、"双击 Alt"、"长按鼠标中键"
    is_mouse: bool = False


def _resolve_single_key_token(token: str) -> tuple[tuple[int, ...], str, bool] | None:
    """解析单个按键或鼠标按钮名称，返回 (vks, 标准名称, is_mouse)。"""
    clean = token.strip().lower().replace(" ", "")
    if not clean:
        return None

    if clean in _MOUSE_TARGET_MAP:
        vks, label = _MOUSE_TARGET_MAP[clean]
        return vks, label, True

    if clean in _MODIFIER_TARGET_MAP:
        vks, label = _MODIFIER_TARGET_MAP[clean]
        return vks, label, False

    if len(clean) == 1 and "a" <= clean <= "z":
        ch = clean.upper()
        return (ord(ch),), ch, False
    if len(clean) == 1 and "0" <= clean <= "9":
        return (ord(clean),), clean, False
    if clean.startswith("f") and clean[1:].isdigit():
        fn = int(clean[1:])
        if 1 <= fn <= 24:
            return (0x70 + (fn - 1),), f"F{fn}", False
    if clean in _SPECIAL_VK_MAP:
        vk = _SPECIAL_VK_MAP[clean]
        disp = "Space" if vk == 0x20 else ("CapsLock" if vk == VK_CAPITAL else token.strip())
        return (vk,), disp, False

    return None


def parse_hotkey(hotkey_str: str) -> tuple[int, int] | None:
    """
    将标准组合快捷键字符串（如 'Alt+V', 'Ctrl+Shift+X', 'Ctrl+`'）解析为 (win32_modifiers, vk_code)。
    解析失败或属于扩展手势触发器时返回 None。
    """
    if not hotkey_str or not hotkey_str.strip():
        return None

    parts = [p.strip() for p in hotkey_str.strip().split("+") if p.strip()]
    if not parts:
        return None

    modifiers = 0
    key_token: str | None = None

    for part in parts:
        low = part.lower()
        if low in ("ctrl", "control"):
            modifiers |= MOD_CONTROL
        elif low in ("alt", "menu"):
            modifiers |= MOD_ALT
        elif low == "shift":
            modifiers |= MOD_SHIFT
        elif low in ("win", "meta", "super", "windows"):
            modifiers |= MOD_WIN
        else:
            if key_token is not None:
                return None
            key_token = part

    if not key_token:
        return None

    low_key = key_token.lower()
    vk: int | None = None

    if len(key_token) == 1 and "a" <= low_key <= "z":
        vk = ord(low_key.upper())
    elif len(key_token) == 1 and "0" <= low_key <= "9":
        vk = ord(low_key)
    elif low_key.startswith("f") and low_key[1:].isdigit():
        fn = int(low_key[1:])
        if 1 <= fn <= 24:
            vk = 0x70 + (fn - 1)
    elif low_key in _SPECIAL_VK_MAP:
        vk = _SPECIAL_VK_MAP[low_key]

    if vk is None:
        return None

    is_function_key = 0x70 <= vk <= 0x87
    if modifiers == 0 and not is_function_key:
        return None

    return modifiers, vk


def parse_trigger(spec_str: str) -> TriggerSpec | None:
    """
    解析任意唤起配置字符串，支持：
    1. 标准组合键：'Alt+V', 'Ctrl+Shift+X', 'Ctrl+`', 'F11'
    2. 双击按键/鼠标：'双击 Alt', '双击 Ctrl', '双击 Shift', '双击 Win', '双击鼠标中键', '双击鼠标侧键1', 'DoubleClick:Alt'
    3. 长按按键/鼠标：'长按鼠标中键', '长按鼠标侧键1', '长按鼠标侧键2', '长按 Alt', 'LongPress:MouseMiddle'
    4. 单击鼠标按键：'单击鼠标中键', '单击鼠标侧键1', '单击鼠标侧键2', '鼠标中键', '鼠标侧键1', '鼠标侧键2'
    """
    if not spec_str or not spec_str.strip():
        return None
    raw = spec_str.strip()

    # 1. 先检查标准组合键
    std = parse_hotkey(raw)
    if std is not None:
        mods, vk = std
        return TriggerSpec(mode="hotkey", modifiers=mods, vks=(vk,), display=raw, is_mouse=False)

    # 2. 检查前缀式扩展触发器
    # 支持中文前缀（双击 / 长按 / 单击）与英文前缀（DoubleClick: / DoubleTap: / LongPress: / Hold: / Click:）
    m_double = re.match(r"^(?:双击|double[_\-\s]?(?:click|tap)\s*[:：]?)\s*(.+)$", raw, flags=re.IGNORECASE)
    if m_double:
        resolved = _resolve_single_key_token(m_double.group(1))
        if resolved is None:
            return None
        vks, label, is_mouse = resolved
        disp = f"双击{label}" if is_mouse else f"双击 {label}"
        return TriggerSpec(mode="double_tap", modifiers=0, vks=vks, display=disp, is_mouse=is_mouse)

    m_long = re.match(r"^(?:长按|long[_\-\s]?press\s*[:：]?|hold\s*[:：]?)\s*(.+)$", raw, flags=re.IGNORECASE)
    if m_long:
        resolved = _resolve_single_key_token(m_long.group(1))
        if resolved is None:
            return None
        vks, label, is_mouse = resolved
        disp = f"长按{label}" if is_mouse else f"长按 {label}"
        return TriggerSpec(mode="long_press", modifiers=0, vks=vks, display=disp, is_mouse=is_mouse)

    m_click = re.match(r"^(?:单击|click\s*[:：]?)\s*(.+)$", raw, flags=re.IGNORECASE)
    if m_click:
        resolved = _resolve_single_key_token(m_click.group(1))
        if resolved is None or not resolved[2]:
            return None
        vks, label, is_mouse = resolved
        return TriggerSpec(mode="single_click", modifiers=0, vks=vks, display=f"单击{label}", is_mouse=is_mouse)

    # 3. 直接写鼠标特殊按键名（如 "鼠标中键"、"鼠标侧键1"、"鼠标侧键2"）视为单击鼠标特殊按键
    resolved_direct = _resolve_single_key_token(raw)
    if resolved_direct is not None and resolved_direct[2]:
        vks, label, _ = resolved_direct
        if VK_LBUTTON not in vks and VK_RBUTTON not in vks:
            return TriggerSpec(mode="single_click", modifiers=0, vks=vks, display=f"单击{label}", is_mouse=True)

    return None


def format_qt_key_event(event: QKeyEvent) -> str | None:
    """
    将 Qt 键盘按下事件转换为标准组合快捷键字符串（如 'Alt+V', 'Ctrl+Shift+X'）。
    若只按下了修饰键或按键不支持，返回 None。
    """
    key = int(event.key())
    if key in _MODIFIER_KEYS or key <= 0 or key == int(Qt.Key.Key_unknown):
        return None

    mods = event.modifiers()
    mod_names: list[str] = []
    if mods & Qt.KeyboardModifier.ControlModifier:
        mod_names.append("Ctrl")
    if mods & Qt.KeyboardModifier.AltModifier:
        mod_names.append("Alt")
    if mods & Qt.KeyboardModifier.ShiftModifier:
        mod_names.append("Shift")
    if mods & Qt.KeyboardModifier.MetaModifier:
        mod_names.append("Win")

    key_name: str | None = None
    if int(Qt.Key.Key_A) <= key <= int(Qt.Key.Key_Z):
        key_name = chr(key)
    elif int(Qt.Key.Key_0) <= key <= int(Qt.Key.Key_9):
        key_name = chr(key)
    elif int(Qt.Key.Key_F1) <= key <= int(Qt.Key.Key_F24):
        key_name = f"F{key - int(Qt.Key.Key_F1) + 1}"
    elif key in _QT_KEY_NAME_MAP:
        key_name = _QT_KEY_NAME_MAP[key]

    if not key_name:
        return None

    is_fn = key_name.startswith("F") and key_name[1:].isdigit()
    if not mod_names and not is_fn:
        return None

    return "+".join([*mod_names, key_name])


def format_qt_single_key_name(key: int) -> str | None:
    """获取单个 Qt 按键（含修饰键）对应的标准名称，用于录制双击/长按单键。"""
    if key in _QT_MODIFIER_NAME_MAP:
        return _QT_MODIFIER_NAME_MAP[key]
    if int(Qt.Key.Key_A) <= key <= int(Qt.Key.Key_Z):
        return chr(key)
    if int(Qt.Key.Key_0) <= key <= int(Qt.Key.Key_9):
        return chr(key)
    if int(Qt.Key.Key_F1) <= key <= int(Qt.Key.Key_F24):
        return f"F{key - int(Qt.Key.Key_F1) + 1}"
    if key in _QT_KEY_NAME_MAP:
        return _QT_KEY_NAME_MAP[key]
    return None


class _WinHotkeyEventFilter(QAbstractNativeEventFilter):
    """拦截 Windows 原生 WM_HOTKEY 消息。"""

    def __init__(self, hotkey_id: int, callback: Callable[[], None]) -> None:
        super().__init__()
        self._hotkey_id = hotkey_id
        self._callback = callback

    def nativeEventFilter(self, eventType: bytes | str, message: int) -> tuple[bool, int]:  # noqa: N802
        if not IS_WINDOWS:
            return False, 0
        try:
            evt = bytes(eventType) if not isinstance(eventType, (bytes, str)) else eventType
            if evt in (b"windows_generic_MSG", "windows_generic_MSG", b"windows_dispatcher_MSG"):
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and int(msg.wParam) == self._hotkey_id:
                    self._callback()
                    return True, 0
        except Exception:  # noqa: BLE001
            log.exception("处理 WM_HOTKEY 消息异常")
        return False, 0


class HotkeyManager(QObject):
    """系统级全局唤起快捷键与鼠标/键盘手势管理器。"""

    #: 用户触发全局唤起时发出
    hotkey_triggered = Signal()

    def __init__(
        self,
        hotkey_id: int = HOTKEY_ID_DEFAULT,
        trigger_timeout_ms: int = DEFAULT_TRIGGER_TIMEOUT_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.hotkey_id = int(hotkey_id)
        self._trigger_timeout_ms: int = max(50, min(5000, int(trigger_timeout_ms)))
        self._current_hotkey: str = ""
        self._current_spec: TriggerSpec | None = None
        self._registered: bool = False
        self._win32_registered: bool = False
        self._paused: bool = False

        # 扩展触发器状态机变量
        self._was_down: bool = False
        self._press_down_mono: float | None = None
        self._press_down_cursor: QPoint = QPoint(0, 0)
        self._long_press_fired: bool = False
        self._contaminated: bool = False
        self._first_tap_up_mono: float | None = None
        self._first_tap_cursor: QPoint = QPoint(0, 0)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(12)
        self._poll_timer.timeout.connect(self._poll_extended_state)

        self._filter = _WinHotkeyEventFilter(self.hotkey_id, self._on_wm_hotkey)
        app = QCoreApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)

    @property
    def current_hotkey(self) -> str:
        return self._current_hotkey

    @property
    def current_spec(self) -> TriggerSpec | None:
        return self._current_spec

    @property
    def is_registered(self) -> bool:
        return self._registered

    @property
    def trigger_timeout_ms(self) -> int:
        return self._trigger_timeout_ms

    def set_trigger_timeout_ms(self, timeout_ms: int) -> None:
        """动态更新双击间隔 / 长按触发响应毫秒数（50 ~ 5000 ms）。"""
        self._trigger_timeout_ms = max(50, min(5000, int(timeout_ms)))
        self._reset_gesture_state()

    def _reset_gesture_state(self) -> None:
        self._was_down = False
        self._press_down_mono = None
        self._long_press_fired = False
        self._contaminated = False
        self._first_tap_up_mono = None

    def _on_wm_hotkey(self) -> None:
        if not self._paused:
            self.hotkey_triggered.emit()

    def set_paused(self, paused: bool) -> None:
        """在设置窗口录制新快捷键期间暂停响应全局热键。"""
        self._paused = bool(paused)
        if self._paused:
            self._reset_gesture_state()

    def register_hotkey(self, hotkey_str: str, trigger_timeout_ms: int | None = None) -> tuple[bool, str]:
        """
        注册或动态更新全局快捷键 / 扩展手势触发器。
        返回 (是否成功, 状态或错误提示信息)。
        若新热键注册失败（如被其他软件占用），自动回滚恢复旧热键。
        """
        if trigger_timeout_ms is not None:
            self.set_trigger_timeout_ms(trigger_timeout_ms)

        spec = parse_trigger(hotkey_str)
        if spec is None:
            return (
                False,
                f"无效的触发设置：{hotkey_str!r}（支持如 Alt+V、双击 Alt、长按鼠标中键、长按鼠标侧键1 等）",
            )

        previous_hotkey = self._current_hotkey
        previous_spec = self._current_spec
        was_registered = self._registered

        # 先停用旧的监听/热键
        self._poll_timer.stop()
        if self._win32_registered:
            self._unregister_raw()
            self._win32_registered = False

        if spec.mode == "hotkey":
            ok = self._register_raw(spec.modifiers | MOD_NOREPEAT, spec.vks[0])
            if ok:
                self._current_hotkey = hotkey_str.strip()
                self._current_spec = spec
                self._registered = True
                self._win32_registered = True
                log.info(
                    "已注册全局组合热键: %s (mod=0x%x, vk=0x%x)",
                    self._current_hotkey,
                    spec.modifiers,
                    spec.vks[0],
                )
                return True, f"已生效：{self._current_hotkey}"

            # 注册失败：尝试恢复旧设置
            if was_registered and previous_spec is not None:
                if previous_spec.mode == "hotkey":
                    if self._register_raw(previous_spec.modifiers | MOD_NOREPEAT, previous_spec.vks[0]):
                        self._current_hotkey = previous_hotkey
                        self._current_spec = previous_spec
                        self._registered = True
                        self._win32_registered = True
                else:
                    self._current_hotkey = previous_hotkey
                    self._current_spec = previous_spec
                    self._registered = True
                    self._reset_gesture_state()
                    self._poll_timer.start()

            msg = f"快捷键 {hotkey_str} 已被其他软件占用或系统保留，请更换其他触发方式。"
            log.warning(msg)
            return False, msg

        # 扩展触发器（双击按键 / 长按鼠标或按键 / 单击鼠标特殊键）
        self._current_hotkey = spec.display
        self._current_spec = spec
        self._registered = True
        self._reset_gesture_state()
        self._poll_timer.start()
        log.info(
            "已启用扩展触发器: %s (mode=%s, vks=%s, timeout=%dms)",
            self._current_hotkey,
            spec.mode,
            spec.vks,
            self._trigger_timeout_ms,
        )
        return True, f"已生效：{self._current_hotkey} ({self._trigger_timeout_ms} ms)"

    def unregister_hotkey(self) -> None:
        self._poll_timer.stop()
        self._reset_gesture_state()
        if self._win32_registered:
            self._unregister_raw()
            self._win32_registered = False
        if self._registered:
            self._registered = False
            log.info("已注销全局触发器: %s", self._current_hotkey)

    # ------------------------------------------------------------------ 扩展触发器轮询与状态机
    def _is_vk_down(self, vk: int) -> bool:
        if not IS_WINDOWS:
            return False
        try:
            return bool(ctypes.windll.user32.GetAsyncKeyState(int(vk)) & 0x8000)
        except Exception:  # noqa: BLE001
            return False

    def _is_other_key_down(self, target_vks: tuple[int, ...]) -> bool:
        """检测在双击/长按键盘单键期间是否有其他非目标键被按下（防止 Alt+Tab 等组合键误触）。"""
        if not IS_WINDOWS:
            return False
        ignore = set(target_vks)
        if VK_MENU in ignore or VK_LMENU in ignore or VK_RMENU in ignore:
            ignore.update((VK_MENU, VK_LMENU, VK_RMENU))
        if VK_CONTROL in ignore or VK_LCONTROL in ignore or VK_RCONTROL in ignore:
            ignore.update((VK_CONTROL, VK_LCONTROL, VK_RCONTROL))
        if VK_SHIFT in ignore or VK_LSHIFT in ignore or VK_RSHIFT in ignore:
            ignore.update((VK_SHIFT, VK_LSHIFT, VK_RSHIFT))
        if VK_LWIN in ignore or VK_RWIN in ignore:
            ignore.update((VK_LWIN, VK_RWIN))

        # 检查常见字母、数字、功能键、Tab/Space/Enter/Esc及其余修饰键
        check_vks = (
            0x09, 0x0D, 0x1B, 0x20,
            VK_MENU, VK_CONTROL, VK_SHIFT, VK_LWIN, VK_RWIN,
            *range(0x30, 0x3A),
            *range(0x41, 0x5B),
            *range(0x70, 0x7D),
        )
        for vk in check_vks:
            if vk not in ignore and self._is_vk_down(vk):
                return True
        return False

    def _poll_extended_state(self) -> None:
        if self._paused or not self._registered or self._current_spec is None:
            return
        spec = self._current_spec
        if spec.mode == "hotkey":
            return

        is_down = any(self._is_vk_down(vk) for vk in spec.vks)
        other_down = False if spec.is_mouse else self._is_other_key_down(spec.vks)
        pos = QCursor.pos()
        self._step_gesture_state(
            is_down=is_down,
            other_key_down=other_down,
            now_mono=time.monotonic(),
            cursor_pt=pos,
        )

    def feed_state_for_test(
        self,
        is_down: bool,
        other_key_down: bool = False,
        now_mono: float | None = None,
        cursor_pos: tuple[int, int] = (0, 0),
    ) -> None:
        """供单元测试确定性驱动扩展触发器状态机。"""
        if self._paused or not self._registered or self._current_spec is None:
            return
        self._step_gesture_state(
            is_down=is_down,
            other_key_down=other_key_down,
            now_mono=time.monotonic() if now_mono is None else float(now_mono),
            cursor_pt=QPoint(cursor_pos[0], cursor_pos[1]),
        )

    def _step_gesture_state(
        self,
        is_down: bool,
        other_key_down: bool,
        now_mono: float,
        cursor_pt: QPoint,
    ) -> None:
        spec = self._current_spec
        if spec is None:
            return

        threshold_sec = self._trigger_timeout_ms / 1000.0
        max_tap_sec = max(threshold_sec, 0.35)

        if other_key_down:
            self._contaminated = True
            self._first_tap_up_mono = None

        # ---------------- 1. 长按模式 (Long Press) ----------------
        if spec.mode == "long_press":
            if is_down and not self._was_down:
                self._press_down_mono = now_mono
                self._press_down_cursor = cursor_pt
                self._long_press_fired = False
                self._contaminated = other_key_down
            elif is_down and self._was_down:
                if not self._long_press_fired and self._press_down_mono is not None:
                    if spec.is_mouse and (cursor_pt - self._press_down_cursor).manhattanLength() > 10:
                        self._contaminated = True
                    if not self._contaminated and (now_mono - self._press_down_mono) >= threshold_sec:
                        self._long_press_fired = True
                        self.hotkey_triggered.emit()
            elif not is_down and self._was_down:
                self._press_down_mono = None
                self._long_press_fired = False
                self._contaminated = False
            self._was_down = is_down
            return

        # ---------------- 2. 双击模式 (Double Tap / Double Click) ----------------
        if spec.mode == "double_tap":
            if is_down and not self._was_down:
                self._press_down_mono = now_mono
                self._press_down_cursor = cursor_pt
                self._contaminated = other_key_down
                if self._first_tap_up_mono is not None:
                    if (now_mono - self._first_tap_up_mono) > threshold_sec:
                        self._first_tap_up_mono = None
                    elif spec.is_mouse and (cursor_pt - self._first_tap_cursor).manhattanLength() > 12:
                        self._first_tap_up_mono = None
            elif not is_down and self._was_down:
                if (
                    not self._contaminated
                    and self._press_down_mono is not None
                    and (now_mono - self._press_down_mono) <= max_tap_sec
                ):
                    if self._first_tap_up_mono is not None and (now_mono - self._first_tap_up_mono) <= (threshold_sec + max_tap_sec):
                        self._first_tap_up_mono = None
                        self._press_down_mono = None
                        self._was_down = False
                        self.hotkey_triggered.emit()
                        return
                    self._first_tap_up_mono = now_mono
                    self._first_tap_cursor = cursor_pt
                else:
                    self._first_tap_up_mono = None
                self._press_down_mono = None
                self._contaminated = False
            self._was_down = is_down
            return

        # ---------------- 3. 单击鼠标特殊键模式 (Single Click) ----------------
        if spec.mode == "single_click":
            if is_down and not self._was_down:
                self._press_down_mono = now_mono
                self._press_down_cursor = cursor_pt
                self._contaminated = False
            elif is_down and self._was_down:
                if (cursor_pt - self._press_down_cursor).manhattanLength() > 10:
                    self._contaminated = True
            elif not is_down and self._was_down:
                if (
                    not self._contaminated
                    and self._press_down_mono is not None
                    and (now_mono - self._press_down_mono) <= max_tap_sec
                ):
                    self._press_down_mono = None
                    self._was_down = False
                    self.hotkey_triggered.emit()
                    return
                self._press_down_mono = None
            self._was_down = is_down

    def _register_raw(self, modifiers: int, vk: int) -> bool:
        if not IS_WINDOWS:
            return False
        try:
            return bool(ctypes.windll.user32.RegisterHotKey(None, self.hotkey_id, int(modifiers), int(vk)))
        except Exception:  # noqa: BLE001
            log.exception("调用 RegisterHotKey 失败")
            return False

    def _unregister_raw(self) -> bool:
        if not IS_WINDOWS:
            return False
        try:
            return bool(ctypes.windll.user32.UnregisterHotKey(None, self.hotkey_id))
        except Exception:  # noqa: BLE001
            return False
