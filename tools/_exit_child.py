"""Child driver for smoke_exit.py — runs the real app.main() exit path.

Opens three videos in three tabs, closes the window, and lets `app.main()`
return. Whether the process then exits with 0 or dies in libmpv's DLL detach
is exactly what the parent test measures.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtCore import QTimer  # noqa: E402


def main() -> int:
    from replaystudio import app as appmod
    from replaystudio.ui.main_window import MainWindow

    holder: dict = {}
    original_show = MainWindow.show

    def _drive():
        win = holder.get("win")
        if win is None:
            return
        samples = []
        for name in ("phase2-sample.mkv", "container.mp4", "container.mkv"):
            p = os.path.join(ROOT, "tests", "_media", name)
            if os.path.isfile(p):
                samples.append(p)
        if not samples:
            print("NO_SAMPLES", flush=True)
            win.close()
            return
        for p in (samples * 3)[:3]:
            row = win.make_row(p)
            row["vname"] = os.path.basename(p)
            win.open_player(row, [row])
        # Close two of them so engines are also terminated, not just opened.
        QTimer.singleShot(2200, lambda: win.player_tabs.close_current())
        QTimer.singleShot(3200, lambda: win.player_tabs.close_current())
        QTimer.singleShot(4200, win.close)

    def show(self):
        original_show(self)
        holder["win"] = self
        QTimer.singleShot(500, _drive)

    MainWindow.show = show
    return appmod.main([])


if __name__ == "__main__":
    raise SystemExit(main())
