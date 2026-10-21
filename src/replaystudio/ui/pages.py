"""Secondary pages: favourites, recent playback (plus the recording workbench)."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import theme as T
from .grid import VideoGridView
from .record_page import RecordPage  # noqa: F401  (re-exported for main_window)

__all__ = ["SimpleGridPage", "RecordPage"]


class SimpleGridPage(QFrame):
    """Favourites / recently played — same grid, different query."""

    playRequested = Signal(dict)

    def __init__(self, db, thumbs, kind: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.kind = kind
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("filterBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 12, 14, 12)
        title = QLabel("收藏夹" if kind == "favorites" else "最近播放")
        T.style(title, "color: $TEXT; font-size: 15px; font-weight: 600;")
        bl.addWidget(title)
        bl.addStretch()
        b = QPushButton("⟳  刷新")
        b.clicked.connect(self.reload)
        bl.addWidget(b)
        self.stats = QLabel("")
        self.stats.setObjectName("statLabel")
        bl.addWidget(self.stats)
        root.addWidget(bar)

        self.grid = VideoGridView(thumbs, self)
        self.grid.playRequested.connect(self.playRequested.emit)
        T.style(self.grid, "QListView { background: $BG; padding: 6px; }")
        root.addWidget(self.grid, 1)
        self.reload()

    def reload(self):
        if self.kind == "favorites":
            rows, total = self.db.query_videos({"favorite": True, "sort": "最新"}, 500, 0)
        else:
            rows = self.db.recent(120)
            total = len(rows)
        self.grid.set_rows(rows)
        self.stats.setText(f"{len(rows)} 个片段")

    def rows(self):
        return self.grid.model.rows
