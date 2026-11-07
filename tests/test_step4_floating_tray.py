"""
第四步单元测试：
验证无边框悬浮窗属性、拖拽位置记忆、失焦自动隐藏（含固定窗口与设置弹窗两大保护例外）以及系统托盘菜单与左键切换。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget  # noqa: E402

from core.database import ClipboardDatabase  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.tray_manager import SystemTrayManager  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


class Step4FloatingAndTrayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="clip_step4_test_"))
        self.db = ClipboardDatabase(data_dir=self.tmp)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_frameless_floating_flags_and_attributes(self) -> None:
        win = MainWindow(self.db)
        flags = win.windowFlags()
        self.assertTrue(bool(flags & Qt.WindowType.FramelessWindowHint))
        self.assertTrue(bool(flags & Qt.WindowType.WindowStaysOnTopHint))
        self.assertTrue(bool(flags & Qt.WindowType.Tool))
        self.assertTrue(win.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        win.close()

    def test_auto_hide_on_focus_loss_and_two_exceptions(self) -> None:
        win = MainWindow(self.db)
        win.summon_window()
        QTest.qWait(80)
        self.assertTrue(win.isVisible())

        # 1. 默认情况下失去焦点（例如切换到桌面或其他软件） -> 立即自动隐藏
        win._on_application_state_changed(Qt.ApplicationState.ApplicationInactive)
        self.assertFalse(win.isVisible(), "失去焦点时悬浮窗应立即自动隐藏")

        # 2. 例外保护 1：点击右下角「固定窗口」按钮开启置顶固定模式 -> 失焦不隐藏
        win.summon_window()
        win.btn_pin_window.click()
        self.assertTrue(win.is_pinned_window)
        win._on_application_state_changed(Qt.ApplicationState.ApplicationInactive)
        self.assertTrue(win.isVisible(), "固定窗口模式下失焦不应隐藏")

        # 再次点击取消固定 -> 恢复失焦自动隐藏
        win.btn_pin_window.click()
        self.assertFalse(win.is_pinned_window)
        win._on_application_state_changed(Qt.ApplicationState.ApplicationInactive)
        self.assertFalse(win.isVisible(), "取消固定后应恢复失焦自动隐藏")

        # 3. 例外保护 2：打开设置对话框期间挂起失焦隐藏 -> 失焦不隐藏
        win.summon_window()
        win.set_suspend_auto_hide(True)
        win._on_application_state_changed(Qt.ApplicationState.ApplicationInactive)
        self.assertTrue(win.isVisible(), "设置窗口打开期间不应误隐藏主窗口")

        win.set_suspend_auto_hide(False)
        win._on_application_state_changed(Qt.ApplicationState.ApplicationInactive)
        self.assertFalse(win.isVisible(), "关闭设置窗口恢复后应自动隐藏")

        # 4. 模拟点击外部另一个窗口导致 ActivationChange
        win.summon_window()
        win._was_active = True
        QTest.qWait(60)
        other_win = QWidget()
        other_win.resize(200, 200)
        other_win.show()
        other_win.activateWindow()
        win.changeEvent(QTest.createEvent(win, "ActivationChange") if hasattr(QTest, "createEvent") else __import__("PySide6.QtCore").QtCore.QEvent(__import__("PySide6.QtCore").QtCore.QEvent.Type.ActivationChange))
        self.assertFalse(win.isVisible(), "点击外部其他窗口时悬浮窗应自动隐藏")

        other_win.close()
        win.close()

    def test_drag_position_memory_and_summon(self) -> None:
        win = MainWindow(self.db)
        win.summon_window()
        QTest.qWait(50)

        # 模拟按住顶部栏拖拽到新位置 (220, 180)
        target_pos = QPoint(220, 180)
        win._last_dragged_pos = target_pos
        win.hide()

        win.summon_window()
        self.assertEqual(win.pos(), target_pos, "再次唤起时应恢复上次拖拽后的位置")
        win.close()

    def test_system_tray_manager_actions_and_toggle(self) -> None:
        self.db.add_item("text", "待清理未收藏", "h-1", "6 字符")
        fav_id = self.db.add_item("text", "保留收藏", "h-2", "4 字符").item_id
        self.db.toggle_favorite(fav_id)

        win = MainWindow(self.db)
        tray = SystemTrayManager(win)
        tray.show()
        win.hide()

        # 检查右键菜单的 4 个菜单项
        titles = [a.text() for a in tray.menu.actions() if not a.isSeparator()]
        self.assertEqual(titles, ["显示剪贴板", "打开设置", "清空未收藏记录", "退出程序"])

        # 左键单击托盘图标：隐藏 -> 显示
        win._last_auto_hide_mono = time.monotonic() - 1.0
        tray._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
        self.assertTrue(win.isVisible())

        # 再次左键单击托盘图标：显示 -> 隐藏
        tray._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
        self.assertFalse(win.isVisible())

        # 右键菜单「显示剪贴板」
        tray.action_show.trigger()
        self.assertTrue(win.isVisible())

        # 右键菜单「清空未收藏记录」
        tray.action_clear.trigger()
        self.assertEqual([it["id"] for it in self.db.get_items("all")], [fav_id])

        tray.hide()
        win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
