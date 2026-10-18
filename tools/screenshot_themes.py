"""Real screenshots proving every Valorant-themed palette renders correctly."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
SHOT_DIR = os.path.join(ROOT, "docs", "screenshots", "themes")
os.makedirs(SHOT_DIR, exist_ok=True)


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from replaystudio.ui import theme as T
    from replaystudio.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(1400, 900)
    win.show()

    keys = [p.key for p in T.palette_list()]
    step = {"i": -1, "shot": False}

    def tick():
        step["i"] += 1
        i = step["i"]
        if i < len(keys) * 2:
            if i % 2 == 0:
                key = keys[i // 2]
                win.apply_theme(key)
                win.navigate("moments")
            else:
                win.grab().save(os.path.join(SHOT_DIR, f"{keys[i // 2]}.png"))
                print(f"shot {keys[i // 2]}", flush=True)
        else:
            win.apply_theme("valorant")
            win.navigate("settings")
            step["shot"] = True
        if step["shot"] and step["i"] > len(keys) * 2 + 2:
            win.grab().save(os.path.join(SHOT_DIR, "picker.png"))
            print("shot picker", flush=True)
            app.quit()

    t = QTimer()
    t.setInterval(1400)
    t.timeout.connect(tick)
    t.start()
    QTimer.singleShot(60000, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
