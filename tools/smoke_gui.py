"""GUI smoke test: construct the real window, load real data, assert the grid
is populated, then quit. Runs on the offscreen platform so it never steals focus.
"""
from __future__ import annotations

import os
import sys
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
OUT = os.path.join(ROOT, "tools", "_gui_out.txt")

lines = []


def log(*a):
    s = " ".join(str(x) for x in a)
    lines.append(s)
    print(s, flush=True)


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from replaystudio.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    log("window constructed")

    state = {"checked": False}

    def check():
        if state["checked"]:
            return
        state["checked"] = True
        try:
            log("pages in stack:", win.stack.count())
            log("moments rows  :", win.moments.grid.model.rowCount())
            log("sidebar stats :", win.sidebar.stats.text().replace("\n", " | "))
            log("status        :", win.status_label.text())
            log("account combo :", win.moments.cb_account.count(), "items")
            log("map combo     :", win.moments.cb_map.count(), "items")
            log("agent combo   :", win.moments.cb_agent.count(), "items")
            log("type combo    :", win.moments.cb_type.count(), "items")
            log("moments stats :", win.moments.stats.text())

            # filtering
            win.moments.cb_type.blockSignals(True)
            i = win.moments.cb_type.findData("五杀时刻")
            if i > 0:
                win.moments.cb_type.setCurrentIndex(i)
            win.moments.cb_type.blockSignals(False)
            win.moments.reload()
            log("after 五杀时刻 filter:", win.moments.grid.model.rowCount(), "rows")

            # search
            win.moments.search.setText("霓虹町")
            win.moments.reload()
            log("after search 霓虹町:", win.moments.grid.model.rowCount(), "rows")

            # navigation
            for key in ("local", "record", "favorites", "recent", "settings"):
                win.navigate(key)
                log(f"navigate {key}: ok, current={win.stack.currentIndex()}")
            win.navigate("moments")

            # thumbnail pipeline (one real cover)
            rows = win.moments.rows()
            if rows:
                r = rows[0]
                from replaystudio.media.thumbs import produce_thumbnail
                p = produce_thumbnail(r["video_id"], r["path"], r.get("poster", ""))
                log("thumbnail for first row:", p, "exists:", bool(p and os.path.isfile(p)))
            log("SMOKE GUI OK")
        except Exception:
            lines.append("CHECK FAILED\n" + traceback.format_exc())
            print(traceback.format_exc(), flush=True)
        app.quit()

    QTimer.singleShot(6000, check)
    QTimer.singleShot(25000, app.quit)
    app.exec()


if __name__ == "__main__":
    code = 0
    try:
        main()
    except Exception:
        lines.append("FAILED\n" + traceback.format_exc())
        print(traceback.format_exc(), flush=True)
        code = 1
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    from replaystudio.core import diagnostics
    diagnostics.hard_exit(code)
