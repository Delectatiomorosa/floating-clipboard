"""
矢量图标绘制模块（QPainter 高分屏抗锯齿绘制）。

统一的线性图标语言：1.5px 圆头描边、圆角转折、16/18/20 网格对齐，
所有图标均在逻辑坐标系中绘制，并以 ≥2x 设备像素比渲染，保证在任何缩放下都清晰锐利。
"""

from __future__ import annotations

import math
import os
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)

from .theme import Palette, rgba

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tiff", ".heic"}
_EXT_GROUPS: dict[str, tuple[set[str], str]] = {
    "image": (IMAGE_EXTS, "#4fb3f6"),
    "video": ({".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm"}, "#b07cf7"),
    "audio": ({".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"}, "#f27fb2"),
    "archive": ({".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}, "#e9b44c"),
    "pdf": ({".pdf"}, "#f2776b"),
    "doc": ({".doc", ".docx", ".rtf", ".odt", ".md", ".txt"}, "#5b8def"),
    "sheet": ({".xls", ".xlsx", ".csv", ".ods"}, "#4cc38a"),
    "slide": ({".ppt", ".pptx", ".key", ".odp"}, "#f39a4c"),
    "code": (
        {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs",
         ".html", ".css", ".json", ".xml", ".yaml", ".yml", ".sql", ".sh", ".ps1", ".bat"},
        "#8f8cf5",
    ),
    "app": ({".exe", ".msi", ".dll", ".lnk", ".apk"}, "#8a94a6"),
}


# ---------------------------------------------------------------------- 基础工具
def _dpr() -> float:
    app = QGuiApplication.instance()
    screen = app.primaryScreen() if app else None
    return max(screen.devicePixelRatio() if screen else 2.0, 2.0)


def _make_canvas(size: int) -> tuple[QPixmap, QPainter]:
    dpr = _dpr()
    px = QPixmap(int(math.ceil(size * dpr)), int(math.ceil(size * dpr)))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    return px, painter


def _pen(color: QColor | str, width: float = 1.5) -> QPen:
    return QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def _star_polygon(cx: float, cy: float, r_outer: float, r_inner: float, points: int = 5) -> QPolygonF:
    poly = QPolygonF()
    for i in range(points * 2):
        r = r_outer if i % 2 == 0 else r_inner
        angle = -math.pi / 2 + i * math.pi / points
        poly.append(QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return poly


def _draw_star(p: QPainter, c: QColor, cx: float, cy: float, r: float, filled: bool, width: float = 1.4) -> None:
    poly = _star_polygon(cx, cy, r, r * 0.45)
    if filled:
        p.setPen(_pen(c, width))
        p.setBrush(c)
    else:
        p.setPen(_pen(c, width))
        p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolygon(poly)


def _clipboard_glyph(p: QPainter, rect: QRectF, color: QColor, stroke: float) -> None:
    """在 rect 中绘制剪贴板线性字形（应用图标、空状态等复用）。"""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    p.setPen(_pen(color, stroke))
    p.setBrush(Qt.BrushStyle.NoBrush)
    board = QRectF(x + w * 0.16, y + h * 0.14, w * 0.68, h * 0.78)
    p.drawRoundedRect(board, w * 0.12, w * 0.12)
    clip = QRectF(x + w * 0.33, y + h * 0.04, w * 0.34, h * 0.18)
    p.setBrush(color)
    p.drawRoundedRect(clip, w * 0.06, w * 0.06)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(x + w * 0.32, y + h * 0.46), QPointF(x + w * 0.68, y + h * 0.46))
    p.drawLine(QPointF(x + w * 0.32, y + h * 0.64), QPointF(x + w * 0.56, y + h * 0.64))


# ---------------------------------------------------------------------- 应用图标
def _paint_app_mark(p: QPainter, s: float) -> None:
    grad = QLinearGradient(0, 0, s, s)
    grad.setColorAt(0.0, QColor("#a3c4fb"))
    grad.setColorAt(1.0, QColor("#6c86f5"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    body = QRectF(s * 0.04, s * 0.04, s * 0.92, s * 0.92)
    p.drawRoundedRect(body, s * 0.26, s * 0.26)
    # 顶部高光
    hl = QLinearGradient(0, body.top(), 0, body.top() + s * 0.5)
    hl.setColorAt(0.0, QColor(255, 255, 255, 70))
    hl.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setBrush(hl)
    p.drawRoundedRect(body, s * 0.26, s * 0.26)
    _clipboard_glyph(p, QRectF(s * 0.2, s * 0.17, s * 0.6, s * 0.66), QColor("#ffffff"), max(1.15, s * 0.075))


def make_capsule_clipboard_pixmap(size: int = 16) -> QPixmap:
    """顶部品牌胶囊左侧的渐变剪贴板标志。"""
    px, p = _make_canvas(size)
    _paint_app_mark(p, float(size))
    p.end()
    return px


def make_app_icon(size: int = 32) -> QIcon:
    """系统托盘与主窗口使用的圆角剪贴板应用图标（优先从内置资源 assets/icon.ico 加载）。"""
    from core import paths

    ico_path = paths.resource_path("assets", "icon.ico")
    if ico_path.exists():
        ico = QIcon(str(ico_path))
        if not ico.isNull():
            return ico

    icon = QIcon()
    for s in sorted({16, 20, 24, 32, 48, 64, size}):
        px = QPixmap(s, s)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _paint_app_mark(p, float(s))
        p.end()
        icon.addPixmap(px)
    return icon


# ---------------------------------------------------------------------- 分类标签图标
def make_tab_icon(kind: str, color: str = "#a0a0a0", size: int = 16, filled: bool = False) -> QIcon:
    """分类图标：all / text / image / files / favorite（16 网格绘制，可缩放到任意尺寸）。"""
    return QIcon(make_tab_pixmap(kind, color, size, filled))


def make_tab_pixmap(kind: str, color: str = "#a0a0a0", size: int = 16, filled: bool = False) -> QPixmap:
    px, p = _make_canvas(size)
    p.scale(size / 16.0, size / 16.0)
    c = QColor(color)
    p.setPen(_pen(c, 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "all":
        # 2x2 圆角宫格
        for (x, y) in ((2.2, 2.2), (9.0, 2.2), (2.2, 9.0), (9.0, 9.0)):
            r = QRectF(x, y, 4.8, 4.8)
            if filled and (x, y) == (2.2, 2.2):
                p.setBrush(c)
                p.drawRoundedRect(r, 1.4, 1.4)
                p.setBrush(Qt.BrushStyle.NoBrush)
            else:
                p.drawRoundedRect(r, 1.4, 1.4)

    elif kind == "text":
        # 衬线 T 字形
        p.drawLine(QPointF(3.0, 4.6), QPointF(3.0, 3.0))
        p.drawLine(QPointF(3.0, 3.0), QPointF(13.0, 3.0))
        p.drawLine(QPointF(13.0, 3.0), QPointF(13.0, 4.6))
        p.drawLine(QPointF(8.0, 3.0), QPointF(8.0, 13.2))
        p.drawLine(QPointF(6.0, 13.2), QPointF(10.0, 13.2))

    elif kind == "image":
        frame = QRectF(2.0, 2.4, 12.0, 11.2)
        clip = QPainterPath()
        clip.addRoundedRect(frame, 2.4, 2.4)
        if filled:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(rgba(color, 0.22))
            p.drawPath(clip)
            p.setPen(_pen(c, 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(frame, 2.4, 2.4)
        p.drawEllipse(QPointF(5.9, 6.2), 1.25, 1.25)
        p.save()
        p.setClipPath(clip)
        m = QPainterPath()
        m.moveTo(14.4, 10.2)
        m.lineTo(11.6, 7.6)
        m.lineTo(3.0, 14.4)
        p.drawPath(m)
        p.restore()

    elif kind == "files":
        doc = QPainterPath()
        doc.moveTo(9.6, 1.9)
        doc.lineTo(5.0, 1.9)
        doc.quadTo(3.4, 1.9, 3.4, 3.5)
        doc.lineTo(3.4, 12.6)
        doc.quadTo(3.4, 14.2, 5.0, 14.2)
        doc.lineTo(11.2, 14.2)
        doc.quadTo(12.8, 14.2, 12.8, 12.6)
        doc.lineTo(12.8, 5.1)
        doc.closeSubpath()
        if filled:
            p.setBrush(rgba(color, 0.22))
        p.drawPath(doc)
        p.setBrush(Qt.BrushStyle.NoBrush)
        fold = QPainterPath()
        fold.moveTo(9.6, 1.9)
        fold.lineTo(9.6, 5.1)
        fold.lineTo(12.8, 5.1)
        p.drawPath(fold)
        p.drawLine(QPointF(5.9, 8.4), QPointF(10.3, 8.4))
        p.drawLine(QPointF(5.9, 11.0), QPointF(8.8, 11.0))

    elif kind == "favorite":
        _draw_star(p, c, 8.0, 8.4, 6.3, filled, 1.35)

    p.end()
    return px


# ---------------------------------------------------------------------- 文件类型图标
def _file_group(filename: str, is_dir: bool = False) -> tuple[str, str]:
    if is_dir:
        return "folder", "#e8b45a"
    ext = os.path.splitext(filename)[1].lower()
    for group, (exts, color) in _EXT_GROUPS.items():
        if ext in exts:
            return group, color
    return "file", "#8a94a6"


@lru_cache(maxsize=64)
def _file_pixmap_cached(group: str, color: str, size: int) -> QPixmap:
    px, p = _make_canvas(size)
    p.scale(size / 18.0, size / 18.0)
    base = QColor(color)

    if group == "folder":
        back = QPainterPath()
        back.moveTo(1.8, 4.4)
        back.quadTo(1.8, 3.0, 3.2, 3.0)
        back.lineTo(6.6, 3.0)
        back.lineTo(8.2, 4.6)
        back.lineTo(14.8, 4.6)
        back.quadTo(16.2, 4.6, 16.2, 6.0)
        back.lineTo(16.2, 13.6)
        back.quadTo(16.2, 15.0, 14.8, 15.0)
        back.lineTo(3.2, 15.0)
        back.quadTo(1.8, 15.0, 1.8, 13.6)
        back.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(base.darker(125))
        p.drawPath(back)
        front = QLinearGradient(0, 6, 0, 15)
        front.setColorAt(0, base.lighter(112))
        front.setColorAt(1, base)
        p.setBrush(front)
        p.drawRoundedRect(QRectF(1.8, 6.2, 14.4, 8.8), 1.4, 1.4)
        p.end()
        return px

    body = QPainterPath()
    body.moveTo(11.0, 1.2)
    body.lineTo(4.6, 1.2)
    body.quadTo(3.0, 1.2, 3.0, 2.8)
    body.lineTo(3.0, 15.2)
    body.quadTo(3.0, 16.8, 4.6, 16.8)
    body.lineTo(13.4, 16.8)
    body.quadTo(15.0, 16.8, 15.0, 15.2)
    body.lineTo(15.0, 5.2)
    body.closeSubpath()
    grad = QLinearGradient(0, 1.2, 0, 16.8)
    grad.setColorAt(0, base.lighter(115))
    grad.setColorAt(1, base.darker(108))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawPath(body)

    fold = QPainterPath()
    fold.moveTo(11.0, 1.2)
    fold.lineTo(11.0, 3.9)
    fold.quadTo(11.0, 5.2, 12.3, 5.2)
    fold.lineTo(15.0, 5.2)
    fold.closeSubpath()
    p.setBrush(QColor(255, 255, 255, 120))
    p.drawPath(fold)

    white = QColor(255, 255, 255, 235)
    if group == "image":
        p.setBrush(white)
        p.drawEllipse(QPointF(7.0, 8.6), 1.15, 1.15)
        m = QPainterPath()
        m.moveTo(5.0, 14.2)
        m.lineTo(8.4, 10.6)
        m.lineTo(10.2, 12.2)
        m.lineTo(11.4, 11.0)
        m.lineTo(13.0, 14.2)
        m.closeSubpath()
        p.drawPath(m)
    elif group == "video":
        p.setBrush(white)
        tri = QPolygonF([QPointF(7.4, 8.4), QPointF(12.0, 11.0), QPointF(7.4, 13.6)])
        p.setPen(_pen(white, 0.8))
        p.drawPolygon(tri)
    elif group == "audio":
        p.setPen(_pen(white, 1.3))
        p.drawLine(QPointF(10.6, 8.0), QPointF(10.6, 12.6))
        p.drawLine(QPointF(10.6, 8.0), QPointF(12.6, 8.8))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(white)
        p.drawEllipse(QPointF(9.2, 12.9), 1.5, 1.25)
    elif group == "archive":
        p.setBrush(white)
        for i, yy in enumerate((6.4, 8.2, 10.0)):
            xx = 8.0 if i % 2 == 0 else 9.4
            p.drawRect(QRectF(xx, yy, 1.4, 1.4))
        p.drawRoundedRect(QRectF(7.6, 11.6, 2.8, 2.6), 0.6, 0.6)
    elif group == "code":
        p.setPen(_pen(white, 1.3))
        p.drawPolyline(QPolygonF([QPointF(7.6, 9.0), QPointF(5.8, 11.0), QPointF(7.6, 13.0)]))
        p.drawPolyline(QPolygonF([QPointF(10.4, 9.0), QPointF(12.2, 11.0), QPointF(10.4, 13.0)]))
    elif group == "pdf":
        p.setPen(_pen(white, 1.3))
        p.drawLine(QPointF(6.0, 9.2), QPointF(12.0, 9.2))
        p.drawLine(QPointF(6.0, 11.6), QPointF(12.0, 11.6))
        p.drawLine(QPointF(6.0, 14.0), QPointF(9.6, 14.0))
    elif group == "sheet":
        p.setPen(_pen(white, 1.1))
        p.drawRect(QRectF(5.8, 8.4, 6.4, 5.6))
        p.drawLine(QPointF(5.8, 11.2), QPointF(12.2, 11.2))
        p.drawLine(QPointF(9.0, 8.4), QPointF(9.0, 14.0))
    else:
        p.setPen(_pen(white, 1.3))
        p.drawLine(QPointF(6.0, 9.2), QPointF(12.0, 9.2))
        p.drawLine(QPointF(6.0, 11.6), QPointF(12.0, 11.6))
        p.drawLine(QPointF(6.0, 14.0), QPointF(9.6, 14.0))
    p.end()
    return px


def make_file_item_pixmap(filename: str, size: int = 18, is_dir: bool = False) -> QPixmap:
    """文件列表项左侧的彩色文件类型图标（按扩展名着色：图片/文档/表格/压缩包/代码……）。"""
    group, color = _file_group(filename, is_dir)
    return _file_pixmap_cached(group, color, size)


# ---------------------------------------------------------------------- 主操作按钮图标
def make_action_icon(kind: str, color: str = "#1e1e1e", size: int = 20) -> QIcon:
    """右侧主操作按钮内部图标：copy / send / favorite。"""
    px, p = _make_canvas(size)
    p.scale(size / 20.0, size / 20.0)
    c = QColor(color)

    if kind == "copy":
        stroke = 1.6
        front = QRectF(7.2, 7.2, 9.6, 9.6)
        back = QRectF(3.2, 3.2, 9.6, 9.6)
        back_path = QPainterPath()
        back_path.addRoundedRect(back, 2.2, 2.2)
        stroker = QPainterPathStroker()
        stroker.setWidth(stroke)
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        back_outline = stroker.createStroke(back_path)
        gap = QPainterPath()
        gap.addRoundedRect(front.adjusted(-1.9, -1.9, 1.9, 1.9), 3.4, 3.4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPath(back_outline.subtracted(gap))
        p.setPen(_pen(c, stroke))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(front, 2.2, 2.2)

    elif kind == "send":
        plane = QPainterPath()
        plane.moveTo(3.4, 3.6)
        plane.lineTo(17.2, 10.0)
        plane.lineTo(3.4, 16.4)
        plane.lineTo(5.6, 10.8)
        plane.lineTo(10.6, 10.0)
        plane.lineTo(5.6, 9.2)
        plane.closeSubpath()
        p.setPen(_pen(c, 1.2))
        p.setBrush(c)
        p.drawPath(plane)

    elif kind == "favorite":
        _draw_star(p, c, 10.0, 10.5, 7.0, True, 1.6)

    p.end()
    return QIcon(px)


# ---------------------------------------------------------------------- 工具按钮图标
def _pin_path() -> QPainterPath:
    """18 网格内的直立图钉轮廓。"""
    path = QPainterPath()
    path.moveTo(6.6, 2.0)
    path.lineTo(11.4, 2.0)
    path.quadTo(12.6, 2.0, 12.6, 3.2)
    path.quadTo(12.6, 4.3, 11.4, 4.3)
    path.lineTo(11.2, 4.3)
    path.lineTo(11.2, 7.9)
    path.quadTo(11.2, 8.5, 11.7, 8.9)
    path.lineTo(13.2, 10.1)
    path.quadTo(13.8, 10.6, 13.8, 11.4)
    path.lineTo(13.8, 11.9)
    path.quadTo(13.8, 12.4, 13.3, 12.4)
    path.lineTo(4.7, 12.4)
    path.quadTo(4.2, 12.4, 4.2, 11.9)
    path.lineTo(4.2, 11.4)
    path.quadTo(4.2, 10.6, 4.8, 10.1)
    path.lineTo(6.3, 8.9)
    path.quadTo(6.8, 8.5, 6.8, 7.9)
    path.lineTo(6.8, 4.3)
    path.lineTo(6.6, 4.3)
    path.quadTo(5.4, 4.3, 5.4, 3.2)
    path.quadTo(5.4, 2.0, 6.6, 2.0)
    path.closeSubpath()
    return path


def make_tool_icon(kind: str, color: str = "#dadce0", size: int = 18, filled: bool = False) -> QIcon:
    """右侧下方工具图标：pin_window / settings / clear / power / show。"""
    px, p = _make_canvas(size)
    p.scale(size / 18.0, size / 18.0)
    c = QColor(color)
    p.setPen(_pen(c, 1.45))
    p.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "pin_window":
        # 图钉：未固定时倾斜 45° 线性；固定后直立并实心填充
        if not filled:
            p.translate(9.0, 9.0)
            p.rotate(40.0)
            p.translate(-9.0, -9.0)
            p.setBrush(Qt.BrushStyle.NoBrush)
        else:
            p.setBrush(c)
        p.drawPath(_pin_path())
        p.drawLine(QPointF(9.0, 12.4), QPointF(9.0, 16.4))

    elif kind == "settings":
        cx, cy = 9.0, 9.0
        teeth = 8
        r_out, r_in = 7.3, 5.5
        gear = QPainterPath()
        for i in range(teeth):
            base = i * 2 * math.pi / teeth
            pts = [
                (r_in, base - 0.29),
                (r_out, base - 0.15),
                (r_out, base + 0.15),
                (r_in, base + 0.29),
            ]
            for j, (r, a) in enumerate(pts):
                pt = QPointF(cx + r * math.cos(a), cy + r * math.sin(a))
                if i == 0 and j == 0:
                    gear.moveTo(pt)
                else:
                    gear.lineTo(pt)
        gear.closeSubpath()
        p.setPen(_pen(c, 1.35))
        p.drawPath(gear)
        p.drawEllipse(QPointF(cx, cy), 2.3, 2.3)

    elif kind == "clear":
        # 线性扫帚
        p.drawLine(QPointF(9.0, 1.8), QPointF(9.0, 7.0))
        p.drawRoundedRect(QRectF(5.6, 7.0, 6.8, 2.6), 1.0, 1.0)
        skirt = QPainterPath()
        skirt.moveTo(5.4, 9.6)
        skirt.lineTo(4.2, 15.8)
        skirt.lineTo(13.8, 15.8)
        skirt.lineTo(12.6, 9.6)
        p.drawPath(skirt)
        p.drawLine(QPointF(7.4, 12.6), QPointF(7.1, 15.8))
        p.drawLine(QPointF(10.6, 12.6), QPointF(10.9, 15.8))

    elif kind == "power":
        p.drawArc(QRectF(3.2, 3.6, 11.6, 11.6), 60 * 16, -300 * 16)
        p.drawLine(QPointF(9.0, 2.2), QPointF(9.0, 8.4))

    elif kind == "show":
        p.drawRoundedRect(QRectF(2.4, 3.2, 13.2, 11.6), 2.4, 2.4)
        p.drawLine(QPointF(2.4, 6.6), QPointF(15.6, 6.6))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(4.7, 4.9), 0.7, 0.7)
        p.drawEllipse(QPointF(6.7, 4.9), 0.7, 0.7)

    p.end()
    return QIcon(px)


# ---------------------------------------------------------------------- 杂项
def make_chevron_icon(expanded: bool, color: str = "#888888", size: int = 12) -> QIcon:
    """卡片底部 '展开' / '收起' 箭头图标。"""
    px, p = _make_canvas(size)
    p.scale(size / 12.0, size / 12.0)
    p.setPen(_pen(color, 1.4))
    if expanded:
        p.drawPolyline(QPolygonF([QPointF(2.8, 7.6), QPointF(6.0, 4.4), QPointF(9.2, 7.6)]))
    else:
        p.drawPolyline(QPolygonF([QPointF(2.8, 4.6), QPointF(6.0, 7.8), QPointF(9.2, 4.6)]))
    p.end()
    return QIcon(px)


def make_search_pixmap(color: str = Palette.TEXT_3, size: int = 16) -> QPixmap:
    px, p = _make_canvas(size)
    p.scale(size / 16.0, size / 16.0)
    p.setPen(_pen(color, 1.5))
    p.drawEllipse(QPointF(7.0, 7.0), 4.7, 4.7)
    p.drawLine(QPointF(10.5, 10.5), QPointF(13.6, 13.6))
    p.end()
    return px


def make_close_pixmap(color: str = Palette.TEXT_2, size: int = 10) -> QPixmap:
    px, p = _make_canvas(size)
    p.scale(size / 10.0, size / 10.0)
    p.setPen(_pen(color, 1.4))
    p.drawLine(QPointF(2.6, 2.6), QPointF(7.4, 7.4))
    p.drawLine(QPointF(7.4, 2.6), QPointF(2.6, 7.4))
    p.end()
    return px


def make_toast_pixmap(kind: str, size: int = 18) -> QPixmap:
    """Toast 左侧状态徽标：success / info / star / pin / clear。"""
    px, p = _make_canvas(size)
    p.scale(size / 18.0, size / 18.0)
    colors = {
        "success": Palette.ACCENT,
        "info": Palette.GOLD,
        "star": Palette.GOLD,
        "pin": Palette.ACCENT,
        "clear": Palette.ACCENT,
    }
    base = QColor(colors.get(kind, Palette.ACCENT))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(rgba(base.name(), 0.18))
    p.drawEllipse(QRectF(0.5, 0.5, 17.0, 17.0))
    ink = base
    if kind == "info":
        p.setPen(_pen(ink, 1.7))
        p.drawLine(QPointF(9.0, 8.2), QPointF(9.0, 12.6))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        p.drawEllipse(QPointF(9.0, 5.6), 1.05, 1.05)
    elif kind == "star":
        _draw_star(p, ink, 9.0, 9.4, 4.9, True, 1.2)
    elif kind == "pin":
        p.translate(4.5, 4.2)
        p.scale(0.5, 0.5)
        p.setPen(_pen(ink, 2.6))
        p.setBrush(ink)
        p.drawPath(_pin_path())
        p.drawLine(QPointF(9.0, 12.4), QPointF(9.0, 16.4))
    else:
        p.setPen(_pen(ink, 1.7))
        p.drawPolyline(QPolygonF([QPointF(5.4, 9.3), QPointF(7.9, 11.7), QPointF(12.6, 6.6)]))
    p.end()
    return px


def make_empty_illustration(size: int = 88) -> QPixmap:
    """空列表插画：柔光光晕 + 渐变剪贴板 + 星芒点缀。"""
    px, p = _make_canvas(size)
    s = float(size)
    halo = QRadialGradient(QPointF(s / 2, s / 2), s / 2)
    halo.setColorAt(0.0, rgba(Palette.ACCENT, 0.16))
    halo.setColorAt(0.6, rgba(Palette.ACCENT, 0.05))
    halo.setColorAt(1.0, rgba(Palette.ACCENT, 0.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(halo)
    p.drawEllipse(QRectF(0, 0, s, s))

    tile = QRectF(s * 0.27, s * 0.25, s * 0.46, s * 0.5)
    grad = QLinearGradient(tile.topLeft(), tile.bottomRight())
    grad.setColorAt(0.0, QColor("#2a3550"))
    grad.setColorAt(1.0, QColor("#1f2537"))
    p.setBrush(grad)
    p.setPen(QPen(rgba(Palette.ACCENT, 0.45), 1.2))
    p.drawRoundedRect(tile, s * 0.08, s * 0.08)
    _clipboard_glyph(p, tile.adjusted(s * 0.08, s * 0.07, -s * 0.08, -s * 0.07), QColor(Palette.ACCENT), max(1.4, s * 0.025))

    # 星芒
    def sparkle(cx: float, cy: float, r: float, alpha: float) -> None:
        path = QPainterPath()
        path.moveTo(cx, cy - r)
        path.quadTo(cx, cy, cx + r, cy)
        path.quadTo(cx, cy, cx, cy + r)
        path.quadTo(cx, cy, cx - r, cy)
        path.quadTo(cx, cy, cx, cy - r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(rgba(Palette.ACCENT_HI, alpha))
        p.drawPath(path)

    sparkle(s * 0.78, s * 0.24, s * 0.06, 0.9)
    sparkle(s * 0.2, s * 0.72, s * 0.04, 0.6)
    sparkle(s * 0.84, s * 0.66, s * 0.03, 0.5)
    p.end()
    return px


def make_badge_font(pixel: int = 10) -> QFont:
    f = QFont("Segoe UI")
    f.setPixelSize(pixel)
    f.setWeight(QFont.Weight.Bold)
    return f
