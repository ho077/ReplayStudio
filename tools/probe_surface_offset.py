"""When is the mpv host HWND actually pinned to its Qt geometry?

tools/smoke_tabs.py asserts that the host HWND sits exactly on its parent's
client rect. After the async-write change it reports (-283,-148) for the first
tab, so the question is whether `_sync_native_surface()` runs too early, never
runs, or runs and then gets undone.

Usage: probe_surface_offset.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

TMP = tempfile.mkdtemp(prefix="rs-off-")
os.environ["LOCALAPPDATA"] = TMP
MEDIA = os.path.join(TMP, "media")
os.makedirs(MEDIA, exist_ok=True)
CLIP = os.path.join(MEDIA, "clip.mp4")

subprocess.run(
    [shutil.which("ffmpeg"), "-y", "-loglevel", "error",
     "-f", "lavfi", "-i", "color=c=navy:size=320x240:rate=15:duration=3",
     "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
     "-c:a", "aac", "-shortest", CLIP],
    check=True, capture_output=True)


def main() -> int:
    import ctypes
    from ctypes import wintypes

    from PySide6.QtWidgets import QApplication

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]

    def client_origin(hwnd: int) -> tuple[int, int]:
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt))
        return pt.x, pt.y

    app = QApplication([])
    from replaystudio.core import diagnostics
    from replaystudio.ui.main_window import MainWindow

    diagnostics.install()
    diagnostics.install_qt(app)

    win = MainWindow()
    empty = os.path.join(TMP, "empty-aclos")
    os.makedirs(empty, exist_ok=True)
    win.settings["aclos_dir"] = empty
    win.show()

    def pump(seconds: float):
        from PySide6.QtCore import QEventLoop, QTimer

        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    def offset(page) -> tuple[int, int]:
        host = page.video_host
        hx, hy = client_origin(int(host.winId()))
        px, py = client_origin(int(host.parentWidget().winId()))
        return hx - px, hy - py

    pump(0.8)
    win.open_paths([CLIP])

    for t in (0.2, 0.4, 1.0, 2.0):
        pump(t)
        page = win.player_tabs.current_page()
        if page is None:
            continue
        host = page.video_host
        print(f"t+{t:<4} visible={host.isVisible()} "
              f"player={page.player is not None} offset={offset(page)}", flush=True)

    page = win.player_tabs.current_page()
    print("\n--- calling _sync_native_surface() by hand ---", flush=True)
    page._sync_native_surface()
    pump(0.1)
    print(f"after explicit sync: offset={offset(page)}", flush=True)
    parent = page.video_host.parentWidget()
    print(f"host.isVisible()={page.video_host.isVisible()} "
          f"parent={type(parent).__name__} "
          f"native={parent.testAttribute(__import__('PySide6.QtCore', fromlist=['Qt']).Qt.WidgetAttribute.WA_NativeWindow)}",
          flush=True)
    return 0


if __name__ == "__main__":
    from replaystudio.core import diagnostics

    diagnostics.install()
    diagnostics.hard_exit(main())
