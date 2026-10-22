"""Does fullscreen crash the process, and does the fix hold?

Reported: "全屏后好像还会闪退". The old toggle wrote mpv's own `fullscreen`
property, which in embedded mode (wid=) tells libmpv to maximise a window it
does not own — Qt owns it — and the two fight over the HWND.

Usage: probe_fullscreen.py [qt|mpv] [rounds]

  qt  — the current implementation: QMainWindow.showFullScreen()
  mpv — the old one: player.set_fullscreen(), i.e. mpv_set_property("fullscreen")
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

MODE = sys.argv[1] if len(sys.argv) > 1 else "qt"
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 8

TMP = tempfile.mkdtemp(prefix="rs-fs-")
os.environ["LOCALAPPDATA"] = TMP
MEDIA = os.path.join(TMP, "media")
os.makedirs(MEDIA, exist_ok=True)
CLIP = os.path.join(MEDIA, "clip.mp4")

subprocess.run(
    [shutil.which("ffmpeg"), "-y", "-loglevel", "error",
     "-f", "lavfi", "-i", "color=c=navy:size=320x240:rate=15:duration=4",
     "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
     "-c:a", "aac", "-shortest", CLIP],
    check=True, capture_output=True)


def main() -> int:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

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
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    pump(0.8)
    win.open_paths([CLIP])
    page = None
    for _ in range(60):
        pump(0.05)
        page = win.player_tabs.current_page()
        if page is not None and page.player:
            break
    if page is None or not page.player:
        print("FAIL: no player")
        return 1
    pump(0.6)

    def host_rect():
        """Geometry of the HWND Qt gave mpv — the one mpv must not touch."""
        import ctypes
        from ctypes import wintypes

        u = ctypes.WinDLL("user32", use_last_error=True)
        r = wintypes.RECT()
        hwnd = int(page.video_host.winId())
        u.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
        style = u.GetWindowLongPtrW(wintypes.HWND(hwnd), -16)   # GWL_STYLE
        return f"{(r.left, r.top, r.right - r.left, r.bottom - r.top)} popup={bool(style & 0x80000000)}"

    print(f"mode={MODE} rounds={ROUNDS}", flush=True)
    print(f"  host before: {host_rect()}", flush=True)
    for i in range(ROUNDS):
        on = (i % 2 == 0)
        if MODE.startswith("mpv"):
            page.player.set_fullscreen(on)
        else:
            if bool(page._fs) != on:
                page.toggle_fullscreen()
        pump(0.45)
        state = "fullscreen" if win.isFullScreen() else "normal"
        print(f"  round {i}: want={'on' if on else 'off'} -> window {state}, host {host_rect()}",
              flush=True)
        if page.player is None or not page.player._alive:
            print(f"FAIL: engine died at round {i}")
            return 1

    if MODE == "mpv-close":
        # The reported sequence: go fullscreen, then close the tab (and repaint
        # the shell on the way out). If mpv restyled our HWND, this is where it
        # bites — Qt and libmpv both think they own the same window.
        print("closing the tab while mpv still thinks it is fullscreen", flush=True)
        page.player.set_fullscreen(True)
        pump(0.4)
        win.player_tabs.close_current()
        pump(0.8)
        from replaystudio.ui import theme as T
        for key in list(T.PALETTES.keys()) * 2:
            win.apply_theme(key)
            pump(0.2)
        print("survived", flush=True)
        return 0

    if MODE != "mpv":
        # Coming back down must restore the shell.
        if page._fs:
            page.toggle_fullscreen()
            pump(0.4)
        checks = {
            "window normal": not win.isFullScreen(),
            "sidebar back": win.sidebar.isVisible(),
            "titlebar back": win.titlebar.isVisible(),
            "status back": win.status_bar.isVisible(),
            "side panel back": page.side_panel.isVisible(),
            "tab strip back": win.player_tabs.bar.isVisible(),
        }
        for name, ok in checks.items():
            print(f"  {name}: {'OK' if ok else 'FAIL'}")
        if not all(checks.values()):
            return 1

        if MODE == "qt-stress":
            # Fullscreen is a path that never ran before (the old toggle did
            # nothing), so exercise it the way a session actually would:
            # fullscreen while playing, repaint the shell over the top of it,
            # come back down, close the tab.
            from replaystudio.ui import theme as T
            for round_no in range(3):
                page.toggle_fullscreen()
                pump(0.5)
                for key in list(T.PALETTES.keys()) * 2:
                    win.apply_theme(key)
                    pump(0.2)
                page.toggle_fullscreen()
                pump(0.5)
                print(f"  stress {round_no}: ok, fullscreen={win.isFullScreen()}",
                      flush=True)

    win.player_tabs.close_current()
    pump(0.6)
    print("survived", flush=True)
    return 0


if __name__ == "__main__":
    from replaystudio.core import diagnostics

    diagnostics.install()
    diagnostics.hard_exit(main())
