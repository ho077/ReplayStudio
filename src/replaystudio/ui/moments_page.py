"""无畏时刻 library page: faceted filters + poster grid."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout,
                               QLabel, QPushButton, QVBoxLayout, QWidget)

from . import theme as T
from .grid import VideoGridView
from .widgets import SearchEdit

SORTS = ["最新", "最早", "时长", "大小", "击杀数"]
PAGE = 240


class MomentsPage(QFrame):
    playRequested = Signal(dict)
    scanRequested = Signal(bool)
    favoriteToggled = Signal(dict)
    tagRequested = Signal(dict)
    locateRequested = Signal(dict)

    def __init__(self, db, thumbs, parent=None):
        super().__init__(db and parent)
        self.db = db
        self.offset = 0
        self._loading = False
        self._total = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------------- filter bar
        bar = QFrame()
        bar.setObjectName("filterBar")
        bl = QVBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.cb_account = QComboBox()
        self.cb_account.setMinimumWidth(140)
        self.cb_map = QComboBox(); self.cb_map.setMinimumWidth(110)
        self.cb_agent = QComboBox(); self.cb_agent.setMinimumWidth(96)
        self.cb_type = QComboBox(); self.cb_type.setMinimumWidth(104)
        self.cb_mode = QComboBox(); self.cb_mode.setMinimumWidth(100)
        self.cb_result = QComboBox(); self.cb_result.setMinimumWidth(80)
        for cb, label in ((self.cb_account, "全部账号"), (self.cb_map, "全部地图"),
                          (self.cb_agent, "全部特工"), (self.cb_type, "全部类型"),
                          (self.cb_mode, "全部模式"), (self.cb_result, "全部结果")):
            cb.addItem(label, "")
            cb.currentIndexChanged.connect(self.reload)
            row1.addWidget(cb)
        self.cb_result.addItem("仅胜利", "win")
        self.cb_result.addItem("仅失败", "lose")
        row1.addStretch()

        self.btn_scan = QPushButton("⟳  扫描 / 刷新")
        self.btn_scan.setProperty("accent", "true")
        self.btn_scan.clicked.connect(lambda: self.scanRequested.emit(False))
        row1.addWidget(self.btn_scan)
        btn_force = QPushButton("重建索引")
        btn_force.setToolTip("忽略增量缓存，重新解密并解析全部账号数据")
        btn_force.clicked.connect(lambda: self.scanRequested.emit(True))
        row1.addWidget(btn_force)
        bl.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.search = SearchEdit()
        self.search.setMinimumWidth(260)
        self.search.returnPressed.connect(self.reload)
        self.search.textChanged.connect(self._debounce)
        row2.addWidget(self.search)

        self.cb_sort = QComboBox()
        self.cb_sort.addItems(SORTS)
        self.cb_sort.currentIndexChanged.connect(self.reload)
        row2.addWidget(self.cb_sort)

        self.chk_fav = QCheckBox("仅收藏")
        self.chk_mine = QCheckBox("仅我的击杀/死亡")
        self.chk_missing = QCheckBox("仅缺失文件")
        for c in (self.chk_fav, self.chk_mine, self.chk_missing):
            c.stateChanged.connect(self.reload)
            row2.addWidget(c)
        row2.addStretch()

        self.stats = QLabel("")
        self.stats.setObjectName("statLabel")
        row2.addWidget(self.stats)
        bl.addLayout(row2)
        root.addWidget(bar)

        # ---------------- grid
        self.grid = VideoGridView(thumbs, self)
        self.grid.playRequested.connect(self.playRequested.emit)
        self.grid.toggleFavorite.connect(self.favoriteToggled.emit)
        self.grid.addTagRequested.connect(self.tagRequested.emit)
        self.grid.locateRequested.connect(self.locateRequested.emit)
        self.grid.needMore.connect(self.load_more)
        T.style(self.grid, "QListView { background: $BG; padding: 6px; }")
        root.addWidget(self.grid, 1)

        self._debounce_timer = None

    def _debounce(self):
        from PySide6.QtCore import QTimer
        if self._debounce_timer is None:
            self._debounce_timer = QTimer(self)
            self._debounce_timer.setSingleShot(True)
            self._debounce_timer.timeout.connect(self.reload)
        self._debounce_timer.start(320)

    # ------------------------------------------------------------------ filters
    def refresh_facets(self, accounts: bool = True):
        """Rebuild the filter combo boxes.

        The counts depend on the *other* filters currently picked, not just on
        the index, so they are refreshed on every `reload()` — otherwise
        "霓虹町 (154)" keeps promising the unfiltered total while the grid,
        honouring 仅我的击杀/死亡 or the search box, lists 153.

        `accounts=False` leaves the account list alone: it only changes when a
        scan runs, and rebuilding it on every keystroke would throw away the
        user's selection for nothing.
        """
        if accounts:
            acc_rows = self.db.list_accounts()
            self.cb_account.blockSignals(True)
            cur = self.cb_account.currentData()
            self.cb_account.clear()
            self.cb_account.addItem("全部账号", "")
            for a in acc_rows:
                label = f"{a['nick']}#{a['tag']}" if a["nick"] else f"账号 {a['openid']}"
                self.cb_account.addItem(label, a["openid"])
            if cur:
                i = self.cb_account.findData(cur)
                if i >= 0:
                    self.cb_account.setCurrentIndex(i)
            self.cb_account.blockSignals(False)

        fac = self.db.facets(self.cb_account.currentData() or None,
                             self.current_filters())
        for cb, key in ((self.cb_map, "maps"), (self.cb_agent, "agents"),
                        (self.cb_type, "types"), (self.cb_mode, "modes")):
            items = fac[key]
            cb.blockSignals(True)
            cur = cb.currentData()
            if [cb.itemData(i) for i in range(1, cb.count())] == [n for n, _ in items]:
                # Same values, new counts: rewrite the labels in place. A full
                # rebuild would drop the current selection mid-interaction.
                for i, (name, n) in enumerate(items, start=1):
                    cb.setItemText(i, f"{name} ({n})")
            else:
                cb.clear()
                cb.addItem({"maps": "全部地图", "agents": "全部特工",
                            "types": "全部类型", "modes": "全部模式"}[key], "")
                for name, n in items:
                    cb.addItem(f"{name} ({n})", name)
                if cur:
                    i = cb.findData(cur)
                    if i >= 0:
                        cb.setCurrentIndex(i)
            cb.blockSignals(False)

    def current_filters(self) -> dict:
        f = {
            "openid": self.cb_account.currentData() or "",
            "map": self.cb_map.currentData() or "",
            "agent": self.cb_agent.currentData() or "",
            "vtype": self.cb_type.currentData() or "",
            "mode": self.cb_mode.currentData() or "",
            "sort": self.cb_sort.currentText(),
            "q": self.search.text().strip(),
        }
        r = self.cb_result.currentData()
        if r:
            f["result"] = r
        if self.chk_fav.isChecked():
            f["favorite"] = True
        if self.chk_mine.isChecked():
            f["mine_only"] = True
        if self.chk_missing.isChecked():
            f["state"] = "missing"
        return f

    # -------------------------------------------------------------------- data
    def reload(self):
        self.offset = 0
        self._loading = False
        # Counts first: they are derived from these very filters, so the labels
        # and the grid can never disagree.
        self.refresh_facets(accounts=False)
        rows, total = self.db.query_videos(self.current_filters(), PAGE, 0)
        self.grid.set_rows(rows)
        self.offset = len(rows)
        self._total = total
        self._update_stats()

    def load_more(self):
        if self._loading or self.offset >= self._total:
            return
        self._loading = True
        rows, _ = self.db.query_videos(self.current_filters(), PAGE, self.offset)
        if rows:
            self.grid.append_rows(rows)
            self.offset += len(rows)
        self._loading = False
        self._update_stats()

    def _update_stats(self):
        # "索引共" is a property of the library, so it ignores the filters —
        # unlike the facet counts, which must honour them.
        s = self.db.stats()
        shown = self.grid.model.rowCount()
        miss = f" · {s['missing']} 个源文件缺失" if s["missing"] else ""
        self.stats.setText(f"显示 {shown} / {self._total} 个片段 · 索引共 {s['videos']} 个{miss}")

    def rows(self):
        return self.grid.model.rows

    def refresh_row(self, video_id: str, **fields):
        self.grid.model.update_row(video_id, **fields)
