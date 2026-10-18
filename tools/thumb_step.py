"""Pinpoint exactly where produce fails inside a worker thread."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

OUT = os.path.join(ROOT, "tools", "_thumbstep.txt")

import sqlite3

db = sqlite3.connect("C:/Users/Admin/AppData/Local/ReplayStudio/index.db")
db.row_factory = sqlite3.Row
rows = [dict(r) for r in db.execute(
    "select video_id, path, poster from videos where file_state=1 limit 3")]


def main():
    log = []
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from replaystudio.media import thumbs as TH

    orig = TH.produce_thumbnail

    def traced(video_id, path, poster="", width=360):
        try:
            log.append(f"STEP {video_id[:12]} poster_exists={os.path.isfile(poster) if poster else '-'} "
                       f"path_exists={os.path.isfile(path)}")
            out = TH._cache_path(video_id)
            log.append(f"STEP cache={os.path.basename(out)} cached={os.path.isfile(out)}")
            if os.path.isfile(out) and os.path.getsize(out) > 0:
                log.append(f"STEP returning cached")
                return out
            if poster and os.path.isfile(poster):
                with open(poster, "rb") as src, open(out, "wb") as dst:
                    dst.write(src.read())
                log.append(f"STEP copied size={os.path.getsize(out)}")
                return out
            log.append("STEP no poster -> ffmpeg")
            r = orig(video_id, path, poster, width)
            log.append(f"STEP ffmpeg result={bool(r)}")
            return r
        except Exception as exc:
            import traceback
            log.append("STEP-EXC " + traceback.format_exc())
            return ""

    TH.produce_thumbnail = traced

    app = QApplication(sys.argv)
    svc = TH.ThumbnailService()
    svc.done.connect(lambda vid, img: log.append(f"DONE {vid[:12]}"))
    svc.failed.connect(lambda vid: log.append(f"FAIL {vid[:12]}"))
    for r in rows:
        svc.request(r["video_id"], r["path"], r["poster"], 300, 170)

    def finish():
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(log))
        app.quit()

    QTimer.singleShot(5000, finish)
    app.exec()


if __name__ == "__main__":
    main()
