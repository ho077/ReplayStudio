"""Does `--load-scripts=no` actually stop the LuaJIT unwinding exceptions?

Every `crash-*.log` in this project starts with a run of

    Windows fatal exception: code 0xe24c4a02

raised on an mpv event thread. 0xe24c4a02 is `LJ_EXCODE | LUA_ERRRUN`: LuaJIT
unwinds its stack by *raising an SEH exception and catching it again*. On its
own that is harmless. The problem is that our faulthandler installs a vectored
exception handler that sees it first and walks every thread's stack — including
the thread whose stack pointer LuaJIT is in the middle of rewriting. Dumps from
those runs contain `<invalid frame>`, and the process has been seen to die of an
access violation right afterwards.

So the question is not "is this exception fatal" (it is not) but "can we stop
mpv from raising it at all". This probe counts them for a given option set.

Usage: probe_luajit_count.py [extra option=value ...]   (e.g. load_scripts=False)
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

TMP = tempfile.mkdtemp(prefix="rs-lj-")
os.environ["LOCALAPPDATA"] = TMP
SAMPLE = os.path.join(TMP, "clip.mp4")

subprocess.run(
    [shutil.which("ffmpeg"), "-y", "-loglevel", "error",
     "-f", "lavfi", "-i", "color=c=navy:size=320x240:rate=15:duration=3",
     "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
     "-c:a", "aac", "-shortest", SAMPLE],
    check=True, capture_output=True)

EXTRA: dict = {}
for arg in sys.argv[1:]:
    if "=" in arg:
        k, _, v = arg.partition("=")
        EXTRA[k] = {"false": False, "true": True, "no": "no", "yes": "yes"}.get(
            v.lower(), v)


def main() -> int:
    from PySide6.QtCore import QEventLoop, Qt, QTimer
    from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

    app = QApplication([])
    win = QMainWindow()
    host = QWidget()
    host.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
    host.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(host)
    win.resize(900, 620)
    win.show()

    def pump(seconds: float):
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    pump(0.4)

    from replaystudio.core import diagnostics
    from replaystudio.media.player import MpvPlayer

    diagnostics.install()
    logdir = diagnostics.log_dir()
    print(f"options: {EXTRA or '(none)'}", flush=True)

    player = MpvPlayer(wid=int(host.winId()), hwdec="auto", **EXTRA)
    player.play(SAMPLE)
    # Property work from the GUI thread, the way the UI does it.
    for _ in range(8):
        pump(0.3)
        try:
            player.set_paused(player.paused)
            player.suspend_audio()
            player.resume_audio("auto")
            _ = player.duration
        except Exception:                                  # noqa: BLE001
            pass
    player.shutdown()
    pump(0.4)

    hits = 0
    kinds: dict[str, int] = {}
    for path in logdir.glob("crash-*.log"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("Windows fatal exception"):
                hits += 1
                kinds[line.strip()] = kinds.get(line.strip(), 0) + 1
    print(f"faulthandler dumps: {hits}", flush=True)
    for k, c in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"   {c:>3}x {k}", flush=True)
    return 0


if __name__ == "__main__":
    from replaystudio.core import diagnostics

    diagnostics.install()
    diagnostics.hard_exit(main())
