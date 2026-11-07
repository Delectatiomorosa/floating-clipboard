"""数据库层单元测试（不依赖 Qt）：python -m unittest tests.test_database -v"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import (ACTION_DUPLICATE, ACTION_INSERTED, ACTION_UPDATED,  # noqa: E402
                           ClipboardDatabase)


class DatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="clip_db_test_"))
        self.db = ClipboardDatabase(data_dir=self.tmp)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add_text(self, text: str):
        return self.db.add_item("text", text, f"h-{text}", f"{len(text)} 字符")

    def test_schema_and_paths(self) -> None:
        self.assertTrue((self.tmp / "clipboard.db").exists())
        self.assertTrue((self.tmp / "images").is_dir())
        cols = [r[1] for r in self.db._conn.execute("PRAGMA table_info(clipboard_history)")]
        self.assertEqual(cols, ["id", "item_type", "content", "content_hash", "meta_info",
                                "is_favorite", "created_at"])

    def test_add_and_dedup(self) -> None:
        r1 = self._add_text("A")
        self.assertEqual(r1.action, ACTION_INSERTED)
        # 与最新一条相同 -> 不插入
        r2 = self._add_text("A")
        self.assertEqual((r2.action, r2.item_id), (ACTION_DUPLICATE, r1.item_id))
        self.assertEqual(self.db.get_total_count(), 1)

        self._add_text("B")
        before = self.db.get_item(r1.item_id)["created_at"]
        time.sleep(0.01)
        # 历史中存在 -> 刷新时间并排到最前
        r3 = self._add_text("A")
        self.assertEqual((r3.action, r3.item_id), (ACTION_UPDATED, r1.item_id))
        self.assertGreater(self.db.get_item(r1.item_id)["created_at"], before)
        self.assertEqual([i["content"] for i in self.db.get_items()], ["A", "B"])
        self.assertEqual(self.db.get_total_count(), 2)

        with self.assertRaises(ValueError):
            self.db.add_item("video", "x", "h", "")

    def test_get_items_category_search(self) -> None:
        self._add_text("hello world")
        self._add_text("100%_done")
        self.db.add_item("image", "images/abc.png", "h-img", "1203 X 903")
        files = [r"D:\work\报告.docx", r"C:\tmp\a_b.png"]
        self.db.add_item("files", json.dumps(files, ensure_ascii=False), "h-files", "2 个文件")

        self.assertEqual(len(self.db.get_items("all")), 4)
        self.assertEqual(len(self.db.get_items("text")), 2)
        self.assertEqual(self.db.get_items("image")[0]["meta_info"], "1203 X 903")
        self.assertEqual(self.db.get_items("files")[0]["meta_info"], "2 个文件")
        # 最新在前
        self.assertEqual([i["item_type"] for i in self.db.get_items()], ["files", "image", "text", "text"])

        self.assertEqual([i["content"] for i in self.db.get_items("all", "WORLD")], ["hello world"])
        # % 和 _ 不应被当作通配符
        self.assertEqual([i["content"] for i in self.db.get_items("all", "%_")], ["100%_done"])
        self.assertEqual(self.db.get_items("text", "zzz"), [])
        # 带反斜杠的 Windows 路径 / 中文文件名能搜到
        self.assertEqual(len(self.db.get_items("files", r"D:\work")), 1)
        self.assertEqual(len(self.db.get_items("all", "报告")), 1)
        self.assertEqual(len(self.db.get_items("all", "", limit=2)), 2)

        with self.assertRaises(ValueError):
            self.db.get_items("unknown")

    def test_toggle_favorite(self) -> None:
        a = self._add_text("A").item_id
        b = self._add_text("B").item_id
        self.assertEqual(self.db.toggle_favorite(a), (True, 1))
        self.assertEqual(self.db.toggle_favorite(b), (True, 2))
        self.assertEqual([i["id"] for i in self.db.get_items("favorite")], [b, a])
        self.assertEqual(self.db.toggle_favorite(a), (False, 1))
        self.assertEqual(self.db.get_favorite_count(), 1)
        self.assertEqual(self.db.toggle_favorite(99999), (None, 1))

    def test_get_items_by_ids_keeps_order(self) -> None:
        a = self._add_text("A").item_id
        b = self._add_text("B").item_id
        c = self._add_text("C").item_id
        got = [i["content"] for i in self.db.get_items_by_ids([c, a, 404, b])]
        self.assertEqual(got, ["C", "A", "B"])

    def test_clear_unfavorited_removes_images(self) -> None:
        img_dir = self.db.images_dir
        for name in ("keep.png", "drop.png", "orphan.png"):
            (img_dir / name).write_bytes(b"\x89PNG fake")
        keep = self.db.add_item("image", "images/keep.png", "h-keep", "1 X 1").item_id
        self.db.add_item("image", "images/drop.png", "h-drop", "1 X 1")
        self._add_text("plain")
        fav_text = self._add_text("fav").item_id
        self.db.toggle_favorite(keep)
        self.db.toggle_favorite(fav_text)

        deleted = self.db.clear_unfavorited()
        self.assertEqual(deleted, 2)
        self.assertEqual({i["id"] for i in self.db.get_items()}, {keep, fav_text})
        self.assertTrue((img_dir / "keep.png").exists())
        self.assertFalse((img_dir / "drop.png").exists())
        self.assertFalse((img_dir / "orphan.png").exists())

    def test_persistence(self) -> None:
        self._add_text("persist")
        self.db.close()
        self.db = ClipboardDatabase(data_dir=self.tmp)
        self.assertEqual(self.db.get_items()[0]["content"], "persist")


if __name__ == "__main__":
    unittest.main(verbosity=2)
