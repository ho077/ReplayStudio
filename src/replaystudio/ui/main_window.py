"""Application shell: navigation, scan orchestration, playback routing."""
from __future__ import annotations

import os
import subprocess
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QInputDialog,
                               QLabel, QMainWindow, QProgressBar, QPushButton,
                               QSizeGrip, QStackedWidget, QVBoxLayout, QWidget)

from ..aclos import reader
from ..core.config import Settings
from ..core.db import Database
from ..core.scanner import ScanWorker
from ..media.thumbs import ThumbnailService
from . import theme as T
from .local_page import LocalPage
from .moments_page import MomentsPage
from .pages import RecordPage, SimpleGridPage
from .player_page import PlayerPage
from .settings_page import SettingsPage
from .widgets import Sidebar, TitleBar


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ReplayStudio")
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setMinimumSize(1180, 720)
        self.resize(1440, 900)
        self.setStyleSheet(T.QSS)

        self.db = Database()
        self.settings = Settings(self.db)
        T.init_theme(None, self.settings.get("theme", T.DEFAULT_KEY))
        self.setStyleSheet(T.QSS)
        self.thumbs = ThumbnailService(self)
        self._scan_worker = None
        self._play_context: list[dict] = []
        self._last_history_write = 0.0

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.titlebar = TitleBar(self, "ReplayStudio")
        root.addWidget(self.titlebar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = Sidebar()
        self.sidebar.navigate.connect(self.navigate)
        body.addWidget(self.sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)

        # ---- status bar
        status = QFrame()
        T.style(status, "background: $SURFACE; border-top: 1px solid $BORDER;")
        sl = QHBoxLayout(status)
        sl.setContentsMargins(14, 5, 14, 5)
        self.status_label = QLabel("就绪")
        T.style(self.status_label, "color: $TEXT_DIM; font-size: 12px;")
        sl.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(180)
        self.progress.hide()
        sl.addWidget(self.progress)
        grip = QSizeGrip(status)
        sl.addWidget(grip, 0, Qt.AlignBottom | Qt.AlignRight)
        right.addWidget(status)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        self._build_pages()
        self._bind_shortcuts()
        self.navigate("moments")
        QTimer.singleShot(120, self._bootstrap)

    # ------------------------------------------------------------------- pages
    def _build_pages(self):
        self.moments = MomentsPage(self.db, self.thumbs, self)
        self.local = LocalPage(self.db, self.thumbs, self)
        self.record = RecordPage(self.db, self)
        self.favorites = SimpleGridPage(self.db, self.thumbs, "favorites", self)
        self.recent = SimpleGridPage(self.db, self.thumbs, "recent", self)
        self.settings_page = SettingsPage(self.db, self.settings, self)
        self.player_page = PlayerPage(self.settings, self)
        self.player_page.set_db(self.db)

        for w in (self.moments, self.local, self.record, self.favorites,
                  self.recent, self.settings_page, self.player_page):
            self.stack.addWidget(w)

        # page signals
        self.moments.playRequested.connect(self.open_player)
        self.local.playRequested.connect(self.open_player)
        self.favorites.playRequested.connect(self.open_player)
        self.recent.playRequested.connect(self.open_player)
        self.moments.scanRequested.connect(self.start_scan)
        self.moments.favoriteToggled.connect(self.toggle_favorite)
        self.moments.tagRequested.connect(self.edit_tags)
        self.moments.locateRequested.connect(self.locate_file)
        self.local.statusMessage.connect(self.set_status)
        self.settings_page.rebuildRequested.connect(lambda: self.start_scan(True))
        self.settings_page.recheckRequested.connect(self.recheck_files)
        self.settings_page.dirChanged.connect(lambda _d: self.start_scan(False))

        self.player_page.backRequested.connect(lambda: self.navigate(self._nav_before_player))
        self.player_page.prevRequested.connect(lambda: self.step(-1))
        self.player_page.nextRequested.connect(lambda: self.step(1))
        self.player_page.positionReported.connect(self._record_position)

        self._nav_before_player = "moments"

    def apply_theme(self, key: str):
        """Swap the palette live: rebind constants → re-render every registered
        `$KEY` template → repaint everything the delegate draws."""
        T.apply_theme(QApplication.instance(), key)
        # The window carries its own copy of the QSS, which takes precedence
        # over the app-level one — refresh it too.
        self.setStyleSheet(T.QSS)
        self.settings["theme"] = T.current_key()
        for page in (self.moments, self.local, self.favorites, self.recent):
            page.grid.viewport().update()
        self.player_page.timeline.update()
        self.set_status(f"已切换主题：{T.palette().name}")

    def navigate(self, key: str):
        pages = {"moments": self.moments, "local": self.local, "record": self.record,
                 "favorites": self.favorites, "recent": self.recent,
                 "settings": self.settings_page}
        page = pages.get(key, self.moments)
        if key in ("favorites", "recent"):
            page.reload()
        if key == "settings":
            self.settings_page.refresh_stats()
        self.stack.setCurrentWidget(page)
        self.sidebar.set_active(key)
        self._current_nav = key
        if key != "settings":
            self._nav_before_player = key
        self._refresh_sidebar_stats()

    def _refresh_sidebar_stats(self):
        s = self.db.stats()
        self.sidebar.stats.setText(
            f"索引 {s['videos']} 个片段\n{s['bytes']/1024**3:.1f} GB · {s['matches']} 场对局")

    # ------------------------------------------------------------------ startup
    def _bootstrap(self):
        s = self.db.stats()
        if s["videos"] == 0:
            self.set_status("首次启动：正在建立本地索引 …")
            QTimer.singleShot(200, lambda: self.start_scan(False))
        else:
            self.moments.refresh_facets()
            self.moments.reload()
            self.set_status(f"已载入本地索引：{s['videos']} 个片段")
            QTimer.singleShot(1500, self.recheck_files)

    # -------------------------------------------------------------------- scan
    def start_scan(self, force: bool = False):
        if self._scan_worker and self._scan_worker.isRunning():
            self.set_status("扫描正在进行中 …")
            return
        db_dir = self.settings.get("aclos_dir") or reader.default_db_dir()
        self._scan_worker = ScanWorker(self.db, db_dir, force=force)
        self._scan_worker.progress.connect(self._scan_progress)
        self._scan_worker.finished_ok.connect(self._scan_done)
        self._scan_worker.failed.connect(self._scan_failed)
        self.progress.show()
        self._scan_worker.start()

    def _scan_progress(self, pct: int, msg: str):
        self.progress.setValue(pct)
        self.set_status(msg)

    def _scan_done(self, summary: dict):
        self.progress.hide()
        self.moments.refresh_facets()
        self.moments.reload()
        self._refresh_sidebar_stats()
        parts = [f"账号 {summary['accounts']}", f"对局 {summary['matches']}",
                 f"片段 {summary['videos']}", f"事件 {summary['events']}"]
        if summary["skipped"]:
            parts.append(f"增量跳过 {summary['skipped']}")
        if summary["missing"]:
            parts.append(f"缺失 {summary['missing']}")
        msg = "扫描完成：" + " · ".join(parts) + f"（{summary['seconds']}s）"
        if summary["errors"]:
            msg += f"｜{len(summary['errors'])} 个账号解析失败"
        self.set_status(msg)

    def _scan_failed(self, msg: str):
        self.progress.hide()
        self.set_status(msg)

    def recheck_files(self):
        n = self.db.refresh_file_states()
        self.moments.reload()
        self.set_status(f"文件状态已刷新，{n} 个条目状态发生变化")

    # ----------------------------------------------------------------- playback
    def open_player(self, row: dict):
        source_page = self.stack.currentWidget()
        if source_page is not self.player_page:
            if hasattr(source_page, "rows"):
                self._play_context = list(source_page.rows())
        start = 0.0
        if int(self.settings.get("remember_position", 1)):
            start = self.db.last_position(row.get("video_id", "")) or 0.0
        self.player_page.clear_error()
        self.stack.setCurrentWidget(self.player_page)
        self.player_page.load(row, start)

    def step(self, delta: int):
        if not self._play_context:
            return
        cur = self.player_page._current
        if not cur:
            return
        vid = cur.get("video_id")
        idx = next((i for i, r in enumerate(self._play_context)
                    if r.get("video_id") == vid), -1)
        if idx < 0:
            return
        nxt = idx + delta
        if 0 <= nxt < len(self._play_context):
            row = self._play_context[nxt]
            self.player_page.clear_error()
            self.player_page.load(row, 0.0)

    def _record_position(self, video_id: str, pos: float, duration: float):
        if not video_id or duration <= 0:
            return
        now = time.time()
        if now - self._last_history_write < 5.0:
            return
        if pos < 1 or pos > duration * 0.95:
            return
        self._last_history_write = now
        self.db.add_history(video_id, pos, duration)

    # ------------------------------------------------------------------ actions
    def toggle_favorite(self, row: dict):
        fav = self.db.toggle_favorite(row.get("video_id", ""))
        self.moments.refresh_row(row.get("video_id", ""), fav=1 if fav else 0)
        self.set_status(("已收藏： " if fav else "已取消收藏： ") + (row.get("vname") or ""))

    def edit_tags(self, row: dict):
        vid = row.get("video_id", "")
        current = ", ".join(self.db.get_tags(vid))
        text, ok = QInputDialog.getText(self, "编辑标签", "用逗号分隔多个标签：", text=current)
        if not ok:
            return
        tags = [t.strip() for t in text.replace("，", ",").split(",") if t.strip()]
        self.db.set_tags(vid, tags)
        self.set_status(f"已保存 {len(tags)} 个标签")

    @staticmethod
    def locate_file(row: dict):
        path = os.path.normpath(row.get("path", ""))
        if not path or not os.path.exists(path):
            return
        subprocess.Popen(["explorer", "/select,", path])

    def set_status(self, text: str):
        self.status_label.setText(text)

    # ---------------------------------------------------------------- shortcuts
    def _bind_shortcuts(self):
        def add(key, fn):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(fn)

        add("Space", lambda: self.player_page.toggle_play()
            if self.stack.currentWidget() is self.player_page else None)
        add("Right", lambda: self.player_page.seek_relative(5)
            if self.stack.currentWidget() is self.player_page else None)
        add("Left", lambda: self.player_page.seek_relative(-5)
            if self.stack.currentWidget() is self.player_page else None)
        add("Up", lambda: self._volume_step(5))
        add("Down", lambda: self._volume_step(-5))
        add("F", lambda: self.player_page.toggle_fullscreen()
            if self.stack.currentWidget() is self.player_page else None)
        add("Escape", lambda: self.navigate(self._nav_before_player)
            if self.stack.currentWidget() is self.player_page else None)
        add("[", lambda: self.player_page.set_speed(max(0.25, self.player_page.player.speed - 0.25))
            if self.stack.currentWidget() is self.player_page and self.player_page.player else None)
        add("]", lambda: self.player_page.set_speed(min(4.0, self.player_page.player.speed + 0.25))
            if self.stack.currentWidget() is self.player_page and self.player_page.player else None)
        add("Ctrl+F", lambda: self.moments.search.setFocus())

    def _volume_step(self, delta: int):
        if self.stack.currentWidget() is not self.player_page:
            return
        v = self.player_page.vol.value() + delta
        self.player_page.vol.setValue(max(0, min(130, v)))

    # ------------------------------------------------------------------ teardown
    def closeEvent(self, e):  # noqa: N802
        self.player_page.shutdown()
        self.thumbs.pool.waitForDone(3000)
        super().closeEvent(e)
