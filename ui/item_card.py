"""
剪贴板历史卡片组件 (ClipboardItemCard)。

卡片排版：
- 上方内容区：
    - text  : 默认最多展示 3 行（超出省略），展开后展示完整文本
    - image : 水平居中圆角缩略图（默认最大高度 150px，透明棋盘格底，展开后展示大图预览）
    - files : 纵向彩色文件类型图标 + 文件名 + 所在目录（默认最多展示 5 个文件，超出可展开）
- 底部元数据行：
    - 左下角：类型小图标 + 相对时间（刚刚 / X 分钟前 / X 小时前 / 日期）+ 收藏金星
    - 正中间：当文本超 3 行、图片较大或文件超 5 个时显示 `展开` / `收起` 胶囊按钮
    - 右下角：`meta_info`（如 `1203 X 903`、`169 字符`、`2 个文件`）+ 序号键帽 + 多选顺序徽标
- 视觉：10px 圆角浮起卡片，悬停平滑提亮，选中时品牌蓝描边 + 外发光 + 微蓝底色。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core import paths
from core.database import ITEM_FILES, ITEM_IMAGE, ITEM_TEXT
from .icons import make_badge_font, make_chevron_icon, make_file_item_pixmap, make_tab_pixmap
from .theme import CARD_RADIUS, FONT_CSS, Palette, css_rgba, mix, rgba, ui_font

COLLAPSED_TEXT_LINES = 3
COLLAPSED_IMAGE_MAX_H = 150
EXPANDED_IMAGE_MAX_H = 380
COLLAPSED_FILES_MAX = 5

_CIRCLED_NUMBERS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def format_relative_time(created_at: float, now: float | None = None) -> str:
    """
    格式化相对时间：
    - < 60秒   -> '刚刚'
    - < 60分钟 -> 'X 分钟前'
    - < 24小时 -> 'X 小时前'
    - 其余     -> 'YYYY-MM-DD HH:MM'
    """
    current = time.time() if now is None else now
    diff = max(0.0, current - float(created_at))
    if diff < 60:
        return "刚刚"
    if diff < 3600:
        return f"{int(diff // 60)} 分钟前"
    if diff < 86400:
        return f"{int(diff // 3600)} 小时前"
    return datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M")


class _CollapsibleTextWidget(QWidget):
    """支持精确 3 行截断与完整展开渲染的文本绘制组件。"""

    overflow_changed = Signal(bool)

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 将连续空行规范化但保留原始换行语义
        self._raw_text = text.replace("\r\n", "\n").replace("\r", "\n")
        self._expanded = False
        self._has_overflow = False

        self.setFont(ui_font(13))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    @property
    def has_overflow(self) -> bool:
        return self._has_overflow

    def set_expanded(self, expanded: bool) -> None:
        if self._expanded != expanded:
            self._expanded = expanded
            self.updateGeometry()
            self.update()

    def _layout_lines(self, width: int) -> tuple[list[str], bool]:
        """按给定像素宽度进行文本排版，返回 (要渲染的行文本列表, 是否超出 3 行)。"""
        avail_w = max(80, width)
        fm = QFontMetrics(self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAnywhere)

        all_lines: list[str] = []
        paragraphs = self._raw_text.split("\n")
        for para in paragraphs:
            if not para:
                all_lines.append("")
                continue
            layout = QTextLayout(para, self.font())
            layout.setTextOption(option)
            layout.beginLayout()
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(avail_w)
                start = line.textStart()
                length = line.textLength()
                all_lines.append(para[start : start + length])
            layout.endLayout()

        overflow = len(all_lines) > COLLAPSED_TEXT_LINES
        if not self._expanded and overflow:
            visible = all_lines[:COLLAPSED_TEXT_LINES]
            # 第三行末尾加省略号
            last = visible[-1].rstrip()
            elided = fm.elidedText(last + " …", Qt.TextElideMode.ElideRight, avail_w)
            if not elided.endswith("…") and not elided.endswith("..."):
                elided = fm.elidedText(last, Qt.TextElideMode.ElideRight, max(20, avail_w - fm.horizontalAdvance("…"))) + "…"
            visible[-1] = elided
            return visible, True

        return all_lines, overflow

    def _line_height(self) -> int:
        fm = QFontMetrics(self.font())
        return fm.height() + 5

    def sizeHint(self) -> QSize:
        w = self.width() if self.width() > 60 else 410
        lines, overflow = self._layout_lines(w)
        if overflow != self._has_overflow:
            self._has_overflow = overflow
            self.overflow_changed.emit(overflow)
        lh = self._line_height()
        return QSize(w, max(lh, len(lines) * lh))

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        _, overflow = self._layout_lines(self.width())
        if overflow != self._has_overflow:
            self._has_overflow = overflow
            self.overflow_changed.emit(overflow)
        self.updateGeometry()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(self.font())
        painter.setPen(QColor(Palette.TEXT))

        lines, overflow = self._layout_lines(self.width())
        if overflow != self._has_overflow:
            self._has_overflow = overflow
            self.overflow_changed.emit(overflow)

        lh = self._line_height()
        fm = QFontMetrics(self.font())
        y = fm.ascent() + 2
        for line in lines:
            painter.drawText(0, y, line)
            y += lh
        painter.end()


class _ImagePreviewWidget(QWidget):
    """居中展示的圆角缩略图（透明棋盘格底 + 发丝描边 + 柔和投影）。"""

    def __init__(self, pixmap: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self._expanded = False
        self._scaled_cache: tuple[tuple[int, int], QPixmap] | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    @property
    def is_large_image(self) -> bool:
        if self._pixmap.isNull():
            return False
        return self._pixmap.height() > COLLAPSED_IMAGE_MAX_H or self._pixmap.width() > 240

    def set_expanded(self, expanded: bool) -> None:
        if self._expanded != expanded:
            self._expanded = expanded
            self.updateGeometry()
            self.update()

    def _target_size(self) -> QSize:
        if self._pixmap.isNull():
            return QSize(160, 56)
        max_h = EXPANDED_IMAGE_MAX_H if self._expanded else COLLAPSED_IMAGE_MAX_H
        avail_w = max(180, (self.width() - 16) if self.width() > 100 else 400)
        max_w = avail_w if self._expanded else min(avail_w, 280)
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(max_w / max(1, pw), max_h / max(1, ph), 1.0)
        return QSize(max(24, int(pw * scale)), max(24, int(ph * scale)))

    def sizeHint(self) -> QSize:
        ts = self._target_size()
        return QSize(ts.width() + 8, ts.height() + 8)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self.updateGeometry()

    def _scaled_pixmap(self, ts: QSize, dpr: float) -> QPixmap:
        key = (int(ts.width() * dpr), int(ts.height() * dpr))
        if self._scaled_cache is None or self._scaled_cache[0] != key:
            scaled = self._pixmap.scaled(
                key[0], key[1], Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            scaled.setDevicePixelRatio(dpr)
            self._scaled_cache = (key, scaled)
        return self._scaled_cache[1]

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        ts = self._target_size()
        x = round((self.width() - ts.width()) / 2.0)
        y = round((self.height() - ts.height()) / 2.0)
        box = QRectF(x, y, ts.width(), ts.height())
        radius = 8.0

        if self._pixmap.isNull():
            painter.setPen(QPen(rgba("#ffffff", 0.10), 1.0, Qt.PenStyle.DashLine))
            painter.setBrush(rgba("#ffffff", 0.02))
            painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
            painter.setFont(ui_font(12))
            painter.setPen(QColor(Palette.TEXT_3))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "图片文件不存在")
            painter.end()
            return

        # 柔和投影
        painter.setPen(Qt.PenStyle.NoPen)
        for i, a in ((3, 0.10), (2, 0.14), (1, 0.20)):
            painter.setBrush(QColor(0, 0, 0, int(255 * a)))
            painter.drawRoundedRect(box.adjusted(-i * 0.5, i * 0.4, i * 0.5, i * 1.0), radius + i, radius + i)

        clip = QPainterPath()
        clip.addRoundedRect(box, radius, radius)
        painter.save()
        painter.setClipPath(clip)
        # 透明背景棋盘格
        painter.fillRect(box, QColor("#202228"))
        cell = 6
        dark = QColor("#191a1f")
        yy = int(box.top())
        row = 0
        while yy < box.bottom():
            xx = int(box.left()) + (cell if row % 2 else 0)
            while xx < box.right():
                painter.fillRect(QRectF(xx, yy, cell, cell), dark)
                xx += cell * 2
            yy += cell
            row += 1
        painter.drawPixmap(QPointF(x, y), self._scaled_pixmap(ts, self.devicePixelRatioF()))
        painter.restore()

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(rgba("#ffffff", 0.09), 1.0))
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        painter.end()


class _FilesListWidget(QWidget):
    """纵向展示彩色文件类型图标 + 文件名 + 所在目录的列表组件。"""

    ROW_H = 24
    ROW_GAP = 2

    def __init__(self, file_paths: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._files = file_paths
        self._expanded = False
        self._name_font = ui_font(13)
        self._dir_font = ui_font(12)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    @property
    def has_overflow(self) -> bool:
        return len(self._files) > COLLAPSED_FILES_MAX

    def _visible_files(self) -> list[str]:
        return self._files if self._expanded else self._files[:COLLAPSED_FILES_MAX]

    def set_expanded(self, expanded: bool) -> None:
        if self._expanded != expanded:
            self._expanded = expanded
            self.updateGeometry()
            self.update()

    def sizeHint(self) -> QSize:
        n = max(1, len(self._visible_files()))
        return QSize(300, n * self.ROW_H + (n - 1) * self.ROW_GAP)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        name_fm = QFontMetrics(self._name_font)
        dir_fm = QFontMetrics(self._dir_font)
        icon_size = 18
        text_x = 28
        avail = max(40, self.width() - text_x - 4)

        y = 0
        for fp in self._visible_files():
            norm = fp.rstrip("\\/")
            name = os.path.basename(norm) or fp
            parent_dir = os.path.dirname(norm)
            is_dir = not os.path.splitext(name)[1]
            painter.drawPixmap(2, y + (self.ROW_H - icon_size) // 2, make_file_item_pixmap(name, icon_size, is_dir))

            name_txt = name_fm.elidedText(name, Qt.TextElideMode.ElideMiddle, avail)
            name_w = name_fm.horizontalAdvance(name_txt)
            painter.setFont(self._name_font)
            painter.setPen(QColor(Palette.TEXT))
            painter.drawText(
                QRectF(text_x, y, name_w + 2, self.ROW_H),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                name_txt,
            )

            remain = avail - name_w - 12
            if parent_dir and remain > 48:
                dir_txt = dir_fm.elidedText(parent_dir, Qt.TextElideMode.ElideMiddle, remain)
                painter.setFont(self._dir_font)
                painter.setPen(QColor(Palette.TEXT_3))
                painter.drawText(
                    QRectF(text_x + name_w + 12, y + 1, remain, self.ROW_H),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    dir_txt,
                )
            y += self.ROW_H + self.ROW_GAP
        painter.end()


class _MetaLabel(QLabel):
    """
    右下角元数据：`meta_info` + 序号键帽 + 多选顺序徽标。

    text() 始终保持纯文本 `"{meta}    {index}"`（多选时追加圆圈序号），
    实际视觉由 paintEvent 精细绘制。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._meta = ""
        self._index = 0
        self._order: int | None = None
        self._font = ui_font(12)
        self._idx_font = ui_font(11, QFont.Weight.DemiBold)
        self._badge_font = make_badge_font(10)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(20)

    def set_parts(self, meta: str, index: int, order: int | None) -> None:
        self._meta, self._index, self._order = meta, index, order
        base = f"{meta}    {index}" if meta else str(index)
        if order is not None:
            badge = _CIRCLED_NUMBERS[order - 1] if 1 <= order <= len(_CIRCLED_NUMBERS) else f"[{order}]"
            base = f"{base}  {badge}"
        self.setText(base)
        self.updateGeometry()
        self.update()

    def _chip_width(self) -> int:
        return max(18, QFontMetrics(self._idx_font).horizontalAdvance(str(self._index)) + 10)

    def sizeHint(self) -> QSize:
        w = self._chip_width()
        if self._meta:
            w += QFontMetrics(self._font).horizontalAdvance(self._meta) + 8
        if self._order is not None:
            w += 18 + 6
        return QSize(w + 2, 20)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        h = float(self.height())
        x = float(self.width())

        if self._order is not None:
            d = 18.0
            rect = QRectF(x - d, (h - d) / 2, d, d)
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            grad.setColorAt(0, QColor(Palette.ACCENT_HI))
            grad.setColorAt(1, QColor(Palette.ACCENT_LO))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(rgba(Palette.ACCENT, 0.22))
            p.drawEllipse(rect.adjusted(-2, -2, 2, 2))
            p.setBrush(grad)
            p.drawEllipse(rect)
            p.setFont(self._badge_font)
            p.setPen(QColor(Palette.ON_ACCENT))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(self._order))
            x -= d + 6

        cw = float(self._chip_width())
        chip = QRectF(x - cw, (h - 16) / 2, cw, 16)
        selected = self._order is not None
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(rgba(Palette.ACCENT, 0.14) if selected else rgba("#ffffff", 0.05))
        p.drawRoundedRect(chip, 5, 5)
        p.setFont(self._idx_font)
        p.setPen(QColor(Palette.ACCENT if selected else Palette.TEXT_3))
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, str(self._index))
        x -= cw + 8

        if self._meta:
            p.setFont(self._font)
            p.setPen(QColor(Palette.TEXT_3))
            p.drawText(QRectF(0, 0, max(0.0, x), h), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._meta)
        p.end()


class ClipboardItemCard(QFrame):
    """单条剪贴板记录卡片。"""

    #: 用户左键单击卡片（参数为 item_id）
    card_clicked = Signal(int)
    #: 展开/收起状态变化
    expand_toggled = Signal(int, bool)

    def __init__(
        self,
        item: Mapping[str, Any],
        index: int,
        data_dir: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.item = dict(item)
        self.item_id: int = int(item["id"])
        self.item_type: str = str(item["item_type"])
        self.index: int = index
        self._data_dir = data_dir or paths.get_data_dir()

        self._selected = False
        self._select_order: int | None = None
        self._hovered = False
        self._hover_t = 0.0
        self._hover_anim: QVariantAnimation | None = None
        self._expanded = False

        self.setObjectName("ClipboardItemCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 13, 14, 9)
        root.setSpacing(8)

        # 1. 上方内容区
        self._text_widget: _CollapsibleTextWidget | None = None
        self._image_widget: _ImagePreviewWidget | None = None
        self._files_widget: _FilesListWidget | None = None

        content_widget, can_expand = self._build_content_widget()
        root.addWidget(content_widget)

        # 2. 底部元数据行
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(6)

        # 左下角：类型图标 + 相对时间 + 收藏金星
        left = QWidget(self)
        left.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(5)

        type_key = self.item_type if self.item_type in (ITEM_TEXT, ITEM_IMAGE, ITEM_FILES) else ITEM_TEXT
        self._type_icon = QLabel(left)
        self._type_icon.setFixedSize(12, 12)
        self._type_icon.setPixmap(make_tab_pixmap(type_key, Palette.TEXT_3, 12))

        self._time_label = QLabel(left)
        self._time_label.setStyleSheet(
            f"color: {Palette.TEXT_3}; font-family: {FONT_CSS}; font-size: 12px; background: transparent;"
        )

        self._fav_icon = QLabel(left)
        self._fav_icon.setFixedSize(12, 12)
        self._fav_icon.setPixmap(make_tab_pixmap("favorite", Palette.GOLD, 12, filled=True))
        self._fav_icon.setToolTip("已收藏")

        left_layout.addWidget(self._type_icon)
        left_layout.addWidget(self._time_label)
        left_layout.addWidget(self._fav_icon)
        left_layout.addStretch(1)
        self.refresh_relative_time()
        meta_row.addWidget(left, 1)

        # 正中间：展开 / 收起 胶囊按钮
        self._expand_btn = QPushButton(self)
        self._expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._expand_btn.setFixedHeight(20)
        self._expand_btn.setIconSize(QSize(11, 11))
        self._expand_btn.setStyleSheet(
            f"""
            QPushButton {{
                color: {Palette.TEXT_3};
                background: transparent;
                border: none;
                border-radius: 10px;
                font-family: {FONT_CSS};
                font-size: 12px;
                padding: 0px 9px 0px 7px;
            }}
            QPushButton:hover {{
                color: {Palette.TEXT};
                background-color: {css_rgba('#ffffff', 0.07)};
            }}
            QPushButton:pressed {{
                background-color: {css_rgba('#ffffff', 0.11)};
            }}
            """
        )
        self._expand_btn.clicked.connect(self._on_expand_clicked)
        self._update_expand_button_ui()
        self._expand_btn.setVisible(can_expand)
        meta_row.addWidget(self._expand_btn, 0, Qt.AlignmentFlag.AlignCenter)

        # 右下角：meta_info + 序号 + 多选顺序徽标
        self._right_meta_label = _MetaLabel(self)
        self._update_right_meta_text()
        right_wrap = QHBoxLayout()
        right_wrap.setContentsMargins(0, 0, 0, 0)
        right_wrap.addStretch(1)
        right_wrap.addWidget(self._right_meta_label)
        meta_row.addLayout(right_wrap, 1)

        root.addLayout(meta_row)

        if self._text_widget is not None:
            self._text_widget.overflow_changed.connect(self._expand_btn.setVisible)

    # ------------------------------------------------------------------ 内容构建
    def _build_content_widget(self) -> tuple[QWidget, bool]:
        content = str(self.item.get("content", ""))
        if self.item_type == ITEM_IMAGE:
            img_path = paths.resolve_data_path(content, self._data_dir)
            pixmap = QPixmap(str(img_path)) if img_path.exists() else QPixmap()
            self._image_widget = _ImagePreviewWidget(pixmap, self)
            return self._image_widget, self._image_widget.is_large_image

        if self.item_type == ITEM_FILES:
            try:
                raw_files = json.loads(content)
                files = [str(p) for p in raw_files] if isinstance(raw_files, list) else [content]
            except (TypeError, ValueError):
                files = [content] if content else []
            self._files_widget = _FilesListWidget(files, self)
            return self._files_widget, self._files_widget.has_overflow

        self._text_widget = _CollapsibleTextWidget(content, self)
        # 初始估算是否超过 3 行
        _, overflow = self._text_widget._layout_lines(410)
        return self._text_widget, overflow

    # ------------------------------------------------------------------ 状态更新
    def refresh_relative_time(self, now: float | None = None) -> None:
        rel = format_relative_time(float(self.item.get("created_at", time.time())), now=now)
        self._time_label.setText(rel)
        self._fav_icon.setVisible(bool(self.item.get("is_favorite")))

    def set_favorite_state(self, is_favorite: bool) -> None:
        self.item["is_favorite"] = int(bool(is_favorite))
        self.refresh_relative_time()

    def set_selected(self, selected: bool, order_index: int | None = None, total_selected: int = 0) -> None:
        """设置卡片选中高亮状态与多选顺序角标（order_index 从 1 开始）。"""
        effective_order = order_index if (selected and total_selected > 1) else None
        changed = (
            self._selected != selected
            or self._select_order != effective_order
        )
        self._selected = selected
        self._select_order = effective_order
        if changed:
            self._update_right_meta_text()
            self.update()

    @property
    def is_selected(self) -> bool:
        return self._selected

    @property
    def select_order(self) -> int | None:
        return self._select_order

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    def _update_right_meta_text(self) -> None:
        meta = str(self.item.get("meta_info", ""))
        self._right_meta_label.set_parts(meta, self.index, self._select_order)

    def _update_expand_button_ui(self) -> None:
        self._expand_btn.setIcon(make_chevron_icon(self._expanded, Palette.TEXT_3, 11))
        self._expand_btn.setText(" 收起" if self._expanded else " 展开")

    def _on_expand_clicked(self) -> None:
        self._expanded = not self._expanded
        self._update_expand_button_ui()
        if self._text_widget is not None:
            self._text_widget.set_expanded(self._expanded)
        elif self._image_widget is not None:
            self._image_widget.set_expanded(self._expanded)
        elif self._files_widget is not None:
            self._files_widget.set_expanded(self._expanded)
        self.updateGeometry()
        self.expand_toggled.emit(self.item_id, self._expanded)

    # ------------------------------------------------------------------ 事件与绘制
    def _animate_hover(self, target: float) -> None:
        if self._hover_anim is None:
            self._hover_anim = QVariantAnimation(self)
            self._hover_anim.setDuration(160)
            self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._hover_anim.valueChanged.connect(self._on_hover_value)
        self._hover_anim.stop()
        self._hover_anim.setStartValue(float(self._hover_t))
        self._hover_anim.setEndValue(float(target))
        self._hover_anim.start()

    def _on_hover_value(self, value: float) -> None:
        self._hover_t = float(value)
        self.update()

    def enterEvent(self, event) -> None:  # noqa: ANN001
        self._hovered = True
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001
        self._hovered = False
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # 若点击在展开按钮区域内，由按钮自身处理，不触发卡片选中切换
            if self._expand_btn.isVisible() and self._expand_btn.geometry().contains(event.position().toPoint()):
                super().mousePressEvent(event)
                return
            self.card_clicked.emit(self.item_id)
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        body = QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0)
        r = CARD_RADIUS
        t = self._hover_t

        if self._selected:
            # 外发光
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(rgba(Palette.ACCENT, 0.16), 3.0))
            painter.drawRoundedRect(body.adjusted(-0.5, -0.5, 0.5, 0.5), r + 0.5, r + 0.5)
            # 微蓝底 + 顶部柔光
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(mix(Palette.CARD_SELECTED, "#1e2738", t))
            painter.drawRoundedRect(body, r, r)
            sheen = QLinearGradient(body.topLeft(), QPointF(body.left(), body.top() + 36))
            sheen.setColorAt(0, rgba(Palette.ACCENT, 0.07))
            sheen.setColorAt(1, rgba(Palette.ACCENT, 0.0))
            painter.setBrush(sheen)
            painter.drawRoundedRect(body, r, r)
            # 品牌蓝描边
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(Palette.ACCENT), 1.4))
            painter.drawRoundedRect(body.adjusted(0.7, 0.7, -0.7, -0.7), r - 0.7, r - 0.7)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(mix(Palette.CARD, Palette.CARD_HOVER, t))
            painter.drawRoundedRect(body, r, r)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(rgba("#ffffff", 0.045 + 0.04 * t), 1.0))
            painter.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), r - 0.5, r - 0.5)

        painter.end()
