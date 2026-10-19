"""Measure where mpv actually draws the video inside its Qt host window.

Reported symptom: the *first* video opened appears off-centre inside the black
stage, later ones look fine. Rather than guess at pixel offsets, ask mpv:

  osd-dimensions  -> w/h (the window mpv thinks it has), aspect, margins mw/mh
  video-out-params-> actual decoded size / aspect / pixel aspect

and compare against the Qt widget geometry we handed it as `wid`. If mpv's
notion of the window differs from the host's real size, that mismatch is the
bug: mpv computes letterbox margins from a stale rectangle and the picture
lands off-centre.

Run:  .venv\\Scripts\\python.exe tools\\probe_video_rect.py [video ...]
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

MEDIA = os.path.join(ROOT, "tests", "_media")

# Redirect app state away from the user's real index.
TMP = tempfile.mkdtemp(prefix="rs-rect-")
os.environ["LOCALAPPDATA"] = TMP

from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = os.path.join(ROOT, "tools", "_rect.txt")


def child_windows(parent_hwnd: int) -> list[tuple[int, int, int, int, int]]:
    """Return (hwnd, x, y, w, h) of every direct child, in *physical* pixels.

    This is the decisive measurement: it tells us whether mpv's own child
    window covers its host, independent of what Qt or mpv *think*.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    res: list[tuple[int, int, int, int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lp):
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        res.append((int(hwnd), rect.left, rect.top,
                    rect.right - rect.left, rect.bottom - rect.top))
        return True

    user32.EnumChildWindows(wintypes.HWND(parent_hwnd), _cb, 0)
    return res


def client_origin(hwnd: int) -> tuple[int, int, int, int]:
    """Client rect origin in screen coords + client size, in physical pixels."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt))
    rect = wintypes.RECT()
    user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return pt.x, pt.y, rect.right, rect.bottom


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    app = QApplication([])
    from replaystudio.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    pump(app, 0.8)

    paths = sys.argv[1:]
    if not paths:
        paths = [os.path.join(MEDIA, n) for n in ("container.mp4", "phase2-sample.mkv")]
        paths = [p for p in paths if os.path.isfile(p)]
    # A third entry reuses the first file so we can compare a later tab.
    if paths:
        paths = (paths * 3)[:3]

    lines: list[str] = []

    def say(s: str):
        lines.append(s)
        print(s, flush=True)

    for i, p in enumerate(paths):
        row = win.make_row(p)
        row["vname"] = f"T{i + 1}-{os.path.basename(p)}"
        # Must go through the window: opening the tab alone leaves it off-stack,
        # so the page never gets a showEvent and the engine is never created.
        win.open_player(row, [row])
        pump(app, 3.0)
        page = win.player_tabs.current_page()

        host = page.video_host
        eng = page.player._mpv if page.player else None
        say(f"--- tab {i + 1}: {os.path.basename(p)}")
        if eng is None:
            say("    engine missing")
            continue
        hg = host.geometry()
        gp = host.mapToGlobal(host.rect().topLeft())
        say(f"    qt host   : size={hg.width()}x{hg.height()} "
            f"global=({gp.x()},{gp.y()}) winId={int(host.winId())}")
        try:
            d = eng.osd_dimensions
            if isinstance(d, dict):
                say(f"    mpv osd   : w={d.get('w')} h={d.get('h')} "
                    f"aspect={d.get('aspect')} mw={d.get('mw')} mh={d.get('mh')}")
            else:
                say(f"    mpv osd   : {d}")
        except Exception as exc:
            say(f"    mpv osd   : <error {exc}>")
        for attr in ("video_out_params", "video_params"):
            try:
                v = getattr(eng, attr)
                if isinstance(v, dict):
                    say(f"    {attr:16s}: w={v.get('w')} h={v.get('h')} "
                        f"ar={v.get('aspect')} par={v.get('par')} "
                        f"dwidth={v.get('dwidth')} dheight={v.get('dheight')}")
            except Exception:
                pass
        # Did mpv size its window to the host *before* Qt finished laying out?
        try:
            say(f"    aspect prop : {eng.aspect}")
        except Exception:
            pass

        # Ground truth: mpv's child HWND vs its host, both in physical pixels.
        hwnd = int(host.winId())
        cx, cy, cw, ch = client_origin(hwnd)
        say(f"    host client: origin=({cx},{cy}) size={cw}x{ch} (physical)")
        for ch_hwnd, x, y, w, h in child_windows(hwnd):
            say(f"    mpv child  : hwnd={ch_hwnd} pos=({x - cx},{y - cy}) "
                f"size={w}x{h}  -> covers {w / cw:.0%} x {h / ch:.0%} of host")

    # Resize the window on purpose: does mpv follow?
    say("--- after window resize")
    win.resize(1100, 760)
    pump(app, 1.2)
    for page in win.player_tabs.pages():
        eng = page.player._mpv if page.player else None
        if eng is None:
            continue
        hg = page.video_host.geometry()
        try:
            d = eng.osd_dimensions
            say(f"    host={hg.width()}x{hg.height()} "
                f"mpv={d.get('w')}x{d.get('h')} mw={d.get('mw')} mh={d.get('mh')}")
        except Exception:
            pass

    win.player_tabs.close_all()
    pump(app, 0.4)
    win.close()

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
