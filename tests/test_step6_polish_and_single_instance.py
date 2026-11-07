"""
第六步单元测试：
验证单实例防重开机制 (SingleInstanceLock)、窗口唤起时相对时间动态刷新、
多选状态与操作栏角标/工具提示联动、Ctrl+A 全选与 Delete 删除快捷键、以及剪贴板短暂锁定自动重试机制。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QMimeData, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.clipboard_watcher import ClipboardWatcher  # noqa: E402
from core.database import ClipboardDatabase  # noqa: E402
from core.single_instance import SingleInstanceLock  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


class Step6PolishAndSingleInstanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="clip_step6_test_"))
        self.db = ClipboardDatabase(data_dir=self.tmp)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_instance_lock_and_summon_existing_window(self) -> None:
        pipe_name = f"FloatingClipboard_Test_{uuid.uuid4().hex[:10]}"
        lock1 = SingleInstanceLock(server_name=pipe_name)
        lock2 = SingleInstanceLock(server_name=pipe_name)
        win = MainWindow(self.db)
        win.hide()

        try:
            # 1. 第一个实例成功获取单实例锁
            self.assertTrue(lock1.try_acquire())
            self.assertTrue(lock1.is_primary)
            lock1.activate_requested.connect(win.summon_window)

            # 2. 第二个实例尝试启动时返回 False，并通过 QLocalSocket 唤醒第一个实例的隐藏主窗口
            self.assertFalse(win.isVisible())
            self.assertFalse(lock2.try_acquire())
            QTest.qWait(80)
            self.assertTrue(win.isVisible(), "二次启动时应通过本地 Socket 自动呼出已有实例的悬浮窗")
        finally:
            win.close()
            lock2.release()
            lock1.release()

    def test_relative_time_refreshes_on_show_event(self) -> None:
        r1 = self.db.add_item("text", "动态时间刷新测试", "h-time", "8 字符")
        win = MainWindow(self.db)
        win.show()
        QTest.qWait(40)

        card = win._cards_by_id[r1.item_id]
        self.assertEqual(card._time_label.text(), "刚刚")

        # 隐藏窗口，模拟时间过去了 3 分钟，再次显示窗口时应自动刷新为 "3 分钟前"
        win.hide()
        card.item["created_at"] = time.time() - 185.0
        win.show()
        QTest.qWait(40)
        self.assertEqual(card._time_label.text(), "3 分钟前")
        win.close()

    def test_selection_feedback_ctrl_a_and_delete_shortcuts(self) -> None:
        id1 = self.db.add_item("text", "记录 1", "h1", "4 字符").item_id
        id2 = self.db.add_item("text", "记录 2", "h2", "4 字符").item_id
        id3 = self.db.add_item("text", "记录 3", "h3", "4 字符").item_id

        win = MainWindow(self.db)
        win.show()
        QTest.qWait(40)

        # 1. 选中 2 项 -> 顶部状态显示 "已选中 2 项"，右侧复制按钮角标为 2，工具提示变为 "合并复制 2 项 (Ctrl+C)"
        win.toggle_card_selection(id1)
        win.toggle_card_selection(id2)
        self.assertTrue(win._selection_badge.isVisible())
        self.assertEqual(win._selection_badge.text(), "已选中 2 项")
        self.assertEqual(win.btn_copy.badge_count, 2)
        self.assertEqual(win.btn_copy.toolTip(), "合并复制 2 项 (Ctrl+C)")

        # 2. 按 Ctrl + A 全选当前列表全部 3 项
        QTest.keyClick(win, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(win.selected_ids), 3)
        self.assertEqual(win._selection_badge.text(), "已选中 3 项")
        self.assertEqual(win.btn_copy.badge_count, 3)

        # 3. 仅选中 id2 并按 Delete 键删除选中记录
        win.clear_selection()
        win.toggle_card_selection(id2)
        QTest.keyClick(win, Qt.Key.Key_Delete)
        remaining_ids = [it["id"] for it in self.db.get_items("all")]
        self.assertEqual(remaining_ids, [id3, id1])
        self.assertEqual(win.selected_ids, [])
        self.assertEqual(win.btn_copy.badge_count, 0)
        win.close()

    def test_clipboard_transient_lock_retry(self) -> None:
        # 模拟系统剪贴板第一次读取时被其他进程短暂锁定（返回空 formats），50ms 重试后恢复正常
        empty_mime = QMimeData()
        valid_mime = QMimeData()
        valid_mime.setText("短暂锁定后重试成功文本")

        mock_clip = MagicMock()
        mock_clip.mimeData.side_effect = [empty_mime, valid_mime]

        watcher = ClipboardWatcher(self.db, clipboard=mock_clip)
        watcher._running = True
        watcher._change_seq = 1
        watcher._process_clipboard(seq=1, attempt=0)

        # 等待重试定时器触发 (RETRY_MS = 60ms)
        QTest.qWait(140)
        items = self.db.get_items("all")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content"], "短暂锁定后重试成功文本")


if __name__ == "__main__":
    unittest.main(verbosity=2)
