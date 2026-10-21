"""Try to reproduce the reported crash: switch themes repeatedly over a playing video.

The user's report was "切了几遍颜色，点不动视频了，然后崩溃". This drives the
*real* MainWindow through that exact sequence — a live MpvPlayer behind a
PlayerPage with its position timer firing, then `apply_theme()` in a loop,
poking the play button the way a hand would.

Run it under a parent that watches the exit code: a native crash does not
raise, it just kills the process.

Usage: probe_theme_crash.py [rounds] [sleep_per_round]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

TMP = tempfile.mkdtemp(prefix="rs-theme-")
os.environ["LOCALAPPDATA"] = TMP
MEDIA = os.path.join(TMP, "media")
os.makedirs(MEDIA, exist_ok=True)


def make_sample(path: str):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg")
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=navy:size=640x480:rate=30:duration=30",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
         "-c:a", "aac", "-shortest", path],
        check=True, capture_output=True)


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    dwell = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

    sample = os.path.join(MEDIA, "loop.mp4")
    make_sample(sample)

    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from replaystudio.core import diagnostics
    from replaystudio.ui import theme as T
    from replaystudio.ui.main_window import MainWindow

    diagnostics.install()
    diagnostics.install_qt(app)

    win = MainWindow()
    empty = os.path.join(TMP, "empty-aclos")
    os.makedirs(empty, exist_ok=True)
    win.settings["aclos_dir"] = empty
    win.show()

    def pump(seconds: float):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    pump(0.8)

    win.open_paths([sample])
    tabs = win.player_tabs
    for _ in range(100):
        pump(0.05)
        page = tabs.current_page()
        if page is not None and page.player is not None and page.player.duration:
            break
    page = tabs.current_page()
    print(f"video open: player={page.player is not None} "
          f"dur={page.player.duration if page.player else 0}", flush=True)

    keys = list(T.PALETTES.keys())
    print(f"rounds={rounds} dwell={dwell}s themes={keys}", flush=True)

    for i in range(rounds):
        key = keys[i % len(keys)]
        t0 = time.time()
        win.apply_theme(key)
        pump(dwell)
        # a hand would also hit the play button ("点不动视频")
        p = page.player
        alive = p is not None
        dur = None
        try:
            dur = p.duration if p else None
        except Exception as exc:
            print(f"  read raised {exc!r}", flush=True)
        pos = None
        try:
            pos = p.position if p else None
        except Exception:
            pos = None
        print(f"[{i:>3}] {key:<9} apply={time.time() - t0:.2f}s "
              f"alive={alive} theme={T.current_key()} "
              f"dur={dur} pos={pos}", flush=True)
        if i % 3 == 0:
            page.toggle_play()      # pause/resume, the thing that "did not move"
            pump(0.3)

    print("survived all rounds", flush=True)
    win.close()
    pump(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
