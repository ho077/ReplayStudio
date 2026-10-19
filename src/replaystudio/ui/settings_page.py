"""Settings: data location, playback engine, index maintenance, about."""
from __future__ import annotations

import os
import shutil
import subprocess

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from ..aclos import reader
from ..core import assoc, diagnostics
from ..core.db import user_data_dir
from ..media import thumbs
from ..media.player import ensure_mpv_dll
from . import theme as T

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m2ts", ".wmv", ".mpg", ".mpeg"}


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("panel")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)
    h = QLabel(title)
    T.style(h, "color: $TEXT; font-size: 14px; font-weight: 600;")
    lay.addWidget(h)
    return f, lay


class ThemeCard(QPushButton):
    """One themed tile: horizon strip of swatches + name + description."""

    def __init__(self, pal, parent=None):
        super().__init__(parent)
        self.pal = pal
        self.setObjectName("themeCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(84)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        strip = QHBoxLayout()
        strip.setSpacing(0)
        for color, stretch in ((pal.BG, 3), (pal.SURFACE, 2),
                               (pal.CARD, 2), (pal.ACCENT, 3)):
            sw = QLabel()
            sw.setObjectName("swatch")
            sw.setFixedHeight(18)
            sw.setStyleSheet(f"background: {color}; border-radius: 3px;")
            strip.addWidget(sw, stretch)
        outer.addLayout(strip)

        name = QLabel(pal.name)
        name.setObjectName("themeName")
        outer.addWidget(name)
        desc = QLabel(pal.desc)
        desc.setObjectName("themeDesc")
        desc.setWordWrap(True)
        outer.addWidget(desc)

    def set_active(self, active: bool):
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class SettingsPage(QFrame):
    rebuildRequested = Signal()
    dirChanged = Signal(str)

    def __init__(self, db, settings, parent=None):
        super().__init__(parent)
        self.db = db
        self.settings = settings

        # Settings keeps growing (Phase 2 added playback/association/shortcut
        # cards), so the whole page scrolls instead of squeezing.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        wrap = QWidget()
        scroll.setWidget(wrap)
        outer.addWidget(scroll)
        self.scroll = scroll   # exposed so tests/screenshots can scroll to a card

        root = QVBoxLayout(wrap)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        # ---------------- appearance
        c5, l5 = _card("外观主题")
        grid = QGridLayout()
        grid.setSpacing(10)
        self.theme_cards: list[ThemeCard] = []
        for i, pal in enumerate(T.palette_list()):
            card = ThemeCard(pal, self)
            card.clicked.connect(lambda _=False, k=pal.key: self._pick_theme(k))
            self.theme_cards.append(card)
            grid.addWidget(card, i // 3, i % 3)
        l5.addLayout(grid)
        root.addWidget(c5)

        # ---------------- data source
        c1, l1 = _card("无畏时刻数据源")
        row = QHBoxLayout()
        self.dir_edit = QLineEdit(settings.get("aclos_dir") or reader.default_db_dir())
        self.dir_edit.setReadOnly(True)
        row.addWidget(self.dir_edit, 1)
        b_pick = QPushButton("选择目录…")
        b_pick.clicked.connect(self._pick_dir)
        row.addWidget(b_pick)
        b_def = QPushButton("恢复默认")
        b_def.clicked.connect(self._reset_dir)
        row.addWidget(b_def)
        l1.addLayout(row)
        hint = QLabel("只读取，不修改、不移动、不删除任何 ACLOS 原始文件。")
        T.style(hint, "color: $TEXT_FAINT; font-size: 11.5px;")
        l1.addWidget(hint)
        root.addWidget(c1)

        # ---------------- playback
        c2, l2 = _card("播放引擎")
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("硬件解码"))
        self.cb_hw = QComboBox()
        self.cb_hw.addItem("自动（推荐）", "auto")
        self.cb_hw.addItem("D3D11VA（Windows 通用）", "d3d11va")
        self.cb_hw.addItem("NVDEC（NVIDIA）", "nvdec")
        self.cb_hw.addItem("仅软件解码", "no")
        cur = settings.get("hwdec", "auto")
        i = self.cb_hw.findData(cur)
        self.cb_hw.setCurrentIndex(i if i >= 0 else 0)
        self.cb_hw.currentIndexChanged.connect(
            lambda _: settings.__setitem__("hwdec", self.cb_hw.currentData()))
        r2.addWidget(self.cb_hw)
        r2.addStretch()
        l2.addLayout(r2)
        self.lbl_engine = QLabel("")
        T.style(self.lbl_engine, "color: $TEXT_DIM; font-size: 11.5px;")
        l2.addWidget(self.lbl_engine)
        self._fill_engine_info()
        root.addWidget(c2)

        # ---------------- playback behaviour
        c6, l6 = _card("播放行为")
        hints = QLabel("跳转间隔决定 ← / → 每次移动的秒数；Shift + 方向键使用长跳转。")
        T.style(hints, "color: $TEXT_FAINT; font-size: 11.5px;")
        l6.addWidget(hints)
        r6 = QHBoxLayout()
        r6.addWidget(QLabel("短跳转"))
        self.sp_step = QSpinBox()
        self.sp_step.setRange(1, 120)
        self.sp_step.setSuffix(" 秒")
        self.sp_step.setValue(int(settings.get("seek_step", 5)))
        self.sp_step.valueChanged.connect(
            lambda v: settings.__setitem__("seek_step", v))
        r6.addWidget(self.sp_step)
        r6.addSpacing(12)
        r6.addWidget(QLabel("长跳转"))
        self.sp_step_long = QSpinBox()
        self.sp_step_long.setRange(5, 600)
        self.sp_step_long.setSuffix(" 秒")
        self.sp_step_long.setValue(int(settings.get("seek_step_long", 30)))
        self.sp_step_long.valueChanged.connect(
            lambda v: settings.__setitem__("seek_step_long", v))
        r6.addWidget(self.sp_step_long)
        r6.addStretch()
        l6.addLayout(r6)

        r6b = QHBoxLayout()
        self.cb_autoplay = QCheckBox("播放结束后自动连播下一个")
        self.cb_autoplay.setChecked(bool(int(settings.get("autoplay_next", 1))))
        self.cb_autoplay.stateChanged.connect(
            lambda v: settings.__setitem__("autoplay_next", 1 if v else 0))
        r6b.addWidget(self.cb_autoplay)
        self.cb_remember = QCheckBox("记住播放进度（断点续播）")
        self.cb_remember.setChecked(bool(int(settings.get("remember_position", 1))))
        self.cb_remember.stateChanged.connect(
            lambda v: settings.__setitem__("remember_position", 1 if v else 0))
        r6b.addWidget(self.cb_remember)
        r6b.addStretch()
        l6.addLayout(r6b)
        root.addWidget(c6)

        # ---------------- file association
        c7, l7 = _card("文件关联")
        self.lbl_assoc = QLabel("")
        T.style(self.lbl_assoc, "color: $TEXT_DIM; font-size: 12px;")
        self.lbl_assoc.setWordWrap(True)
        l7.addWidget(self.lbl_assoc)
        r7 = QHBoxLayout()
        self.b_assoc = QPushButton("添加到「打开方式」")
        self.b_assoc.setProperty("accent", "true")
        self.b_assoc.clicked.connect(self._toggle_assoc)
        r7.addWidget(self.b_assoc)
        r7.addStretch()
        l7.addLayout(r7)
        note = QLabel("只写入当前用户的注册表（HKCU），不会夺走系统默认播放器，可随时撤销。")
        T.style(note, "color: $TEXT_FAINT; font-size: 11.5px;")
        l7.addWidget(note)
        root.addWidget(c7)
        self._sync_assoc()

        # ---------------- keyboard
        c8, l8 = _card("快捷键")
        keys = QLabel(
            "空格 播放/暂停　·　← → 跳转（Shift 长跳转）　·　↑ ↓ 音量\n"
            "F 全屏　·　M 静音　·　S 截图　·　P / N 上一个 / 下一个\n"
            "J 切换字幕（Shift+J 关闭）　·　K 切换音轨　·　A 画面比例\n"
            "[ ] 倍速　·　Esc 返回　·　Ctrl+O 打开文件　·　Ctrl+F 搜索")
        T.style(keys, "color: $TEXT_DIM; font-size: 12px; line-height: 175%;")
        keys.setWordWrap(True)
        l8.addWidget(keys)
        root.addWidget(c8)

        # ---------------- index
        c3, l3 = _card("索引与缓存")
        self.lbl_stats = QLabel("")
        T.style(self.lbl_stats, "color: $TEXT_DIM; font-size: 12px;")
        l3.addWidget(self.lbl_stats)
        r3 = QHBoxLayout()
        b_rebuild = QPushButton("重建完整索引")
        b_rebuild.setProperty("accent", "true")
        b_rebuild.clicked.connect(self.rebuildRequested.emit)
        r3.addWidget(b_rebuild)
        b_recheck = QPushButton("重新检查文件状态")
        b_recheck.clicked.connect(self.recheckRequested.emit)
        r3.addWidget(b_recheck)
        b_clear_thumbs = QPushButton("清空缩略图缓存")
        b_clear_thumbs.clicked.connect(self._clear_thumbs)
        r3.addWidget(b_clear_thumbs)
        b_open = QPushButton("打开数据目录")
        b_open.clicked.connect(lambda: self._open(user_data_dir()))
        r3.addWidget(b_open)
        b_logs = QPushButton("打开日志目录")
        b_logs.setToolTip("崩溃日志与运行日志都在这里；反馈问题时请附上最新的 session-*.log")
        b_logs.clicked.connect(lambda: self._open(str(diagnostics.log_dir())))
        r3.addWidget(b_logs)
        r3.addStretch()
        l3.addLayout(r3)
        self.cb_show_missing = QCheckBox("显示「源文件缺失」的片段")
        self.cb_show_missing.setToolTip(
            "关闭时（默认）已缺失的片段不会出现在任何列表里。\n"
            "它们仍保留在索引中，文件回来后会自动恢复显示。")
        self.cb_show_missing.toggled.connect(self._on_show_missing)
        l3.addWidget(self.cb_show_missing)
        self.lbl_log = QLabel("")
        T.style(self.lbl_log, "color: $TEXT_FAINT; font-size: 11px;")
        self.lbl_log.setWordWrap(True)
        l3.addWidget(self.lbl_log)
        root.addWidget(c3)

        # ---------------- about
        c4, l4 = _card("关于")
        about = QLabel(
            "ReplayStudio — 本地视频管理、播放与录屏工作台\n"
            "播放引擎：mpv / libmpv（GPLv2+）；界面：Qt for Python（LGPLv3）；\n"
            "缩略图：FFmpeg；数据库：SQLite。所有数据保存在本机，不会上传。")
        T.style(about, "color: $TEXT_DIM; font-size: 12px; line-height: 160%;")
        about.setWordWrap(True)
        l4.addWidget(about)
        root.addWidget(c4)

        root.addStretch()
        self.refresh_stats()

    recheckRequested = Signal()
    listChanged = Signal()      # a filter/policy change: grids must reload

    # ------------------------------------------------------------------ helpers
    def _fill_engine_info(self):
        try:
            d = ensure_mpv_dll()
            size = os.path.getsize(os.path.join(d, "libmpv-2.dll")) / 1024 / 1024
            self.lbl_engine.setText(f"libmpv 已加载：{d}（{size:.0f} MB）")
        except Exception as exc:
            self.lbl_engine.setText(f"libmpv 不可用：{exc}")

    def _pick_theme(self, key: str):
        win = self.window()
        if hasattr(win, "apply_theme"):
            win.apply_theme(key)
        for card in self.theme_cards:
            card.set_active(card.pal.key == T.current_key())

    def _sync_theme_ui(self):
        for card in self.theme_cards:
            card.set_active(card.pal.key == T.current_key())

    def _on_show_missing(self, on: bool):
        self.settings["show_missing_files"] = 1 if on else 0
        self.db.show_missing = bool(on)
        self.refresh_stats()
        self.listChanged.emit()

    # ------------------------------------------------------------ association
    def _sync_assoc(self):
        on = assoc.is_registered()
        self.b_assoc.setText("移除「打开方式」关联" if on else "添加到「打开方式」")
        self.lbl_assoc.setText(
            "状态：" + ("已注册，可在资源管理器右键 → 打开方式中看到 ReplayStudio。"
                        if on else "未注册。注册后可直接在资源管理器用 ReplayStudio 打开视频。"))

    def _toggle_assoc(self):
        ok, msg = assoc.unregister() if assoc.is_registered() else assoc.register()
        self._sync_assoc()
        if not ok:
            QMessageBox.warning(self, "操作失败", msg)
        else:
            QMessageBox.information(self, "完成", msg)

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择 ACLOS WonderfulDb 目录",
                                             self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)
            self.settings["aclos_dir"] = d
            self.dirChanged.emit(d)

    def _reset_dir(self):
        d = reader.default_db_dir()
        self.dir_edit.setText(d)
        self.settings["aclos_dir"] = d
        self.dirChanged.emit(d)

    def _clear_thumbs(self):
        d = thumbs.cache_dir()
        n = 0
        for f in os.listdir(d):
            try:
                os.remove(os.path.join(d, f))
                n += 1
            except OSError:
                pass
        QMessageBox.information(self, "已清理", f"删除 {n} 个缓存缩略图。\n下次浏览时会按需重新生成。")

    @staticmethod
    def _open(path: str):
        try:
            os.startfile(path)  # noqa: S606 - explicit user action
        except OSError:
            subprocess.Popen(["explorer", path])

    def refresh_stats(self):
        self._sync_theme_ui()
        s = self.db.stats()
        self.lbl_stats.setText(
            f"对局 {s['matches']} 场 · 片段 {s['videos']} 个 · 事件 {s['events']} 条 · "
            f"占用 {s['bytes']/1024**3:.1f} GB"
            + (f"　（另有 {s['missing']} 个源文件已缺失，默认不显示）"
               if s["missing"] else ""))
        self.cb_show_missing.blockSignals(True)
        self.cb_show_missing.setChecked(bool(self.db.show_missing))
        self.cb_show_missing.blockSignals(False)
        self.cb_show_missing.setEnabled(s["missing"] > 0)
        cur = diagnostics.current_log()
        if cur is not None:
            self.lbl_log.setText(f"本次运行日志：{cur}")
        else:
            self.lbl_log.setText(f"日志目录：{diagnostics.log_dir()}")
