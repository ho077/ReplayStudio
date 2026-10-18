"""Capture real screenshots of the running UI so the look can be reviewed."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
SHOT_DIR = os.path.join(ROOT, "docs", "screenshots")
os.makedirs(SHOT_DIR, exist_ok=True)


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from replaystudio.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(1500, 940)
    win.show()

    step = {"n": 0}

    def tick():
        step["n"] += 1
        n = step["n"]
        if n == 14:
            win.grab().save(os.path.join(SHOT_DIR, "01-moments.png"))
            print("shot 01", flush=True)
        elif n == 18:
            rows = [r for r in win.moments.rows()
                    if r.get("file_state") == 1 and r.get("path")]
            if rows:
                win.open_player(rows[0])
        elif n == 24:
            win.grab().save(os.path.join(SHOT_DIR, "02-player.png"))
            print("shot 02", flush=True)
        elif n == 26:
            win.player_page.shutdown()
            win.navigate("settings")
        elif n == 28:
            win.grab().save(os.path.join(SHOT_DIR, "03-settings.png"))
            print("shot 03", flush=True)
            app.quit()

    t = QTimer()
    t.setInterval(800)
    t.timeout.connect(tick)
    t.start()
    QTimer.singleShot(45000, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
