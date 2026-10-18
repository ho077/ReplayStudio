"""Debug why thumbnails never appear inside the GUI."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

OUT = os.path.join(ROOT, "tools", "_thumbdbg.txt")
log_lines = []


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from replaystudio.ui.main_window import MainWindow

    def tick_out():
        m = win.moments
        model = m.grid.model
        rows = model.rows[:3]
        for r in rows:
            log_lines.append(f"row: vid={r.get('video_id','')[:12]} poster={r.get('poster','')!r} "
                             f"state={r.get('file_state')} path_exists={os.path.isfile(r.get('path',''))}")
        log_lines.append(f"pixmaps cached: {len(model._pixmaps)}")
        ts = model.thumbs
        log_lines.append(f"service inflight: {len(ts._inflight)} ready: {len(ts._ready)}")
        log_lines.append(f"cache files on disk: {len(os.listdir(ts.cache_dir() if hasattr(ts,'cache_dir') else os.path.join(os.environ['LOCALAPPDATA'],'ReplayStudio','thumbs')))}")

    def finish():
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        app.quit()

    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(1500, 940)
    win.show()

    t = QTimer()
    t.setInterval(2000)
    t.timeout.connect(tick_out)
    t.start()
    QTimer.singleShot(12000, finish)
    app.exec()


if __name__ == "__main__":
    main()
