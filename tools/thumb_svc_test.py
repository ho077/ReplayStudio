"""Isolated test of ThumbnailService signal delivery."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

OUT = os.path.join(ROOT, "tools", "_thumbsvc.txt")

import sqlite3

db = sqlite3.connect("C:/Users/Admin/AppData/Local/ReplayStudio/index.db")
db.row_factory = sqlite3.Row
rows = [dict(r) for r in db.execute(
    "select video_id, path, poster from videos where file_state=1 limit 3")]


def main():
    log = []
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from replaystudio.media.thumbs import ThumbnailService

    app = QApplication(sys.argv)
    svc = ThumbnailService()
    svc.done.connect(lambda vid, img: log.append(f"DONE {vid[:12]} null={img.isNull()}"))
    svc.failed.connect(lambda vid: log.append(f"FAIL {vid[:12]}"))
    for r in rows:
        svc.request(r["video_id"], r["path"], r["poster"], 300, 170)

    def finish():
        log.append(f"inflight={len(svc._inflight)} ready={len(svc._ready)}")
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(log))
        app.quit()

    QTimer.singleShot(5000, finish)
    app.exec()


if __name__ == "__main__":
    main()
