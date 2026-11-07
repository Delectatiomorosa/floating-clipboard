"""
第五步单元测试：
验证 ConfigManager 配置文件读写、Windows 注册表开机自启 (autostart)、
Win32 全局热键注册/动态切换 (HotkeyManager) 以及深色设置弹窗 (SettingsDialog / HotkeyEditWidget)。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import autostart  # noqa: E402
from core.config_manager import DEFAULT_HOTKEY, ConfigManager  # noqa: E402
from core.database import ClipboardDatabase  # noqa: E402
from core.hotkey_manager import (  # noqa: E402
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    VK_MBUTTON,
    VK_MENU,
    VK_XBUTTON1,
    VK_XBUTTON2,
    HotkeyManager,
    format_qt_key_event,
    parse_hotkey,
    parse_trigger,
)
from ui.main_window import MainWindow  # noqa: E402
from ui.settings_dialog import SettingsDialog  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
TEST_REG_NAME = "FloatingClipboardTool_UnitTest"


class Step5SettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="clip_step5_test_"))
        self.db = ClipboardDatabase(data_dir=self.tmp)
        self.config = ConfigManager(data_dir=self.tmp)

    def tearDown(self) -> None:
        autostart.set_autostart(False, value_name=TEST_REG_NAME)
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_config_manager_defaults_and_persistence(self) -> None:
        self.assertEqual(self.config.hotkey, "Alt+V")
        self.assertEqual(self.config.auto_start, False)
        self.assertEqual(self.config.merge_separator, "\n")
        self.assertEqual(self.config.clear_selection_after_copy, True)
        self.assertEqual(self.config.hide_after_copy, False)
        self.assertEqual(self.config.max_history, 500)
        self.assertTrue((self.tmp / "config.json").exists())

        self.config.update(
            {
                "hotkey": "Ctrl+Shift+X",
                "auto_start": True,
                "merge_separator": "\n\n",
                "hide_after_copy": True,
                "max_history": 200,
            }
        )
        reloaded = ConfigManager(data_dir=self.tmp)
        self.assertEqual(reloaded.hotkey, "Ctrl+Shift+X")
        self.assertTrue(reloaded.auto_start)
        self.assertEqual(reloaded.merge_separator, "\n\n")
        self.assertTrue(reloaded.hide_after_copy)
        self.assertEqual(reloaded.max_history, 200)

    def test_autostart_registry_and_frozen_path_compatibility(self) -> None:
        # 1. 开发环境命令格式校验
        dev_cmd = autostart.build_autostart_command()
        self.assertIn("main.py", dev_cmd)
        self.assertTrue(dev_cmd.endswith("--minimized"))

        # 2. 打包为 .exe 后的命令格式校验
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", r"D:\DSH_WORK_learn\项目_09_剪切板\悬浮剪贴板.exe"
        ):
            frozen_cmd = autostart.build_autostart_command()
            self.assertEqual(frozen_cmd, r'"D:\DSH_WORK_learn\项目_09_剪切板\悬浮剪贴板.exe" --minimized')

        # 3. 真实写入与移除 Windows 注册表 HKCU\...\Run 测试（使用独立测试键名）
        self.assertFalse(autostart.is_autostart_enabled(TEST_REG_NAME))
        self.assertTrue(autostart.set_autostart(True, value_name=TEST_REG_NAME))
        self.assertTrue(autostart.is_autostart_enabled(TEST_REG_NAME))
        self.assertEqual(autostart.get_autostart_command_in_registry(TEST_REG_NAME), dev_cmd)

        self.assertTrue(autostart.set_autostart(False, value_name=TEST_REG_NAME))
        self.assertFalse(autostart.is_autostart_enabled(TEST_REG_NAME))

    def test_hotkey_parser_and_dynamic_registration(self) -> None:
        self.assertEqual(parse_hotkey("Alt+V"), (MOD_ALT, ord("V")))
        self.assertEqual(parse_hotkey("Ctrl+Shift+X"), (MOD_CONTROL | MOD_SHIFT, ord("X")))
        self.assertEqual(parse_hotkey("Ctrl+`"), (MOD_CONTROL, 0xC0))
        self.assertIsNone(parse_hotkey("A"))  # 缺少修饰键
        self.assertIsNone(parse_hotkey(""))

        mgr = HotkeyManager(hotkey_id=0xB912)
        try:
            ok1, _ = mgr.register_hotkey("Ctrl+Alt+F11")
            self.assertTrue(ok1)
            self.assertEqual(mgr.current_hotkey, "Ctrl+Alt+F11")

            # 动态修改快捷键
            ok2, _ = mgr.register_hotkey("Ctrl+Shift+F12")
            self.assertTrue(ok2)
            self.assertEqual(mgr.current_hotkey, "Ctrl+Shift+F12")

            # 与主窗口联动：触发热键信号时切换窗口显示/隐藏
            win = MainWindow(self.db, config_manager=self.config, hotkey_manager=mgr)
            win.hide()
            mgr.hotkey_triggered.emit()
            self.assertTrue(win.isVisible())
            mgr.hotkey_triggered.emit()
            self.assertFalse(win.isVisible())
            win.close()
        finally:
            mgr.unregister_hotkey()

    def test_settings_dialog_hotkey_recorder_and_save(self) -> None:
        mgr = HotkeyManager(hotkey_id=0xB913)
        try:
            dlg = SettingsDialog(
                config_manager=self.config,
                hotkey_manager=mgr,
                autostart_setter=lambda en: autostart.set_autostart(en, value_name=TEST_REG_NAME),
                autostart_checker=lambda: autostart.is_autostart_enabled(TEST_REG_NAME),
            )
            dlg.show()
            QTest.qWait(50)

            # 1. 点击快捷键录制框 -> 显示 "请按下快捷键..."
            dlg.hotkey_edit.start_recording()
            self.assertTrue(dlg.hotkey_edit.is_recording)
            self.assertEqual(dlg.hotkey_edit._label.text(), "请按下快捷键...")

            # 2. 模拟按下 Ctrl + Shift + K
            key_evt = QKeyEvent(
                QKeyEvent.Type.KeyPress,
                int(Qt.Key.Key_K),
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
            )
            self.assertEqual(format_qt_key_event(key_evt), "Ctrl+Shift+K")
            dlg.hotkey_edit.keyPressEvent(key_evt)
            self.assertFalse(dlg.hotkey_edit.is_recording)
            self.assertEqual(dlg.hotkey_edit.hotkey, "Ctrl+Shift+K")

            # 3. 测试“恢复默认”按钮
            dlg.btn_reset_hotkey.click()
            self.assertEqual(dlg.hotkey_edit.hotkey, DEFAULT_HOTKEY)

            # 再录制为 Ctrl+Alt+F10 以便保存测试
            dlg.hotkey_edit.set_hotkey("Ctrl+Alt+F10")

            # 4. 切换开机自启开关
            dlg.autostart_switch.setChecked(True, emit_signal=True)
            self.assertTrue(autostart.is_autostart_enabled(TEST_REG_NAME))

            # 5. 修改合并分隔符为“空行 (\n\n)”并勾选“合并复制后自动隐藏悬浮窗”
            dlg.sep_combo.setCurrentIndex(1)
            dlg.hide_after_copy_cb.setChecked(True)

            # 6. 点击保存
            dlg.btn_save.click()
            self.assertEqual(self.config.hotkey, "Ctrl+Alt+F10")
            self.assertEqual(mgr.current_hotkey, "Ctrl+Alt+F10")
            self.assertEqual(self.config.merge_separator, "\n\n")
            self.assertTrue(self.config.hide_after_copy)
            dlg.close()
        finally:
            mgr.unregister_hotkey()

    def test_extended_triggers_double_alt_mouse_long_press_and_ms_timeout(self) -> None:
        # 1. 解析器支持双击 Alt、长按鼠标中键、长按鼠标侧键1/2
        spec_alt = parse_trigger("双击 Alt")
        self.assertIsNotNone(spec_alt)
        assert spec_alt is not None
        self.assertEqual(spec_alt.mode, "double_tap")
        self.assertIn(VK_MENU, spec_alt.vks)

        spec_mid = parse_trigger("长按鼠标中键")
        self.assertIsNotNone(spec_mid)
        assert spec_mid is not None
        self.assertEqual(spec_mid.mode, "long_press")
        self.assertEqual(spec_mid.vks, (VK_MBUTTON,))
        self.assertTrue(spec_mid.is_mouse)

        spec_x1 = parse_trigger("长按鼠标侧键1")
        self.assertIsNotNone(spec_x1)
        assert spec_x1 is not None
        self.assertEqual(spec_x1.vks, (VK_XBUTTON1,))

        spec_x2 = parse_trigger("长按鼠标侧键2")
        self.assertIsNotNone(spec_x2)
        assert spec_x2 is not None
        self.assertEqual(spec_x2.vks, (VK_XBUTTON2,))

        # 2. 状态机实测：双击 Alt（间隔 <= 250ms 触发，间隔过长不触发）
        mgr = HotkeyManager(hotkey_id=0xB914, trigger_timeout_ms=250)
        triggered: list[str] = []
        mgr.hotkey_triggered.connect(lambda: triggered.append("fired"))
        try:
            ok, _ = mgr.register_hotkey("双击 Alt", trigger_timeout_ms=250)
            self.assertTrue(ok)
            self.assertEqual(mgr.current_hotkey, "双击 Alt")
            self.assertEqual(mgr.trigger_timeout_ms, 250)

            # 第一次按下并松开 Alt (t=1.00 -> 1.06)
            mgr.feed_state_for_test(True, now_mono=1.00)
            mgr.feed_state_for_test(False, now_mono=1.06)
            self.assertEqual(len(triggered), 0)

            # 间隔 120ms (<= 250ms) 第二次按下并松开 Alt -> 触发！
            mgr.feed_state_for_test(True, now_mono=1.18)
            mgr.feed_state_for_test(False, now_mono=1.24)
            self.assertEqual(len(triggered), 1)

            # 3. 状态机实测：长按鼠标侧键1（设置 400ms 响应）
            ok, _ = mgr.register_hotkey("长按鼠标侧键1", trigger_timeout_ms=400)
            self.assertTrue(ok)
            self.assertEqual(mgr.trigger_timeout_ms, 400)

            mgr.feed_state_for_test(True, now_mono=10.00, cursor_pos=(100, 100))
            mgr.feed_state_for_test(True, now_mono=10.25, cursor_pos=(101, 100))  # 250ms < 400ms，尚未触发
            self.assertEqual(len(triggered), 1)
            mgr.feed_state_for_test(True, now_mono=10.42, cursor_pos=(101, 100))  # 420ms >= 400ms，立即触发！
            self.assertEqual(len(triggered), 2)
            mgr.feed_state_for_test(False, now_mono=10.50, cursor_pos=(101, 100))

            # 4. 设置弹窗录制双击 Alt 与毫秒数保存
            dlg = SettingsDialog(
                config_manager=self.config,
                hotkey_manager=mgr,
                autostart_setter=lambda en: True,
                autostart_checker=lambda: False,
            )
            dlg.show()
            QTest.qWait(30)
            dlg.hotkey_edit.start_recording()
            alt_press = QKeyEvent(QKeyEvent.Type.KeyPress, int(Qt.Key.Key_Alt), Qt.KeyboardModifier.AltModifier)
            alt_release = QKeyEvent(QKeyEvent.Type.KeyRelease, int(Qt.Key.Key_Alt), Qt.KeyboardModifier.NoModifier)
            dlg.hotkey_edit.keyPressEvent(alt_press)
            dlg.hotkey_edit.keyReleaseEvent(alt_release)
            dlg.hotkey_edit.keyPressEvent(alt_press)
            self.assertEqual(dlg.hotkey_edit.hotkey, "双击 Alt")

            dlg.trigger_ms_spin.setValue(380)
            dlg.btn_save.click()
            self.assertEqual(self.config.hotkey, "双击 Alt")
            self.assertEqual(self.config.trigger_timeout_ms, 380)
            self.assertEqual(mgr.trigger_timeout_ms, 380)
            dlg.close()
        finally:
            mgr.unregister_hotkey()


if __name__ == "__main__":
    unittest.main(verbosity=2)
