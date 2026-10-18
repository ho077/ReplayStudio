"""Virtualised poster grid shared by every media page.

Qt's model/view already gives us virtualisation: only visible cells are painted,
so 1471 (or 14710) items cost the same as one screenful. Thumbnails are pulled
in on demand by the delegate asking the model, and the model asks the
ThumbnailService; completed thumbnails emit `dataChanged` for their own row so
only that cell repaints.
"""
from __future__ import annotations

from PySide6.QtCore import (QAbstractListModel, QModelIndex, QObject, Qt,
                            Signal)
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QMenu, QScroller

from .widgets import VideoCardDelegate


class VideoListModel(QAbstractListModel):
    def __init__(self, thumbs, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self._pixmaps: dict[str, QPixmap] = {}
        self.thumbs = thumbs
        self.thumbs.done.connect(self._thumb_ready)

    # ---- model interface
    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return len(self.rows)

    def data(self, index, role=Qt.DisplayRole):  # noqa: N802
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.UserRole:
            return row
        if role == Qt.DecorationRole:
            pm = self._pixmaps.get(row.get("video_id", ""))
            if pm:
                return pm
            self._request(index.row(), row)
            return None
        if role == Qt.ToolTipRole:
            return row.get("path", "")
        return None

    def _request(self, row: int, rowdata: dict):
        vid = rowdata.get("video_id", "")
        if not vid or self.thumbs.is_cached(vid):
            return
        self.thumbs.request(vid, rowdata.get("path", ""),
                            rowdata.get("poster", ""), 300, 170)

    def _thumb_ready(self, video_id: str, image):
        pm = QPixmap.fromImage(image)
        self._pixmaps[video_id] = pm
        for i, r in enumerate(self.rows):
            if r.get("video_id") == video_id:
                self.dataChanged.emit(self.index(i), self.index(i), [Qt.DecorationRole])
                break

    # ---- mutation
    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def append_rows(self, rows):
        if not rows:
            return
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(rows) - 1)
        self.rows.extend(rows)
        self.endInsertRows()

    def update_row(self, video_id: str, **fields):
        for i, r in enumerate(self.rows):
            if r.get("video_id") == video_id:
                r.update(fields)
                self.dataChanged.emit(self.index(i), self.index(i), [Qt.UserRole])
                break

    def row_at(self, index: QModelIndex):
        if index.isValid() and index.row() < len(self.rows):
            return self.rows[index.row()]
        return None

    def index_of(self, video_id: str) -> int:
        for i, r in enumerate(self.rows):
            if r.get("video_id") == video_id:
                return i
        return -1


class VideoGridView(QListView):
    """Icon-mode grid with lazy thumbnails, paging and a context menu."""

    playRequested = Signal(dict)
    toggleFavorite = Signal(dict)
    addTagRequested = Signal(dict)
    locateRequested = Signal(dict)
    needMore = Signal()

    def __init__(self, thumbs, parent=None):
        super().__init__(parent)
        self.model = VideoListModel(thumbs, self)
        self.setModel(self.model)
        self.delegate = VideoCardDelegate(self)
        self.setItemDelegate(self.delegate)

        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setSpacing(4)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setFrameShape(QListView.NoFrame)
        self.setMinimumWidth(320)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.doubleClicked.connect(lambda i: self._emit_play(i))
        self.verticalScrollBar().valueChanged.connect(self._maybe_more)

    def _emit_play(self, index):
        row = self.model.row_at(index)
        if row:
            self.playRequested.emit(row)

    def _maybe_more(self, value):
        sb = self.verticalScrollBar()
        if value >= sb.maximum() - 400:
            self.needMore.emit()

    def _menu(self, pos):
        index = self.indexAt(pos)
        row = self.model.row_at(index)
        if not row:
            return
        menu = QMenu(self)
        menu.setStyleSheet("QMenu{background:#26262E;color:#EDEDF0;border:1px solid #33333D;"
                           "padding:4px;} QMenu::item{padding:7px 24px;border-radius:5px;}"
                           "QMenu::item:selected{background:#FF4655;color:white;}")
        a1 = menu.addAction("▶  播放")
        a2 = menu.addAction("★  收藏 / 取消收藏")
        a3 = menu.addAction("🏷  编辑标签…")
        a4 = menu.addAction("📂  在资源管理器中显示")
        act = menu.exec(self.viewport().mapToGlobal(pos))
        if act == a1:
            self.playRequested.emit(row)
        elif act == a2:
            self.toggleFavorite.emit(row)
        elif act == a3:
            self.addTagRequested.emit(row)
        elif act == a4:
            self.locateRequested.emit(row)

    def set_rows(self, rows):
        self.model.set_rows(rows)

    def append_rows(self, rows):
        self.model.append_rows(rows)

    def current_row(self):
        return self.model.row_at(self.currentIndex())

    def select_video(self, video_id: str):
        i = self.model.index_of(video_id)
        if i >= 0:
            self.setCurrentIndex(self.model.index(i))
            self.scrollTo(self.model.index(i))
