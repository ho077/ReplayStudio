"""Does a theme switch rebuild the HWND that mpv renders into?

A crash would prove it too, but it is slow and unrepeatable. `winId()`
is the cheap canary: if polishing the native mpv host hands it a new
HWND, every frame mpv draws afterwards lands on a discarded window.

Usage: probe_theme_hwnd.py <video> [rounds]
"""

import ctypes
import os
import sys
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def rect_of(hwnd):
    r = wintypes.RECT()
    ctypes.WinDLL("user32", use_last_error=True).GetWindowRect(
        wintypes.HWND(hwnd), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def main():
    video = sys.argv[1] if len(sys.argv) > 1 else ""
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    if not video or not os.path.exists(video):
        print("usage: probe_theme_hwnd.py <video> [rounds]")
        return 2

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

    from replaystudio.ui import theme as T

    app = QApplication.instance() or QApplication([])
    T.init_theme(app, T.DEFAULT_KEY)

    win = QMainWindow()
    central = QWidget()
    win.setCentralWidget(central)
    lay = QVBoxLayout(central)
    lay.setContentsMargins(0, 0, 0, 0)

    host = QWidget()
    # exactly how player_page.py builds the mpv host
    T.style(host, "background: #000; border-radius: ${RADIUS}px;")
    host.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
    host.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
    lay.addWidget(host)
    win.resize(960, 640)
    win.show()
    app.processEvents()

    from replaystudio.media.player import MpvPlayer

    player = MpvPlayer(wid=int(host.winId()), hwdec="auto")
    player.play(video)

    for _ in range(40):                      # wait for the first frame
        app.processEvents()
        if player.duration and player.duration > 0:
            break
        import time
        time.sleep(0.1)

    keys = list(T.PALETTES.keys())
    print(f"themes: {keys}")
    print(f"rounds: {rounds}")
    print()

    before = int(host.winId())
    print(f"{'#':>3}  {'theme':<12} {'hwnd':<12} {'changed':<8} window rect")

    for i in range(rounds):
        key = keys[i % len(keys)]
        T.apply_theme(app, key)
        for _ in range(6):
            app.processEvents()
        now = int(host.winId())
        rect = rect_of(now)
        dur = player.duration
        print(f"{i:>3}  {key:<12} {now:#x}{'':<{max(0, 10 - len(hex(now)))}}"
              f" {str(now != before):<8} {rect}  dur={dur}")
        if now != before:
            print(f"     !! HWND changed {before:#x} -> {now:#x}"
                  f"  (mpv still draws to {before:#x})")
            before = now

    # and one more read of mpv after all that polishing
    try:
        print(f"\nmpv still answering: duration={player.duration}")
    except Exception as exc:
        print(f"\nmpv read failed: {exc}")

    player.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
