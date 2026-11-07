"""
系统托盘管理器 (SystemTrayManager)。

基于 QSystemTrayIcon 实现：
- 常驻 Windows 右下角系统托盘
- 左键单击托盘图标：切换悬浮窗的显示/隐藏
- 右键深色托盘菜单：
    1. 显示剪贴板
    2. 打开设置
    3. 清空未收藏记录
    4. 退出程序
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QCursor, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from core import paths
from .icons import make_app_icon, make_tool_icon
from .theme import FONT_CSS, Palette, css_rgba

if TYPE_CHECKING:
    from .main_window import MainWindow

log = logging.getLogger(__name__)

_DARK_MENU_QSS = f"""
QMenu {{
    background-color: {Palette.ELEVATED};
    color: {Palette.TEXT};
    border: 1px solid {Palette.BORDER_STRONG};
    border-radius: 8px;
    padding: 6px 4px;
    font-family: {FONT_CSS};
    font-size: 12px;
}}
QMenu::item {{
    padding: 7px 24px 7px 14px;
    border-radius: 5px;
    margin: 1px 3px;
}}
QMenu::item:selected {{
    background-color: {css_rgba(Palette.ACCENT, 0.18)};
    color: {Palette.ACCENT};
}}
QMenu::separator {{
    height: 1px;
    background-color: {Palette.HAIRLINE};
    margin: 4px 8px;
}}
"""


def load_or_create_app_icon() -> QIcon:
    ico_path = paths.resource_path("assets", "icon.ico")
    if ico_path.exists():
        icon = QIcon(str(ico_path))
        if not icon.isNull():
            return icon
    return make_app_icon(32)


class SystemTrayManager(QObject):
    """Windows 系统托盘图标与右键深色菜单管理。"""

    show_requested = Signal()
    settings_requested = Signal()
    clear_requested = Signal()
    quit_requested = Signal()

    def __init__(self, main_window: "MainWindow", parent: QObject | None = None) -> None:
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.icon = load_or_create_app_icon()

        self.tray_icon = QSystemTrayIcon(self.icon, self)
        self.tray_icon.setToolTip("悬浮剪贴板")

        self.menu = QMenu()
        self.menu.setStyleSheet(_DARK_MENU_QSS)

        self.action_show = QAction("显示剪贴板", self.menu)
        self.action_settings = QAction("打开设置", self.menu)
        self.action_clear = QAction("清空未收藏记录", self.menu)
        self.action_quit = QAction("退出程序", self.menu)

        self.action_show.triggered.connect(self._on_show_triggered)
        self.action_settings.triggered.connect(self._on_settings_triggered)
        self.action_clear.triggered.connect(self._on_clear_triggered)
        self.action_quit.triggered.connect(self._on_quit_triggered)

        self.menu.addAction(self.action_show)
        self.menu.addAction(self.action_settings)
        self.menu.addAction(self.action_clear)
        self.menu.addSeparator()
        self.menu.addAction(self.action_quit)

        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.activated.connect(self._on_tray_activated)

    def show(self) -> None:
        self.tray_icon.show()
        log.info("系统托盘图标已显示")

    def hide(self) -> None:
        self.tray_icon.hide()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.main_window.toggle_window_from_tray()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            self.menu.popup(QCursor.pos())

    def _on_show_triggered(self) -> None:
        self.show_requested.emit()
        self.main_window.summon_window()

    def _on_settings_triggered(self) -> None:
        self.settings_requested.emit()
        self.main_window.open_settings_dialog()

    def _on_clear_triggered(self) -> None:
        self.clear_requested.emit()
        self.main_window.clear_unfavorited_items()

    def _on_quit_triggered(self) -> None:
        self.quit_requested.emit()
        self.hide()
        app = QApplication.instance()
        if app is not None:
            app.quit()
