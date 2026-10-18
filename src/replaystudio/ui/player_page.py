"""Playback page shared by highlights, local videos and recordings."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QSlider, QVBoxLayout, QWidget)

from ..media.player import MpvPlayer
from . import theme as T
from .widgets import human_time, timestamp_ms


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

        # base track
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.SURFACE_2))
        p.drawRoundedRect(0, track_y, w, track_h, 2, 2)

        # played portion
        if ratio > 0:
            p.setBrush(QColor(T.ACCENT))
            p.drawRoundedRect(0, track_y, int(w * ratio), track_h, 2, 2)

        # event markers
        for sec, kind in self.markers:
            if not self._maximum or sec < 0:
                continue
            x = w * (sec / self._maximum)
            if x > w:
                continue
            color = QColor(T.ACCENT if kind == "kill" else "#7A7A8C")
            p.setBrush(color)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(int(x) - 1, track_y - 5, 2, track_h + 10, 1, 1)

        # handle
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
    """Video surface + transport controls + event timeline + match metadata."""

    backRequested = Signal()
    prevRequested = Signal()
    nextRequested = Signal()
    positionReported = Signal(str, float, float)   # video_id, pos_s, duration_s

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.player: MpvPlayer | None = None
        self._current: dict | None = None
        self._player_ready = False
        self._user_seeking = False

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
        self.title_label.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {T.TEXT};")
        head.addWidget(self.title_label)
        head.addStretch()
        self.info_label = QLabel("")
        self.info_label.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")
        head.addWidget(self.info_label)
        root.addLayout(head)

        # ---- body: video + side panel
        body = QHBoxLayout()
        body.setContentsMargins(14, 0, 14, 0)
        body.setSpacing(12)

        self.video_host = QWidget()
        self.video_host.setStyleSheet("background: #000; border-radius: 10px;")
        self.video_host.setMinimumSize(480, 270)
        self.video_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_host.setAttribute(Qt.WA_NativeWindow, True)
        self.video_host.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
        body.addWidget(self.video_host, 1)

        side = QFrame()
        side.setObjectName("panel")
        side.setFixedWidth(268)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 12, 12, 12)
        sl.setSpacing(8)
        self.meta_label = QLabel("对局信息")
        self.meta_label.setStyleSheet(f"color: {T.TEXT}; font-weight: 600; font-size: 13px;")
        sl.addWidget(self.meta_label)
        self.meta_body = QLabel("—")
        self.meta_body.setWordWrap(True)
        self.meta_body.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px; line-height: 150%;")
        sl.addWidget(self.meta_body)
        sl.addSpacing(6)
        ev_title = QLabel("事件时间轴")
        ev_title.setStyleSheet(f"color: {T.TEXT}; font-weight: 600; font-size: 13px;")
        sl.addWidget(ev_title)
        self.event_list = QWidget()
        self.event_list.setLayout(QVBoxLayout())
        self.event_list.layout().setContentsMargins(0, 0, 0, 0)
        self.event_list.layout().setSpacing(3)
        sl.addWidget(self.event_list, 1)
        sl.addStretch()
        body.addWidget(side)
        root.addLayout(body, 1)

        # ---- transport
        bar = QFrame()
        bar.setStyleSheet(f"background: {T.SURFACE}; border-top: 1px solid {T.BORDER};")
        bl = QVBoxLayout(bar)
        bl.setContentsMargins(14, 6, 14, 10)
        bl.setSpacing(4)

        self.timeline = TimelineSlider()
        self.timeline.seekRequested.connect(self._on_seek)
        bl.addWidget(self.timeline)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_play = QPushButton("⏸")
        self.btn_play.setFixedSize(38, 34)
        self.btn_play.clicked.connect(self.toggle_play)
        row.addWidget(self.btn_play)

        for text, cb in (("⏮", self.prevRequested.emit), ("⏭", self.nextRequested.emit)):
            b = QPushButton(text)
            b.setFixedSize(34, 32)
            b.setProperty("flat", "true")
            b.clicked.connect(cb)
            row.addWidget(b)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")
        self.time_label.setMinimumWidth(105)
        row.addWidget(self.time_label)
        row.addStretch()

        self.speed_btn = QPushButton("1.0×")
        self.speed_btn.setFixedSize(58, 30)
        self.speed_btn.setToolTip("播放速度（快捷键 [ / ]）")
        self.speed_btn.clicked.connect(self._cycle_speed)
        row.addWidget(self.speed_btn)

        mute = QPushButton("🔊")
        mute.setFixedSize(34, 30)
        mute.setProperty("flat", "true")
        mute.clicked.connect(self._toggle_mute)
        row.addWidget(mute)
        self.mute_btn = mute

        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 130)
        self.vol.setValue(80)
        self.vol.setFixedWidth(88)
        self.vol.valueChanged.connect(self._on_volume)
        row.addWidget(self.vol)

        full = QPushButton("⛶")
        full.setFixedSize(34, 30)
        full.setProperty("flat", "true")
        full.setToolTip("全屏（F）")
        full.clicked.connect(self.toggle_fullscreen)
        row.addWidget(full)
        bl.addLayout(row)
        root.addWidget(bar)

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)

    # -------------------------------------------------------------- lifecycle
    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        if not self._player_ready:
            self._player_ready = True
            QTimer.singleShot(0, self._init_player)

    def _init_player(self):
        try:
            wid = int(self.video_host.winId())
            self.player = MpvPlayer(wid=wid, hwdec=self.settings.get("hwdec", "auto"))
        except Exception as exc:
            self.info_label.setText(f"播放引擎不可用：{exc}")
            return
        p = self.player
        p.positionChanged.connect(self._on_position)
        p.durationChanged.connect(self._on_duration)
        p.pausedChanged.connect(self._on_paused)
        p.ended.connect(self._on_ended)
        p.errorOccurred.connect(self._on_error)
        p.volumeChanged.connect(lambda v: self.vol.setValue(int(v)))
        p.set_volume(int(self.settings.get("volume", 80)))
        self._timer.start()
        if self._pending:
            self.load(self._pending[0], self._pending[1])
            self._pending = None

    _pending = None

    def load(self, row: dict, start_pos: float | None = None):
        """Open one indexed video row."""
        self._current = row
        self.title_label.setText(row.get("vname") or row.get("vtype") or
                                 os.path.basename(row.get("path", "")))
        parts = [x for x in (row.get("map_cn"), row.get("agent_cn"), row.get("mode_cn")) if x]
        self.info_label.setText(" · ".join(parts))
        self._render_meta(row)
        self._render_events(row.get("video_id", ""))

        if not self.player:
            self._pending = (row, start_pos)
            return
        if row.get("file_state") == 2 or not os.path.isfile(row.get("path", "")):
            self._on_error("源文件不存在或已被移动。请重新扫描，或在列表中确认状态。")
            return
        self.player.play(row["path"], start_pos)
        self.btn_play.setText("⏸")

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
        if row.get("duration_ms"):
            lines.append(f"时长：{human_time(row['duration_ms']/1000.0)}")
        if row.get("width"):
            lines.append(f"分辨率：{row['width']}x{row['height']}  {int(row.get('fps',0))}fps")
        if row.get("path"):
            lines.append(f"\n{row['path']}")
        self.meta_body.setText("\n".join(lines) if lines else "—")

    def _render_events(self, video_id: str):
        lay = self.event_list.layout()
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._event_markers = []
        if not video_id or not hasattr(self, "_db"):
            self.timeline.set_markers([])
            return
        for ev in self._db.get_events(video_id):
            sec = ev["ms"] / 1000.0
            self._event_markers.append((sec, ev["etype"]))
            b = QPushButton(f"{human_time(sec)}  {self._event_text(ev)}")
            b.setCursor(Qt.PointingHandCursor)
            color = T.ACCENT if ev["etype"] == "kill" else "#7A7A8C"
            b.setStyleSheet(f"""
                QPushButton {{ background: transparent; border: none; text-align: left;
                    color: {T.TEXT_DIM}; padding: 5px 8px; border-radius: 6px; font-size: 12px; }}
                QPushButton:hover {{ background: {T.SURFACE_2}; color: {T.TEXT}; }}
            """)
            b.clicked.connect(lambda _=False, s=sec: self.seek_to(s))
            lay.addWidget(b)
        lay.addStretch()
        self.timeline.set_markers(self._event_markers)

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

    def toggle_fullscreen(self):
        if self.player:
            self.player.set_fullscreen(not getattr(self, "_fs", False))
            self._fs = not getattr(self, "_fs", False)

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
            self.positionReported.emit(self._current.get("video_id", ""), pos, self.player.duration)

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

    def _on_error(self, msg: str):
        self.info_label.setStyleSheet(f"color: {T.AMBER}; font-size: 12px;")
        self.info_label.setText(msg)
        self.btn_play.setText("▶")

    def clear_error(self):
        self.info_label.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")

    def shutdown(self):
        self._timer.stop()
        if self.player:
            self.player.shutdown()
