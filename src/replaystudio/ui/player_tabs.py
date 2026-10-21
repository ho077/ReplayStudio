"""Tab host for playback: one video per tab, one engine per tab.

Why tabs instead of one reused page
-----------------------------------
The original design kept a single ``PlayerPage`` and merely switched the
stacked widget when the user pressed 返回. The page was *hidden* but the mpv
instance kept decoding — audio kept playing, the tick timer kept writing
history, and frames kept being pushed into a native surface that was no longer
visible. That last part is also the most likely source of the hard crash we saw:
rendering into a hidden window with d3d11va is not a state libmpv is designed to
survive indefinitely.

So: a tab **is** a playback session. Opening a video creates a session; closing
the tab (or pressing 返回) tears the engine down and frees the surface. Anything
parked in a background tab is paused *and* muted, so no two videos can ever make
noise at the same time.
"""
from __future__ import annotations

import os
import weakref

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QLabel, QStackedWidget, QTabBar,
                               QToolButton, QVBoxLayout)

from ..core import diagnostics
from . import theme as T
from .player_page import PlayerPage

MAX_TABS = 6
"""Hard cap so a user dragging twenty files in cannot spawn twenty decoders
and exhaust GPU memory."""


class PlayerTabs(QFrame):
    """Holds the open playback sessions and routes signals from the active one."""

    empty = Signal()                          # last tab was closed
    changed = Signal(int)                     # tab count changed
    positionReported = Signal(str, float, float)
    fileDropped = Signal(str)
    prevRequested = Signal()
    nextRequested = Signal()
    playlistIndexSelected = Signal(int)

    def __init__(self, settings, db, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.db = db

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.bar = QTabBar()
        self.bar.setExpanding(False)
        # NOT movable: `close_tab`/`_on_current_changed` address tabs by index,
        # and the QStackedWidget body keeps its own insertion order. Dragging a
        # tab would desynchronise the two and show the wrong player.
        self.bar.setMovable(False)
        # The built-in close button is drawn by the style and cannot be kept
        # inside a rounded tab: it overflowed the corner (reported). We supply
        # our own 18px button per tab instead, which Qt positions inside the
        # tab rect by construction.
        self.bar.setTabsClosable(False)
        self.bar.setElideMode(Qt.ElideRight)
        self.bar.setUsesScrollButtons(True)
        self.bar.setDrawBase(False)
        self.bar.setFocusPolicy(Qt.NoFocus)
        self.bar.setAccessibleName("播放标签")
        T.style(self.bar, """
            QTabBar { background: $BG; }
            QTabBar::tab {
                background: $SURFACE_2;
                color: $TEXT_DIM;
                border: 1px solid $BORDER;
                border-bottom: none;
                border-top-left-radius: ${RADIUS}px;
                border-top-right-radius: ${RADIUS}px;
                padding: 6px 9px 6px 11px;
                margin-right: 4px;
                margin-top: 4px;
                max-width: 220px;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background: $SURFACE;
                color: $TEXT;
                border-color: $ACCENT;
            }
            QTabBar::tab:hover:!selected { color: $TEXT; background: $CARD_HOVER; }
            QToolButton#tabClose {
                background: transparent;
                border: none;
                border-radius: 9px;
                color: $TEXT_FAINT;
                font-size: 11px;
                font-weight: 700;
                padding: 0px;
            }
            QToolButton#tabClose:hover { background: $CLOSE_HOVER; color: #FFFFFF; }
            QToolButton#tabClose:pressed { background: $ACCENT; color: $ON_ACCENT; }
        """)
        self.bar.currentChanged.connect(self._on_current_changed)
        root.addWidget(self.bar)

        self.body = QStackedWidget()
        root.addWidget(self.body, 1)

        self.placeholder = QLabel(
            "未打开任何视频\n\n从媒体库双击任意片段，或把视频文件拖进窗口")
        self.placeholder.setAlignment(Qt.AlignCenter)
        T.style(self.placeholder,
                "color: $TEXT_FAINT; font-size: 13px; line-height: 170%;")
        root.addWidget(self.placeholder, 1)

        self._sync_visibility()

    # ------------------------------------------------------------- accessors
    def count(self) -> int:
        return self.body.count()

    def current_page(self) -> PlayerPage | None:
        w = self.body.currentWidget()
        return w if isinstance(w, PlayerPage) else None

    def pages(self) -> list[PlayerPage]:
        return [self.body.widget(i) for i in range(self.body.count())
                if isinstance(self.body.widget(i), PlayerPage)]

    def page_for(self, video_id: str) -> PlayerPage | None:
        if not video_id:
            return None
        for page in self.pages():
            cur = page.current_row()
            if cur and cur.get("video_id") == video_id:
                return page
        return None

    # ---------------------------------------------------------------- open
    def open_row(self, row: dict, context: list[dict] | None = None,
                 start: float = 0.0) -> PlayerPage:
        """Open `row` in its own tab, or focus the tab already showing it."""
        vid = row.get("video_id", "")
        existing = self.page_for(vid)
        if existing is not None:
            idx = self.body.indexOf(existing)
            self.bar.setCurrentIndex(idx)
            self._on_current_changed(idx)
            page = existing
            # Same file already loaded: just resume instead of reloading it,
            # which would throw away the buffered position.
            if page.player and page.player.path == row.get("path"):
                page.player.set_paused(False)
                if start:
                    page.player.seek(start)
            else:
                page.load(row, start)
            return page

        if self.count() >= MAX_TABS:
            # Close the oldest *inactive* tab; if every tab is somehow active,
            # fall back to the first one.
            victim = 0
            for i in range(self.count()):
                if i != self.bar.currentIndex():
                    victim = i
                    break
            diagnostics.note(f"tab cap reached ({MAX_TABS}); closing tab {victim}")
            self.close_tab(victim)

        page = PlayerPage(self.settings, self)
        page.set_db(self.db)
        self._wire(page)
        page.set_context(context or [row], vid)

        idx = self.body.addWidget(page)
        self.bar.blockSignals(True)
        tab_index = self.bar.addTab(self._title_for(row))
        self.bar.setTabToolTip(tab_index, row.get("path", ""))
        self.bar.setTabButton(tab_index, QTabBar.ButtonPosition.RightSide,
                              self._make_close_button(page))
        self.bar.setCurrentIndex(tab_index)
        self.bar.blockSignals(False)
        self.body.setCurrentWidget(page)
        self._sync_visibility()
        self._park_others(page)

        page.load(row, start)
        diagnostics.note(f"open tab {tab_index}: {row.get('path', '')}")
        self.changed.emit(self.count())
        return page

    @staticmethod
    def _title_for(row: dict) -> str:
        name = row.get("vname") or row.get("vtype") or ""
        if not name:
            name = os.path.splitext(os.path.basename(row.get("path", "")))[0]
        name = name.strip() or "未命名"
        return name if len(name) <= 26 else name[:25] + "…"

    def _make_close_button(self, page: PlayerPage) -> QToolButton:
        """A fixed-size close button that Qt keeps inside the tab rect.

        Bound to the *page*, not the tab index: closing the current tab shifts
        every later index, and an index-bound slot would then close the wrong
        one.
        """
        btn = QToolButton()
        btn.setObjectName("tabClose")
        btn.setText("✕")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedSize(18, 18)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setToolTip("关闭该标签（Ctrl+W）")
        btn.clicked.connect(self._slot(page, lambda p: self.close_page(p)))
        return btn

    @staticmethod
    def _slot(page: PlayerPage, callback):
        """Bind `callback(page, ...)` as a slot without keeping the page alive.

        Writing ``lambda p=page: ...`` stores the page in the function's
        ``__defaults__`` — a strong reference held for as long as the slot
        exists, and these slots live on PlayerTabs, i.e. as long as the window.
        Because a Python wrapper still referenced the page, PySide6 never
        destroyed its C++ object, so the signal connections were never torn
        down either, which is what kept the references alive. A neat loop:
        every tab ever opened stayed valid (measured: 3 closed tabs, 3 still
        alive) along with its native video surface.
        """
        ref = weakref.ref(page)

        def slot(*args):
            p = ref()
            if p is not None:
                callback(p, *args)
        return slot

    def _wire(self, page: PlayerPage):
        """Route one tab's signals outward — but only while it is focused.

        A background tab must not drive app-level actions. Before this guard,
        a parked tab reaching EOF fired `nextRequested`, the main window applied
        it to the *active* tab, and auto-play skipped a video. Each connection
        carries its source page so the check stays attached to the right tab.
        """
        page.backRequested.connect(self._slot(page, lambda p: self.close_page(p)))
        page.rowChanged.connect(self._slot(page, lambda p, _r: self._retitle(p)))
        page.positionReported.connect(
            self._slot(page, lambda p, vid, pos, dur:
                       self._relay(p, self.positionReported, vid, pos, dur)))
        page.fileDropped.connect(
            self._slot(page, lambda p, path: self._relay(p, self.fileDropped, path)))
        page.prevRequested.connect(
            self._slot(page, lambda p: self._relay(p, self.prevRequested)))
        page.nextRequested.connect(
            self._slot(page, lambda p: self._relay(p, self.nextRequested)))
        page.playlistIndexSelected.connect(
            self._slot(page, lambda p, i:
                       self._relay(p, self.playlistIndexSelected, int(i))))

    def _relay(self, page: PlayerPage, signal, *args):
        """Forward only if `page` is the one the user is looking at."""
        if page is self.current_page():
            signal.emit(*args)

    def _retitle(self, page: PlayerPage):
        """Auto-play changes the file inside a tab; keep the label honest."""
        idx = self.body.indexOf(page)
        row = page.current_row()
        if idx >= 0 and row:
            self.bar.setTabText(idx, self._title_for(row))
            self.bar.setTabToolTip(idx, row.get("path", ""))
            if page is self.current_page():
                self.changed.emit(self.count())    # refresh sidebar tooltip

    # --------------------------------------------------------------- close
    def close_tab(self, index: int):
        if not (0 <= index < self.count()):
            return
        page = self.body.widget(index)
        self.bar.blockSignals(True)
        self.bar.removeTab(index)
        self.bar.blockSignals(False)
        self.body.blockSignals(True)
        self.body.removeWidget(page)
        self.body.blockSignals(False)

        diagnostics.note(f"close tab {index}: "
                         f"{page.current_row() and page.current_row().get('path')}")
        page.release()            # stop → terminate engine → drop the surface
        page.deleteLater()

        if self.count():
            nxt = min(index, self.count() - 1)
            self.bar.setCurrentIndex(nxt)
            self._on_current_changed(nxt)
        else:
            self._sync_visibility()
            self.empty.emit()
        self.changed.emit(self.count())

    def close_page(self, page: PlayerPage):
        """Close by widget — used by the in-page 返回 button."""
        idx = self.body.indexOf(page)
        if idx >= 0:
            self.close_tab(idx)

    def close_current(self):
        idx = self.bar.currentIndex()
        if idx >= 0:
            self.close_tab(idx)

    def close_all(self):
        for i in range(self.count() - 1, -1, -1):
            self.close_tab(i)

    # ------------------------------------------------------------ switching
    def activate_current(self):
        """(Re-)enter the visible tab. Called when the user navigates back to
        the player area, which must resume whatever was parked on the way out."""
        idx = self.bar.currentIndex()
        if idx >= 0:
            self._on_current_changed(idx)

    def active_title(self) -> str:
        page = self.current_page()
        row = page.current_row() if page else None
        return self._title_for(row) if row else ""

    def _on_current_changed(self, index: int):
        if index < 0 or index >= self.count():
            return
        page = self.body.widget(index)
        if page is None:
            return
        self.body.setCurrentWidget(page)
        self._park_others(page)
        self._resume(page)
        self._sync_visibility()

    def _park_others(self, keep: PlayerPage):
        for other in self.pages():
            if other is not keep:
                self._park(other)

    def park_all(self):
        """Silence every tab. Used when the user leaves the whole player area —
        this is the fix for "返回后后台还有声音"."""
        for page in self.pages():
            self._park(page)

    @staticmethod
    def _park(page: PlayerPage):
        """Silence and freeze a background tab but keep its position.

        Muting alone is not enough. Two engines that both hold a real audio
        *and* video output crash the process while the second one starts
        (measured: 3 of 10 runs, access violation). So a parked tab also drops
        its audio track, which makes mpv release the device — only the tab the
        user is looking at ever owns one.
        """
        p = page.player
        if p is None:
            return
        page.parked_playing = not p.paused
        page.parked_muted = p.muted
        p.set_paused(True)
        p.set_muted(True)
        page.parked_aid = p.suspend_audio()
        page.stop_ticking()

    @staticmethod
    def _resume(page: PlayerPage):
        p = page.player
        if p is None:
            return
        p.set_muted(getattr(page, "parked_muted", False))
        p.resume_audio(getattr(page, "parked_aid", ""))
        page.start_ticking()
        if getattr(page, "parked_playing", False):
            p.set_paused(False)

    # ------------------------------------------------------------ appearance
    def _sync_visibility(self):
        has = self.count() > 0
        self.bar.setVisible(has)
        self.body.setVisible(has)
        self.placeholder.setVisible(not has)

    def refresh_theme(self):
        for page in self.pages():
            page.refresh_theme()
        self.update()
