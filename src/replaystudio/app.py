"""ReplayStudio entry point."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .core import diagnostics
from .core.single_instance import SingleInstance
from .media.player import ensure_mpv_dll
from .ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    # First thing that runs: if the process dies later there must already be a
    # file explaining why.
    diagnostics.install()

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

    # libmpv must be reachable before anything imports the player module.
    try:
        ensure_mpv_dll()
    except Exception as exc:
        print(f"[ReplayStudio] 播放引擎不可用：{exc}", file=sys.stderr)

    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(argv or sys.argv)
    app.setApplicationName("ReplayStudio")
    app.setStyle("Fusion")
    font = QFont("Microsoft YaHei UI", 9)
    app.setFont(font)
    diagnostics.install_qt(app)

    # Command-line arguments: `run.py <file>` or Windows "open with" (%1).
    files = [os.path.abspath(a) for a in list(argv or sys.argv)[1:]
             if a and not a.startswith("-") and os.path.isfile(a)]

    # One instance only. Two copies freeze each other on the SQLite write lock
    # and race the thumbnail cache; the user experienced exactly that as
    # "卡住了" after double-clicking files a few times.
    inst = SingleInstance(app)
    if not inst.acquire(None):
        # Hand the files to the running instance (it picks the request up on
        # its next 400 ms poll and raises its window), then leave quietly. No
        # dialog here: the raised window is the feedback, and "open with"
        # launches must not block on a modal box. A stale lock from a crashed
        # holder does not reach this path: tryLock recovers those on its own.
        inst.drop_request(files)
        return 0

    win = MainWindow()
    win.show()

    inst._on_open = win.open_paths

    # Deferred one tick so the window exists (and mpv has a window handle).
    if files:
        QTimer.singleShot(350, lambda: win.open_paths(files))

    code = app.exec()
    # Do NOT return normally: libmpv's DLL detach segfaults once two engine
    # lifetimes have existed (measured; see diagnostics.hard_exit). Everything
    # worth persisting already is, so leave through the front door.
    diagnostics.hard_exit(code)


if __name__ == "__main__":
    raise SystemExit(main())
