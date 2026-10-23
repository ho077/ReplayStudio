"""Why is quitting slow? Times each step of MainWindow.closeEvent.

Reported: "这个软件关闭时有点慢". Three things in the teardown can block:
closing every playback tab (each engine joins its event thread for up to 1.5 s),
shutting OBS down, and — the prime suspect — `thumbs.pool.waitForDone(3000)`,
which asks a pool of ffmpeg jobs to finish and waits the full three seconds
whenever any of them is still running.

Usage: probe_shutdown.py [tabs] [thumbs]

  tabs   — how many playback tabs to open before quitting (default 2)
  thumbs — how many thumbnail jobs to queue (default 0)
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

TABS = int(sys.argv[1]) if len(sys.argv) > 1 else 2
THUMBS = int(sys.argv[2]) if len(sys.argv) > 2 else 0

TMP = tempfile.mkdtemp(prefix="rs-sd-")
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

    pump(0.6)
    for _ in range(TABS):
        win.open_paths([CLIP])
        pump(0.7)

    if THUMBS:
        # Queue real ffmpeg work so the pool is busy when we quit.
        for i in range(THUMBS):
            copy = os.path.join(MEDIA, f"clip{i:03d}.mp4")
            shutil.copy(CLIP, copy)
            win.thumbs.request(f"video-{i}", copy, "", 300, 170)
        pump(0.2)
        print(f"thumbnail pool: {win.thumbs.pool.activeThreadCount()} threads busy",
              flush=True)

    steps: dict[str, float] = {}

    def wrap(obj, name, label):
        orig = getattr(obj, name)

        def timed(*a, **k):
            t0 = time.perf_counter()
            try:
                return orig(*a, **k)
            finally:
                steps[label] = (time.perf_counter() - t0) * 1000

        setattr(obj, name, timed)

    wrap(win.player_tabs, "close_all", "close_all (all play tabs)")
    wrap(win.record, "close_down", "record.close_down (OBS?)")
    wrap(win.thumbs.pool, "waitForDone", "thumbs.pool.waitForDone")

    print(f"quitting with {TABS} tab(s), {THUMBS} queued thumbnail(s)", flush=True)
    t0 = time.perf_counter()
    win.close()
    total = (time.perf_counter() - t0) * 1000
    print(f"  {'TOTAL closeEvent':<34} {total:8.1f} ms", flush=True)
    for label, ms in steps.items():
        print(f"  {label:<34} {ms:8.1f} ms", flush=True)
    accounted = sum(steps.values())
    print(f"  {'accounted for':<34} {accounted:8.1f} ms "
          f"({accounted / total * 100:.0f}% of total)", flush=True)
    return 0


if __name__ == "__main__":
    from replaystudio.core import diagnostics

    diagnostics.install()
    diagnostics.hard_exit(main())
