"""Does the number on the filter label match what the grid lists?

Reported: "按照地图或英雄分类时，实际数量不到分类括号后面带的数量". The SQL
agrees with itself, so this drives the real MomentsPage and reads the numbers
off the widgets: pick 仅我的击杀/死亡, then compare every map/agent label with
the row count the grid ends up showing.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from replaystudio.core.db import Database
    from replaystudio.media.thumbs import ThumbnailService
    from replaystudio.ui.moments_page import MomentsPage

    db = Database()
    thumbs = ThumbnailService(app)
    page = MomentsPage(db, thumbs)
    page.show()

    scenarios = [
        ("no extra filter", lambda: None),
        ("仅我的击杀/死亡", lambda: page.chk_mine.setChecked(True)),
    ]

    bad = 0
    for label, setup in scenarios:
        page.chk_mine.setChecked(False)
        page.chk_fav.setChecked(False)
        page.search.setText("")
        setup()
        page.reload()
        app.processEvents()
        print(f"\n### {label} — stats: {page.stats.text()}")

        for cb, name in ((page.cb_map, "map"), (page.cb_agent, "agent")):
            print(f"  --- {name} ---")
            for i in range(1, min(cb.count(), 5)):
                text = cb.itemText(i)
                promised = int(re.search(r"\((\d+)\)", text).group(1))
                cb.blockSignals(True)
                cb.setCurrentIndex(i)
                cb.blockSignals(False)
                page.reload()
                app.processEvents()
                got = page._total
                ok = promised == got
                bad += 0 if ok else 1
                print(f"    {text:<26} label={promised:<6} grid={got:<6}"
                      f" shown={page.grid.model.rowCount():<6}"
                      f" {'OK' if ok else '<<< MISMATCH'}")
            cb.blockSignals(True)
            cb.setCurrentIndex(0)
            cb.blockSignals(False)
            page.reload()

    # Paging: a facet bigger than one page (PAGE=240) has to load completely,
    # otherwise scrolling to the bottom still shows fewer than promised.
    page.chk_mine.setChecked(False)
    idx = next((i for i in range(page.cb_type.count())
                if page.cb_type.itemText(i).startswith("三杀时刻")), 0)
    page.cb_type.blockSignals(True)
    page.cb_type.setCurrentIndex(idx)
    page.cb_type.blockSignals(False)
    page.reload()
    app.processEvents()
    promised = int(re.search(r"\((\d+)\)", page.cb_type.itemText(idx)).group(1))
    for _ in range(20):
        if page.offset >= page._total:
            break
        page.load_more()
        app.processEvents()
    shown = page.grid.model.rowCount()
    ok = shown == promised == page._total
    print(f"\n### paging: {page.cb_type.itemText(idx)} -> promised={promised} "
          f"loaded={shown} total={page._total} {'OK' if ok else '<<< MISMATCH'}")
    bad += 0 if ok else 1

    print(f"\nmismatches: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    from replaystudio.core import diagnostics

    diagnostics.install()
    diagnostics.hard_exit(main())
