"""Screenshot the tab bed (single tab + multiple tabs) for the docs."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

SHOT_DIR = os.path.join(ROOT, "docs", "screenshots", "phase3")
MEDIA = os.path.join(ROOT, "tests", "_media")


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    os.makedirs(SHOT_DIR, exist_ok=True)
    app = QApplication([])
    from replaystudio.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    pump(app, 1.0)

    samples = [os.path.join(MEDIA, n) for n in
               ("container.mp4", "container.mkv", "phase2-sample.mkv")]
    samples = [p for p in samples if os.path.isfile(p)]
    if not samples:
        print("缺少样本，请先运行 tools/smoke_containers.py", flush=True)
        return 1

    # three tabs, each its own engine
    rows = []
    for p in samples:
        r = win.make_row(p)
        r["vname"] = os.path.splitext(os.path.basename(p))[0]
        rows.append(r)
        win.open_player(r, context=rows)
        pump(app, 2.2)

    win.open_player(rows[0], context=rows)
    pump(app, 1.6)
    win.grab().save(os.path.join(SHOT_DIR, "01-tabs.png"))
    print("shot 01-tabs", flush=True)

    # a parked tab: switch to the second one, capture that state too
    win.player_tabs.bar.setCurrentIndex(1)
    pump(app, 1.6)
    win.grab().save(os.path.join(SHOT_DIR, "02-tabs-second.png"))
    print("shot 02-tabs-second", flush=True)

    win.player_tabs.close_all()
    pump(app, 0.4)

    # settings page: proves the diagnostics entry point is reachable
    win.navigate("settings")
    pump(app, 0.8)
    bar = win.settings_page.scroll.verticalScrollBar()
    bar.setValue(bar.maximum())          # the log buttons live in the last card
    pump(app, 0.4)
    win.grab().save(os.path.join(SHOT_DIR, "03-settings-logs.png"))
    print("shot 03-settings-logs", flush=True)

    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
