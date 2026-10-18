"""Secondary pages: favourites, recent playback, and the recording workbench."""
from __future__ import annotations

import os
import shutil

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout)

from . import theme as T
from .grid import VideoGridView


class SimpleGridPage(QFrame):
    """Favourites / recently played — same grid, different query."""

    playRequested = Signal(dict)

    def __init__(self, db, thumbs, kind: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.kind = kind
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("filterBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 12, 14, 12)
        title = QLabel("收藏夹" if kind == "favorites" else "最近播放")
        T.style(title, "color: $TEXT; font-size: 15px; font-weight: 600;")
        bl.addWidget(title)
        bl.addStretch()
        b = QPushButton("⟳  刷新")
        b.clicked.connect(self.reload)
        bl.addWidget(b)
        self.stats = QLabel("")
        self.stats.setObjectName("statLabel")
        bl.addWidget(self.stats)
        root.addWidget(bar)

        self.grid = VideoGridView(thumbs, self)
        self.grid.playRequested.connect(self.playRequested.emit)
        T.style(self.grid, "QListView { background: $BG; padding: 6px; }")
        root.addWidget(self.grid, 1)
        self.reload()

    def reload(self):
        if self.kind == "favorites":
            rows, total = self.db.query_videos({"favorite": True, "sort": "最新"}, 500, 0)
        else:
            rows = self.db.recent(120)
            total = len(rows)
        self.grid.set_rows(rows)
        self.stats.setText(f"{len(rows)} 个片段")

    def rows(self):
        return self.grid.model.rows


class RecordPage(QFrame):
    """Phase 3 workbench — currently an honest capability probe, not fake UI."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)

        h = QLabel("录屏工作台")
        T.style(h, "color: $TEXT; font-size: 18px; font-weight: 700;")
        root.addWidget(h)

        self.status = QFrame()
        self.status.setObjectName("panel")
        sl = QVBoxLayout(self.status)
        sl.setContentsMargins(18, 16, 18, 16)
        sl.setSpacing(8)
        self.lbl_obs = QLabel("")
        self.lbl_obs.setWordWrap(True)
        T.style(self.lbl_obs, "color: $TEXT; font-size: 13px; line-height: 160%;")
        sl.addWidget(self.lbl_obs)
        self.lbl_detail = QLabel("")
        self.lbl_detail.setWordWrap(True)
        T.style(self.lbl_detail, "color: $TEXT_DIM; font-size: 12px; line-height: 165%;")
        sl.addWidget(self.lbl_detail)
        root.addWidget(self.status)

        self._probe()

        b = QPushButton("重新检测")
        b.setFixedWidth(120)
        b.clicked.connect(self._probe)
        root.addWidget(b)
        root.addStretch()

    def _find_obs(self) -> str:
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     r"D:\Program", r"D:\Program Files",
                     os.path.join(os.path.expanduser("~"), "Scoop", "apps", "obs-studio", "current")):
            cand = os.path.join(base, "obs-studio", "bin", "64bit", "obs64.exe")
            if os.path.isfile(cand):
                return cand
        w = shutil.which("obs64") or shutil.which("obs")
        return w or ""

    def _probe(self):
        exe = self._find_obs()
        if not exe:
            self.lbl_obs.setText("状态：未检测到 OBS Studio")
            self.lbl_detail.setText(
                "第一阶段录屏方案是「OBS Studio + obs-websocket」：由 OBS 负责采集与编码，"
                "ReplayStudio 负责录制控制、参数预设、全局热键和录制后的入库。\n\n"
                "因此录屏模块依赖 OBS：\n"
                "  · 需要单独安装 OBS Studio（28 以上版本自带 obs-websocket 5）；\n"
                "  · 录制时 OBS 需要保持运行（可作为后端常驻，界面可关闭）；\n"
                "  · 分辨率、帧率、码率、编码器、封装格式由本应用通过 WebSocket 设置；\n"
                "  · 部分高级编码参数仍需 OBS 自身的设置界面或配置文件调整。\n\n"
                "安装 OBS 后点击「重新检测」，录屏控制、热键与输出入库将在 Phase 3 接通。")
            return
        self.lbl_obs.setText(f"状态：已发现 OBS Studio\n{exe}")
        self.lbl_detail.setText(
            "下一步（Phase 3）：通过 obs-websocket 5 建立本地 WebSocket 连接并完成鉴权，"
            "之后即可在本页实现录制源选择、开始/停止、全局热键与录制完成后的自动入库。\n\n"
            "说明：WebSocket 仅监听 127.0.0.1 并使用口令鉴权，不会对外暴露控制端口。")
