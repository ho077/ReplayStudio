"""End-to-end playback test with a real window: open a real highlight file
through PlayerPage and confirm the clock actually advances.
"""
from __future__ import annotations

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
OUT = os.path.join(ROOT, "tools", "_playback_out.txt")

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
    win.resize(1280, 800)
    win.show()

    stage = {"n": 0}

    def tick():
        stage["n"] += 1
        n = stage["n"]
        try:
            if n == 1:
                return  # let the scan finish
            if n == 2:
                rows = [r for r in win.moments.rows()
                        if r.get("file_state") == 1 and r.get("path")]
                if not rows:
                    log("NO PLAYABLE ROW")
                    app.quit()
                    return
                row = rows[0]
                log("playing:", row.get("vname"), row.get("map_cn"), row.get("agent_cn"))
                log("path   :", row["path"])
                win.open_player(row)
                return
            pp = win.player_page
            if pp.player is None:
                log("player not ready yet")
                return
            pos = pp.player.position
            dur = pp.player.duration
            log(f"t={n-2}s pos={pos:.2f} dur={dur:.2f} hwdec={pp.player.hwdec_current()} "
                f"paused={pp.player.paused}")
            if n == 4:
                # seek to the middle and re-read
                if dur:
                    pp.seek_to(dur * 0.5)
            if n == 6:
                pp.seek_to(2.0)
                log("seek back ok:", abs(pp.player.position - 2.0) < 2.5)
            if n >= 8:
                ok = pos > 0.3
                log("PLAYBACK", "OK" if ok else "NO PROGRESS")
                pp.shutdown()
                app.quit()
        except Exception:
            lines.append(traceback.format_exc())
            print(traceback.format_exc(), flush=True)
            app.quit()

    t = QTimer()
    t.setInterval(1000)
    t.timeout.connect(tick)
    t.start()
    QTimer.singleShot(40000, app.quit)
    app.exec()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        lines.append("FAILED\n" + traceback.format_exc())
        print(traceback.format_exc(), flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
