"""Filesystem indexer for the generic local-video library.

Deliberately cheap: it only walks directories and records path/size/mtime.
Duration and resolution are filled in by mpv when a file is actually played, so
adding a 2 TB movie folder never triggers thousands of ffprobe calls.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, Signal

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts",
              ".m2ts", ".wmv", ".mpg", ".mpeg", ".m4v", ".rmvb"}

# Rows written between commits during one directory walk; see LocalScanWorker.
BATCH = 500


@dataclass
class LocalVideo:
    video_id: str
    path: str
    size_bytes: int = 0
    created_ms: int = 0
    vname: str = ""
    vtype: str = "本地视频"
    source: str = "local"
    match_id: str = ""
    openid: str = ""
    poster: str = ""
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    level: str = ""
    is_montage: bool = False
    events: list = field(default_factory=list)


def video_id_for(path: str) -> str:
    return "L" + hashlib.md5(path.encode("utf-8", "replace")).hexdigest()[:31]


class LocalScanWorker(QThread):
    progress = Signal(int, str)
    finished_ok = Signal(int)

    def __init__(self, db, roots: list[str], parent=None):
        super().__init__(parent)
        self.db = db
        self.roots = roots
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        count = 0
        with self.db.conn() as c:
            for root in self.roots:
                if self._cancel:
                    break
                for dirpath, dirnames, filenames in os.walk(root):
                    if self._cancel:
                        break
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                    for fn in sorted(filenames):
                        ext = os.path.splitext(fn)[1].lower()
                        if ext not in VIDEO_EXTS:
                            continue
                        full = os.path.join(dirpath, fn)
                        try:
                            st = os.stat(full)
                        except OSError:
                            continue
                        v = LocalVideo(
                            video_id=video_id_for(full),
                            path=full,
                            size_bytes=st.st_size,
                            created_ms=int(st.st_mtime * 1000),
                            vname=os.path.splitext(fn)[0],
                            vtype=("剧集" if _looks_like_episode(fn) else "本地视频"),
                        )
                        self.db.upsert_video(v, file_state=1, _conn=c)
                        count += 1
                        if count % BATCH == 0:
                            # Commit periodically. One transaction for the
                            # whole walk means SQLite cannot checkpoint the WAL
                            # until it ends, so a long scan over a big library
                            # grows the -wal file for its entire duration and
                            # nothing is durable until the last file is seen.
                            c.commit()
                            self.progress.emit(count, os.path.basename(dirpath))
                        elif count % 50 == 0:
                            self.progress.emit(count, os.path.basename(dirpath))
        self.finished_ok.emit(count)


def _looks_like_episode(fn: str) -> bool:
    import re
    return bool(re.search(r"[Ss]\d{1,2}[Ee]\d{1,3}|第\s*\d+\s*集|EP?\d{1,3}", fn))
