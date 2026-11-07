"""
第二步 UI 自动化单元测试：
验证深色主界面布局、5 大分类 Tab 切换、搜索实时过滤、卡片渲染（相对时间/展开按钮/meta_info/序号）及实时监听置顶。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.clipboard_watcher import ClipboardWatcher  # noqa: E402
from core.database import ClipboardDatabase  # noqa: E402
from ui.item_card import ClipboardItemCard, format_relative_time  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


class MainWindowStep2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="clip_ui_test_"))
        self.db = ClipboardDatabase(data_dir=self.tmp)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_relative_time_formatting(self) -> None:
        now = 1_700_000_000.0
        self.assertEqual(format_relative_time(now - 10, now=now), "刚刚")
        self.assertEqual(format_relative_time(now - 180, now=now), "3 分钟前")
        self.assertEqual(format_relative_time(now - 7200, now=now), "2 小时前")
        self.assertRegex(format_relative_time(now - 90000, now=now), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_main_window_tabs_search_and_cards(self) -> None:
        # 准备 4 条不同类型的数据：短文本、超 3 行长文本、大图、文件列表
        long_text = "\n".join([f"SELECT * FROM table_{i} WHERE id = {i};" for i in range(1, 7)])
        r_long = self.db.add_item("text", long_text, "h-long", f"{len(long_text)} 字符")
        r_short = self.db.add_item("text", r"D:\DSH_WORK_learn\项目_08_sql", "h-short", "27 字符")

        img_path = self.db.images_dir / "demo.png"
        img = QImage(1203, 903, QImage.Format.Format_RGB32)
        img.fill(QColor("#2b579a"))
        img.save(str(img_path), "PNG")
        r_img = self.db.add_item("image", "images/demo.png", "h-img", "1203 X 903")

        files = [f"C:/work/179337251182{i}.png" for i in range(6)]
        r_files = self.db.add_item("files", json.dumps(files), "h-files", "6 个文件")

        self.db.toggle_favorite(r_short.item_id)

        win = MainWindow(self.db)
        win.show()
        QTest.qWait(80)

        # 1. 默认尺寸范围检查 (520~560 x 620)
        self.assertTrue(520 <= win.width() <= 560)
        self.assertEqual(win.height(), 620)

        # 1b. 顶部栏气泡布局检查：左侧剪贴板气泡无多余 × 按钮，中间搜索长条气泡右缘与下方「收藏」Tab 齐平
        from PySide6.QtWidgets import QPushButton

        self.assertEqual(len(win._brand_capsule.findChildren(QPushButton)), 0, "左侧剪贴板气泡内不应有 × 按钮")
        fav_right = win._tab_buttons["favorite"].geometry().right()
        top_row_right = win._top_inner_row.geometry().right()
        self.assertLessEqual(abs(top_row_right - fav_right), 6, "搜索长条气泡右边缘应与收藏标签基本齐平")

        # 2. 收藏标签动态显示数量 "收藏 (1)"
        self.assertEqual(win._tab_buttons["favorite"]._title, "收藏 (1)")

        # 3. 全部标签下共 4 张卡片，序号为 1, 2, 3, 4
        cards = [win._cards_by_id[i["id"]] for i in self.db.get_items("all")]
        self.assertEqual([c.index for c in cards], [1, 2, 3, 4])
        self.assertIn("6 个文件    1", cards[0]._right_meta_label.text())
        self.assertIn("1203 X 903    2", cards[1]._right_meta_label.text())

        # 4. 展开按钮可见性与展开/收起切换测试
        files_card: ClipboardItemCard = win._cards_by_id[r_files.item_id]
        img_card: ClipboardItemCard = win._cards_by_id[r_img.item_id]
        short_card: ClipboardItemCard = win._cards_by_id[r_short.item_id]
        long_card: ClipboardItemCard = win._cards_by_id[r_long.item_id]

        self.assertTrue(files_card._expand_btn.isVisible(), "超过 5 个文件应显示展开按钮")
        self.assertTrue(img_card._expand_btn.isVisible(), "大图应显示展开按钮")
        self.assertFalse(short_card._expand_btn.isVisible(), "单行短文本不应显示展开按钮")
        self.assertTrue(long_card._expand_btn.isVisible(), "超 3 行文本应显示展开按钮")

        # 点击展开按钮，按钮文字变为 '收起'，且不触发卡片选中
        files_card._expand_btn.click()
        self.assertEqual(files_card._expand_btn.text().strip(), "收起")
        self.assertFalse(files_card.is_selected)

        # 5. 分类切换测试
        win.switch_category("text")
        self.assertEqual(len(win._cards_by_id), 2)
        win.switch_category("image")
        self.assertEqual(len(win._cards_by_id), 1)
        win.switch_category("files")
        self.assertEqual(len(win._cards_by_id), 1)
        win.switch_category("favorite")
        self.assertEqual(len(win._cards_by_id), 1)
        self.assertIn(r_short.item_id, win._cards_by_id)

        # 6. 搜索框实时过滤测试
        win.switch_category("all")
        win._search_input.setText("项目_08_sql")
        self.assertEqual(len(win._cards_by_id), 1)
        self.assertIn(r_short.item_id, win._cards_by_id)
        win._search_input.clear()
        self.assertEqual(len(win._cards_by_id), 4)

        win.close()

    def test_realtime_clipboard_updates_first_item(self) -> None:
        watcher = ClipboardWatcher(self.db)
        win = MainWindow(self.db, watcher)
        watcher.start()
        win.show()
        QTest.qWait(150)

        try:
            QApplication.clipboard().setText("实时复制的新内容置顶测试")
            QTest.qWait(400)
            items = self.db.get_items("all")
            self.assertEqual(len(items), 1)
            first_card = win._cards_by_id[items[0]["id"]]
            self.assertEqual(first_card.index, 1)
            self.assertIn("12 字符    1", first_card._right_meta_label.text())
        finally:
            watcher.stop()
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
