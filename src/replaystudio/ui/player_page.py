"""Playback page shared by highlights, local videos and recordings.

Layout
------
  header ── back / title / state
  body   ── video stage (native mpv surface + overlay) │ side panel (tabs)
  bar    ── timeline + transport controls

The side panel switches between *info*, *events* and *playlist* so the same
page serves a 12-second highlight (lots of events, one item) and a 45-minute
episode (no events, twenty siblings) without wasting space.
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMenu,
                               QPushButton, QScrollArea, QSizePolicy,
                               QSlider, QStackedWidget, QVBoxLayout, QWidget)

from ..core import diagnostics
from ..media.player import MpvPlayer
from . import qt_teardown as qt
from . import theme as T
from .widgets import ChipButton, human_time, timestamp_ms

SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup", ".lrc"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m2ts",
              ".wmv", ".mpg", ".mpeg", ".m4v", ".rmvb"}

ASPECTS = [("原始比例（推荐）", "-1"), ("16:9", "16:9"), ("4:3", "4:3"),
           ("拉伸填满窗口", "no"), ("2.35:1", "2.35:1"), ("1:1", "1:1")]


class TimelineSlider(QWidget):
    """Seek bar that also draws kill/death markers at their timestamps."""

    seekRequested = Signal(float)
    hoverChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._value = 0.0
        self._maximum = 0.0
        self.markers: list[tuple[float, str]] = []   # (seconds, "kill"|"death")
        self._hover_x = -1.0
        self._dragging = False

    # ---- public
    def set_range(self, maximum: float):
        self._maximum = max(0.0, float(maximum))
        self.update()

    def set_value(self, v: float):
        self._value = max(0.0, float(v))
        self.update()

    def set_markers(self, markers):
        self.markers = markers
        self.update()

    # ---- interaction
    def _ratio_at(self, x: float) -> float:
        w = self.width()
        return 0.0 if w <= 0 else min(1.0, max(0.0, x / w))

    def mousePressEvent(self, e):
        self._dragging = True
        self._emit(e.position().x())

    def mouseMoveEvent(self, e):
        self._hover_x = e.position().x()
        self.hoverChanged.emit(self._ratio_at(self._hover_x) * self._maximum)
        if self._dragging:
            self._emit(self._hover_x)
        self.update()

    def mouseReleaseEvent(self, e):
        if self._dragging:
            self._emit(e.position().x())
        self._dragging = False

    def leaveEvent(self, e):
        self._hover_x = -1.0
        self.update()

    def _emit(self, x):
        if self._maximum > 0:
            self.seekRequested.emit(self._ratio_at(x) * self._maximum)

    # ---- paint
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        track_y = h // 2 - 2
        track_h = 4
        ratio = (self._value / self._maximum) if self._maximum else 0.0

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.SURFACE_2))
        p.drawRoundedRect(0, track_y, w, track_h, 2, 2)

        if ratio > 0:
            p.setBrush(QColor(T.ACCENT))
            p.drawRoundedRect(0, track_y, int(w * ratio), track_h, 2, 2)

        for sec, kind in self.markers:
            if not self._maximum or sec < 0:
                continue
            x = w * (sec / self._maximum)
            if x > w:
                continue
            p.setBrush(QColor(T.ACCENT if kind == "kill" else T.DEATH))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(int(x) - 1, track_y - 5, 2, track_h + 10, 1, 1)

        hx = int(w * ratio)
        hovered = self._hover_x >= 0
        r = 6 if hovered else 5
        p.setBrush(QColor(T.TEXT))
        p.drawEllipse(hx - r, track_y + track_h // 2 - r, r * 2, r * 2)

        if hovered and self._maximum:
            sec = self._ratio_at(self._hover_x) * self._maximum
            txt = human_time(sec)
            fm = p.fontMetrics()
            bw = fm.horizontalAdvance(txt) + 10
            bx = min(max(hx - bw // 2, 0), w - bw)
            p.setBrush(QColor(0, 0, 0, 200))
            p.drawRoundedRect(bx, 0, bw, 16, 4, 4)
            p.setPen(QColor(T.TEXT))
            p.drawText(bx, 0, bw, 16, Qt.AlignCenter, txt)
        p.end()


class PlayerPage(QFrame):
    """Video surface + transport controls + event timeline + playlist."""

    backRequested = Signal()
    prevRequested = Signal()
    nextRequested = Signal()
    positionReported = Signal(str, float, float)   # video_id, pos_s, duration_s
    playlistIndexSelected = Signal(int)            # index into current context
    fileDropped = Signal(str)                      # a video file dropped in
    rowChanged = Signal(dict)                      # a different row got loaded

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.player: MpvPlayer | None = None
        self._current: dict | None = None
        self._player_ready = False
        self._fs = False
        self._context: list[dict] = []
        self._event_markers: list[tuple[float, str]] = []
        # Parking state: set by the tab host when this page goes to the
        # background, so switching back restores what the user was doing.
        self.parked_playing = False
        self.parked_muted = False
        self.parked_aid = ""          # audio track id while parked (AO released)
        # Last load error, kept so diagnostics (and tests) can tell "the engine
        # said nothing" apart from "the engine complained".
        self.last_error = ""
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- header
        head = QHBoxLayout()
        head.setContentsMargins(14, 10, 14, 6)
        back = QPushButton("←  返回")
        back.setProperty("flat", "true")
        back.clicked.connect(self.backRequested.emit)
        head.addWidget(back)
        self.title_label = QLabel("")
        T.style(self.title_label, "font-size: 15px; font-weight: 600; color: $TEXT;")
        head.addWidget(self.title_label)
        head.addStretch()
        self.info_label = QLabel("")
        T.style(self.info_label, "color: $TEXT_DIM; font-size: 12px;")
        head.addWidget(self.info_label)
        root.addLayout(head)

        # ---- body: video stage + side panel
        body = QHBoxLayout()
        body.setContentsMargins(14, 0, 14, 0)
        body.setSpacing(12)

        self.stage = QWidget()
        self.stage.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.stage.setMinimumSize(480, 270)
        stage_lay = QVBoxLayout(self.stage)
        stage_lay.setContentsMargins(0, 0, 0, 0)
        self.video_host = QWidget(self.stage)
        T.style(self.video_host, "background: #000; border-radius: ${RADIUS}px;")
        self.video_host.setAttribute(Qt.WA_NativeWindow, True)
        self.video_host.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
        stage_lay.addWidget(self.video_host)
        # "F / 双击画面" is what the button promises; the surface is a native
        # window and swallows mouse events, so it needs an event filter rather
        # than a subclass.
        self.video_host.installEventFilter(self)
        # floating overlay (OSD toast + error panel), positioned in resizeEvent
        self.overlay = QLabel(self.stage)
        self.overlay.hide()
        self.overlay.setAlignment(Qt.AlignCenter)
        self.overlay.setWordWrap(True)
        self.osd = QLabel(self.stage)
        self.osd.hide()
        self.osd.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
        self._osd_timer = QTimer(self)
        self._osd_timer.setSingleShot(True)
        self._osd_timer.timeout.connect(self.osd.hide)
        body.addWidget(self.stage, 1)

        side = QFrame()
        side.setObjectName("panel")
        side.setFixedWidth(276)
        # Kept as an attribute: fullscreen has to be able to hide it.
        self.side_panel = side
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 12, 12, 12)
        sl.setSpacing(8)

        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.tab_info = ChipButton("信息", "info", self)
        self.tab_events = ChipButton("事件", "events", self)
        self.tab_list = ChipButton("列表", "list", self)
        self._tabs = [self.tab_info, self.tab_events, self.tab_list]
        for b in self._tabs:
            b.setChecked(False)
            b.clicked.connect(lambda _=False, v=b.value: self._switch_tab(v))
            tabs.addWidget(b)
        self.tab_info.setChecked(True)
        sl.addLayout(tabs)

        self.side_stack = QStackedWidget()
        sl.addWidget(self.side_stack, 1)

        # tab: info
        info_wrap = QWidget()
        il = QVBoxLayout(info_wrap)
        il.setContentsMargins(0, 0, 0, 0)
        self.meta_body = QLabel("—")
        self.meta_body.setWordWrap(True)
        self.meta_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        T.style(self.meta_body, "color: $TEXT_DIM; font-size: 12px; line-height: 150%;")
        il.addWidget(self.meta_body)
        il.addStretch()
        self.side_stack.addWidget(info_wrap)

        # tab: events
        ev_wrap = QWidget()
        el = QVBoxLayout(ev_wrap)
        el.setContentsMargins(0, 0, 0, 0)
        self.event_list = QVBoxLayout()
        self.event_list.setSpacing(3)
        el.addLayout(self.event_list)
        el.addStretch()
        ev_scroll = QScrollArea()
        ev_scroll.setWidgetResizable(True)
        ev_scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        holder.setLayout(el)
        ev_scroll.setWidget(holder)
        self.side_stack.addWidget(ev_scroll)

        # tab: playlist
        self.playlist = QListWidget()
        self.playlist.setFrameShape(QFrame.NoFrame)
        T.style(self.playlist, """
            QListWidget { background: transparent; }
            QListWidget::item { padding: 7px 9px; border-radius: 6px;
                color: $TEXT_DIM; margin-bottom: 2px; }
            QListWidget::item:selected { background: $ACCENT_SOFT; color: $TEXT; }
            QListWidget::item:hover { background: $SURFACE_2; color: $TEXT; }
        """)
        self.playlist.itemClicked.connect(
            lambda item: self.playlistIndexSelected.emit(int(item.data(Qt.UserRole))))
        self.side_stack.addWidget(self.playlist)

        body.addWidget(side)
        root.addLayout(body, 1)

        # ---- transport
        bar = QFrame()
        T.style(bar, "background: $SURFACE; border-top: 1px solid $BORDER;")
        bl = QVBoxLayout(bar)
        bl.setContentsMargins(14, 6, 14, 10)
        bl.setSpacing(4)

        self.timeline = TimelineSlider()
        self.timeline.seekRequested.connect(self._on_seek)
        bl.addWidget(self.timeline)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_play = QPushButton("⏸")
        self.btn_play.setFixedSize(38, 34)
        self.btn_play.clicked.connect(self.toggle_play)
        row.addWidget(self.btn_play)

        for text, cb, tip in (("⏮", self.prevRequested.emit, "上一个（P）"),
                              ("⏭", self.nextRequested.emit, "下一个（N）")):
            b = QPushButton(text)
            b.setFixedSize(34, 32)
            b.setProperty("flat", "true")
            b.setToolTip(tip)
            b.clicked.connect(cb)
            row.addWidget(b)

        self.time_label = QLabel("00:00 / 00:00")
        T.style(self.time_label, "color: $TEXT_DIM; font-size: 12px;")
        self.time_label.setMinimumWidth(105)
        row.addWidget(self.time_label)
        row.addStretch()

        self.btn_sub = QPushButton("CC")
        self.btn_sub.setFixedSize(40, 30)
        self.btn_sub.setToolTip("字幕轨道（J 切换 / Shift+J 关闭；可拖入 .srt/.ass）")
        self.btn_sub.clicked.connect(self._menu_sub)
        row.addWidget(self.btn_sub)

        self.btn_audio = QPushButton("♪")
        self.btn_audio.setFixedSize(34, 30)
        self.btn_audio.setToolTip("音轨切换（K）")
        self.btn_audio.clicked.connect(self._menu_audio)
        row.addWidget(self.btn_audio)

        self.btn_aspect = QPushButton("▣")
        self.btn_aspect.setFixedSize(34, 30)
        self.btn_aspect.setToolTip("画面比例（A）")
        self.btn_aspect.clicked.connect(self._menu_aspect)
        row.addWidget(self.btn_aspect)

        shot = QPushButton("📷")
        shot.setFixedSize(34, 30)
        shot.setToolTip("截图（S）")
        shot.clicked.connect(self.take_screenshot)
        row.addWidget(shot)

        self.speed_btn = QPushButton("1.0×")
        self.speed_btn.setFixedSize(58, 30)
        self.speed_btn.setToolTip("播放速度（[ / ]）")
        self.speed_btn.clicked.connect(self._cycle_speed)
        row.addWidget(self.speed_btn)

        self.mute_btn = QPushButton("🔊")
        self.mute_btn.setFixedSize(34, 30)
        self.mute_btn.setToolTip("静音（M）")
        self.mute_btn.clicked.connect(self._toggle_mute)
        row.addWidget(self.mute_btn)

        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 130)
        self.vol.setValue(80)
        self.vol.setFixedWidth(78)
        self.vol.valueChanged.connect(self._on_volume)
        row.addWidget(self.vol)

        full = QPushButton("⛶")
        full.setFixedSize(34, 30)
        full.setToolTip("全屏（F / 双击画面）")
        full.clicked.connect(self.toggle_fullscreen)
        row.addWidget(full)
        bl.addLayout(row)
        root.addWidget(bar)

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)

    # -------------------------------------------------------------- lifecycle
    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._place_overlays()
        # The mpv surface is a real HWND; keep it pinned to the widget rect.
        self._sync_native_surface()

    def _place_overlays(self):
        w, h = self.stage.width(), self.stage.height()
        self.overlay.setGeometry(0, 0, w, h)
        self.osd.setGeometry(0, h - 70, w, 34)

    def _sync_native_surface(self):
        """Pin the mpv host's native window onto its Qt geometry.

        Why this exists: a bare ``QWidget`` with ``WA_NativeWindow`` gets a real
        HWND, but Qt positions that HWND from the widget geometry *at creation
        time*. The first player page is built while the main window is still
        being placed by the window manager, so the HWND ends up offset inside
        its parent (measured: (283,145) px off on a 125% display) and mpv then
        renders into that offset rectangle — the "first video is not centred"
        report. Re-asserting the rect afterwards is cheap and idempotent, and
        it also covers window moves and DPI changes.

        The coordinates must be expressed against the HWND that Windows
        actually made the parent of ours, which is *not* necessarily
        ``host.parentWidget()``. With ``WA_DontCreateNativeAncestors`` Qt skips
        intermediate widgets, so the real parent HWND is the nearest native
        ancestor — sometimes the top-level window. Passing coordinates
        measured against ``stage`` moved the surface to the top-left of the
        window instead: tools/probe_surface_offset.py shows a manual call
        *creating* a (-282,-148) offset that later corrects itself. So: go via
        screen coordinates and let Windows convert.
        """
        if sys.platform != "win32":
            return
        host = self.video_host
        if not host.testAttribute(Qt.WidgetAttribute.WA_NativeWindow):
            return
        if not host.isVisible():
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetParent.restype = wintypes.HWND
            user32.ScreenToClient.argtypes = [wintypes.HWND,
                                              ctypes.POINTER(wintypes.POINT)]
            hwnd = wintypes.HWND(int(host.winId()))
            parent_hwnd = user32.GetParent(hwnd)
            if not parent_hwnd:
                return
            dpr = host.devicePixelRatioF() or 1.0
            top_left = host.mapToGlobal(QPoint(0, 0))
            pt = wintypes.POINT(int(round(top_left.x() * dpr)),
                                int(round(top_left.y() * dpr)))
            user32.ScreenToClient(parent_hwnd, ctypes.byref(pt))
            w = max(1, int(round(host.width() * dpr)))
            h = max(1, int(round(host.height() * dpr)))
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            user32.SetWindowPos(hwnd, 0, pt.x, pt.y, w, h,
                                SWP_NOZORDER | SWP_NOACTIVATE)
        except Exception:
            # Never let a cosmetic fix break playback.
            pass

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.video_host and event.type() == QEvent.Type.MouseButtonDblClick:
            self.toggle_fullscreen()
            return True
        return super().eventFilter(obj, event)

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        # Layout has not run yet at this point, so sync on the next tick.
        QTimer.singleShot(0, self._sync_native_surface)
        if not self._player_ready:
            self._player_ready = True
            QTimer.singleShot(0, self._init_player)

    def _init_player(self):
        try:
            wid = int(self.video_host.winId())
            # Volume travels in as a construction option: writing it right
            # after the engine existed raced the event thread (see MpvPlayer).
            self.player = MpvPlayer(wid=wid,
                                    hwdec=self.settings.get("hwdec", "auto"),
                                    volume=int(self.settings.get("volume", 80)))
        except Exception as exc:
            self._show_overlay(f"播放引擎不可用\n{exc}")
            self.info_label.setText(f"播放引擎不可用：{exc}")
            return
        p = self.player
        p.positionChanged.connect(self._on_position)
        p.durationChanged.connect(self._on_duration)
        p.pausedChanged.connect(self._on_paused)
        p.ended.connect(self._on_ended)
        p.errorOccurred.connect(self._on_error)
        p.tracksChanged.connect(self._on_tracks)
        p.osdText.connect(self.show_osd)
        p.fileLoaded.connect(lambda _p: self._on_file_loaded())
        p.volumeChanged.connect(lambda v: self.vol.setValue(int(v)))
        self.vol.setValue(int(self.settings.get("volume", 80)))
        self._timer.start()
        # mpv has now attached its child window; make sure it covers the stage.
        self._sync_native_surface()
        if self._pending:
            self.load(self._pending[0], self._pending[1])
            self._pending = None

    _pending = None

    # ---------------------------------------------------------------- loading
    def load(self, row: dict, start_pos: float | None = None):
        """Open one indexed video row."""
        # Reset the error state *before* loading: a missing file reports
        # synchronously, and clearing afterwards would erase that message.
        self.clear_error()
        self._current = row
        self.rowChanged.emit(row)
        self.title_label.setText(row.get("vname") or row.get("vtype") or
                                 os.path.basename(row.get("path", "")))
        parts = [x for x in (row.get("map_cn"), row.get("agent_cn"), row.get("mode_cn")) if x]
        self.info_label.setText(" · ".join(parts))
        self._render_meta(row)
        self._render_events(row.get("video_id", ""))
        self._mark_playlist_current(row.get("video_id", ""))
        self.overlay.hide()

        if not self.player:
            self._pending = (row, start_pos)
            return
        if row.get("file_state") == 2 or not os.path.isfile(row.get("path", "")):
            self._on_error("源文件不存在或已被移动。\n请重新扫描，或在列表中确认状态。")
            return
        self.player.play(row["path"], start_pos)
        self.btn_play.setText("⏸")

    def _on_file_loaded(self):
        """Re-apply per-file preferences that mpv resets on every load."""
        if not self.player:
            return
        try:
            self.player.set_sub_delay(float(self.settings.get("sub_delay", 0.0)))
        except (TypeError, ValueError):
            pass
        self._on_tracks()

    # ------------------------------------------------------------- side panel
    def _switch_tab(self, value):
        idx = {"info": 0, "events": 1, "list": 2}[value]
        self.side_stack.setCurrentIndex(idx)
        for b in self._tabs:
            b.setChecked(b.value == value)

    def set_context(self, rows: list[dict], current_id: str = ""):
        """Fill the playlist tab with the list the user came from."""
        self._context = list(rows)
        self.playlist.blockSignals(True)
        self.playlist.clear()
        for i, r in enumerate(rows):
            name = r.get("vname") or os.path.basename(r.get("path", ""))
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, i)
            item.setToolTip(r.get("path", ""))
            if r.get("file_state") == 2:
                item.setText(name + "  （已缺失）")
                item.setForeground(QColor(T.TEXT_FAINT))
            self.playlist.addItem(item)
        self.playlist.blockSignals(False)
        self._mark_playlist_current(current_id)
        n = len(rows)
        self.tab_list.setText(f"列表 {n}" if n else "列表")
        if n > 1:
            self.tab_list.setToolTip(f"当前播放上下文共 {n} 项")

    def _mark_playlist_current(self, video_id: str):
        if not video_id:
            return
        for i, r in enumerate(self._context):
            if r.get("video_id") == video_id:
                self.playlist.blockSignals(True)
                self.playlist.setCurrentRow(i)
                self.playlist.blockSignals(False)
                self.playlist.scrollToItem(self.playlist.item(i))
                return

    def _render_meta(self, row: dict):
        lines = []
        if row.get("nick"):
            lines.append(f"账号：{row['nick']}#{row.get('tag','')}")
        if row.get("map_cn"):
            lines.append(f"地图：{row['map_cn']}")
        if row.get("agent_cn"):
            lines.append(f"特工：{row['agent_cn']}")
        if row.get("mode_cn"):
            lines.append(f"模式：{row['mode_cn']}")
        if row.get("kills") is not None:
            lines.append(f"战绩：{row.get('kills')}/{row.get('deaths')}/{row.get('assists')}"
                         f"  {'胜' if row.get('has_won') else '负'}"
                         f"  {row.get('rounds_won','-')}:{row.get('rounds_lost','-')}")
        if row.get("created_ms"):
            lines.append(f"录制：{timestamp_ms(row['created_ms'])}")
        if row.get("size_bytes"):
            lines.append(f"大小：{row['size_bytes']/1024/1024:.1f} MB")
        if row.get("path"):
            lines.append(f"\n{row['path']}")
        self.meta_body.setText("\n".join(lines) if lines else "—")

    def _render_events(self, video_id: str):
        lay = self.event_list
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._event_markers = []
        if not video_id or not hasattr(self, "_db"):
            self.timeline.set_markers([])
            self.tab_events.setText("事件")
            return
        events = self.db_events(video_id)
        for ev in events:
            sec = ev["ms"] / 1000.0
            self._event_markers.append((sec, ev["etype"]))
            b = QPushButton(f"{human_time(sec)}  {self._event_text(ev)}")
            b.setCursor(Qt.PointingHandCursor)
            T.style(b, """
                QPushButton { background: transparent; border: none; text-align: left;
                    color: $TEXT_DIM; padding: 5px 8px; border-radius: 6px; font-size: 12px; }
                QPushButton:hover { background: $SURFACE_2; color: $TEXT; }
            """)
            b.clicked.connect(lambda _=False, s=sec: self.seek_to(s))
            lay.addWidget(b)
        lay.addStretch()
        self.timeline.set_markers(self._event_markers)
        self.tab_events.setText(f"事件 {len(events)}" if events else "事件")

    def db_events(self, video_id: str):
        if not hasattr(self, "_db"):
            return []
        try:
            return self._db.get_events(video_id)
        except Exception:
            return []

    @staticmethod
    def _event_text(ev: dict) -> str:
        if ev["etype"] == "kill":
            who = ev.get("killed") or "对手"
            extra = f" · {ev['weapon_class']}" if ev.get("weapon_class") else ""
            return f"击杀 {who}{extra}"
        who = ev.get("killer") or "对手"
        return f"被 {who} 击杀"

    def set_db(self, db):
        self._db = db

    # ------------------------------------------------------------ track menus
    # Menus are split into "build" (pure widget construction, unit-testable and
    # screenshot-able) and "show" (blocking exec at the button). Keeping them
    # apart is what lets tools/screenshot_phase2.py render a menu without a
    # live event loop driving a popup grab.
    def _new_menu(self) -> QMenu:
        menu = QMenu(self)
        T.style(menu, """
            QMenu { background: $SURFACE; border: 1px solid $BORDER; padding: 5px;
                    color: $TEXT; }
            QMenu::item { padding: 6px 22px; border-radius: 5px; }
            QMenu::item:selected { background: $ACCENT; color: $ON_ACCENT; }
        """)
        return menu

    def _over_button(self, btn: QPushButton, menu: QMenu):
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        menu.exec(pos)

    def _build_sub_menu(self) -> QMenu | None:
        if not self.player:
            return None
        menu = self._new_menu()
        act_off = menu.addAction("关闭字幕")
        act_off.triggered.connect(lambda: self.player.select_subtitle(None))
        subs = self.player.subtitle_tracks()
        cur = self.player.current_subtitle()
        for t in subs:
            label = f"#{t['id']}  {t.get('title') or t.get('lang') or '字幕'}"
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(t["id"] == cur)
            a.triggered.connect(lambda _=False, i=t["id"]: self.player.select_subtitle(i))
        menu.addSeparator()
        a_load = menu.addAction("加载外部字幕文件 …")
        a_load.triggered.connect(self._pick_subtitle)
        menu.addSeparator()
        d = float(self.player.sub_delay)
        for delta, label in ((-0.5, "字幕提前 0.5s"), (0.5, "字幕延后 0.5s"),
                             (0.0, f"重置延迟（当前 {d:+.1f}s）")):
            a = menu.addAction(label)
            a.triggered.connect(
                lambda _=False, v=(0.0 if delta == 0.0 else d + delta): self._set_sub_delay(v))
        return menu

    def _build_audio_menu(self) -> QMenu | None:
        if not self.player:
            return None
        menu = self._new_menu()
        cur = self.player.current_audio()
        for t in self.player.audio_tracks():
            bits = [t.get("title") or "", t.get("lang") or "",
                    f"{t.get('audio-channels', '')}ch" if t.get("audio-channels") else "",
                    t.get("codec") or ""]
            label = f"#{t['id']}  " + " · ".join(x for x in bits if x)
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(t["id"] == cur)
            a.triggered.connect(lambda _=False, i=t["id"]: self.player.select_audio(i))
        return menu

    def _build_aspect_menu(self) -> QMenu | None:
        if not self.player:
            return None
        menu = self._new_menu()
        for label, value in ASPECTS:
            a = menu.addAction(label)
            a.triggered.connect(lambda _=False, v=value: self.player.set_aspect(v))
        return menu

    def _menu_sub(self):
        menu = self._build_sub_menu()
        if menu:
            self._over_button(self.btn_sub, menu)

    def _menu_audio(self):
        menu = self._build_audio_menu()
        if menu:
            self._over_button(self.btn_audio, menu)

    def _menu_aspect(self):
        menu = self._build_aspect_menu()
        if menu:
            self._over_button(self.btn_aspect, menu)

    def _pick_subtitle(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择字幕文件", "",
            "字幕文件 (*.srt *.ass *.ssa *.vtt *.sub *.idx);;所有文件 (*.*)")
        if path:
            self.player.add_subtitle(path)
            self.show_osd(f"已加载字幕：{os.path.basename(path)}")

    def _set_sub_delay(self, v: float):
        self.player.set_sub_delay(v)
        self.settings["sub_delay"] = v

    def _on_tracks(self):
        """Reflect track availability on the buttons so dead controls never lie."""
        if not self.player:
            return
        subs = self.player.subtitle_tracks()
        auds = self.player.audio_tracks()
        self.btn_sub.setEnabled(bool(subs))
        self.btn_audio.setEnabled(len(auds) > 1)
        self.btn_sub.setText("CC" if subs else "CC̶")
        n_show = self.player.current_subtitle()
        self.btn_sub.setToolTip(
            f"字幕：{len(subs)} 条轨道" + (f"，当前 #{n_show}" if n_show else "（已关闭）"))

    # ---------------------------------------------------------------- controls
    def toggle_play(self):
        if self.player:
            self.player.toggle_pause()

    def seek_to(self, seconds: float):
        if self.player:
            self.player.seek(seconds)

    def seek_relative(self, delta: float):
        if self.player:
            self.player.seek_relative(delta)

    def step_seconds(self) -> int:
        try:
            return int(self.settings.get("seek_step", 5))
        except (TypeError, ValueError):
            return 5

    def toggle_fullscreen(self):
        """Fullscreen the *window*, not mpv.

        mpv's own ``fullscreen`` property drives the window mpv would have
        created for itself. In embedded mode (``wid=``) there is no such
        window — ours is — so setting it makes libmpv resize and restyle a
        HWND that Qt also believes it owns, and the two fight over it. That is
        the "全屏后闪退" report. Qt's ``showFullScreen()`` needs no help from
        mpv and never touches the HWND behind Qt's back; the surface follows
        because the layout resizes the host widget like any other.
        """
        win = self.window()
        if win is None:
            return
        self._fs = not self._fs
        host = getattr(win, "set_fullscreen", None)
        if host is not None:
            host(self._fs)
        elif self._fs:
            win.showFullScreen()
        else:
            win.showNormal()
        self.show_osd("全屏（Esc 退出）" if self._fs else "退出全屏")

    def set_fullscreen(self, on: bool):
        """Apply/undo the fullscreen layout. Called by the window and by
        PlayerTabs when the user switches tab while still fullscreen."""
        was, self._fs = self._fs, bool(on)
        if was == self._fs:
            return
        self.side_panel.setVisible(not on)
        # The stage just changed size; the native surface does not follow the
        # layout by itself (see _sync_native_surface).
        QTimer.singleShot(80, self._sync_native_surface)

    def take_screenshot(self):
        if not self.player:
            return
        base = os.path.join(os.path.expanduser("~"), "Pictures", "ReplayStudio")
        os.makedirs(base, exist_ok=True)
        name = os.path.splitext(os.path.basename(self.player.path or "frame"))[0]
        out = os.path.join(base, f"{name}_{int(self.player.position*1000)}.png")
        if self.player.screenshot(out):
            self.show_osd(f"截图已保存：{out}")
        else:
            self.show_osd("截图失败")

    def cycle_subtitle(self, forward: bool = True):
        if self.player:
            self.player.cycle_subtitle(forward)

    def cycle_audio(self, forward: bool = True):
        if self.player:
            self.player.cycle_audio(forward)

    def _cycle_speed(self):
        if not self.player:
            return
        steps = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]
        cur = self.player.speed
        nxt = steps[0]
        for s in steps:
            if s > cur + 0.01:
                nxt = s
                break
        self.player.set_speed(nxt)
        self.speed_btn.setText(f"{nxt}×")

    def set_speed(self, value: float):
        if not self.player:
            return
        self.player.set_speed(value)
        self.speed_btn.setText(f"{self.player.speed:g}×")
        self.show_osd(f"{self.player.speed:g}× 速度", 900)

    def _toggle_mute(self):
        if self.player:
            self.player.set_muted(not self.player.muted)
            self.mute_btn.setText("🔇" if self.player.muted else "🔊")

    def _on_volume(self, v: int):
        if self.player:
            self.player.set_volume(v)
            self.settings["volume"] = v

    def _on_seek(self, seconds: float):
        self.seek_to(seconds)

    def _tick(self):
        if self.player and self._current:
            pos = self.player.position
            self.positionReported.emit(self._current.get("video_id", ""), pos,
                                       self.player.duration)

    # ---------------------------------------------------------------- overlays
    def show_osd(self, text: str, ms: int = 1600):
        self.osd.setText(text)
        T.style(self.osd,
                "background: rgba(0,0,0,170); color: $TEXT; border-radius: 6px;"
                " padding: 6px 14px; font-size: 12.5px;")
        self.osd.adjustSize()
        self.osd.setGeometry(0, self.stage.height() - 70, self.stage.width(),
                             self.osd.sizeHint().height())
        self.osd.show()
        self._osd_timer.start(ms)

    def _show_overlay(self, text: str):
        self.overlay.setText(text)
        T.style(self.overlay,
                "background: rgba(12,12,16,215); color: $TEXT_DIM; font-size: 13px;"
                " border-radius: ${RADIUS}px; padding: 24px;")
        self.overlay.show()
        self.overlay.raise_()

    # ------------------------------------------------------------------ slots
    def _on_position(self, pos: float):
        self.timeline.set_value(pos)
        d = self.player.duration if self.player else 0
        self.time_label.setText(f"{human_time(pos)} / {human_time(d)}")
        if self._current:
            vid = self._current.get("video_id", "")
            if vid and pos > 1 and int(pos) % 5 == 0:
                self.positionReported.emit(vid, pos, d)

    def _on_duration(self, d: float):
        self.timeline.set_range(d)
        self.time_label.setText(f"{human_time(0)} / {human_time(d)}")

    def _on_paused(self, paused: bool):
        self.btn_play.setText("▶" if paused else "⏸")

    def _on_ended(self):
        self.btn_play.setText("▶")
        if self.player is None:      # tab is closing; never chain into next
            return
        if int(self.settings.get("autoplay_next", 1)):
            self.nextRequested.emit()

    def _on_error(self, msg: str):
        self.last_error = msg
        T.style(self.info_label, "color: $AMBER; font-size: 12px;")
        self.info_label.setText(msg.replace("\n", " "))
        self.btn_play.setText("▶")
        self._show_overlay(msg)

    def clear_error(self):
        self.last_error = ""
        T.style(self.info_label, "color: $TEXT_DIM; font-size: 12px;")
        self.overlay.hide()

    # ------------------------------------------------------------- drag & drop
    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if not path:
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext in SUB_EXTS:
                if self.player:
                    self.player.add_subtitle(path)
                    self.show_osd(f"已挂载字幕：{os.path.basename(path)}")
            elif ext in VIDEO_EXTS:
                self.fileDropped.emit(path)
        e.acceptProposedAction()

    def shutdown(self):
        self._timer.stop()
        if self.player:
            self.player.shutdown()

    # ------------------------------------------------------------ tab host API
    def current_row(self) -> dict | None:
        """What this page is playing (the tab host keys off `video_id`)."""
        return self._current

    def context(self) -> list[dict]:
        """The list this tab was opened from (playlist / next / prev)."""
        return self._context

    def start_ticking(self):
        if self.player and not self._timer.isActive():
            self._timer.start()

    def stop_ticking(self):
        self._timer.stop()

    def refresh_theme(self):
        self.update()
        self.timeline.update()

    def release(self):
        """Stop playback and destroy the engine. Idempotent.

        Order matters: detach our reference *first* so the still-running mpv
        callbacks hit the `if not self.player` guards instead of touching an
        object we are about to terminate. Then stop, then terminate.
        """
        self._timer.stop()
        self._osd_timer.stop()
        try:
            self.video_host.removeEventFilter(self)
        except RuntimeError:
            pass
        # A fullscreen window with no video in it is a black screen with no
        # visible way out (the shell is hidden), so come back down first.
        if self._fs:
            win = self.window()
            try:
                if win is not None and hasattr(win, "set_fullscreen"):
                    win.set_fullscreen(False)
            except RuntimeError:
                pass                    # window already gone during shutdown
        # Drop our ~11 entries from the theme registry *before* the C++ side
        # goes away. The registry held them strongly, which kept this page (and
        # the engine it owns) reachable long after the tab was closed.
        T.unregister(self)
        # Then cut every signal we own. Slots in this class are written as
        # `lambda: self.…()`, which closes over `self` — the page ends up
        # referencing itself, so it can never be collected and PySide6 never
        # destroys its C++ object either. `deleteLater()` alone does not break
        # that cycle; disconnecting does.
        #
        # It has to be done per SignalInstance: the blanket
        # `QObject.disconnect()` raises TypeError in PySide6 (measured with
        # tools/probe_disconnect.py), so the version of this loop that called it
        # silently disconnected nothing at all.
        #
        # Only *our* signals, never the children's. Disarming every widget
        # underneath took out the connections Qt makes inside its own composite
        # widgets (scroll areas, item views, …) and the process then died of an
        # access violation the next time the palette changed — reproduced with
        # tools/probe_crash_bisect.py. The children do not need it: they are
        # destroyed a few lines below, and a connection cannot outlive its
        # sender.
        cut = qt.disconnect_object(self)
        diagnostics.note(f"page release: disconnected {cut} signals")
        page_player = self.player
        self.player = None
        self._player_ready = False
        # Break the engine → closure → page cycle before the engine goes.
        if page_player is not None:
            page_player.disconnect_all()
        if page_player is not None:
            # shutdown() stops *and* terminates, and silences callbacks first —
            # a stop() here would emit end-file and could trigger auto-play on
            # a tab that is being closed. It has to run *before* we remove the
            # native window: destroying the surface out from under a running
            # engine is one of the ways this process used to die.
            page_player.shutdown()
        # Finally drop the widget tree. Slots like
        # `b.clicked.connect(lambda _=False, v=…: self.seek_to(v))` close over
        # the page, so the page holds its children which hold the page. In
        # PySide6 a C++ object is only destroyed once its Python wrapper is
        # collected, and the wrapper can only be collected once that cycle is
        # gone — so `deleteLater()` alone leaves the whole page (native video
        # surface included) alive for the rest of the session. The handlers live
        # on the C++ side where the garbage collector cannot see them, so the
        # children themselves have to be destroyed to be sure.
        qt.purge_children(self)
