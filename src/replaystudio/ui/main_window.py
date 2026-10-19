"""Application shell: navigation, scan orchestration, playback routing."""
from __future__ import annotations

import os
import subprocess
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QInputDialog,
                               QLabel, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton,
                               QSizeGrip, QStackedWidget, QVBoxLayout, QWidget)

from ..aclos import reader
from ..core import diagnostics
from ..core.config import Settings
from ..core.db import Database
from ..core.local_scanner import VIDEO_EXTS, video_id_for
from ..core.scanner import ScanWorker
from ..media.thumbs import ThumbnailService
from . import theme as T
from .local_page import LocalPage
from .moments_page import MomentsPage
from .pages import RecordPage, SimpleGridPage
from .player_page import SUB_EXTS
from .player_tabs import PlayerTabs
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
        self.setAcceptDrops(True)

        self.db = Database()
        self.settings = Settings(self.db)
        T.init_theme(None, self.settings.get("theme", T.DEFAULT_KEY))
        self.setStyleSheet(T.QSS)
        self.thumbs = ThumbnailService(self)
        self._scan_worker = None
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
        # One tab == one playback session (own page, own mpv instance).
        self.player_tabs = PlayerTabs(self.settings, self.db, self)

        for w in (self.moments, self.local, self.record, self.favorites,
                  self.recent, self.settings_page, self.player_tabs):
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

        self.player_tabs.positionReported.connect(self._record_position)
        self.player_tabs.fileDropped.connect(lambda p: self.open_paths([p]))
        self.player_tabs.prevRequested.connect(lambda: self.step(-1))
        self.player_tabs.nextRequested.connect(lambda: self.step(1))
        self.player_tabs.playlistIndexSelected.connect(self.play_index)
        self.player_tabs.empty.connect(self._on_tabs_empty)
        self.player_tabs.changed.connect(self._on_tab_count_changed)

        self._nav_before_player = "moments"
        self._shutting_down = False

    @property
    def player_page(self):
        """The currently focused playback page (None when no tab is open)."""
        return self.player_tabs.current_page()

    def _on_tabs_empty(self):
        if self._shutting_down:
            return
        self.navigate(self._nav_before_player)

    def _on_tab_count_changed(self, count: int):
        """Keep the sidebar in sync: show/refresh 正在播放, drop it when empty."""
        self.sidebar.set_playing(count, self.player_tabs.active_title())
        if count and self.stack.currentWidget() is self.player_tabs:
            self.sidebar.set_active("player")

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
        self.player_tabs.refresh_theme()
        self.set_status(f"已切换主题：{T.palette().name}")

    def navigate(self, key: str):
        # "player" is the playback area itself; it only exists while a tab is
        # open, so an empty request falls back to where the user came from.
        if key == "player" and self.player_tabs.count() == 0:
            key = self._nav_before_player
        pages = {"moments": self.moments, "local": self.local, "record": self.record,
                 "favorites": self.favorites, "recent": self.recent,
                 "player": self.player_tabs, "settings": self.settings_page}
        page = pages.get(key, self.moments)
        if key in ("favorites", "recent"):
            page.reload()
        if key == "settings":
            self.settings_page.refresh_stats()
        self.stack.setCurrentWidget(page)
        self.sidebar.set_active(key)
        self._current_nav = key
        if key not in ("settings", "player"):
            self._nav_before_player = key
        # Leaving the player area must silence it. Previously the page was only
        # hidden, so mpv kept decoding and the audio kept playing in the
        # background — that was the reported bug.
        if page is self.player_tabs:
            self.player_tabs.activate_current()
        else:
            self.player_tabs.park_all()
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
    def open_player(self, row: dict, context: list[dict] | None = None):
        """Open `row` in a fresh playback tab; `context` fills that tab's playlist."""
        source_page = self.stack.currentWidget()
        if context is not None:
            ctx = list(context)
        elif source_page is not self.player_tabs and hasattr(source_page, "rows"):
            ctx = list(source_page.rows())
        else:
            ctx = [row]
        start = 0.0
        if int(self.settings.get("remember_position", 1)):
            start = self.db.last_position(row.get("video_id", "")) or 0.0
        self.stack.setCurrentWidget(self.player_tabs)
        page = self.player_tabs.open_row(row, ctx, start)
        self.sidebar.set_active("player")
        return page

    def play_index(self, index: int) -> bool:
        """Playback requested straight from the playlist tab."""
        page = self.player_tabs.current_page()
        if page is None:
            return False
        ctx = page.context()
        if not (0 <= index < len(ctx)):
            return False
        page.clear_error()
        page.load(ctx[index], 0.0)
        return True

    def step(self, delta: int) -> bool:
        """Previous/next inside the active tab's context. Returns False at the
        ends so auto-play can stop instead of looping forever."""
        page = self.player_tabs.current_page()
        if page is None:
            return False
        ctx = page.context()
        cur = page.current_row()
        if not cur or not ctx:
            return False
        vid = cur.get("video_id")
        idx = next((i for i, r in enumerate(ctx) if r.get("video_id") == vid), -1)
        if idx < 0:
            return False
        nxt = idx + delta
        if not (0 <= nxt < len(ctx)):
            return False
        page.clear_error()
        page.load(ctx[nxt], 0.0)
        return True

    # ------------------------------------------------------- external files
    def make_row(self, path: str) -> dict:
        """Build a playable row for a file that may not be indexed yet.

        Indexed files keep their rich metadata; anything else gets a lightweight
        ad-hoc row so drag&drop / "open with" works before any scanning.
        """
        row = self.db.get_video_by_path(path)
        if row:
            return row
        try:
            st = os.stat(path)
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, 0.0
        return {
            "video_id": video_id_for(path),
            "path": path,
            "vname": os.path.splitext(os.path.basename(path))[0],
            "vtype": "外部文件",
            "source": "local",
            "size_bytes": size,
            "created_ms": int(mtime * 1000),
            "file_state": 1,
        }

    def open_paths(self, paths: list[str], add_to_library: bool = False):
        """Open one or more files dropped / passed on the command line."""
        ok = [p for p in paths if os.path.isfile(p)
              and os.path.splitext(p)[1].lower() in VIDEO_EXTS]
        if not ok:
            self.set_status("没有可播放的视频文件")
            return
        rows = [self.make_row(p) for p in ok]
        if add_to_library:
            for p in ok:
                self.db.add_root(os.path.dirname(p), "local")
        self.open_player(rows[0], context=rows)
        self.set_status(f"已打开 {len(rows)} 个文件" + ("（已加入本地库）" if add_to_library else ""))

    # ------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        files: list[str] = []
        dirs: list[str] = []
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if not p:
                continue
            (dirs if os.path.isdir(p) else files).append(p)
        e.acceptProposedAction()

        vids = [p for p in files if os.path.splitext(p)[1].lower() in VIDEO_EXTS]
        subs = [p for p in files if os.path.splitext(p)[1].lower() in SUB_EXTS]

        if vids:
            self.open_paths(vids)
        elif subs:
            page = self.player_tabs.current_page()
            if page and page.player:
                for s in subs:
                    page.player.add_subtitle(s)
                page.show_osd(f"已挂载 {len(subs)} 个字幕文件")

        if dirs:
            d = dirs[0]
            ans = QMessageBox.question(
                self, "添加文件夹",
                f"是否把「{d}」加入本地视频库？\n\n"
                "仅建立索引，不会移动、复制或转码任何文件。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ans == QMessageBox.Yes:
                self.db.add_root(d, "local")
                self.local.refresh_roots()
                self.local._scan()
                self.set_status(f"已添加文件夹并开始扫描：{d}")

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

        def page():
            """The active playback tab, or None when the player is not visible."""
            if self.stack.currentWidget() is not self.player_tabs:
                return None
            return self.player_tabs.current_page()

        def run(fn):
            pg = page()
            if pg is not None:
                fn(pg)

        def seek(direction: int, long_jump: bool = False):
            pg = page()
            if pg is None:
                return
            key = "seek_step_long" if long_jump else "seek_step"
            try:
                step = abs(int(self.settings.get(key, 5)))
            except (TypeError, ValueError):
                step = 5
            pg.seek_relative(direction * step)
            pg.show_osd(f"{direction*step:+d}s", 900)

        add("Space", lambda: run(lambda pg: pg.toggle_play()))
        add("Right", lambda: seek(1))
        add("Left", lambda: seek(-1))
        add("Shift+Right", lambda: seek(1, True))
        add("Shift+Left", lambda: seek(-1, True))
        add("Up", lambda: self._volume_step(5))
        add("Down", lambda: self._volume_step(-5))
        add("F", lambda: run(lambda pg: pg.toggle_fullscreen()))
        add("M", lambda: run(lambda pg: pg._toggle_mute()))
        add("S", lambda: run(lambda pg: pg.take_screenshot()))
        add("J", lambda: run(lambda pg: pg.cycle_subtitle(True)))
        add("Shift+J", lambda: run(lambda pg: pg.player and pg.player.select_subtitle(None)))
        add("K", lambda: run(lambda pg: pg.cycle_audio(True)))
        add("A", lambda: run(lambda pg: pg._menu_aspect()))
        add("P", lambda: page() and self.step(-1))
        add("N", lambda: page() and self.step(1))
        # Escape / 返回 both mean "close this playback tab", which also stops
        # the engine — closing the tab *is* stopping the video.
        add("Escape", lambda: page() and self.player_tabs.close_current())
        add("[", lambda: run(lambda pg: pg.player and pg.set_speed(max(0.25, pg.player.speed - 0.25))))
        add("]", lambda: run(lambda pg: pg.player and pg.set_speed(min(4.0, pg.player.speed + 0.25))))
        add("Ctrl+F", lambda: self.moments.search.setFocus())
        add("Ctrl+O", lambda: self._open_file_dialog())

    def _open_file_dialog(self):
        from PySide6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            self, "打开视频文件", "",
            "视频文件 (*.mp4 *.mkv *.mov *.avi *.webm *.flv *.wmv *.m4v *.ts);;所有文件 (*.*)")
        if paths:
            self.open_paths(paths)

    def _volume_step(self, delta: int):
        page = self.player_tabs.current_page() if self.stack.currentWidget() is self.player_tabs else None
        if page is None:
            return
        v = page.vol.value() + delta
        page.vol.setValue(max(0, min(130, v)))

    # ------------------------------------------------------------------ teardown
    def closeEvent(self, e):  # noqa: N802
        self._shutting_down = True
        # Destroy every engine before the widgets go away, so mpv never gets a
        # chance to render into a window that is being torn down (the likely
        # cause of the silent crash we were chasing).
        self.player_tabs.close_all()
        self.thumbs.pool.waitForDone(3000)
        diagnostics.note("window closed")
        super().closeEvent(e)
