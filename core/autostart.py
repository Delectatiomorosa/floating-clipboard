"""
Windows 注册表开机自启管理 (core/autostart.py)。

通过内置 winreg 模块操作：
    HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
键名：
    "FloatingClipboardTool"

路径兼容：
- 打包为 .exe 后运行 (getattr(sys, 'frozen', False) == True)：
    写入 '"<exe完整路径>" --minimized'
- 开发环境运行：
    写入 '"<pythonw.exe或python.exe完整路径>" "<main.py完整路径>" --minimized'
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

RUN_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_VALUE_NAME = "FloatingClipboardTool"


def build_autostart_command() -> str:
    """构建开机自启命令行字符串（兼容 PyInstaller 打包后环境与开发环境）。"""
    if getattr(sys, "frozen", False):
        exe_path = os.path.abspath(sys.executable)
        return f'"{exe_path}" --minimized'

    exe_dir = Path(os.path.abspath(sys.executable)).parent
    pythonw = exe_dir / "pythonw.exe"
    interpreter = str(pythonw if pythonw.exists() else Path(os.path.abspath(sys.executable)))
    main_script = str((paths.get_app_dir() / "main.py").resolve())
    return f'"{interpreter}" "{main_script}" --minimized'


def is_autostart_enabled(value_name: str = REG_VALUE_NAME) -> bool:
    """读取注册表真实状态，校验当前是否已开启开机自启。"""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_REG_PATH,
            0,
            winreg.KEY_READ,
        ) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
            return bool(value and str(value).strip())
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("读取注册表开机自启状态失败: %s", exc)
        return False


def get_autostart_command_in_registry(value_name: str = REG_VALUE_NAME) -> str | None:
    """读取注册表中实际保存的启动命令字符串（不存在时返回 None）。"""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_REG_PATH,
            0,
            winreg.KEY_READ,
        ) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError:
        return None


def set_autostart(enable: bool, value_name: str = REG_VALUE_NAME) -> bool:
    """
    设置或移除 Windows 开机自启注册表项，返回操作是否成功。
    """
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_REG_PATH,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_READ,
        ) as key:
            if enable:
                cmd = build_autostart_command()
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, cmd)
                log.info("已写入开机自启注册表 [%s] = %s", value_name, cmd)
            else:
                try:
                    winreg.DeleteValue(key, value_name)
                    log.info("已移除开机自启注册表 [%s]", value_name)
                except FileNotFoundError:
                    pass
        return True
    except OSError as exc:
        log.error("设置开机自启失败 (enable=%s): %s", enable, exc)
        return False
