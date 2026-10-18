"""Reusable widgets: frameless title bar, sidebar navigation, video card delegate."""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSizePolicy,
                               QStyledItemDelegate, QStyle, QToolButton,
                               QVBoxLayout, QWidget)

from . import theme as T

# --------------------------------------------------------------------------- utils
def elide(text: str, width: int, font: QFont) -> str:
    fm = QFontMetrics(font)
    return fm.elidedText(text, Qt.ElideRight, width)


def human_size(n: int | float) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def human_time(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def timestamp_ms(ms: int) -> str:
    if not ms:
        return ""
    import datetime as _dt
    try:
        return _dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, OverflowError):
        return ""


# ---------------------------------------------------------------------- title bar
class TitleBar(QFrame):
    """Frameless window title bar with drag-to-move and window buttons."""

    def __init__(self, window: QWidget, title: str = "ReplayStudio", parent=None):
        super().__init__(parent)
        self.window = window
        self.setObjectName("titleBar")
        self.setFixedHeight(38)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(2)

        icon = QLabel("●")
        T.style(icon, "color: $ACCENT; font-size: 16px;")
        lay.addWidget(icon)
        self.title = QLabel(title)
        T.style(self.title, "color: $TEXT_DIM; font-size: 12.5px;")
        lay.addWidget(self.title)
        lay.addStretch()

        for text, role in (("—", "min"), ("□", "max"), ("✕", "close")):
            b = QPushButton(text)
            b.setObjectName("titleButton")
            b.setProperty("role", role)
            b.setFixedSize(42, 30)
            b.clicked.connect(lambda _=False, r=role: self._act(r))
            lay.addWidget(b)

        self._drag_pos = None

    def _act(self, role: str):
        w = self.window
        if role == "min":
            w.showMinimized()
        elif role == "max":
            w.showMaximized() if w.isMaximized() else w.showNormal()
        else:
            w.close()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.window.windowState() == Qt.WindowNoState:
            self._drag_pos = e.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.LeftButton:
            self.window.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, e):
        w = self.window
        w.showMaximized() if w.isMaximized() else w.showNormal()


# ------------------------------------------------------------------------- sidebar
class NavButton(QPushButton):
    def __init__(self, icon: str, text: str, parent=None):
        super().__init__(f"  {icon}    {text}", parent)
        self.setObjectName("navButton")
        self.setCheckable(True)
        self.setFixedHeight(40)
        self.setCursor(Qt.PointingHandCursor)

    def set_active(self, active: bool):
        self.setProperty("active", "true" if active else "false")
        self.setChecked(active)
        self.style().unpolish(self)
        self.style().polish(self)


class Sidebar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(212)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 14, 10, 14)
        lay.setSpacing(3)

        brand = QLabel("ReplayStudio")
        brand.setObjectName("brandLabel")
        sub = QLabel("本地视频 · 无畏时刻 · 录屏")
        sub.setObjectName("brandSub")
        lay.addWidget(brand)
        lay.addWidget(sub)
        lay.addSpacing(14)

        self.buttons: dict[str, NavButton] = {}
        for key, icon, text in (
            ("moments", "◎", "无畏时刻"),
            ("local", "▤", "本地视频"),
            ("record", "●", "录屏"),
            ("favorites", "★", "收藏"),
            ("recent", "⟲", "最近播放"),
            ("settings", "⚙", "设置"),
        ):
            b = NavButton(icon, text)
            b.clicked.connect(lambda _=False, k=key: self.navigate.emit(k))
            self.buttons[key] = b
            lay.addWidget(b)
        lay.addStretch()

        self.stats = QLabel("")
        self.stats.setObjectName("statLabel")
        self.stats.setWordWrap(True)
        lay.addWidget(self.stats)

    navigate = Signal(str)

    def set_active(self, key: str):
        for k, b in self.buttons.items():
            b.set_active(k == key)


# -------------------------------------------------------------------- search input
class SearchEdit(QLineEdit):
    def __init__(self, placeholder="搜索地图、特工、标签、文件名 …", parent=None):
        super().__init__(parent)
        self.setObjectName("searchEdit")
        self.setPlaceholderText(placeholder)
        self.setFixedHeight(32)
        T.style(self, """
            QLineEdit {
                background: $SURFACE_2; border: 1px solid $BORDER;
                border-radius: 8px; padding: 0 10px 0 30px; color: $TEXT;
            }
            QLineEdit:focus { border-color: $ACCENT; }
        """)


# ---------------------------------------------------------------------- chip / tag
class ChipButton(QPushButton):
    """Small toggleable filter chip."""

    def __init__(self, text: str, value=None, parent=None):
        super().__init__(text, parent)
        self.value = value if value is not None else text
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(28)
        self._refresh()

    def _refresh(self):
        if self.isChecked():
            T.style(self, """
                QPushButton { background: $ACCENT; color: $ON_ACCENT; border: none;
                    border-radius: 8px; padding: 0 12px; font-weight: 600; font-size: 12px; }
            """)
        else:
            T.style(self, """
                QPushButton { background: $SURFACE_2; color: $TEXT_DIM;
                    border: 1px solid $BORDER; border-radius: 8px;
                    padding: 0 12px; font-size: 12px; }
                QPushButton:hover { background: $CARD_HOVER; color: $TEXT; }
            """)

    def setChecked(self, checked: bool):
        super().setChecked(checked)
        self._refresh()


# ------------------------------------------------------------------ card delegate
class VideoCardDelegate(QStyledItemDelegate):
    """Paints a poster card: cover, title, map/agent line, badges, favourite."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thumb_w = 288
        self.thumb_h = 162

    def sizeHint(self, option, index):  # noqa: N802
        return self._size(option, index)

    def _size(self, option, index):
        from PySide6.QtCore import QSize
        w = option.rect.width() if option.rect.width() > 40 else self.thumb_w
        return QSize(max(180, min(w, self.thumb_w)), self.thumb_h + 74)

    def paint(self, painter: QPainter, option, index):  # noqa: N802
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        data = index.data(Qt.UserRole) or {}
        rect = option.rect.adjusted(6, 6, -6, -6)
        hovered = bool(option.state & QStyle.State_MouseOver)
        selected = bool(option.state & QStyle.State_Selected)

        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), T.RADIUS, T.RADIUS)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(T.CARD_HOVER if hovered else T.CARD)))
        painter.drawPath(path)

        if selected:
            painter.setPen(QPen(QColor(T.ACCENT), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

        # ---- cover
        cover_rect = QRectF(rect.x(), rect.y(), rect.width(), self.thumb_h)
        pix = index.data(Qt.DecorationRole)
        if isinstance(pix, QPixmap) and not pix.isNull():
            cp = QPainterPath()
            cp.addRoundedRect(cover_rect, T.RADIUS, T.RADIUS)
            painter.setClipPath(cp)
            scaled = pix.scaled(int(cover_rect.width()), int(cover_rect.height()),
                                Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            px = cover_rect.x() + (cover_rect.width() - scaled.width()) / 2
            py = cover_rect.y() + (cover_rect.height() - scaled.height()) / 2
            painter.drawPixmap(int(px), int(py), scaled)
            painter.setClipping(False)
        else:
            g = QLinearGradient(cover_rect.topLeft(), cover_rect.bottomRight())
            g.setColorAt(0, QColor(T.GRAD_A))
            g.setColorAt(1, QColor(T.GRAD_B))
            painter.setBrush(QBrush(g))
            painter.setPen(Qt.NoPen)
            cp = QPainterPath()
            cp.addRoundedRect(cover_rect, T.RADIUS, T.RADIUS)
            painter.drawPath(cp)
            painter.setPen(QColor(T.TEXT_FAINT))
            painter.drawText(cover_rect, Qt.AlignCenter, "载入封面…")

        # ---- duration badge (bottom-right of cover)
        dur = human_time((data.get("duration_ms") or 0) / 1000.0)
        if dur:
            fm = QFontMetrics(painter.font())
            bw = fm.horizontalAdvance(dur) + 12
            br = QRectF(cover_rect.right() - bw - 8, cover_rect.bottom() - 24, bw, 18)
            painter.setBrush(QBrush(QColor(0, 0, 0, 190)))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(br, 4, 4)
            painter.setPen(QColor(T.TEXT))
            f2 = QFont(painter.font())
            f2.setPointSize(8)
            painter.setFont(f2)
            painter.drawText(br, Qt.AlignCenter, dur)
            painter.setFont(painter.font())

        # ---- missing-file overlay
        if data.get("file_state") == 2:
            painter.setBrush(QBrush(QColor(0, 0, 0, 165)))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(cover_rect, T.RADIUS, T.RADIUS)
            painter.setPen(QColor(T.AMBER))
            painter.drawText(cover_rect, Qt.AlignCenter, "源文件缺失")

        # ---- title
        tx = rect.x() + 10
        ty = cover_rect.bottom() + 8
        f_title = QFont(painter.font())
        f_title.setPointSize(9.5)
        f_title.setBold(True)
        painter.setFont(f_title)
        painter.setPen(QColor(T.TEXT))
        title = data.get("vname") or data.get("vtype") or os.path.basename(data.get("path", ""))
        painter.drawText(QRectF(tx, ty, rect.width() - 20, 18),
                         Qt.AlignLeft | Qt.AlignVCenter, elide(title, int(rect.width()) - 20, f_title))

        # ---- meta line
        f_meta = QFont(painter.font())
        f_meta.setPointSize(8.5)
        painter.setFont(f_meta)
        painter.setPen(QColor(T.TEXT_DIM))
        meta = " · ".join(x for x in (data.get("map_cn"), data.get("agent_cn"),
                                      data.get("mode_cn")) if x)
        painter.drawText(QRectF(tx, ty + 19, rect.width() - 20, 16),
                         Qt.AlignLeft | Qt.AlignVCenter, elide(meta, int(rect.width()) - 20, f_meta))

        # ---- footer: date + type badge + fav
        f_small = QFont(painter.font())
        f_small.setPointSize(8)
        painter.setFont(f_small)
        date = timestamp_ms(data.get("created_ms") or 0)
        painter.setPen(QColor(T.TEXT_FAINT))
        painter.drawText(QRectF(tx, ty + 36, 110, 14), Qt.AlignLeft | Qt.AlignVCenter, date)

        vtype = data.get("vtype") or ""
        if vtype:
            color = T.ACCENT if vtype in ("五杀时刻", "四杀时刻", "三杀时刻") else T.BLUE
            fw = QFontMetrics(f_small).horizontalAdvance(vtype) + 12
            bx = rect.right() - fw - 10
            if data.get("fav"):
                bx -= 18
            painter.setBrush(QBrush(QColor(T.qalpha(color, "33"))))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(bx, ty + 35, fw, 16), 4, 4)
            painter.setPen(QColor(color))
            painter.drawText(QRectF(bx, ty + 35, fw, 16), Qt.AlignCenter, vtype)

        if data.get("fav"):
            painter.setPen(QColor(T.AMBER))
            painter.drawText(QRectF(rect.right() - 26, ty + 35, 20, 16), Qt.AlignCenter, "★")

        painter.restore()
