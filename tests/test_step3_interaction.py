"""
第三步交互单元测试：
验证点击切换多选（Toggle Multi-Selection）、序号重排、展开按钮事件隔离、Esc 键、
合并复制（文本/单图/多文件/图文混合 + 防死循环锁）以及右侧粘贴/收藏/清空按钮。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import clipboard_watcher as cw  # noqa: E402
from core.database import ClipboardDatabase  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
WAIT_GUARD_MS = cw.SELF_CHANGE_GUARD_MS + 250


class Step3InteractionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="clip_step3_test_"))
        self.db = ClipboardDatabase(data_dir=self.tmp)
        self.watcher = cw.ClipboardWatcher(self.db)
        self.watcher.start()
        QTest.qWait(120)

    def tearDown(self) -> None:
        self.watcher.stop()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_three_texts(self) -> tuple[int, int, int]:
        long_a = "\n".join([f"第一条长文本第 {i} 行" for i in range(1, 6)])
        a = self.db.add_item("text", long_a, "h-a", f"{len(long_a)} 字符").item_id
        b = self.db.add_item("text", "第二条文本 B", "h-b", "7 字符").item_id
        c = self.db.add_item("text", "第三条文本 C", "h-c", "7 字符").item_id
        return a, b, c

    def test_toggle_multi_selection_and_order_badges(self) -> None:
        a, b, c = self._seed_three_texts()
        win = MainWindow(self.db, self.watcher)
        win.show()
        QTest.qWait(60)

        card_a = win._cards_by_id[a]
        card_b = win._cards_by_id[b]
        card_c = win._cards_by_id[c]

        # 1. 单击卡片 B -> 选中（单选时不额外突兀显示 ①）
        QTest.mouseClick(card_b, Qt.MouseButton.LeftButton, pos=QPoint(30, 15))
        self.assertEqual(win.selected_ids, [b])
        self.assertTrue(card_b.is_selected)
        self.assertIsNone(card_b.select_order)

        # 2. 再次单击卡片 B -> 取消选中
        QTest.mouseClick(card_b, Qt.MouseButton.LeftButton, pos=QPoint(30, 15))
        self.assertEqual(win.selected_ids, [])
        self.assertFalse(card_b.is_selected)

        # 3. 依次单击 C -> A -> B：按点击顺序维护多选，且显示 ① ② ③
        for card in (card_c, card_a, card_b):
            QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=QPoint(30, 15))
        self.assertEqual(win.selected_ids, [c, a, b])
        self.assertEqual((card_c.select_order, card_a.select_order, card_b.select_order), (1, 2, 3))
        self.assertIn("①", card_c._right_meta_label.text())
        self.assertIn("②", card_a._right_meta_label.text())
        self.assertIn("③", card_b._right_meta_label.text())

        # 4. 再次点击中间的 A 取消选中 -> 剩余 C、B 自动重新编号为 ①、②
        QTest.mouseClick(card_a, Qt.MouseButton.LeftButton, pos=QPoint(30, 15))
        self.assertEqual(win.selected_ids, [c, b])
        self.assertFalse(card_a.is_selected)
        self.assertEqual((card_c.select_order, card_b.select_order), (1, 2))
        self.assertIn("①", card_c._right_meta_label.text())
        self.assertIn("②", card_b._right_meta_label.text())

        # 5. 点击卡片 A 内部的 ⌄ 展开 / ⌃ 收起 按钮，只触发卡片展开/收起，不触发选中切换
        self.assertTrue(card_a._expand_btn.isVisible())
        card_a._expand_btn.click()
        self.assertTrue(card_a.is_expanded)
        self.assertEqual(win.selected_ids, [c, b], "点击展开按钮不应改变选中状态")

        # 6. Esc 键：有选中项时按一次清空选中；无选中项时再按一次隐藏窗口
        QTest.keyClick(win, Qt.Key.Key_Escape)
        self.assertEqual(win.selected_ids, [])
        self.assertTrue(win.isVisible())

        QTest.keyClick(win, Qt.Key.Key_Escape)
        self.assertFalse(win.isVisible())
        win.close()

    def test_merge_copy_text_files_image_and_anti_loop(self) -> None:
        a, b, c = self._seed_three_texts()
        win = MainWindow(self.db, self.watcher)
        win.show()
        QTest.qWait(60)

        # 未选中任何项时点击复制 -> 提示且返回 False
        self.assertFalse(win.copy_selected_items())
        self.assertIn("请先点击选择", win._toast.text())

        # 按 B -> C 顺序选中并点击右侧蓝色复制按钮
        win.toggle_card_selection(b)
        win.toggle_card_selection(c)
        total_before = self.db.get_total_count()

        win.btn_copy.click()
        QTest.qWait(WAIT_GUARD_MS)

        self.assertEqual(QApplication.clipboard().text(), "第二条文本 B\n第三条文本 C")
        self.assertEqual(win.selected_ids, [], "复制成功后应清空选中状态")
        self.assertIn("【2项内容已合并】", win._toast.text())
        self.assertEqual(self.db.get_total_count(), total_before + 1, "合并多条记录应生成 1 条新合并记录且不死循环")
        top_after_text_merge = self.db.get_items("all")[0]
        self.assertEqual(top_after_text_merge["content"], "第二条文本 B\n第三条文本 C")

        # 测试多文件合并复制 + 单张图片复制 + 图文混合复制
        f1, f2 = self.tmp / "doc1.txt", self.tmp / "doc2.txt"
        f1.write_text("1", encoding="utf-8")
        f2.write_text("2", encoding="utf-8")
        rf1 = self.db.add_item("files", json.dumps([str(f1)]), "hf1", "1 个文件").item_id
        rf2 = self.db.add_item("files", json.dumps([str(f2)]), "hf2", "1 个文件").item_id

        img_path = self.db.images_dir / "single.png"
        img = QImage(80, 60, QImage.Format.Format_RGB32)
        img.fill(QColor("#4285f4"))
        img.save(str(img_path), "PNG")
        rimg = self.db.add_item("image", "images/single.png", "himg", "80 X 60").item_id
        win.refresh_items()

        # 选中两个文件记录 -> 合并为 QUrl 列表，同时作为新记录入库并提示【2个文件已合并】
        total_before_file_merge = self.db.get_total_count()
        win.toggle_card_selection(rf1)
        win.toggle_card_selection(rf2)
        self.assertTrue(win.copy_selected_items())
        QTest.qWait(WAIT_GUARD_MS)
        urls = [u.toLocalFile() for u in QApplication.clipboard().mimeData().urls()]
        self.assertEqual(cw.normalize_file_paths(urls), cw.normalize_file_paths([str(f1), str(f2)]))
        self.assertEqual(win._toast.text(), "【2个文件已合并】")
        self.assertEqual(self.db.get_total_count(), total_before_file_merge + 1)
        top_after_file_merge = self.db.get_items("all")[0]
        self.assertEqual(top_after_file_merge["item_type"], "files")
        self.assertEqual(top_after_file_merge["meta_info"], "2 个文件")

        # 仅选中 1 张图片 -> 直接写入 QImage
        win.toggle_card_selection(rimg)
        self.assertTrue(win.copy_selected_items())
        QTest.qWait(WAIT_GUARD_MS)
        self.assertTrue(QApplication.clipboard().mimeData().hasImage())

        # 混合选中文本 + 文件 -> 按换行符拼接文本与路径
        win.toggle_card_selection(b)
        win.toggle_card_selection(rf1)
        self.assertTrue(win.copy_selected_items())
        QTest.qWait(WAIT_GUARD_MS)
        self.assertEqual(QApplication.clipboard().text(), f"第二条文本 B\n{str(f1)}")

        win.close()

    def test_favorite_clear_and_send_buttons(self) -> None:
        a, b, c = self._seed_three_texts()
        win = MainWindow(self.db, self.watcher)
        win.show()
        QTest.qWait(60)

        # 选中 A、B 点击收藏按钮 -> 全部设为收藏，收藏 Tab 更新为 "收藏 (2)"
        win.toggle_card_selection(a)
        win.toggle_card_selection(b)
        win.btn_favorite.click()
        self.assertEqual(self.db.get_favorite_count(), 2)
        self.assertEqual(win._tab_buttons["favorite"]._title, "收藏 (2)")

        # 再次点击收藏按钮（由于 A、B 均已收藏）-> 全部取消收藏
        win.btn_favorite.click()
        self.assertEqual(self.db.get_favorite_count(), 0)
        self.assertEqual(win._tab_buttons["favorite"]._title, "收藏 (0)")

        # 单独把 A 设为收藏，然后点击清空按钮 -> B、C 被清空，仅保留收藏项 A
        win.clear_selection()
        win.toggle_card_selection(a)
        win.btn_favorite.click()
        win.clear_selection()

        win.btn_clear.click()
        remaining = [it["id"] for it in self.db.get_items("all")]
        self.assertEqual(remaining, [a])

        # 测试纸飞机发送/粘贴按钮：合并复制 -> 隐藏窗口 -> 延迟 100ms 调用 send_ctrl_v
        win.toggle_card_selection(a)
        with patch("core.win_input.send_ctrl_v", return_value=True) as mock_send:
            win.btn_send.click()
            self.assertFalse(win.isVisible(), "点击粘贴按钮后应立即隐藏悬浮窗")
            QTest.qWait(220)
            mock_send.assert_called_once()

        win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
