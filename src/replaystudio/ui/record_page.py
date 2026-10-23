"""录屏工作台：选采集源、开录、收文件。

UI 上每一处可选项都对应 OBS 真实接受的参数（来源 GetInputDefaultSettings，
见 core/recording.py），没有"先做好看再说"的假开关。状态完全由 OBS 的事件
驱动 —— StartRecord 的回复只代表"请求被接受"，真正开始/停止要看
RecordStateChanged。
"""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout)

from ..core import obs_paths, recording
from ..core.recording import (IDLE, KIND_GAME, KIND_MONITOR, KIND_WINDOW,
                              PAUSED, RECORDING, STARTING, STOPPING)
from . import theme as T


def _fmt(ms: int) -> str:
    s = max(0, int(ms)) // 1000
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("panel")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(10)
    h = QLabel(title)
    T.style(h, "color: $TEXT; font-size: 14px; font-weight: 600;")
    lay.addWidget(h)
    return f, lay


def _row() -> QHBoxLayout:
    r = QHBoxLayout()
    r.setSpacing(8)
    return r


class RecordPage(QFrame):
    """Drive one OBS instance and drop finished files into the local library."""

    recorded = Signal(str)          # path of a finished recording
    statusMessage = Signal(str)

    def __init__(self, db, settings=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.settings = settings
        self.rec = recording.Recorder(db, self)
        self._last_file = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)

        h = QLabel("录屏工作台")
        T.style(h, "color: $TEXT; font-size: 18px; font-weight: 700;")
        root.addWidget(h)

        root.addWidget(self._build_status())
        root.addWidget(self._build_source())
        root.addWidget(self._build_quality())
        root.addWidget(self._build_output())
        root.addStretch()

        # Populating the widgets fires toggled()/currentIndexChanged(), which
        # would immediately write half-initialised values back over the stored
        # ones. Stay mute until the restore is finished.
        self._loading = True
        self._wire()
        self._load_settings()
        self._loading = False
        self._sync()

    # ------------------------------------------------------------------ 状态卡
    def _build_status(self) -> QFrame:
        card, lay = _card("录制")

        line = _row()
        self.dot = QLabel("●")
        T.style(self.dot, "color: $TEXT_FAINT; font-size: 13px;")
        self.lbl_state = QLabel("未连接 OBS")
        T.style(self.lbl_state, "color: $TEXT; font-size: 13px;")
        line.addWidget(self.dot)
        line.addWidget(self.lbl_state)
        line.addStretch()
        lay.addLayout(line)

        self.lbl_time = QLabel("00:00:00")
        T.style(self.lbl_time,
                "color: $TEXT; font-size: 34px; font-weight: 300; "
                "font-family: Consolas, 'Cascadia Mono', monospace;")
        lay.addWidget(self.lbl_time)

        self.lbl_hint = QLabel("")
        self.lbl_hint.setWordWrap(True)
        T.style(self.lbl_hint, "color: $TEXT_DIM; font-size: 11.5px; line-height: 160%;")
        lay.addWidget(self.lbl_hint)

        btns = _row()
        self.b_connect = QPushButton("连接 OBS")
        self.b_start = QPushButton("开始录制")
        self.b_pause = QPushButton("暂停")
        self.b_stop = QPushButton("停止")
        for b, w in ((self.b_connect, 110), (self.b_start, 110),
                     (self.b_pause, 80), (self.b_stop, 80)):
            b.setFixedWidth(w)
            btns.addWidget(b)
        btns.addStretch()
        lay.addLayout(btns)
        return card

    # ------------------------------------------------------------------ 采集源
    def _build_source(self) -> QFrame:
        card, lay = _card("采集源")

        r1 = _row()
        r1.addWidget(QLabel("模式"))
        self.cb_kind = QComboBox()
        self.cb_kind.addItem("整个屏幕", KIND_MONITOR)
        self.cb_kind.addItem("某个窗口", KIND_WINDOW)
        self.cb_kind.addItem("游戏画面", KIND_GAME)
        r1.addWidget(self.cb_kind, 1)
        lay.addLayout(r1)

        r2 = _row()
        r2.addWidget(QLabel("窗口"))
        self.cb_window = QComboBox()
        self.cb_window.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cb_window.setMinimumWidth(260)
        self.b_refresh = QPushButton("刷新列表")
        self.b_refresh.setFixedWidth(90)
        r2.addWidget(self.cb_window, 1)
        r2.addWidget(self.b_refresh)
        lay.addLayout(r2)

        r3 = _row()
        self.ck_cursor = QCheckBox("捕获鼠标")
        self.ck_desktop = QCheckBox("桌面音频")
        self.ck_mic = QCheckBox("麦克风")
        for c in (self.ck_cursor, self.ck_desktop, self.ck_mic):
            r3.addWidget(c)
        r3.addStretch()
        lay.addLayout(r3)
        return card

    def _build_quality(self) -> QFrame:
        card, lay = _card("输出格式")
        r = _row()
        r.addWidget(QLabel("封装"))
        self.cb_container = QComboBox()
        for value, label in recording.CONTAINERS:
            self.cb_container.addItem(label, value)
        r.addWidget(self.cb_container, 1)
        lay.addLayout(r)

        note = QLabel("编码器由 OBS 依据硬件自动选择：简单输出模式下手动指定 "
                      "RecEncoder 实测无效（设成 hevc_nvenc / av1_nvenc 录出来仍是 "
                      "H.264），所以这里不给一个假开关。")
        note.setWordWrap(True)
        T.style(note, "color: $TEXT_FAINT; font-size: 11px; line-height: 165%;")
        lay.addWidget(note)
        return card

    # -------------------------------------------------------------------- 输出
    def _build_output(self) -> QFrame:
        card, lay = _card("输出")
        r = _row()
        self.ed_dir = QLineEdit()
        self.ed_dir.setPlaceholderText("录制保存目录")
        self.b_pick = QPushButton("选择…")
        self.b_pick.setFixedWidth(80)
        self.b_open = QPushButton("打开")
        self.b_open.setFixedWidth(80)
        r.addWidget(self.ed_dir, 1)
        r.addWidget(self.b_pick)
        r.addWidget(self.b_open)
        lay.addLayout(r)

        self.lbl_files = QLabel("")
        T.style(self.lbl_files, "color: $TEXT_DIM; font-size: 11.5px;")
        lay.addWidget(self.lbl_files)
        return card

    # -------------------------------------------------------------------- 接线
    def _wire(self):
        self.rec.connectionChanged.connect(self._on_conn)
        self.rec.stateChanged.connect(self._on_state)
        self.rec.elapsed.connect(lambda ms: self.lbl_time.setText(_fmt(ms)))
        self.rec.error.connect(self._on_error)
        self.rec.finished.connect(self._on_finished)

        self.b_connect.clicked.connect(self.rec.connect_obs)
        self.b_start.clicked.connect(self.rec.start)
        self.b_stop.clicked.connect(self.rec.stop)
        self.b_pause.clicked.connect(self.rec.toggle_pause)
        self.b_refresh.clicked.connect(self._refresh_windows)
        self.b_pick.clicked.connect(self._pick_dir)
        self.b_open.clicked.connect(self._open_dir)

        self.cb_kind.currentIndexChanged.connect(self._on_kind_changed)
        self.cb_kind.currentIndexChanged.connect(self._save_settings)
        self.cb_window.currentTextChanged.connect(self._save_settings)
        self.cb_container.currentIndexChanged.connect(self._save_settings)
        for ck in (self.ck_cursor, self.ck_desktop, self.ck_mic):
            ck.toggled.connect(self._save_settings)

    # ---------------------------------------------------------------- 设置读写
    def _load_settings(self):
        s = self.settings or {}
        kind = s.get("record_kind", KIND_MONITOR) or KIND_MONITOR
        idx = self.cb_kind.findData(kind)
        if idx >= 0:
            self.cb_kind.setCurrentIndex(idx)
        self.ck_cursor.setChecked(bool(int(s.get("record_cursor", 1) or 0)))
        self.ck_desktop.setChecked(bool(int(s.get("record_desktop_audio", 1) or 0)))
        self.ck_mic.setChecked(bool(int(s.get("record_mic", 0) or 0)))
        fmt = s.get("record_format", "hybrid_mp4") or "hybrid_mp4"
        i = self.cb_container.findData(fmt)
        if i >= 0:
            self.cb_container.setCurrentIndex(i)
        d = s.get("record_dir", "")
        # Migrate the old default: it pointed into %LOCALAPPDATA%, i.e. C:, and
        # video does not belong on the system drive. Only a value the user never
        # chose is replaced — anything they picked themselves is left alone.
        if obs_paths.is_legacy_default(d):
            d = obs_paths.default_record_dir(self.db)
            if self.settings is not None:
                self.settings["record_dir"] = d
                try:
                    self.settings.save()
                except Exception:  # noqa: BLE001
                    pass
        self.ed_dir.setText(d)
        self._refresh_windows()
        saved = s.get("record_window", "") or ""
        if saved:
            i = self.cb_window.findText(saved)
            if i >= 0:
                self.cb_window.setCurrentIndex(i)
        # setCurrentIndex() is silent when the index does not change, so the
        # window combo would stay enabled even in monitor mode.
        self._on_kind_changed()
        self._push_spec()

    def _save_settings(self):
        if self.settings is None or getattr(self, "_loading", False):
            return
        self._push_spec()
        s = self.settings
        s["record_kind"] = self.cb_kind.currentData() or KIND_MONITOR
        s["record_window"] = self.cb_window.currentText() if \
            self.cb_kind.currentData() == KIND_WINDOW else ""
        s["record_cursor"] = int(self.ck_cursor.isChecked())
        s["record_desktop_audio"] = int(self.ck_desktop.isChecked())
        s["record_mic"] = int(self.ck_mic.isChecked())
        s["record_format"] = self.cb_container.currentData() or "hybrid_mp4"
        s["record_dir"] = self.ed_dir.text().strip()
        try:
            s.save()
        except Exception:  # noqa: BLE001
            pass

    def _push_spec(self):
        """Copy the widgets into the recorder's spec."""
        sp = self.rec.spec
        sp.kind = self.cb_kind.currentData() or KIND_MONITOR
        sp.window_title = self.cb_window.currentText() if sp.kind == KIND_WINDOW else ""
        sp.capture_cursor = self.ck_cursor.isChecked()
        sp.desktop_audio = self.ck_desktop.isChecked()
        sp.mic = self.ck_mic.isChecked()
        sp.container = self.cb_container.currentData() or "hybrid_mp4"
        sp.output_dir = self.ed_dir.text().strip() or obs_paths.default_record_dir(self.db)

    def _refresh_windows(self):
        self.cb_window.clear()
        titles = recording.list_windows()
        if not titles:
            self.cb_window.addItem("（没有可捕获的窗口）")
        else:
            self.cb_window.addItems(titles)

    def _on_kind_changed(self):
        is_win = self.cb_kind.currentData() == KIND_WINDOW
        self.cb_window.setEnabled(is_win)
        self.b_refresh.setEnabled(is_win)
        if is_win and self.cb_window.count() and \
                self.cb_window.currentText().startswith("（"):
            self._refresh_windows()

    # ------------------------------------------------------------------ 目录
    def _pick_dir(self):
        start = self.ed_dir.text().strip() or obs_paths.default_record_dir(self.db)
        d = QFileDialog.getExistingDirectory(self, "选择录制保存目录", start)
        if d:
            self.ed_dir.setText(d)
            self._save_settings()

    def _open_dir(self):
        d = self.ed_dir.text().strip() or obs_paths.default_record_dir(self.db)
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        if sys.platform == "win32":
            os.startfile(d)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", d])
        else:
            subprocess.Popen(["xdg-open", d])

    # ------------------------------------------------------------------ 槽
    def _on_conn(self, ok: bool, msg: str):
        self.lbl_hint.setText(msg)
        self.b_connect.setEnabled(not ok)
        self._sync()

    def _on_state(self, state: str):
        self._sync()
        text = {
            IDLE: "就绪", STARTING: "正在启动录制…", RECORDING: "录制中",
            PAUSED: "已暂停", STOPPING: "正在停止…",
        }.get(state, state)
        color = "$ACCENT" if state == RECORDING else "$TEXT"
        T.style(self.dot, f"color: {color}; font-size: 13px;")
        T.style(self.lbl_state, f"color: {color}; font-size: 13px;")
        self.lbl_state.setText(text)
        if state == IDLE:
            self.lbl_time.setText("00:00:00")

    def _on_error(self, msg: str):
        self.lbl_hint.setText(msg)
        self.statusMessage.emit(msg)

    def _on_finished(self, path: str):
        if not path:
            return
        self._last_file = path
        self.statusMessage.emit(f"录制完成：{os.path.basename(path)}")
        self.lbl_hint.setText(f"已保存到 {path}，并加入了本地视频库")
        self._count_files()
        self.recorded.emit(path)

    def _sync(self):
        """Enable/disable transport buttons for the current combination."""
        connected = self.rec.client is not None and self.rec.client.ready
        st = self.rec.state
        self.b_start.setEnabled(connected and st == IDLE)
        self.b_stop.setEnabled(st in (RECORDING, PAUSED, STARTING))
        self.b_pause.setEnabled(st in (RECORDING, PAUSED))
        self.b_pause.setText("继续" if st == PAUSED else "暂停")
        self._count_files()

    def _count_files(self):
        d = self.ed_dir.text().strip() or obs_paths.default_record_dir(self.db)
        n = 0
        try:
            with os.scandir(d) as it:
                n = sum(1 for e in it if e.is_file())
        except OSError:
            pass
        self.lbl_files.setText(f"目录里已有 {n} 个文件：{d}")

    # --------------------------------------------------------------- 生命周期
    def close_down(self):
        """Called when the app is about to quit."""
        try:
            self.rec.disconnect_obs()
        except Exception:  # noqa: BLE001
            pass
