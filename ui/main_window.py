"""
主界面窗口 (MainWindow) —— 高级深色极简悬浮剪贴板 UI。

四大区域布局：
1. 顶部胶囊与搜索栏：
   - 最左侧：独立圆角气泡 `📋 剪贴板`（无冗余关闭按钮）
   - 中间：长条形圆角搜索气泡 `🔍 搜索...`，右端与下方分类栏的「收藏 (N)」标签右缘齐平，留出右侧呼吸空间
2. 分类标签栏：
   - 5 个 Tab：`全部`、`文本`、`图像`、`文件`、`收藏 (N)`
   - 激活 Tab 与下方列表区无缝衔接，顶部带品牌蓝微光指示条
3. 左侧主内容区：圆角剪贴板卡片滚动列表 + 精致空状态插画
4. 右侧垂直操作栏：
   - 上方 3 个直径 36px 的品牌蓝圆形主按钮（复制、粘贴到当前窗口、收藏星标）
   - 下方 3 个工具按钮（固定窗口图钉、设置齿轮、清空扫帚）
"""

from __future__ import annotations

import logging
import time
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFocusEvent,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QKeyEvent,
    QKeySequence,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core import win_input
from core.clipboard_watcher import ClipboardWatcher
from core.config_manager import ConfigManager
from core.database import (
    CATEGORY_ALL,
    CATEGORY_FAVORITE,
    ITEM_FILES,
    ITEM_IMAGE,
    ITEM_TEXT,
    ClipboardDatabase,
)
from core.hotkey_manager import HotkeyManager
from .icons import (
    make_action_icon,
    make_app_icon,
    make_capsule_clipboard_pixmap,
    make_close_pixmap,
    make_empty_illustration,
    make_search_pixmap,
    make_tab_icon,
    make_toast_pixmap,
    make_tool_icon,
)
from .item_card import ClipboardItemCard
from .settings_dialog import SettingsDialog
from .theme import (
    FONT_CSS,
    SCROLLBAR_QSS,
    SHADOW_MARGIN,
    SIDEBAR_WIDTH,
    TOOLTIP_QSS,
    WINDOW_RADIUS,
    Palette,
    css_rgba,
    paint_soft_shadow,
    rgba,
    ui_font,
)

log = logging.getLogger(__name__)


class _SearchBubble(QFrame):
    """顶部中间的长条形搜索框气泡，聚焦时自动高亮边框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SearchBubble")
        self.setFixedHeight(28)
        self._focused = False
        self._hovered = False

    def set_focused(self, focused: bool) -> None:
        if self._focused != focused:
            self._focused = focused
            self.update()

    def enterEvent(self, event) -> None:  # noqa: ANN001
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        r = rect.height() / 2.0

        if self._focused:
            bg = QColor("#14161b")
            border = QPen(rgba(Palette.ACCENT, 0.78), 1.3)
        elif self._hovered:
            bg = QColor("#252831")
            border = QPen(rgba("#ffffff", 0.14), 1.0)
        else:
            bg = QColor("#21232b")
            border = QPen(rgba("#ffffff", 0.08), 1.0)

        painter.setPen(border)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, r, r)
        painter.end()


class _CategoryTabBar(QFrame):
    """分类标签栏容器：底部分割线自动避开当前激活的 Tab，使其与下方列表区无缝连通。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CategoryTabBar")
        self.setFixedHeight(38)
        self._active_button: QWidget | None = None

    def set_active_button(self, btn: QWidget | None) -> None:
        self._active_button = btn
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor(Palette.HEADER))

        pen = QPen(QColor(Palette.HAIRLINE), 1.0)
        painter.setPen(pen)
        y = rect.bottom()
        right_limit = max(0, rect.width() - SIDEBAR_WIDTH)
        if self._active_button is not None and self._active_button.isVisible():
            geo = self._active_button.geometry()
            if geo.left() > 0:
                painter.drawLine(0, y, geo.left(), y)
            if geo.right() < right_limit:
                painter.drawLine(geo.right(), y, right_limit, y)
        else:
            painter.drawLine(0, y, right_limit, y)

        # 右侧操作栏顶部分割线延续
        painter.drawLine(right_limit, 0, right_limit, rect.bottom())
        painter.drawLine(right_limit, y, rect.right(), y)
        painter.end()


class _CategoryTabButton(QPushButton):
    """自定义顶部分类 Tab 按钮，激活时底部与列表区无缝连接并带顶部品牌色亮条。"""

    def __init__(self, category: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.category = category
        self._title = title
        self._active = False
        self._hovered = False

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)
        self.setMinimumWidth(76)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._refresh_visuals()

    def set_title(self, title: str) -> None:
        if self._title != title:
            self._title = title
            self.updateGeometry()
            self.update()

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self._refresh_visuals()
            self.update()

    def _refresh_visuals(self) -> None:
        if self._active:
            color = Palette.ACCENT if self.category != CATEGORY_FAVORITE else Palette.GOLD
        elif self._hovered:
            color = Palette.TEXT
        else:
            color = Palette.TEXT_2
        self._icon = make_tab_icon(self.category, color, 15, filled=self._active)

    def sizeHint(self) -> QSize:
        font = ui_font(12, QFont.Weight.DemiBold if self._active else QFont.Weight.Normal)
        from PySide6.QtGui import QFontMetrics

        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(self._title) + 42
        return QSize(max(76, w), 34)

    def enterEvent(self, event) -> None:  # noqa: ANN001
        self._hovered = True
        self._refresh_visuals()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001
        self._hovered = False
        self._refresh_visuals()
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = QRectF(self.rect())

        if self._active:
            path = QPainterPath()
            r = 8.0
            path.moveTo(rect.left() + 0.5, rect.bottom() + 1)
            path.lineTo(rect.left() + 0.5, rect.top() + r)
            path.quadTo(rect.left() + 0.5, rect.top() + 0.5, rect.left() + r, rect.top() + 0.5)
            path.lineTo(rect.right() - r, rect.top() + 0.5)
            path.quadTo(rect.right() - 0.5, rect.top() + 0.5, rect.right() - 0.5, rect.top() + r)
            path.lineTo(rect.right() - 0.5, rect.bottom() + 1)
            path.closeSubpath()

            painter.setPen(QPen(QColor(Palette.HAIRLINE), 1.0))
            painter.setBrush(QColor(Palette.WINDOW))
            painter.drawPath(path)

            # 顶部精致品牌亮条
            bar_w = min(28.0, rect.width() * 0.36)
            bar_rect = QRectF((rect.width() - bar_w) / 2.0, rect.top() + 1.0, bar_w, 2.2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(Palette.GOLD if self.category == CATEGORY_FAVORITE else Palette.ACCENT))
            painter.drawRoundedRect(bar_rect, 1.1, 1.1)
        elif self._hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(rgba("#ffffff", 0.055))
            painter.drawRoundedRect(rect.adjusted(2, 3, -2, -3), 7.0, 7.0)

        font = ui_font(12, QFont.Weight.DemiBold if self._active else QFont.Weight.Normal)
        painter.setFont(font)
        fm = painter.fontMetrics()
        text_w = fm.horizontalAdvance(self._title)
        icon_size = 15
        gap = 5
        total_w = icon_size + gap + text_w
        start_x = (rect.width() - total_w) / 2.0
        icon_y = (rect.height() - icon_size) / 2.0

        px = self._icon.pixmap(icon_size, icon_size)
        painter.drawPixmap(int(start_x), int(icon_y), px)

        painter.setPen(QColor(Palette.TEXT if (self._active or self._hovered) else Palette.TEXT_2))
        text_rect = QRectF(start_x + icon_size + gap, 0, text_w + 4, rect.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._title)
        painter.end()


class _ActionCircleButton(QPushButton):
    """右侧上方主操作圆形按钮，支持在右上角绘制选中数量角标。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._badge_count = 0

    @property
    def badge_count(self) -> int:
        return self._badge_count

    def set_badge_count(self, count: int) -> None:
        val = max(0, int(count))
        if self._badge_count != val:
            self._badge_count = val
            self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        super().paintEvent(event)
        if self._badge_count <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        txt = str(self._badge_count) if self._badge_count < 100 else "99+"
        font = ui_font(10, QFont.Weight.Bold)
        painter.setFont(font)
        fm = painter.fontMetrics()
        w = max(15, fm.horizontalAdvance(txt) + 7)
        h = 15
        rect = QRectF(self.width() - w, 0, w, h)

        painter.setPen(QPen(QColor(Palette.SIDEBAR), 1.5))
        painter.setBrush(QColor("#1e293b"))
        painter.drawRoundedRect(rect, h / 2.0, h / 2.0)

        painter.setPen(QColor(Palette.ACCENT_HI))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, txt)
        painter.end()


class _ToastBanner(QLabel):
    """底部悬浮胶囊操作反馈 Toast（保留 text() 接口供测试校验）。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._kind = "success"
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(ui_font(12, QFont.Weight.DemiBold))
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def _detect_kind(self, text: str) -> str:
        if "请先" in text or "无效" in text or "失败" in text:
            return "info"
        if "收藏" in text or "★" in text:
            return "star"
        if "固定" in text or "📌" in text:
            return "pin"
        if "清空" in text or "删除" in text or "🧹" in text:
            return "clear"
        return "success"

    def show_message(self, text: str, duration_ms: int = 1600) -> None:
        self._kind = self._detect_kind(text)
        self.setText(text)
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(text)
        w = max(text_w + 54, 168)
        h = 32
        parent = self.parentWidget()
        if parent is not None:
            x = (parent.width() - SIDEBAR_WIDTH - w) // 2
            y = parent.height() - h - 18
            self.setGeometry(max(12, x), max(12, y), w, h)
        self.raise_()
        self.show()
        self.update()
        self._timer.start(duration_ms)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        r = rect.height() / 2.0

        p.setPen(QPen(rgba(Palette.ACCENT, 0.45), 1.1))
        p.setBrush(QColor(26, 29, 36, 242))
        p.drawRoundedRect(rect, r, r)

        px = make_toast_pixmap(self._kind, 18)
        fm = self.fontMetrics()
        txt = self.text()
        tw = fm.horizontalAdvance(txt)
        total = 18 + 7 + tw
        sx = (self.width() - total) / 2.0
        p.drawPixmap(int(sx), int((self.height() - 18) / 2), px)

        p.setFont(self.font())
        p.setPen(QColor(Palette.TEXT))
        p.drawText(
            QRectF(sx + 25, 0, tw + 6, self.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            txt,
        )
        p.end()


class MainWindow(QWidget):
    """悬浮剪贴板主窗口。"""

    #: 请求打开设置窗口
    settings_requested = Signal()
    #: 请求粘贴到外部活动窗口
    paste_to_active_window_requested = Signal(list)

    def __init__(
        self,
        database: ClipboardDatabase,
        watcher: ClipboardWatcher | None = None,
        config_manager: ConfigManager | None = None,
        hotkey_manager: HotkeyManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = database
        self.watcher = watcher or ClipboardWatcher(database, parent=self)
        self.config = config_manager or ConfigManager(data_dir=database.data_dir)
        self.hotkey_manager = hotkey_manager

        self._current_category: str = CATEGORY_ALL
        self._search_query: str = ""
        self.selected_ids: list[int] = []
        self.merge_separator: str = self.config.merge_separator
        self.clear_selection_after_copy: bool = self.config.clear_selection_after_copy
        self.hide_after_copy: bool = self.config.hide_after_copy
        self._last_external_hwnd: int = 0
        self._cards_by_id: dict[int, ClipboardItemCard] = {}
        self._is_pinned_window: bool = False
        self._suspend_auto_hide: bool = False
        self._last_auto_hide_mono: float = 0.0
        self._was_active: bool = False
        self._drag_pos: QPoint | None = None
        self._last_dragged_pos: QPoint | None = None

        self.setWindowTitle("剪贴板")
        self.setWindowIcon(make_app_icon(32))
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(548, 620)
        self.setMinimumSize(500, 420)
        self.setObjectName("MainWindow")

        self._setup_ui()
        self._setup_shortcuts()
        self.apply_config()

        app = QApplication.instance()
        if isinstance(app, QGuiApplication):
            app.applicationStateChanged.connect(self._on_application_state_changed)

        if self.watcher is not None:
            self.watcher.item_recorded.connect(self._on_clipboard_item_recorded)

        if self.hotkey_manager is not None:
            self.hotkey_manager.hotkey_triggered.connect(self.toggle_window)

        self.refresh_items()

    def apply_config(self) -> None:
        """从 ConfigManager 同步配置项并立即生效。"""
        self.merge_separator = self.config.merge_separator
        self.clear_selection_after_copy = self.config.clear_selection_after_copy
        self.hide_after_copy = self.config.hide_after_copy
        if self.hotkey_manager is not None:
            self.hotkey_manager.set_trigger_timeout_ms(self.config.trigger_timeout_ms)
        self.db.prune_to_max_history(self.config.max_history)

    # ------------------------------------------------------------------ UI 搭建
    def _setup_ui(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget#MainWindow {{
                background: transparent;
                color: {Palette.TEXT};
                font-family: {FONT_CSS};
            }}
            QFrame#OuterContainer {{
                background-color: {Palette.WINDOW};
                border: 1px solid {Palette.BORDER};
                border-radius: {int(WINDOW_RADIUS)}px;
            }}
            {TOOLTIP_QSS}
            """
        )

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)
        outer_layout.setSpacing(0)

        self._outer_frame = QFrame(self)
        self._outer_frame.setObjectName("OuterContainer")
        outer_layout.addWidget(self._outer_frame)

        root_layout = QVBoxLayout(self._outer_frame)
        root_layout.setContentsMargins(1, 1, 1, 1)
        root_layout.setSpacing(0)

        # 1. 顶部胶囊与长条搜索框气泡栏
        self._top_bar = self._build_top_bar()
        root_layout.addWidget(self._top_bar)

        # 2. 分类标签栏
        self._tab_bar = self._build_tab_bar()
        root_layout.addWidget(self._tab_bar)

        # 3 & 4. 下方主体区域：左侧卡片滚动列表 + 右侧垂直操作栏
        body_widget = QWidget(self._outer_frame)
        body_layout = QHBoxLayout(body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self._scroll_area = self._build_card_list_area()
        self._right_sidebar = self._build_right_sidebar()

        body_layout.addWidget(self._scroll_area, 1)
        body_layout.addWidget(self._right_sidebar, 0)
        root_layout.addWidget(body_widget, 1)

        self._toast = _ToastBanner(self._outer_frame)

    def _build_top_bar(self) -> QWidget:
        bar = QFrame(self._outer_frame)
        bar.setObjectName("TopSearchBar")
        bar.setFixedHeight(44)
        bar.setStyleSheet(
            f"""
            QFrame#TopSearchBar {{
                background-color: {Palette.HEADER};
                border: none;
                border-top-left-radius: {int(WINDOW_RADIUS) - 1}px;
                border-top-right-radius: {int(WINDOW_RADIUS) - 1}px;
            }}
            """
        )
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(10, 8, 10, 6)
        bar_layout.setSpacing(0)

        # 内部水平容器：左端对齐「全部」Tab，右端与「收藏 (N)」Tab 齐平
        self._top_inner_row = QWidget(bar)
        inner_layout = QHBoxLayout(self._top_inner_row)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(8)

        # 1) 最左边：纯粹的「📋 剪贴板」圆角气泡（不含 × 号）
        self._brand_capsule = QFrame(self._top_inner_row)
        self._brand_capsule.setObjectName("ClipboardCapsule")
        self._brand_capsule.setFixedHeight(28)
        self._brand_capsule.setStyleSheet(
            f"""
            QFrame#ClipboardCapsule {{
                background-color: #24262f;
                border: 1px solid {css_rgba('#ffffff', 0.08)};
                border-radius: 14px;
            }}
            """
        )
        cap_layout = QHBoxLayout(self._brand_capsule)
        cap_layout.setContentsMargins(10, 0, 12, 0)
        cap_layout.setSpacing(6)

        icon_lbl = QLabel(self._brand_capsule)
        icon_lbl.setFixedSize(16, 16)
        icon_lbl.setPixmap(make_capsule_clipboard_pixmap(16))

        title_lbl = QLabel("剪贴板", self._brand_capsule)
        title_lbl.setStyleSheet(
            f"color: {Palette.TEXT}; font-family: {FONT_CSS}; font-size: 12px; font-weight: 600; background: transparent; border: none;"
        )

        cap_layout.addWidget(icon_lbl)
        cap_layout.addWidget(title_lbl)
        inner_layout.addWidget(self._brand_capsule, 0, Qt.AlignmentFlag.AlignVCenter)

        # 2) 中间：长条气泡搜索框（右端与下方「收藏 (N)」齐平）
        self._search_bubble = _SearchBubble(self._top_inner_row)
        sb_layout = QHBoxLayout(self._search_bubble)
        sb_layout.setContentsMargins(11, 0, 8, 0)
        sb_layout.setSpacing(6)

        self._search_icon_lbl = QLabel(self._search_bubble)
        self._search_icon_lbl.setFixedSize(14, 14)
        self._search_icon_lbl.setPixmap(make_search_pixmap(Palette.TEXT_3, 14))
        self._search_icon_lbl.setStyleSheet("background: transparent; border: none;")
        sb_layout.addWidget(self._search_icon_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self._search_input = QLineEdit(self._search_bubble)
        self._search_input.setPlaceholderText("搜索...")
        self._search_input.setClearButtonEnabled(False)
        self._search_input.setStyleSheet(
            f"""
            QLineEdit {{
                background: transparent;
                border: none;
                color: {Palette.TEXT};
                font-family: {FONT_CSS};
                font-size: 12px;
                padding: 0px 2px;
            }}
            """
        )
        self._search_input.textChanged.connect(self._on_search_changed)
        sb_layout.addWidget(self._search_input, 1)

        # 搜索框内右侧清空小按钮（仅在有输入文字时出现）
        self._search_clear_btn = QPushButton(self._search_bubble)
        self._search_clear_btn.setFixedSize(16, 16)
        self._search_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_clear_btn.setToolTip("清空搜索")
        self._search_clear_btn.setIcon(make_close_pixmap(Palette.TEXT_2, 10))
        self._search_clear_btn.setIconSize(QSize(10, 10))
        self._search_clear_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {css_rgba('#ffffff', 0.08)};
                border: none;
                border-radius: 8px;
            }}
            QPushButton:hover {{
                background: {css_rgba('#ffffff', 0.18)};
            }}
            """
        )
        self._search_clear_btn.hide()
        self._search_clear_btn.clicked.connect(self._search_input.clear)
        sb_layout.addWidget(self._search_clear_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # 已选数量胶囊（内嵌在长条搜索气泡右侧，不侵占窗口最右侧空白）
        self._selection_badge = QLabel(self._search_bubble)
        self._selection_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._selection_badge.setFixedHeight(18)
        self._selection_badge.hide()
        sb_layout.addWidget(self._selection_badge, 0, Qt.AlignmentFlag.AlignVCenter)

        inner_layout.addWidget(self._search_bubble, 1, Qt.AlignmentFlag.AlignVCenter)

        bar_layout.addWidget(self._top_inner_row, 0, Qt.AlignmentFlag.AlignVCenter)
        bar_layout.addStretch(1)
        return bar

    def _sync_top_bar_alignment(self) -> None:
        """使顶部「剪贴板气泡 + 搜索长条气泡」的右边缘与下方「收藏 (N)」标签右边缘精准齐平。"""
        fav_btn = self._tab_buttons.get(CATEGORY_FAVORITE)
        if fav_btn is None:
            return
        self._tab_bar.layout().activate()
        fav_right = fav_btn.geometry().right()
        left_margin = 10
        target_w = max(280, fav_right - left_margin + 2)
        self._top_inner_row.setFixedWidth(target_w)

    def _build_tab_bar(self) -> _CategoryTabBar:
        bar = _CategoryTabBar(self)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 4, SIDEBAR_WIDTH, 0)
        layout.setSpacing(4)

        self._tab_buttons: dict[str, _CategoryTabButton] = {}
        tab_specs = [
            (CATEGORY_ALL, "全部"),
            (ITEM_TEXT, "文本"),
            (ITEM_IMAGE, "图像"),
            (ITEM_FILES, "文件"),
            (CATEGORY_FAVORITE, "收藏 (0)"),
        ]
        for cat, label in tab_specs:
            btn = _CategoryTabButton(cat, label, bar)
            btn.clicked.connect(lambda _=False, c=cat: self.switch_category(c))
            self._tab_buttons[cat] = btn
            layout.addWidget(btn)

        layout.addStretch(1)
        self._tab_buttons[CATEGORY_ALL].set_active(True)
        bar.set_active_button(self._tab_buttons[CATEGORY_ALL])
        return bar

    def _build_card_list_area(self) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setObjectName("CardScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"""
            QScrollArea#CardScrollArea {{
                background-color: {Palette.WINDOW};
                border: none;
                border-bottom-left-radius: {int(WINDOW_RADIUS) - 1}px;
            }}
            {SCROLLBAR_QSS}
            """
        )

        self._list_container = QWidget(scroll)
        self._list_container.setObjectName("CardListContainer")
        self._list_container.setStyleSheet(
            f"QWidget#CardListContainer {{ background-color: {Palette.WINDOW}; border-bottom-left-radius: {int(WINDOW_RADIUS) - 1}px; }}"
        )

        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(10, 8, 8, 12)
        self._list_layout.setSpacing(6)

        # 空状态组件（保留 self._empty_label 属性名以兼容测试）
        self._empty_label = QWidget(self._list_container)
        empty_layout = QVBoxLayout(self._empty_label)
        empty_layout.setContentsMargins(0, 72, 0, 72)
        empty_layout.setSpacing(10)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        empty_icon = QLabel(self._empty_label)
        empty_icon.setFixedSize(88, 88)
        empty_icon.setPixmap(make_empty_illustration(88))
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        empty_title = QLabel("暂无剪贴板记录", self._empty_label)
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title.setStyleSheet(
            f"color: {Palette.TEXT_2}; font-family: {FONT_CSS}; font-size: 13px; font-weight: 600;"
        )

        empty_sub = QLabel("复制文字、截图或文件后将实时出现在这里", self._empty_label)
        empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_sub.setStyleSheet(f"color: {Palette.TEXT_3}; font-family: {FONT_CSS}; font-size: 11px;")

        empty_layout.addWidget(empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addWidget(empty_title, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addWidget(empty_sub, 0, Qt.AlignmentFlag.AlignHCenter)

        self._list_layout.addWidget(self._empty_label)
        self._list_layout.addStretch(1)

        scroll.setWidget(self._list_container)
        return scroll

    def _build_right_sidebar(self) -> QWidget:
        sidebar = QFrame(self._outer_frame)
        sidebar.setObjectName("RightSidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        sidebar.setStyleSheet(
            f"""
            QFrame#RightSidebar {{
                background-color: {Palette.SIDEBAR};
                border-left: 1px solid {Palette.HAIRLINE};
                border-bottom-right-radius: {int(WINDOW_RADIUS) - 1}px;
            }}
            """
        )

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 14, 10, 14)
        layout.setSpacing(0)

        circle_qss = f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {Palette.ACCENT_HI}, stop:1 {Palette.ACCENT});
                border: none;
                border-radius: 18px;
            }}
            QPushButton:hover {{
                background-color: {Palette.ACCENT_HI};
            }}
            QPushButton:pressed {{
                background-color: {Palette.ACCENT_LO};
            }}
        """

        # 上方按钮组（3 个直径 36px 的品牌蓝圆形按钮，间距 14px）
        self.btn_copy = _ActionCircleButton(sidebar)
        self.btn_copy.setFixedSize(36, 36)
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setIcon(make_action_icon("copy", Palette.ON_ACCENT, 20))
        self.btn_copy.setIconSize(QSize(20, 20))
        self.btn_copy.setToolTip("合并复制选中项 (Ctrl+C)")
        self.btn_copy.setStyleSheet(circle_qss)
        self.btn_copy.clicked.connect(self.copy_selected_items)

        self.btn_send = QPushButton(sidebar)
        self.btn_send.setFixedSize(36, 36)
        self.btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_send.setIcon(make_action_icon("send", Palette.ON_ACCENT, 20))
        self.btn_send.setIconSize(QSize(20, 20))
        self.btn_send.setToolTip("合并粘贴到当前活动窗口")
        self.btn_send.setStyleSheet(circle_qss)
        self.btn_send.clicked.connect(self.paste_selected_to_active_window)

        self.btn_favorite = QPushButton(sidebar)
        self.btn_favorite.setFixedSize(36, 36)
        self.btn_favorite.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_favorite.setIcon(make_action_icon("favorite", Palette.ON_ACCENT, 20))
        self.btn_favorite.setIconSize(QSize(20, 20))
        self.btn_favorite.setToolTip("收藏/取消收藏选中项")
        self.btn_favorite.setStyleSheet(circle_qss)
        self.btn_favorite.clicked.connect(self.toggle_favorite_selected)

        layout.addWidget(self.btn_copy, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(14)
        layout.addWidget(self.btn_send, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(14)
        layout.addWidget(self.btn_favorite, 0, Qt.AlignmentFlag.AlignHCenter)

        layout.addStretch(1)

        # 下方工具按钮组（靠底对齐，固定窗口图钉 / 设置齿轮 / 清空扫帚）
        tool_qss = f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
            }}
            QPushButton:hover {{
                background-color: {css_rgba('#ffffff', 0.08)};
            }}
            QPushButton:pressed {{
                background-color: {css_rgba('#ffffff', 0.14)};
            }}
            QPushButton[pinned="true"] {{
                background-color: {css_rgba(Palette.ACCENT, 0.18)};
                border: 1px solid {css_rgba(Palette.ACCENT, 0.55)};
            }}
        """

        self.btn_pin_window = QPushButton(sidebar)
        self.btn_pin_window.setFixedSize(30, 30)
        self.btn_pin_window.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pin_window.setIcon(make_tool_icon("pin_window", Palette.TEXT_2, 18, filled=False))
        self.btn_pin_window.setIconSize(QSize(18, 18))
        self.btn_pin_window.setToolTip("切换“失焦自动消失”与“固定在桌面”")
        self.btn_pin_window.setStyleSheet(tool_qss)
        self.btn_pin_window.clicked.connect(self.toggle_pin_window)

        self.btn_settings = QPushButton(sidebar)
        self.btn_settings.setFixedSize(30, 30)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setIcon(make_tool_icon("settings", Palette.TEXT_2, 18))
        self.btn_settings.setIconSize(QSize(18, 18))
        self.btn_settings.setToolTip("打开设置面板")
        self.btn_settings.setStyleSheet(tool_qss)
        self.btn_settings.clicked.connect(self._on_settings_clicked)

        self.btn_clear = QPushButton(sidebar)
        self.btn_clear.setFixedSize(30, 30)
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.setIcon(make_tool_icon("clear", Palette.TEXT_2, 18))
        self.btn_clear.setIconSize(QSize(18, 18))
        self.btn_clear.setToolTip("清空所有未收藏历史记录")
        self.btn_clear.setStyleSheet(tool_qss)
        self.btn_clear.clicked.connect(self.clear_unfavorited_items)

        layout.addWidget(self.btn_pin_window, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(10)
        layout.addWidget(self.btn_settings, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(10)
        layout.addWidget(self.btn_clear, 0, Qt.AlignmentFlag.AlignHCenter)

        return sidebar

    def _setup_shortcuts(self) -> None:
        self._shortcut_copy = QShortcut(QKeySequence("Ctrl+C"), self)
        self._shortcut_copy.activated.connect(self.copy_selected_items)

        self._shortcut_select_all = QShortcut(QKeySequence("Ctrl+A"), self)
        self._shortcut_select_all.activated.connect(self.select_all_visible)

        self._shortcut_delete = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        self._shortcut_delete.activated.connect(self.delete_selected_items)

        self._shortcut_esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._shortcut_esc.activated.connect(self.handle_escape_key)

        self._search_input.installEventFilter(self)

    # ------------------------------------------------------------------ 数据刷新与分类筛选
    @property
    def current_category(self) -> str:
        return self._current_category

    def switch_category(self, category: str) -> None:
        if category not in self._tab_buttons:
            return
        self._current_category = category
        for cat, btn in self._tab_buttons.items():
            btn.set_active(cat == category)
        self._tab_bar.set_active_button(self._tab_buttons[category])
        self.refresh_items()

    def _on_search_changed(self, text: str) -> None:
        self._search_query = text.strip()
        self._search_clear_btn.setVisible(bool(text))
        self.refresh_items()

    def _on_clipboard_item_recorded(self, item_id: int, action: str) -> None:
        """监听到剪贴板新内容入库时，裁剪超出上限的历史并立即刷新列表使新项排在第 1 位。"""
        self.db.prune_to_max_history(self.config.max_history)
        self.refresh_items(scroll_to_top=True)

    def refresh_favorite_tab_count(self) -> int:
        fav_count = self.db.get_favorite_count()
        fav_btn = self._tab_buttons.get(CATEGORY_FAVORITE)
        if fav_btn is not None:
            fav_btn.set_title(f"收藏 ({fav_count})")
        self._sync_top_bar_alignment()
        return fav_count

    def refresh_items(self, scroll_to_top: bool = False) -> None:
        """从数据库查询当前分类 + 搜索词的记录并重建卡片列表。"""
        self.refresh_favorite_tab_count()
        items = self.db.get_items(self._current_category, self._search_query)

        # 清理旧卡片
        self._cards_by_id.clear()
        while self._list_layout.count() > 2:
            child = self._list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        # 过滤掉已被删除的选中 id
        existing_all_ids = {it["id"] for it in self.db.get_items(CATEGORY_ALL)}
        self.selected_ids = [i for i in self.selected_ids if i in existing_all_ids]

        self._empty_label.setVisible(len(items) == 0)

        for idx, item in enumerate(items, start=1):
            card = ClipboardItemCard(item, index=idx, data_dir=self.db.data_dir, parent=self._list_container)
            card.card_clicked.connect(self.toggle_card_selection)
            self._cards_by_id[card.item_id] = card
            self._list_layout.insertWidget(idx - 1, card)

        self._sync_selection_visuals()

        if scroll_to_top:
            self._scroll_area.verticalScrollBar().setValue(0)

    # ------------------------------------------------------------------ 选中与操作栏逻辑
    def toggle_card_selection(self, item_id: int) -> None:
        """单击切换卡片选中状态（按点击先后顺序维护 self.selected_ids）。"""
        if item_id in self.selected_ids:
            self.selected_ids.remove(item_id)
        else:
            self.selected_ids.append(item_id)
        self._sync_selection_visuals()

    def clear_selection(self) -> None:
        if self.selected_ids:
            self.selected_ids.clear()
            self._sync_selection_visuals()

    def select_all_visible(self) -> None:
        if self._search_input.hasFocus() and self._search_input.text():
            self._search_input.selectAll()
            return
        visible_ids = list(self._cards_by_id.keys())
        if not visible_ids:
            return
        if self.selected_ids == visible_ids:
            self.clear_selection()
        else:
            self.selected_ids = visible_ids
            self._sync_selection_visuals()

    def _sync_selection_visuals(self) -> None:
        total_sel = len(self.selected_ids)
        order_map = {item_id: idx for idx, item_id in enumerate(self.selected_ids, start=1)}

        for item_id, card in self._cards_by_id.items():
            if item_id in order_map:
                card.set_selected(True, order_index=order_map[item_id], total_selected=total_sel)
            else:
                card.set_selected(False, order_index=None, total_selected=total_sel)

        if total_sel > 0:
            self._selection_badge.setText(f"已选中 {total_sel} 项")
            self._selection_badge.setStyleSheet(
                f"""
                QLabel {{
                    background-color: {css_rgba(Palette.ACCENT, 0.18)};
                    color: {Palette.ACCENT};
                    border: 1px solid {css_rgba(Palette.ACCENT, 0.45)};
                    border-radius: 9px;
                    padding: 0px 8px;
                    font-family: {FONT_CSS};
                    font-size: 11px;
                    font-weight: 600;
                }}
                """
            )
            self._selection_badge.show()
            self.btn_copy.set_badge_count(total_sel)
            self.btn_copy.setToolTip(f"合并复制 {total_sel} 项 (Ctrl+C)")
        else:
            self._selection_badge.hide()
            self.btn_copy.set_badge_count(0)
            self.btn_copy.setToolTip("合并复制选中项 (Ctrl+C)")

    def record_foreground_window(self) -> int:
        """记录唤起悬浮窗之前的外部活动窗口句柄，供一键上屏粘贴使用。"""
        hwnd = win_input.get_foreground_window()
        try:
            self_hwnd = int(self.winId())
        except Exception:  # noqa: BLE001
            self_hwnd = 0
        if hwnd and hwnd != self_hwnd:
            self._last_external_hwnd = hwnd
        return self._last_external_hwnd

    def copy_selected_items(self) -> bool:
        if not self.selected_ids:
            self.show_toast("请先点击选择要复制的内容")
            return False
        items = self.db.get_items_by_ids(self.selected_ids)
        if not items:
            self.clear_selection()
            return False

        count = len(items)
        is_multi_merge = count > 1
        types = {it["item_type"] for it in items}
        is_file_merge = is_multi_merge and (ITEM_TEXT not in types)
        merged_file_count = 0
        if is_file_merge and self.watcher is not None:
            merged_file_count = len(self.watcher.collect_merged_file_paths(items))

        if self.clear_selection_after_copy:
            self.selected_ids.clear()

        if self.watcher is not None:
            ok = self.watcher.copy_items_to_clipboard(
                items,
                separator=self.merge_separator,
                record_merged=is_multi_merge,
            )
        else:
            ok = False

        if ok:
            if not is_multi_merge:
                self._sync_selection_visuals()
            if is_file_merge:
                self.show_toast(f"【{merged_file_count}个文件已合并】", duration_ms=2200)
            elif is_multi_merge:
                self.show_toast(f"【{count}项内容已合并】", duration_ms=2000)
            else:
                self.show_toast("✓ 已复制 1 项内容")
            if self.hide_after_copy:
                self.hide()
        else:
            self._sync_selection_visuals()
        return ok

    def paste_selected_to_active_window(self) -> bool:
        """
        右侧纸飞机「发送/粘贴」按钮逻辑：
        1. 按选中顺序执行合并复制；
        2. 立即隐藏剪贴板悬浮窗；
        3. 延迟 100ms 自动向原外部活动窗口发送 Ctrl + V 按键事件。
        """
        if not self.selected_ids:
            self.show_toast("请先点击选择要粘贴的内容")
            return False
        ids_copy = list(self.selected_ids)
        if not self.copy_selected_items():
            return False
        target_hwnd = self._last_external_hwnd
        self.hide()
        QTimer.singleShot(100, lambda: win_input.send_ctrl_v(target_hwnd))
        self.paste_to_active_window_requested.emit(ids_copy)
        return True

    def toggle_favorite_selected(self) -> None:
        if not self.selected_ids:
            self.show_toast("请先点击选择要收藏的内容")
            return
        items = self.db.get_items_by_ids(self.selected_ids)
        if not items:
            return
        all_fav = all(bool(it.get("is_favorite")) for it in items)
        target_fav = not all_fav
        self.db.set_favorite(self.selected_ids, target_fav)
        self.refresh_favorite_tab_count()

        if self._current_category == CATEGORY_FAVORITE and not target_fav:
            self.refresh_items()
        else:
            for item_id in self.selected_ids:
                card = self._cards_by_id.get(item_id)
                if card is not None:
                    card.set_favorite_state(target_fav)

        action_str = "已收藏" if target_fav else "已取消收藏"
        self.show_toast(f"★ {action_str} {len(items)} 项")

    def delete_selected_items(self) -> None:
        if not self.selected_ids:
            return
        count = self.db.delete_items(self.selected_ids)
        self.selected_ids.clear()
        self.refresh_items()
        if count > 0:
            self.show_toast(f"已删除 {count} 条记录")

    def clear_unfavorited_items(self) -> None:
        deleted = self.db.clear_unfavorited()
        self.refresh_items()
        self.show_toast(f"🧹 已清空 {deleted} 条未收藏记录")

    def toggle_pin_window(self) -> None:
        self._is_pinned_window = not self._is_pinned_window
        self.btn_pin_window.setProperty("pinned", "true" if self._is_pinned_window else "false")
        self.btn_pin_window.style().unpolish(self.btn_pin_window)
        self.btn_pin_window.style().polish(self.btn_pin_window)
        icon_color = Palette.ACCENT if self._is_pinned_window else Palette.TEXT_2
        self.btn_pin_window.setIcon(make_tool_icon("pin_window", icon_color, 18, filled=self._is_pinned_window))
        self.show_toast("📌 已固定在桌面" if self._is_pinned_window else "已恢复失焦自动隐藏")

    @property
    def is_pinned_window(self) -> bool:
        return self._is_pinned_window

    @property
    def is_auto_hide_suspended(self) -> bool:
        return self._suspend_auto_hide

    def set_suspend_auto_hide(self, suspend: bool) -> None:
        """在打开设置弹窗等子对话框期间暂时挂起失焦自动隐藏，关闭后恢复。"""
        self._suspend_auto_hide = bool(suspend)

    def should_auto_hide_on_blur(self) -> bool:
        """当前是否允许因失去焦点而自动隐藏。"""
        return not self._is_pinned_window and not self._suspend_auto_hide

    @property
    def last_dragged_pos(self) -> QPoint | None:
        return self._last_dragged_pos

    def open_settings_dialog(self) -> bool:
        """弹出深色设置窗口（期间挂起主窗口失焦隐藏，关闭后恢复）。"""
        self.settings_requested.emit()
        self.set_suspend_auto_hide(True)
        try:
            dlg = SettingsDialog(
                config_manager=self.config,
                hotkey_manager=self.hotkey_manager,
                parent=self if self.isVisible() else None,
            )
            accepted = dlg.exec() == int(SettingsDialog.DialogCode.Accepted)
            if accepted:
                self.apply_config()
                if self.isVisible():
                    self.show_toast("✓ 设置已保存并立即生效")
            return accepted
        finally:
            self.set_suspend_auto_hide(False)
            if self.isVisible():
                self.raise_()
                self.activateWindow()

    def _on_settings_clicked(self) -> None:
        self.open_settings_dialog()

    def show_toast(self, message: str, duration_ms: int = 1600) -> None:
        self._toast.show_message(message, duration_ms)

    # ------------------------------------------------------------------ 唤起定位与失焦隐藏
    def _apply_smart_position(self) -> None:
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        w, h = self.width(), self.height()

        if self._last_dragged_pos is not None:
            target_x = self._last_dragged_pos.x()
            target_y = self._last_dragged_pos.y()
        else:
            target_x = cursor_pos.x() - w // 2
            target_y = cursor_pos.y() - 60

        clamped_x = max(avail.left() + 8, min(target_x, avail.right() - w - 8))
        clamped_y = max(avail.top() + 8, min(target_y, avail.bottom() - h - 8))
        self.move(clamped_x, clamped_y)

    def summon_window(self) -> None:
        if not self.isVisible():
            self.record_foreground_window()
            self._apply_smart_position()
        self._was_active = False
        self.show()
        self.showNormal()
        self.raise_()
        try:
            win_input.activate_window(int(self.winId()))
        except Exception:  # noqa: BLE001
            pass
        self.activateWindow()
        if self.isActiveWindow():
            self._was_active = True

    def toggle_window(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.summon_window()

    def toggle_window_from_tray(self) -> None:
        if self.isVisible():
            self.hide()
        elif time.monotonic() - self._last_auto_hide_mono > 0.25:
            self.summon_window()

    def _handle_focus_lost(self, force: bool = False) -> None:
        if not self.isVisible():
            return
        if not self.should_auto_hide_on_blur():
            return
        if force or (self._was_active and not self.isActiveWindow()):
            self._was_active = False
            self._last_auto_hide_mono = time.monotonic()
            self.hide()

    def _on_application_state_changed(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            if self.isActiveWindow():
                self._was_active = True
        else:
            self._handle_focus_lost(force=True)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                self._was_active = True
            elif self._was_active:
                self._handle_focus_lost(force=True)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        if self._was_active and not self.isActiveWindow():
            self._handle_focus_lost(force=True)

    # ------------------------------------------------------------------ 键盘与窗口事件
    def handle_escape_key(self) -> None:
        if self.selected_ids:
            self.clear_selection()
        else:
            self.hide()

    def refresh_visible_relative_times(self, now: float | None = None) -> None:
        """动态刷新当前可见卡片左下角的相对时间显示。"""
        for card in self._cards_by_id.values():
            card.refresh_relative_time(now=now)

    def eventFilter(self, watched: Any, event: QEvent) -> bool:
        if watched is self._search_input:
            if event.type() == QEvent.Type.FocusIn:
                self._search_bubble.set_focused(True)
                self._search_icon_lbl.setPixmap(make_search_pixmap(Palette.ACCENT, 14))
            elif event.type() == QEvent.Type.FocusOut:
                self._search_bubble.set_focused(False)
                self._search_icon_lbl.setPixmap(make_search_pixmap(Palette.TEXT_3, 14))
            elif event.type() == QEvent.Type.KeyPress:
                key_event = event  # type: ignore[assignment]
                if isinstance(key_event, QKeyEvent):
                    if key_event.key() == Qt.Key.Key_Escape:
                        self.handle_escape_key()
                        return True
                    if key_event.matches(QKeySequence.StandardKey.Copy) and not self._search_input.hasSelectedText():
                        self.copy_selected_items()
                        return True
                    if key_event.matches(QKeySequence.StandardKey.SelectAll) and not self._search_input.text():
                        self.select_all_visible()
                        return True
                    if key_event.key() == Qt.Key.Key_Delete and not self._search_input.text():
                        self.delete_selected_items()
                        return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.handle_escape_key()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Delete:
            self.delete_selected_items()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.SelectAll) or (
            event.key() == Qt.Key.Key_A and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        ):
            self.select_all_visible()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Copy) or (
            event.key() == Qt.Key.Key_C and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        ):
            self.copy_selected_items()
            event.accept()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        self._sync_top_bar_alignment()
        self.refresh_visible_relative_times()

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._sync_top_bar_alignment()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= SHADOW_MARGIN + 82:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            new_pos = event.globalPosition().toPoint() - self._drag_pos
            self.move(new_pos)
            self._last_dragged_pos = new_pos
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None:
            self._last_dragged_pos = self.pos()
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        """在无边框透明窗口外围绘制九宫格柔和模糊阴影。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_soft_shadow(painter, self._outer_frame.geometry(), radius=WINDOW_RADIUS, margin=SHADOW_MARGIN)
        painter.end()
