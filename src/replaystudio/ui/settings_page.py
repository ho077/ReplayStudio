"""Settings: data location, playback engine, index maintenance, about."""
from __future__ import annotations

import os
import shutil
import subprocess

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..aclos import reader
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

        root = QVBoxLayout(self)
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
        r3.addStretch()
        l3.addLayout(r3)
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
            f"占用 {s['bytes']/1024**3:.1f} GB · 缺失 {s['missing']} 个")
