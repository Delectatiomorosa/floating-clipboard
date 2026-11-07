"""
Windows 原生窗口焦点与键盘模拟输入工具（基于 ctypes 调用 user32.SendInput）。

用于实现「合并粘贴到当前活动窗口（纸飞机 ➤ 按钮）」：
1. 唤起悬浮窗前记录外部活动窗口句柄 hwnd = get_foreground_window()
2. 点击粘贴按钮时：写入剪贴板 -> 隐藏悬浮窗 -> 恢复目标窗口焦点 -> 发送 Ctrl+V 按键序列
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import sys

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12  # Alt
VK_V = 0x56

ULONG_PTR = wintypes.WPARAM


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    # 必须包含 MOUSEINPUT 以保证 64 位 Windows 下 sizeof(INPUT) == 40 字节
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


def _make_key_input(vk: int, key_up: bool = False) -> INPUT:
    flags = KEYEVENTF_KEYUP if key_up else 0
    scan = 0
    if IS_WINDOWS:
        scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.union.ki = ki
    return inp


def get_foreground_window() -> int:
    """获取当前前台活动窗口句柄 (HWND)。"""
    if not IS_WINDOWS:
        return 0
    try:
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001
        return 0


def activate_window(hwnd: int) -> bool:
    """将指定句柄的窗口强制恢复为前台活动窗口（兼容后台进程通过 AttachThreadInput 获取前台权限）。"""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.IsWindow(hwnd):
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE

        fg_hwnd = user32.GetForegroundWindow()
        if fg_hwnd == hwnd:
            return True

        fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
        cur_tid = kernel32.GetCurrentThreadId()
        attached = False
        if fg_tid and cur_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(fg_tid, cur_tid, True))
        try:
            user32.BringWindowToTop(hwnd)
            ok = bool(user32.SetForegroundWindow(hwnd))
        finally:
            if attached:
                user32.AttachThreadInput(fg_tid, cur_tid, False)
        return ok
    except Exception:  # noqa: BLE001
        log.exception("恢复活动窗口失败: hwnd=%s", hwnd)
        return False


def send_ctrl_v(target_hwnd: int | None = None) -> bool:
    """
    使用 user32.SendInput 向当前活动窗口发送 Ctrl + V 组合键。
    若提供了 target_hwnd 且有效，先尝试将其激活为前台窗口。
    """
    if not IS_WINDOWS:
        return False
    try:
        if target_hwnd:
            activate_window(target_hwnd)

        # 先释放可能残留的 Alt/Shift 修饰键，再按下 Ctrl+V 并释放
        inputs = (INPUT * 6)(
            _make_key_input(VK_MENU, key_up=True),
            _make_key_input(VK_SHIFT, key_up=True),
            _make_key_input(VK_CONTROL, key_up=False),
            _make_key_input(VK_V, key_up=False),
            _make_key_input(VK_V, key_up=True),
            _make_key_input(VK_CONTROL, key_up=True),
        )
        sent = ctypes.windll.user32.SendInput(len(inputs), ctypes.byref(inputs), ctypes.sizeof(INPUT))
        if sent != len(inputs):
            err = ctypes.get_last_error()
            log.warning("SendInput 发送数量不完整: sent=%s/%s err=%s", sent, len(inputs), err)
            return False
        log.info("已通过 SendInput 发送 Ctrl+V 到活动窗口")
        return True
    except Exception:  # noqa: BLE001
        log.exception("发送 Ctrl+V 失败")
        return False
