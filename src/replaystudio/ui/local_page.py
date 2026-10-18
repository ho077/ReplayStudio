"""Generic local video library page (folder based)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QListWidget, QMessageBox, QPushButton,
                               QSplitter, QVBoxLayout)

from ..core.local_scanner import LocalScanWorker
from . import theme as T
from .grid import VideoGridView
from .widgets import SearchEdit

PAGE = 240


class LocalPage(QFrame):
    playRequested = Signal(dict)
    statusMessage = Signal(str)

    def __init__(self, db, thumbs, parent=None):
        super().__init__(parent)
        self.db = db
        self.offset = 0
        self._total = 0
        self._loading = False
        self.worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("filterBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(8)
        b_add = QPushButton("＋  添加视频文件夹")
        b_add.setProperty("accent", "true")
        b_add.clicked.connect(self._add_folder)
        bl.addWidget(b_add)
        b_del = QPushButton("移除")
        b_del.clicked.connect(self._remove_folder)
        bl.addWidget(b_del)
        b_rescan = QPushButton("⟳  扫描")
        b_rescan.clicked.connect(self._scan)
        bl.addWidget(b_rescan)
        self.search = SearchEdit("搜索文件名 …")
        self.search.setMinimumWidth(220)
        self.search.returnPressed.connect(self.reload)
        bl.addWidget(self.search)
        bl.addStretch()
        self.stats = QLabel("")
        self.stats.setObjectName("statLabel")
        bl.addWidget(self.stats)
        root.addWidget(bar)

        split = QSplitter(Qt.Horizontal)
        self.folders = QListWidget()
        self.folders.setMaximumWidth(240)
        self.folders.setStyleSheet(f"""
            QListWidget {{ background: {T.SURFACE}; border: none; padding: 6px; }}
            QListWidget::item {{ padding: 8px 10px; border-radius: 6px; color: {T.TEXT_DIM}; }}
            QListWidget::item:selected {{ background: {T.ACCENT}; color: white; }}
            QListWidget::item:hover {{ background: {T.SURFACE_2}; color: {T.TEXT}; }}
        """)
        self.folders.currentTextChanged.connect(self.reload)
        split.addWidget(self.folders)

        self.grid = VideoGridView(thumbs, self)
        self.grid.playRequested.connect(self.playRequested.emit)
        self.grid.needMore.connect(self.load_more)
        self.grid.setStyleSheet(f"QListView {{ background: {T.BG}; padding: 6px; }}")
        split.addWidget(self.grid)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        self.refresh_roots()

    # ------------------------------------------------------------------- roots
    def refresh_roots(self):
        self.folders.blockSignals(True)
        self.folders.clear()
        self.folders.addItem("全部文件夹")
        for r in self.db.list_roots("local"):
            self.folders.addItem(r["path"])
        self.folders.blockSignals(False)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择包含视频的文件夹")
        if not d:
            return
        self.db.add_root(d, "local")
        self.refresh_roots()
        self._scan()

    def _remove_folder(self):
        cur = self.folders.currentItem()
        if not cur or self.folders.currentRow() == 0:
            return
        self.db.remove_root(cur.text())
        self.refresh_roots()
        self.reload()

    def _scan(self):
        roots = [r["path"] for r in self.db.list_roots("local")]
        if not roots:
            QMessageBox.information(self, "提示", "请先添加一个视频文件夹。")
            return
        self.worker = LocalScanWorker(self.db, roots)
        self.worker.finished_ok.connect(self._scan_done)
        self.worker.progress.connect(lambda n, m: self.statusMessage.emit(f"已索引 {n} 个文件 …"))
        self.worker.start()

    def _scan_done(self, n: int):
        self.statusMessage.emit(f"本地视频扫描完成，共 {n} 个文件")
        self.refresh_roots()
        self.reload()

    # -------------------------------------------------------------------- data
    def current_filters(self) -> dict:
        f = {"source": "local", "sort": "最新", "q": self.search.text().strip()}
        cur = self.folders.currentItem()
        if cur and self.folders.currentRow() > 0:
            f["path_prefix"] = cur.text()
        return f

    def reload(self):
        self.offset = 0
        f = self.current_filters()
        prefix = f.pop("path_prefix", None)
        if prefix:
            rows_all, total = self.db.query_videos(f, 5000, 0)
            rows = [r for r in rows_all if r.get("path", "").startswith(prefix)]
            self.grid.set_rows(rows[:PAGE])
            self.offset = len(rows[:PAGE])
            self._total = len(rows)
        else:
            rows, total = self.db.query_videos(f, PAGE, 0)
            self.grid.set_rows(rows)
            self.offset = len(rows)
            self._total = total
        self.stats.setText(f"显示 {self.grid.model.rowCount()} / {self._total}")

    def load_more(self):
        if self._loading or self.offset >= self._total:
            return
        self._loading = True
        rows, _ = self.db.query_videos(self.current_filters(), PAGE, self.offset)
        if rows:
            self.grid.append_rows(rows)
            self.offset += len(rows)
        self._loading = False
        self.stats.setText(f"显示 {self.grid.model.rowCount()} / {self._total}")

    def rows(self):
        return self.grid.model.rows
