"""Create/destroy mpv engines in a loop and watch the event threads.

The crash reports all show the main thread inside a synchronous mpv property
call (`_set_property` / `_get_property`) right after a new engine came up,
with more than one mpv event thread alive in the same dump. This asks the
narrow question: does `terminate()` actually take the old event thread with
it, and does poking a fresh engine immediately make it fall over?

Usage: probe_mpv_lifecycle.py [cycles] [immediate|delayed|nopoke]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

TMP = tempfile.mkdtemp(prefix="rs-mpv-")
os.environ["LOCALAPPDATA"] = TMP


def make_sample(path: str):
    subprocess.run(
        [shutil.which("ffmpeg"), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=navy:size=480x360:rate=30:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", path],
        check=True, capture_output=True)


def mpv_threads() -> int:
    """Threads currently parked inside mpv's _loop."""
    n = 0
    for t in threading.enumerate():
        f = getattr(t, "__module__", "") or ""
        if t.name and "_loop" in str(getattr(t, "_target", None)):
            n += 1
    return n


def main() -> int:
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    mode = sys.argv[2] if len(sys.argv) > 2 else "immediate"

    sample = os.path.join(TMP, "clip.mp4")
    make_sample(sample)

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

    app = QApplication([])

    win = QMainWindow()
    central = QWidget()
    win.setCentralWidget(central)
    lay = QVBoxLayout(central)
    lay.setContentsMargins(0, 0, 0, 0)
    win.resize(900, 620)
    win.show()

    def pump(seconds: float):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    from replaystudio.media.player import MpvPlayer

    print(f"cycles={cycles} mode={mode}", flush=True)
    print(f"{'#':>3} {'threads':>8} {'mpv-thr':>8}  note", flush=True)

    for i in range(cycles):
        host = QWidget()
        host.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        host.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        lay.addWidget(host)
        win.show()
        pump(0.15)

        player = MpvPlayer(wid=int(host.winId()), hwdec="auto")
        note = ""
        if mode == "immediate":
            # exactly what _init_player does: touch the engine right away
            player.set_volume(60 + i)
            try:
                _ = player.tracks
            except Exception as exc:
                note += f" tracks!{type(exc).__name__}"
        player.play(sample)
        if mode == "delayed":
            pump(1.2)
            player.set_volume(60 + i)
            _ = player.tracks

        pump(1.0)
        try:
            _ = player.duration
        except Exception as exc:
            note += f" dur!{type(exc).__name__}"

        player.shutdown()
        host.setParent(None)
        host.deleteLater()
        pump(0.4)

        # how many mpv event threads are still around after a clean shutdown?
        n_mpv = sum(1 for t in threading.enumerate()
                    if "mpv" in str(getattr(t, "_target", "")).lower()
                    or "_loop" in str(getattr(t, "_target", "")))
        print(f"{i:>3} {threading.active_count():>8} {n_mpv:>8}  {note}", flush=True)

    print("done", flush=True)
    return 0


if __name__ == "__main__":
    from replaystudio.core import diagnostics

    diagnostics.install()
    code = main()
    # libmpv crashes inside DllMain(PROCESS_DETACH); skip the normal teardown.
    diagnostics.hard_exit(code)
