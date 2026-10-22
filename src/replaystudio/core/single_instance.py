"""Single-instance guard.

Three copies of ReplayStudio running at once were observed in the wild and the
user's report was "卡住了": each instance holds SQLite write locks (UI queries
sit in the busy-handler) and they race over the same thumbnail cache, so every
one of them looks frozen. A lock file makes the second copy either exit or —
better — hand its command line to the first one, which is what "open with"
double-clicks need (they spawn a fresh process per file).

The hand-off is a JSON file, not a QLocalServer pipe. The pipe version was
built first and its newConnection signal simply never fired across real
processes on Windows (measured with tools/probe_si_cross.py; in-process it
worked, which made it worse to debug). File drop + rename is dull, atomic and
works; the first instance polls for requests every 400 ms.
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QTimer

from .db import user_data_dir

REQ_PREFIX = "open_req_"
REQ_SUFFIX = ".json"


def _requests_dir() -> str:
    d = user_data_dir()
    os.makedirs(d, exist_ok=True)
    return d


class SingleInstance:
    """Takes the singleton lock; methods run in either the first or the
    second copy — the docstring of each says which."""

    def __init__(self, app: QApplication):
        self.app = app
        self._lock = None
        self._on_open = None
        self._poll: QTimer | None = None

    # ---- first instance -------------------------------------------------
    def acquire(self, on_open) -> bool:
        """Become the one true instance. False if someone else already is."""
        from PySide6.QtCore import QLockFile

        self._on_open = on_open
        lock_path = os.path.join(_requests_dir(), "app.lock")
        self._lock = QLockFile(lock_path)
        self._lock.setStaleLockTime(0)
        if not self._lock.tryLock(0):
            return False
        self._poll = QTimer(self.app)
        self._poll.timeout.connect(self._drain)
        self._poll.start(400)
        return True

    def _drain(self):
        """Handle (and delete) open-request files dropped by later launches."""
        d = _requests_dir()
        try:
            names = [n for n in os.listdir(d)
                     if n.startswith(REQ_PREFIX) and n.endswith(REQ_SUFFIX)]
        except OSError:
            return
        for name in names:
            path = os.path.join(d, name)
            done = path + ".done"
            # Claim exclusively: a rename that wins means nobody else reads it.
            try:
                os.replace(path, done)
            except OSError:
                continue          # loser of a race or already gone: skip
            try:
                with open(done, encoding="utf-8") as f:
                    payload = json.load(f)
            except (OSError, ValueError):
                payload = {}
            try:
                os.remove(done)
            except OSError:
                pass
            if payload.get("action") == "raise":
                self.raise_window()
            files = [p for p in payload.get("files", []) if os.path.isfile(p)]
            if files and self._on_open is not None:
                self._on_open(files)
                self.raise_window()

    def raise_window(self):
        w = (self.app.activeWindow()
             or (self.app.topLevelWidgets()[0] if self.app.topLevelWidgets()
                 else None))
        if w is None:
            return
        w.showNormal()
        w.raise_()
        w.activateWindow()

    # ---- second instance ------------------------------------------------
    def drop_request(self, files: list[str]) -> bool:
        """Write our request for the running instance to pick up."""
        payload = {"files": files} if files else {"action": "raise"}
        path = os.path.join(
            _requests_dir(),
            f"{REQ_PREFIX}{os.getpid()}_{os.urandom(3).hex()}{REQ_SUFFIX}")
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, path)     # atomic: never a half-written request
            return True
        except OSError:
            return False
