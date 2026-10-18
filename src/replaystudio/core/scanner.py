"""Background indexer for ACLOS highlight databases.

Two properties matter here:

1. **It never blocks the UI.** All work happens in a QThread; the UI only sees
   progress signals.
2. **It is incremental.** An account file is only decrypted and parsed when its
   (mtime, size) pair differs from what we indexed last time. For an untouched
   library a re-scan is therefore a few `stat` calls, not a 72 MB decrypt.

File existence is verified in a separate cheap pass: `os.stat` per indexed
path. We deliberately do *not* hash file contents — with 140 GB of video that
would turn every scan into a full disk read.
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QThread, Signal

from ..aclos import reader


class ScanWorker(QThread):
    """Scans every ACLOS account directory and refreshes the SQLite index."""

    progress = Signal(int, str)          # percent, message
    finished_ok = Signal(dict)           # summary
    failed = Signal(str)                 # fatal error text

    def __init__(self, db, db_dir: str | None = None, force: bool = False,
                 check_files: bool = True, parent=None):
        super().__init__(parent)
        self.db = db
        self.db_dir = db_dir or reader.default_db_dir()
        self.force = force
        self.check_files = check_files
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        t0 = time.time()
        try:
            accounts = reader.list_accounts(self.db_dir)
        except Exception as exc:
            self.failed.emit(f"无法访问 ACLOS 数据目录：{exc}")
            return
        if not accounts:
            self.failed.emit(
                f"在 {self.db_dir} 没有发现任何账号数据。\n"
                "请确认本机生成过无畏时刻高光，或在设置中指定自定义数据目录。")
            return

        # Snapshot of what we already indexed, taken *before* the write
        # transaction so the incremental check sees the previous state.
        cached = {a["openid"]: (a["db_mtime"], a["db_size"])
                  for a in self.db.list_accounts()}

        summary = {"accounts": 0, "matches": 0, "videos": 0, "events": 0,
                   "skipped": 0, "errors": [], "seconds": 0.0, "missing": 0}
        self.progress.emit(2, f"发现 {len(accounts)} 个账号")

        with self.db.conn() as c:
            for idx, acc in enumerate(accounts):
                if self._cancelled:
                    break
                try:
                    st = os.stat(acc.db_path)
                    mtime, size = st.st_mtime, st.st_size
                except OSError as exc:
                    summary["errors"].append(f"{acc.openid}: {exc}")
                    continue

                snap = reader.parse_snapshot_file(acc.snapshot_path, acc.openid)
                acc.nick, acc.tag = snap.nick, snap.tag
                self.db.upsert_account(acc.openid, acc.nick, acc.tag,
                                       acc.db_path, mtime, size, _conn=c)
                summary["accounts"] += 1

                if not self.force and cached.get(acc.openid) == (mtime, size):
                    summary["skipped"] += 1
                    self.progress.emit(
                        int(90 * (idx + 1) / len(accounts)),
                        f"{acc.display} 无变化，跳过解析")
                    continue

                self.progress.emit(
                    int(90 * idx / len(accounts)), f"解析 {acc.display} …")
                try:
                    matches = reader.parse_account_file(acc.db_path, acc.openid)
                except Exception as exc:
                    summary["errors"].append(f"{acc.display}: {type(exc).__name__}: {exc}")
                    continue

                for m in matches:
                    if self._cancelled:
                        break
                    self.db.upsert_match(m, _conn=c)
                    summary["matches"] += 1
                    for v in m.videos:
                        self.db.upsert_video(v, _conn=c)
                        summary["videos"] += 1
                        if v.events:
                            self.db.replace_events(v.video_id, v.events, _conn=c)
                            summary["events"] += len(v.events)

        if self._cancelled:
            self.finished_ok.emit(summary)
            return

        if self.check_files:
            self.progress.emit(92, "检查文件是否仍然存在 …")
            summary["missing"] = self.db.refresh_file_states()

        summary["seconds"] = round(time.time() - t0, 2)
        self.progress.emit(100, "完成")
        self.finished_ok.emit(summary)


class FileStateWorker(QThread):
    """Cheap existence-only refresh, run when the user just wants a status update."""
    finished_ok = Signal(int)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db

    def run(self):
        self.finished_ok.emit(self.db.refresh_file_states())
