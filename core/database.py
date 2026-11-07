"""
SQLite3 数据库管理：剪贴板历史记录的增删改查。

表结构 clipboard_history:
    id            INTEGER PRIMARY KEY AUTOINCREMENT
    item_type     TEXT     'text' | 'image' | 'files'
    content       TEXT     文本内容 / 图片相对路径(images/xxx.png) / 文件路径数组 JSON
    content_hash  TEXT     SHA256，用于去重
    meta_info     TEXT     "169 字符" / "1203 X 903" / "2 个文件"
    is_favorite   INTEGER  0=普通 1=收藏
    created_at    REAL     Unix 时间戳（秒，保留小数以保证同一秒内的先后顺序）
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import paths

log = logging.getLogger(__name__)

ITEM_TEXT = "text"
ITEM_IMAGE = "image"
ITEM_FILES = "files"
ITEM_TYPES = (ITEM_TEXT, ITEM_IMAGE, ITEM_FILES)

CATEGORY_ALL = "all"
CATEGORY_FAVORITE = "favorite"
CATEGORIES = (CATEGORY_ALL, ITEM_TEXT, ITEM_IMAGE, ITEM_FILES, CATEGORY_FAVORITE)

# add_item 的处理结果
ACTION_INSERTED = "inserted"    # 新插入
ACTION_UPDATED = "updated"      # 历史中已存在 -> 刷新时间并排到最前
ACTION_DUPLICATE = "duplicate"  # 与最新一条完全相同 -> 忽略

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clipboard_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type    TEXT    NOT NULL CHECK (item_type IN ('text', 'image', 'files')),
    content      TEXT    NOT NULL,
    content_hash TEXT    NOT NULL,
    meta_info    TEXT    NOT NULL DEFAULT '',
    is_favorite  INTEGER NOT NULL DEFAULT 0,
    created_at   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_hash     ON clipboard_history (content_hash);
CREATE INDEX IF NOT EXISTS idx_history_created  ON clipboard_history (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_favorite ON clipboard_history (is_favorite);
"""

_ORDER_BY = "ORDER BY created_at DESC, id DESC"


@dataclass(frozen=True)
class AddResult:
    item_id: int
    action: str  # ACTION_INSERTED / ACTION_UPDATED / ACTION_DUPLICATE

    @property
    def changed(self) -> bool:
        """列表是否发生了变化（需要刷新 UI）。"""
        return self.action != ACTION_DUPLICATE


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ClipboardDatabase:
    """剪贴板历史数据库。线程安全（内部加锁），一般在 Qt 主线程中使用。"""

    def __init__(self, db_path: str | Path | None = None, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else paths.get_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir = paths.get_images_dir(self.data_dir)
        self.db_path = Path(db_path) if db_path else paths.get_db_path(self.data_dir)

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        log.info("数据库已打开: %s", self.db_path)

    # ------------------------------------------------------------------ 基础
    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> "ClipboardDatabase":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    # ------------------------------------------------------------------ 写入
    def add_item(self, item_type: str, content: str, content_hash: str, meta_info: str) -> AddResult:
        """
        添加一条记录（带去重）：
        - 最新一条记录的 content_hash 与当前相同 -> 不插入（duplicate）；
        - 历史中存在相同 content_hash -> 更新其 created_at 为当前时间，排到最前（updated）；
        - 否则插入新记录（inserted）。
        """
        if item_type not in ITEM_TYPES:
            raise ValueError(f"不支持的 item_type: {item_type!r}")
        now = time.time()
        with self._lock, self._conn:
            latest = self._conn.execute(
                f"SELECT id, content_hash FROM clipboard_history {_ORDER_BY} LIMIT 1"
            ).fetchone()
            if latest is not None and latest["content_hash"] == content_hash:
                return AddResult(latest["id"], ACTION_DUPLICATE)

            existing = self._conn.execute(
                "SELECT id FROM clipboard_history WHERE content_hash = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (content_hash,),
            ).fetchone()
            if existing is not None:
                self._conn.execute(
                    "UPDATE clipboard_history SET created_at = ?, meta_info = ? WHERE id = ?",
                    (now, meta_info, existing["id"]),
                )
                return AddResult(existing["id"], ACTION_UPDATED)

            cur = self._conn.execute(
                "INSERT INTO clipboard_history (item_type, content, content_hash, meta_info, is_favorite, created_at) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                (item_type, content, content_hash, meta_info, now),
            )
            return AddResult(int(cur.lastrowid), ACTION_INSERTED)

    # ------------------------------------------------------------------ 查询
    def get_items(self, category: str = CATEGORY_ALL, search_query: str = "",
                  limit: int | None = None) -> list[dict[str, Any]]:
        """
        按分类 + 关键词查询，按 created_at DESC 排序。

        category: 'all' | 'text' | 'image' | 'files' | 'favorite'
        search_query: 模糊匹配 content（对文件类型同时兼容 JSON 中被转义的反斜杠路径）
        """
        if category not in CATEGORIES:
            raise ValueError(f"不支持的分类: {category!r}，可选 {CATEGORIES}")

        where: list[str] = []
        params: list[Any] = []
        if category == CATEGORY_FAVORITE:
            where.append("is_favorite = 1")
        elif category != CATEGORY_ALL:
            where.append("item_type = ?")
            params.append(category)

        query = (search_query or "").strip()
        if query:
            patterns = [query]
            # 文件路径以 JSON 存储，'\' 会被转义为 '\\'，额外匹配转义后的形式
            json_form = json.dumps(query, ensure_ascii=False)[1:-1]
            if json_form != query:
                patterns.append(json_form)
            where.append("(" + " OR ".join("content LIKE ? ESCAPE '\\'" for _ in patterns) + ")")
            params.extend(f"%{_escape_like(p)}%" for p in patterns)

        sql = "SELECT * FROM clipboard_history"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" {_ORDER_BY}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM clipboard_history WHERE id = ?", (item_id,)).fetchone()
        return self._row_to_dict(row)

    def get_items_by_ids(self, item_ids: Iterable[int]) -> list[dict[str, Any]]:
        """按传入 id 的顺序返回记录（用于多选按点击顺序合并复制），不存在的 id 会被跳过。"""
        ids = list(dict.fromkeys(int(i) for i in item_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM clipboard_history WHERE id IN ({placeholders})", ids
            ).fetchall()
        by_id = {r["id"]: dict(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def get_favorite_count(self) -> int:
        with self._lock:
            return int(self._conn.execute(
                "SELECT COUNT(*) FROM clipboard_history WHERE is_favorite = 1").fetchone()[0])

    def get_total_count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM clipboard_history").fetchone()[0])

    # ------------------------------------------------------------------ 收藏
    def toggle_favorite(self, item_id: int) -> tuple[bool | None, int]:
        """
        切换收藏状态。
        返回 (切换后的状态, 当前收藏总数)；记录不存在时状态为 None。
        """
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT is_favorite FROM clipboard_history WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                new_state: bool | None = None
            else:
                new_state = not bool(row["is_favorite"])
                self._conn.execute(
                    "UPDATE clipboard_history SET is_favorite = ? WHERE id = ?", (int(new_state), item_id))
            count = int(self._conn.execute(
                "SELECT COUNT(*) FROM clipboard_history WHERE is_favorite = 1").fetchone()[0])
        return new_state, count

    def set_favorite(self, item_ids: Iterable[int], favorite: bool) -> int:
        """批量设置收藏状态，返回当前收藏总数。"""
        ids = [int(i) for i in item_ids]
        with self._lock, self._conn:
            self._conn.executemany(
                "UPDATE clipboard_history SET is_favorite = ? WHERE id = ?",
                [(int(bool(favorite)), i) for i in ids],
            )
            return int(self._conn.execute(
                "SELECT COUNT(*) FROM clipboard_history WHERE is_favorite = 1").fetchone()[0])

    # ------------------------------------------------------------------ 删除 / 清理
    def delete_items(self, item_ids: Iterable[int]) -> int:
        """删除指定记录（含收藏），并清理不再被引用的图片文件，返回删除条数。"""
        ids = [int(i) for i in item_ids]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"DELETE FROM clipboard_history WHERE id IN ({placeholders})", ids)
            deleted = cur.rowcount
        self.cleanup_orphan_images()
        return deleted

    def clear_unfavorited(self) -> int:
        """清空所有未收藏记录，并删除对应的无用图片缓存文件，返回删除的记录数。"""
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM clipboard_history WHERE is_favorite = 0")
            deleted = cur.rowcount
        removed_images = self.cleanup_orphan_images()
        log.info("已清空 %d 条未收藏记录，删除 %d 个图片缓存", deleted, removed_images)
        return deleted

    def prune_to_max_history(self, max_history: int = 500) -> int:
        """当总记录数超出 max_history 时，裁剪最早的未收藏记录并清理无用图片。"""
        limit = max(10, int(max_history))
        with self._lock, self._conn:
            total = int(self._conn.execute("SELECT COUNT(*) FROM clipboard_history").fetchone()[0])
            if total <= limit:
                return 0
            excess = total - limit
            cur = self._conn.execute(
                """
                DELETE FROM clipboard_history
                WHERE id IN (
                    SELECT id FROM clipboard_history
                    WHERE is_favorite = 0
                    ORDER BY created_at ASC, id ASC
                    LIMIT ?
                )
                """,
                (excess,),
            )
            deleted = cur.rowcount
        if deleted > 0:
            self.cleanup_orphan_images()
        return deleted

    def cleanup_orphan_images(self) -> int:
        """删除 images/ 目录中不再被任何记录引用的图片文件，返回删除的文件数。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT content FROM clipboard_history WHERE item_type = ?", (ITEM_IMAGE,)).fetchall()
        referenced = set()
        for r in rows:
            try:
                referenced.add(paths.resolve_data_path(r["content"], self.data_dir).resolve())
            except OSError:
                continue

        removed = 0
        if not self.images_dir.exists():
            return 0
        for file in self.images_dir.iterdir():
            if not file.is_file() or file.suffix.lower() != ".png":
                continue
            if file.resolve() in referenced:
                continue
            try:
                file.unlink()
                removed += 1
            except OSError as exc:  # 文件被占用等情况，下次再清理
                log.warning("删除图片缓存失败 %s: %s", file, exc)
        return removed
