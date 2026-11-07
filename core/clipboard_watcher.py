"""
系统剪贴板监听与写入管理器。

监听：基于 ``QClipboard.dataChanged`` 信号，自动识别三种类型并写入数据库
    1. files : mimeData.hasUrls() 且包含本地文件路径 -> 绝对路径列表 JSON，"N 个文件"
    2. image : mimeData.hasImage()                   -> 保存 PNG 到 data/images/，"W X H"
    3. text  : mimeData.hasText() 且非空              -> 原文，"N 字符"

写入：``copy_items_to_clipboard(items, separator)`` 支持单选 / 多选合并复制。

防回环保护（双保险）：
    a) 主动写入前置 ``_ignore_self_change = True``，写入后延迟一段时间（保护窗口）再复位，
       期间收到的 dataChanged 全部忽略（Windows 上该通知是异步投递的，不能写完立刻复位）；
    b) 同时记录本次写入内容的 hash，若保护窗口结束后仍收到迟到的通知且内容 hash 一致，同样忽略。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from PySide6.QtCore import QMimeData, QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QClipboard, QGuiApplication, QImage, QPixmap

from . import paths
from .database import ITEM_FILES, ITEM_IMAGE, ITEM_TEXT, AddResult, ClipboardDatabase

log = logging.getLogger(__name__)

DEBOUNCE_MS = 80            # 合并一次复制触发的多次 dataChanged
RETRY_MS = 60               # 剪贴板被其他程序短暂锁定时的重试间隔
MAX_RETRIES = 3
SELF_CHANGE_GUARD_MS = 500  # 主动写入后的忽略窗口
SELF_HASH_TTL_S = 3.0       # 主动写入内容 hash 的有效期

# Office / WPS 复制单元格或文字时会同时附带一张渲染图，此时应优先识别为文本
_OFFICE_FORMAT_MARKERS = (
    "rich text format", "xml spreadsheet", "biff", "csv", "link source",
)


# ---------------------------------------------------------------------- 哈希工具
def _sha256(*chunks: bytes) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(c)
    return h.hexdigest()


def compute_text_hash(text: str) -> str:
    return _sha256(b"text\0", text.encode("utf-8", "surrogatepass"))


def compute_files_hash(file_paths: list[str]) -> str:
    return _sha256(b"files\0", json.dumps(file_paths, ensure_ascii=False).encode("utf-8"))


def compute_image_hash(image: QImage) -> str:
    """像素级哈希：统一转换为 RGBA8888 后对像素数据做 SHA256。"""
    img = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    hasher = hashlib.sha256()
    hasher.update(b"image\0")
    hasher.update(f"{w}x{h}\0".encode())
    buf = memoryview(img.constBits())
    row_bytes, bpl = w * 4, img.bytesPerLine()
    if bpl == row_bytes:
        hasher.update(buf[: row_bytes * h])
    else:  # 理论上 RGBA8888 不会有行填充，这里兜底
        for y in range(h):
            hasher.update(buf[y * bpl: y * bpl + row_bytes])
    return hasher.hexdigest()


def normalize_file_paths(file_paths: Iterable[str]) -> list[str]:
    """转绝对路径、规范分隔符，并按原顺序去重。"""
    result: list[str] = []
    seen: set[str] = set()
    for p in file_paths:
        if not p:
            continue
        norm = os.path.normpath(os.path.abspath(p))
        key = os.path.normcase(norm)
        if key not in seen:
            seen.add(key)
            result.append(norm)
    return result


def _sanitize_text(text: str) -> str:
    """去除无法编码为 UTF-8 的孤立代理字符，避免 SQLite 写入失败。"""
    return text.encode("utf-8", "surrogatepass").decode("utf-8", "replace")


@dataclass
class _Snapshot:
    item_type: str
    content_hash: str
    meta_info: str
    text: str = ""
    files: list[str] = field(default_factory=list)
    image: QImage | None = None


# ---------------------------------------------------------------------- 监听器
class ClipboardWatcher(QObject):
    """剪贴板监听器 + 合并写入器。"""

    #: 新记录写入数据库 (item_id, action)，action 为 'inserted' 或 'updated'
    item_recorded = Signal(int, str)
    #: 程序自身写入剪贴板完成 (写入的条目数)
    self_copy_done = Signal(int)
    #: 发生错误（已被捕获，不会中断监听）
    error_occurred = Signal(str)

    def __init__(self, database: ClipboardDatabase, clipboard: QClipboard | None = None,
                 parent: QObject | None = None):
        super().__init__(parent)
        if QGuiApplication.instance() is None:
            raise RuntimeError("请先创建 QApplication 再创建 ClipboardWatcher")
        self._db = database
        self._clipboard = clipboard or QGuiApplication.clipboard()
        self._running = False

        # 防回环状态
        self._ignore_self_change = False
        self._self_written_hash: str | None = None
        self._self_written_at = 0.0

        self._change_seq = 0  # 每次 dataChanged 自增，用于丢弃过期的重试

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(lambda: self._process_clipboard(self._change_seq, 0))

        self._guard_timer = QTimer(self)
        self._guard_timer.setSingleShot(True)
        self._guard_timer.setInterval(SELF_CHANGE_GUARD_MS)
        self._guard_timer.timeout.connect(self._release_self_guard)

    # ------------------------------------------------------------------ 生命周期
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_ignoring_self_change(self) -> bool:
        return self._ignore_self_change

    def start(self) -> None:
        if not self._running:
            self._clipboard.dataChanged.connect(self._on_data_changed)
            self._running = True
            log.info("剪贴板监听已启动")

    def stop(self) -> None:
        if self._running:
            try:
                self._clipboard.dataChanged.disconnect(self._on_data_changed)
            except (RuntimeError, TypeError):
                pass
            self._debounce_timer.stop()
            self._running = False
            log.info("剪贴板监听已停止")

    # ------------------------------------------------------------------ 监听流程
    def _on_data_changed(self) -> None:
        self._change_seq += 1
        if self._ignore_self_change:
            # 程序自己写入导致的变化：直接丢弃，并取消可能挂起的处理
            self._debounce_timer.stop()
            log.debug("忽略程序自身写入引起的剪贴板变化")
            return
        self._debounce_timer.start()  # 重新计时，合并连续触发

    def _process_clipboard(self, seq: int, attempt: int) -> None:
        if seq != self._change_seq or self._ignore_self_change or not self._running:
            return  # 已有更新的变化 / 处于自写保护期 / 已停止
        try:
            mime = self._clipboard.mimeData()
            if mime is None or not mime.formats():
                # 剪贴板可能被其他程序短暂占用（OpenClipboard 失败），稍后重试
                if attempt < MAX_RETRIES:
                    QTimer.singleShot(RETRY_MS, lambda: self._process_clipboard(seq, attempt + 1))
                return
            snapshot = self._read_snapshot(mime)
            if snapshot is None:
                return
            if self._is_recent_self_write(snapshot.content_hash):
                log.debug("内容与程序最近一次写入一致，忽略")
                return
            result = self._store(snapshot)
            if result is not None and result.changed:
                log.info("记录剪贴板 [%s] %s -> id=%s (%s)",
                         snapshot.item_type, snapshot.meta_info, result.item_id, result.action)
                self.item_recorded.emit(result.item_id, result.action)
        except Exception as exc:  # noqa: BLE001 - 监听不能因单次异常中断
            log.exception("处理剪贴板变化失败")
            if attempt < MAX_RETRIES:
                QTimer.singleShot(RETRY_MS, lambda: self._process_clipboard(seq, attempt + 1))
            else:
                self.error_occurred.emit(str(exc))

    def _read_snapshot(self, mime: QMimeData) -> _Snapshot | None:
        """识别剪贴板类型：files > image > text。"""
        # 1. 文件
        if mime.hasUrls():
            files = normalize_file_paths(u.toLocalFile() for u in mime.urls() if u.isLocalFile())
            if files:
                return _Snapshot(ITEM_FILES, compute_files_hash(files), f"{len(files)} 个文件", files=files)

        text = _sanitize_text(mime.text()) if mime.hasText() else ""

        # 2. 图片（Office/WPS 复制文字时附带的渲染图除外）
        if mime.hasImage() and not self._prefer_text_over_image(mime, text):
            image = self._extract_image(mime)
            if image is not None and not image.isNull():
                return _Snapshot(ITEM_IMAGE, compute_image_hash(image),
                                 f"{image.width()} X {image.height()}", image=image)

        # 3. 文本
        if text.strip():
            return _Snapshot(ITEM_TEXT, compute_text_hash(text), f"{len(text)} 字符", text=text)
        return None

    @staticmethod
    def _prefer_text_over_image(mime: QMimeData, text: str) -> bool:
        if not text.strip():
            return False
        formats = " ".join(mime.formats()).lower()
        return any(marker in formats for marker in _OFFICE_FORMAT_MARKERS)

    def _extract_image(self, mime: QMimeData) -> QImage | None:
        data = mime.imageData()
        if isinstance(data, QImage):
            return data
        if isinstance(data, QPixmap):
            return data.toImage()
        image = self._clipboard.image()
        return image if not image.isNull() else None

    def _store(self, snap: _Snapshot) -> AddResult | None:
        if snap.item_type == ITEM_TEXT:
            content = snap.text
        elif snap.item_type == ITEM_FILES:
            content = json.dumps(snap.files, ensure_ascii=False)
        else:
            content = self._save_image(snap.image, snap.content_hash)  # type: ignore[arg-type]
            if content is None:
                return None
        return self._db.add_item(snap.item_type, content, snap.content_hash, snap.meta_info)

    def _save_image(self, image: QImage, content_hash: str) -> str | None:
        """按哈希命名保存 PNG（同一图片只存一份），返回相对数据目录的路径。"""
        images_dir = self._db.images_dir
        images_dir.mkdir(parents=True, exist_ok=True)
        target = images_dir / f"{content_hash[:32]}.png"
        if not target.exists():
            tmp = target.with_suffix(".png.tmp")
            if not image.save(str(tmp), "PNG"):
                log.error("保存剪贴板图片失败: %s", target)
                return None
            os.replace(tmp, target)
        return paths.to_data_relative(target, self._db.data_dir)

    # ------------------------------------------------------------------ 防回环
    def _is_recent_self_write(self, content_hash: str) -> bool:
        if self._self_written_hash is None:
            return False
        if time.monotonic() - self._self_written_at > SELF_HASH_TTL_S:
            self._self_written_hash = None
            return False
        if content_hash == self._self_written_hash:
            self._self_written_hash = None  # 只抵消一次
            return True
        return False

    def _release_self_guard(self) -> None:
        self._ignore_self_change = False
        log.debug("自写保护窗口结束")

    @staticmethod
    def _clone_mime_data(src: QMimeData) -> QMimeData:
        dst = QMimeData()
        if src.hasUrls():
            dst.setUrls(list(src.urls()))
        if src.hasText():
            dst.setText(src.text())
        if src.hasImage():
            dst.setImageData(src.imageData())
        return dst

    def _write_mime(self, mime: QMimeData, expected_hash: str | None, attempt: int = 0) -> None:
        """在防回环保护下写入系统剪贴板（若剪贴板被其他进程短暂锁定则自动延迟 50ms 重试）。"""
        self._ignore_self_change = True
        self._debounce_timer.stop()
        self._self_written_hash = expected_hash
        self._self_written_at = time.monotonic()
        backup = self._clone_mime_data(mime) if attempt < MAX_RETRIES else None
        try:
            self._clipboard.setMimeData(mime)
        except Exception as exc:  # noqa: BLE001
            log.warning("写入系统剪贴板异常 (attempt=%d): %s", attempt + 1, exc)
            if backup is not None and attempt < MAX_RETRIES:
                QTimer.singleShot(50, lambda b=backup, h=expected_hash, a=attempt + 1: self._write_mime(b, h, a))
            else:
                self.error_occurred.emit(str(exc))
        finally:
            # 延迟复位：Windows 的 WM_CLIPBOARDUPDATE 是异步到达的
            self._guard_timer.start()

    # ------------------------------------------------------------------ 合并复制
    def collect_merged_file_paths(self, items_list: Iterable[Mapping[str, Any] | int]) -> list[str]:
        """收集并去重多条记录合并后的本地文件路径列表。"""
        items = self._normalize_items(items_list)
        return normalize_file_paths(p for it in items for p in self._item_file_paths(it))

    def copy_items_to_clipboard(
        self,
        items_list: Iterable[Mapping[str, Any] | int],
        separator: str = "\n",
        record_merged: bool = False,
    ) -> bool:
        """
        将一条或多条记录（按传入顺序）合并写入系统剪贴板，返回是否成功。

        items_list 元素可以是 get_items() 返回的 dict，也可以直接是记录 id。
        record_merged: 当合并多条记录（len(items) > 1）时，是否将合并结果作为一条新剪贴板记录写入数据库并置顶。
        合并规则：
        - 仅 1 张图片           -> 写入图片本身（QImage）
        - 全部为文件/图片(无文本) -> 合并去重为 QUrl 列表（资源管理器可直接粘贴全部文件）
        - 含文本（或混合）       -> 文本原文 / 文件路径 / 图片路径按 separator 拼接为文本
        """
        items = self._normalize_items(items_list)
        if not items:
            log.warning("copy_items_to_clipboard: 没有可复制的记录")
            return False

        types = {it["item_type"] for it in items}
        mime = QMimeData()
        expected_hash: str | None
        merged_type: str | None = None
        merged_content: str = ""
        merged_meta: str = ""

        if len(items) == 1 and types == {ITEM_IMAGE}:
            path = paths.resolve_data_path(items[0]["content"], self._db.data_dir)
            image = QImage(str(path))
            if image.isNull():
                log.error("图片缓存不存在或已损坏: %s", path)
                return False
            mime.setImageData(image)
            expected_hash = compute_image_hash(image)

        elif ITEM_TEXT not in types:
            file_paths = normalize_file_paths(p for it in items for p in self._item_file_paths(it))
            if not file_paths:
                return False
            mime.setUrls([QUrl.fromLocalFile(p) for p in file_paths])
            mime.setText(separator.join(file_paths))
            expected_hash = compute_files_hash(file_paths)
            merged_type = ITEM_FILES
            merged_content = json.dumps(file_paths, ensure_ascii=False)
            merged_meta = f"{len(file_paths)} 个文件"

        else:
            parts = [self._item_to_text(it) for it in items]
            text = separator.join(p for p in parts if p)
            mime.setText(text)
            expected_hash = compute_text_hash(text)
            merged_type = ITEM_TEXT
            merged_content = text
            merged_meta = f"{len(text)} 字符"

        self._write_mime(mime, expected_hash)

        if record_merged and len(items) > 1 and merged_type is not None and expected_hash is not None:
            result = self._db.add_item(merged_type, merged_content, expected_hash, merged_meta)
            log.info("合并记录已入库 [%s] %s -> id=%s (%s)",
                     merged_type, merged_meta, result.item_id, result.action)
            self.item_recorded.emit(result.item_id, result.action)

        self.self_copy_done.emit(len(items))
        log.info("已合并复制 %d 项到剪贴板", len(items))
        return True

    def _normalize_items(self, items_list: Iterable[Mapping[str, Any] | int]) -> list[Mapping[str, Any]]:
        items: list[Mapping[str, Any]] = []
        for it in items_list or []:
            if isinstance(it, int):
                row = self._db.get_item(it)
                if row is not None:
                    items.append(row)
            elif it is not None:
                items.append(it)
        return items

    def _item_file_paths(self, item: Mapping[str, Any]) -> list[str]:
        if item["item_type"] == ITEM_FILES:
            try:
                data = json.loads(item["content"])
                return [str(p) for p in data] if isinstance(data, list) else []
            except (TypeError, ValueError):
                return []
        if item["item_type"] == ITEM_IMAGE:
            return [str(paths.resolve_data_path(item["content"], self._db.data_dir))]
        return []

    def _item_to_text(self, item: Mapping[str, Any]) -> str:
        if item["item_type"] == ITEM_TEXT:
            return str(item["content"])
        return "\n".join(self._item_file_paths(item))
