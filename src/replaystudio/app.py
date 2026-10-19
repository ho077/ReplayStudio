"""ReplayStudio entry point."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .core import diagnostics
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

    win = MainWindow()
    win.show()

    # Command-line arguments: `run.py <file>` or Windows "open with" (%1).
    # Deferred one tick so the window exists (and mpv has a window handle).
    argv = list(argv or sys.argv)[1:]
    files = [os.path.abspath(a) for a in argv
             if a and not a.startswith("-") and os.path.isfile(a)]
    if files:
        QTimer.singleShot(350, lambda: win.open_paths(files))

    code = app.exec()
    # Do NOT return normally: libmpv's DLL detach segfaults once two engine
    # lifetimes have existed (measured; see diagnostics.hard_exit). Everything
    # worth persisting already is, so leave through the front door.
    diagnostics.hard_exit(code)


if __name__ == "__main__":
    raise SystemExit(main())
