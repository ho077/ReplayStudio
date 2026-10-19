"""Does the first video land off-centre? Measure it instead of eyeballing.

Grabs the real desktop region under the video host (mpv paints into a native
child window, so Qt's own grab cannot see it) and finds the bounding box of
"not black" pixels. If that box is not centred inside the host, the offset is
quantified in pixels.

Run:  .venv\\Scripts\\python.exe tools\\probe_video_center.py
"""
from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

TMP = tempfile.mkdtemp(prefix="rs-center-")
os.environ["LOCALAPPDATA"] = TMP
MEDIA = os.path.join(ROOT, "tests", "_media")
SHOT = os.path.join(ROOT, "tools", "_center")

from PIL import ImageGrab  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = os.path.join(ROOT, "tools", "_center.txt")
lines: list[str] = []


def say(s: str):
    lines.append(s)
    print(s, flush=True)


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def physical_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Screen rect of a window in physical pixels."""
    u = ctypes.WinDLL("user32", use_last_error=True)
    r = wintypes.RECT()
    u.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def analyse(img, label: str, tag: str):
    """Report the bounding box of non-black content and its centring error."""
    w, h = img.size
    px = img.convert("RGB").load()
    step = 2                      # sampling step: 2px is plenty for a bbox
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = px[x, y]
            if r > 24 or g > 24 or b > 24:
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
                if y < miny:
                    miny = y
                if y > maxy:
                    maxy = y
    if maxx < 0:
        say(f"    {label}: 全黑（未抓到画面，DWM 未合成）")
        img.save(os.path.join(SHOT, f"{tag}-allblack.png"))
        return
    bw, bh = maxx - minx + step, maxy - miny + step
    left, right = minx, w - (maxx + step)
    top, bottom = miny, h - (maxy + step)
    say(f"    {label}: host={w}x{h} 画面={bw}x{bh} "
        f"左上留白=({left},{top}) 右下留白=({right},{bottom}) "
        f"水平偏差={left - right:+d}px 垂直偏差={top - bottom:+d}px")
    img.save(os.path.join(SHOT, f"{tag}.png"))


def main() -> int:
    os.makedirs(SHOT, exist_ok=True)
    app = QApplication([])
    from replaystudio.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    pump(app, 1.0)

    picks = [os.path.join(MEDIA, n) for n in ("container.mp4", "phase2-sample.mkv")]
    picks = [p for p in picks if os.path.isfile(p)]
    if not picks:
        say("缺少样本，先跑 tools/smoke_containers.py")
        return 1

    for i, p in enumerate(picks):
        row = win.make_row(p)
        row["vname"] = f"T{i + 1}"
        win.open_player(row, [row])
        pump(app, 3.5)
        page = win.player_tabs.current_page()
        hwnd = int(page.video_host.winId())
        x0, y0, x1, y1 = physical_rect(hwnd)
        say(f"--- tab {i + 1}  {os.path.basename(p)}  "
            f"({x0},{y0})-({x1},{y1}) = {x1 - x0}x{y1 - y0}")
        img = ImageGrab.grab(bbox=(x0, y0, x1, y1), all_screens=True)
        analyse(img, f"tab{i + 1}", f"tab{i + 1}")

    # Now switch back to the first tab: does a re-shown tab land correctly?
    win.player_tabs.bar.setCurrentIndex(0)
    pump(app, 2.0)
    page = win.player_tabs.current_page()
    hwnd = int(page.video_host.winId())
    x0, y0, x1, y1 = physical_rect(hwnd)
    say(f"--- back to tab 1  ({x0},{y0})-({x1},{y1}) = {x1 - x0}x{y1 - y0}")
    img = ImageGrab.grab(bbox=(x0, y0, x1, y1), all_screens=True)
    analyse(img, "tab1-reshown", "tab1-reshown")

    win.player_tabs.close_all()
    pump(app, 0.4)
    win.close()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
