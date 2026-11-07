"""
剪贴板监听集成测试（会真实读写系统剪贴板，测试结束后尝试恢复原有文本）：
    python -m unittest tests.test_clipboard_watcher -v
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import clipboard_watcher as cw  # noqa: E402
from core.database import ClipboardDatabase  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

SETTLE_MS = 400                              # 等待监听处理完成
AFTER_GUARD_MS = cw.SELF_CHANGE_GUARD_MS + 300


class ClipboardWatcherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.clipboard = QApplication.clipboard()
        cls._saved_text = cls.clipboard.text()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._saved_text:
            cls.clipboard.setText(cls._saved_text)
            QTest.qWait(100)

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="clip_watch_test_"))
        self.db = ClipboardDatabase(data_dir=self.tmp)
        self.watcher = cw.ClipboardWatcher(self.db)
        self.recorded: list[tuple[int, str]] = []
        self.watcher.item_recorded.connect(lambda i, a: self.recorded.append((i, a)))
        self.watcher.start()
        QTest.qWait(150)  # 让启动前的残留通知过去

    def tearDown(self) -> None:
        self.watcher.stop()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- 工具
    def _external_copy_text(self, text: str) -> None:
        """模拟外部程序复制文本。"""
        self.clipboard.setText(text)
        QTest.qWait(SETTLE_MS)

    @staticmethod
    def _make_image(w: int = 64, h: int = 48, color: str = "#3366cc") -> QImage:
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor(color))
        return img

    # ---------------------------------------------------------------- 类型识别
    def test_capture_text(self) -> None:
        text = f"你好 clipboard {uuid.uuid4().hex}"
        self._external_copy_text(text)
        items = self.db.get_items("text")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content"], text)
        self.assertEqual(items[0]["meta_info"], f"{len(text)} 字符")
        self.assertEqual(self.recorded[-1], (items[0]["id"], "inserted"))

    def test_capture_image(self) -> None:
        self.clipboard.setImage(self._make_image(64, 48))
        QTest.qWait(SETTLE_MS)
        items = self.db.get_items("image")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["meta_info"], "64 X 48")
        self.assertTrue(items[0]["content"].startswith("images/"))
        png = self.tmp / items[0]["content"]
        self.assertTrue(png.exists())
        saved = QImage(str(png))
        self.assertEqual((saved.width(), saved.height()), (64, 48))

    def test_capture_files(self) -> None:
        f1, f2 = self.tmp / "a 文件.txt", self.tmp / "b.png"
        f1.write_text("a", encoding="utf-8")
        f2.write_bytes(b"b")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(f1)), QUrl.fromLocalFile(str(f2))])
        self.clipboard.setMimeData(mime)
        QTest.qWait(SETTLE_MS)
        items = self.db.get_items("files")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["meta_info"], "2 个文件")
        self.assertEqual(json.loads(items[0]["content"]), cw.normalize_file_paths([str(f1), str(f2)]))

    def test_whitespace_text_ignored(self) -> None:
        self._external_copy_text("   \n\t ")
        self.assertEqual(self.db.get_total_count(), 0)

    # ---------------------------------------------------------------- 去重
    def test_dedup_and_bump(self) -> None:
        a, b = f"A-{uuid.uuid4().hex}", f"B-{uuid.uuid4().hex}"
        self._external_copy_text(a)
        self._external_copy_text(a)          # 与最新相同 -> 忽略
        self.assertEqual(self.db.get_total_count(), 1)
        self._external_copy_text(b)
        self._external_copy_text(a)          # 历史已存在 -> 置顶
        self.assertEqual([i["content"] for i in self.db.get_items()], [a, b])
        self.assertEqual(self.recorded[-1][1], "updated")

    # ---------------------------------------------------------------- 防回环
    def test_merge_copy_text_no_loop(self) -> None:
        t1, t2, t3 = (f"{n}-{uuid.uuid4().hex[:8]}" for n in ("一", "二", "三"))
        for t in (t1, t2, t3):
            self._external_copy_text(t)
        items = {i["content"]: i for i in self.db.get_items()}
        count_before = self.db.get_total_count()
        recorded_before = len(self.recorded)

        # 按“点击顺序” 三、一 合并复制（混用 dict 与 id 两种传参）
        ok = self.watcher.copy_items_to_clipboard([items[t3], items[t1]["id"]], separator="\n")
        self.assertTrue(ok)
        self.assertTrue(self.watcher.is_ignoring_self_change)
        QTest.qWait(AFTER_GUARD_MS)

        self.assertFalse(self.watcher.is_ignoring_self_change)
        self.assertEqual(self.clipboard.text(), f"{t3}\n{t1}")
        self.assertEqual(self.db.get_total_count(), count_before, "合并复制不应产生新记录")
        self.assertEqual(len(self.recorded), recorded_before)

        # 保护窗口结束后，外部复制仍能正常记录
        self._external_copy_text(f"after-{uuid.uuid4().hex}")
        self.assertEqual(self.db.get_total_count(), count_before + 1)

    def test_merge_copy_files_and_image_no_loop(self) -> None:
        f1, f2 = self.tmp / "x.txt", self.tmp / "y.txt"
        f1.write_text("x", encoding="utf-8")
        f2.write_text("y", encoding="utf-8")
        for f in (f1, f2):
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(f))])
            self.clipboard.setMimeData(mime)
            QTest.qWait(SETTLE_MS)
        self.clipboard.setImage(self._make_image(32, 32, "#cc3333"))
        QTest.qWait(SETTLE_MS)
        self.assertEqual(self.db.get_total_count(), 3)

        files_items = self.db.get_items("files")
        self.assertTrue(self.watcher.copy_items_to_clipboard(files_items))
        QTest.qWait(AFTER_GUARD_MS)
        urls = [u.toLocalFile() for u in self.clipboard.mimeData().urls()]
        self.assertEqual(cw.normalize_file_paths(urls), cw.normalize_file_paths([str(f2), str(f1)]))

        image_item = self.db.get_items("image")[0]
        self.assertTrue(self.watcher.copy_items_to_clipboard([image_item]))
        QTest.qWait(AFTER_GUARD_MS)
        self.assertTrue(self.clipboard.mimeData().hasImage())
        self.assertEqual(self.db.get_total_count(), 3, "复制文件/图片回剪贴板不应产生新记录")

    def test_stop_watching(self) -> None:
        self.watcher.stop()
        self._external_copy_text(f"stopped-{uuid.uuid4().hex}")
        self.assertEqual(self.db.get_total_count(), 0)

    def test_copy_empty_returns_false(self) -> None:
        self.assertFalse(self.watcher.copy_items_to_clipboard([]))
        self.assertFalse(self.watcher.copy_items_to_clipboard([123456]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
