"""
全局视觉主题 (Design Tokens) —— 高级深色石墨主题。

设计语言：
- 冷调石墨灰分层（窗口 / 顶栏 / 侧栏 / 卡片四级明度），用极细的半透明发丝线区分层级；
- 品牌主色沿用 #8ab4f8 浅蓝，并扩展为柔和的蓝 → 靛蓝渐变用于主操作按钮；
- 圆角体系：窗口 12px / 卡片 10px / 控件 8~9px / 胶囊全圆角；
- 九宫格缓存的高斯模糊窗口阴影，替代逐圈描边的生硬阴影。
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene

# ---------------------------------------------------------------------- 字体
FONT_FAMILY = "Microsoft YaHei UI"
FONT_CSS = "'Microsoft YaHei UI', 'Segoe UI', sans-serif"


def ui_font(pixel_size: int = 13, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(FONT_FAMILY)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


# ---------------------------------------------------------------------- 调色板
class Palette:
    # 分层背景（由深到浅）
    WINDOW = "#141519"        # 列表主背景
    HEADER = "#1a1b20"        # 顶部搜索栏
    SIDEBAR = "#17181c"       # 右侧操作栏
    CARD = "#1c1d22"          # 卡片
    CARD_HOVER = "#23252b"    # 卡片悬停
    CARD_SELECTED = "#1a2130" # 卡片选中（微蓝）
    ELEVATED = "#202127"      # 弹窗 / 菜单 / Toast

    # 线条
    HAIRLINE = "#24252b"
    BORDER = "#2d2f36"
    BORDER_STRONG = "#3a3d46"

    # 文字
    TEXT = "#eceef1"
    TEXT_2 = "#a6aab3"
    TEXT_3 = "#6f737c"

    # 品牌色
    ACCENT = "#8ab4f8"
    ACCENT_HI = "#a8c8fb"
    ACCENT_LO = "#6d9bf1"
    ACCENT_DEEP = "#7b8dfa"
    ON_ACCENT = "#0c1322"

    # 语义色
    GOLD = "#f5c55c"
    DANGER = "#f28b82"
    SUCCESS = "#7fd1a0"


def rgba(color: str, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def css_rgba(color: str, alpha: float) -> str:
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {int(round(alpha * 255))})"


def mix(c1: str | QColor, c2: str | QColor, t: float) -> QColor:
    a, b = QColor(c1), QColor(c2)
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
        int(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


# ---------------------------------------------------------------------- 尺寸
WINDOW_RADIUS = 12.0
CARD_RADIUS = 10.0
SHADOW_MARGIN = 14
SIDEBAR_WIDTH = 56


# ---------------------------------------------------------------------- 九宫格模糊阴影
@lru_cache(maxsize=8)
def _shadow_tile(margin: int, radius: float, blur: float, alpha: int) -> QPixmap:
    """渲染一张小尺寸的圆角矩形高斯模糊阴影瓦片（九宫格切片源）。"""
    core = int(radius * 2 + 8)
    size = margin * 2 + core
    src = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    src.fill(Qt.GlobalColor.transparent)
    p = QPainter(src)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, alpha))
    p.drawRoundedRect(QRectF(margin, margin, core, core), radius, radius)
    p.end()

    try:
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(QPixmap.fromImage(src))
        effect = QGraphicsBlurEffect()
        effect.setBlurRadius(blur)
        effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
        item.setGraphicsEffect(effect)
        scene.addItem(item)
        out = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        scene.render(p, QRectF(0, 0, size, size), QRectF(0, 0, size, size))
        p.end()
        scene.removeItem(item)
        return QPixmap.fromImage(out)
    except Exception:  # noqa: BLE001 - 极端环境下退化为未模糊阴影
        return QPixmap.fromImage(src)


def paint_soft_shadow(
    painter: QPainter,
    surface_rect: QRectF | QRect,
    radius: float = WINDOW_RADIUS,
    margin: int = SHADOW_MARGIN,
    offset_y: float = 3.0,
    alpha: int = 150,
) -> None:
    """在 surface_rect 外围绘制柔和的九宫格模糊阴影。"""
    tile = _shadow_tile(margin, float(radius), float(margin) * 0.9, alpha)
    ts = tile.width()
    c = margin + int(radius) + 2  # 角切片尺寸
    mid = ts - 2 * c
    if mid <= 0:
        return
    r = QRectF(surface_rect).adjusted(-margin, -margin + offset_y, margin, margin + offset_y)
    x0, y0, x1, y1 = r.left(), r.top(), r.right(), r.bottom()
    w_mid = max(0.0, r.width() - 2 * c)
    h_mid = max(0.0, r.height() - 2 * c)

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    pieces = [
        (QRectF(x0, y0, c, c), QRectF(0, 0, c, c)),
        (QRectF(x0 + c, y0, w_mid, c), QRectF(c, 0, mid, c)),
        (QRectF(x1 - c, y0, c, c), QRectF(ts - c, 0, c, c)),
        (QRectF(x0, y0 + c, c, h_mid), QRectF(0, c, c, mid)),
        (QRectF(x1 - c, y0 + c, c, h_mid), QRectF(ts - c, c, c, mid)),
        (QRectF(x0, y1 - c, c, c), QRectF(0, ts - c, c, c)),
        (QRectF(x0 + c, y1 - c, w_mid, c), QRectF(c, ts - c, mid, c)),
        (QRectF(x1 - c, y1 - c, c, c), QRectF(ts - c, ts - c, c, c)),
    ]
    for target, source in pieces:
        if target.width() > 0 and target.height() > 0:
            painter.drawPixmap(target, tile, source)
    painter.restore()


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


# ---------------------------------------------------------------------- 通用 QSS 片段
TOOLTIP_QSS = f"""
QToolTip {{
    background-color: {Palette.ELEVATED};
    color: {Palette.TEXT};
    border: 1px solid {Palette.BORDER_STRONG};
    padding: 5px 9px;
    border-radius: 6px;
    font-family: {FONT_CSS};
    font-size: 12px;
}}
"""

SCROLLBAR_QSS = f"""
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: {css_rgba('#ffffff', 0.10)};
    min-height: 40px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{
    background: {css_rgba('#ffffff', 0.22)};
}}
QScrollBar::handle:vertical:pressed {{
    background: {css_rgba(Palette.ACCENT, 0.55)};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
"""
