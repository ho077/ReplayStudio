"""Patch VideoListModel.data to trace DecorationRole queries."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

OUT = os.path.join(ROOT, "tools", "_thumbdbg2.txt")
lines = []


def main():
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication

    from replaystudio.ui import grid as G

    orig_data = G.VideoListModel.data

    def traced(self, index, role=Qt.DisplayRole):
        if role == Qt.DecorationRole and index.isValid():
            row = self.rows[index.row()] if index.row() < len(self.rows) else {}
            lines.append(f"DEC row={index.row()} vid={row.get('video_id','')[:12]}")
        return orig_data(self, index, role)

    G.VideoListModel.data = traced

    orig_req = G.VideoListModel._request

    def traced_req(self, row, rowdata):
        lines.append(f"REQ row={row} vid={rowdata.get('video_id','')[:12]} "
                     f"poster_isfile={os.path.isfile(rowdata.get('poster',''))}")
        return orig_req(self, row, rowdata)

    G.VideoListModel._request = traced_req

    from replaystudio.media import thumbs as TH

    orig_done = TH.ThumbnailService._on_done
    orig_fail = TH.ThumbnailService._on_failed

    def t_done(self, video_id, image):
        lines.append(f"DONE vid={video_id[:12]} null={image.isNull()}")
        return orig_done(self, video_id, image)

    def t_fail(self, video_id):
        lines.append(f"FAIL vid={video_id[:12]}")
        return orig_fail(self, video_id)

    TH.ThumbnailService._on_done = t_done
    TH.ThumbnailService._on_failed = t_fail

    orig_task_run = TH._ThumbTask.run

    def t_run(self):
        try:
            p = TH.produce_thumbnail(self.video_id, self.path, self.poster, self.width)
            lines.append(f"TASK vid={self.video_id[:12]} produced={bool(p)} path={os.path.basename(p) if p else ''}")
        except Exception as exc:
            lines.append(f"TASK-EXC vid={self.video_id[:12]} {exc!r}")
        return orig_task_run(self)

    TH._ThumbTask.run = t_run

    app = QApplication(sys.argv)
    win = MainWindow = None
    from replaystudio.ui.main_window import MainWindow as MW
    win = MW()
    win.resize(1500, 940)
    win.show()

    def finish():
        lines.append(f"inflight={len(win.moments.grid.model.thumbs._inflight)} "
                     f"ready={len(win.moments.grid.model.thumbs._ready)}")
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines[:80]))
        app.quit()

    QTimer.singleShot(9000, finish)
    app.exec()


if __name__ == "__main__":
    main()
