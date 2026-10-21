"""Is the crash mpv's LuaJIT + our faulthandler, not our code?

0xe24c4a02 is LJ_EXCODE(0xe24c4a00) + LUA_ERRRUN(2) — the exception LuaJIT
raises and catches itself to unwind its stack on x64. mpv embeds LuaJIT to run
its built-in scripts. It is not a crash on its own.

But faulthandler installs a vectored exception handler that sees it first and
runs Python-level traceback dumping in the middle of that unwind. That is the
part that could corrupt state.

Three modes isolate the two variables:
  a = default scripts + faulthandler
  b = load_scripts=False + faulthandler
  c = default scripts, no faulthandler

Usage: probe_luajit.py <a|b|c> <faulthandler_log> [seconds]
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

TMP = tempfile.mkdtemp(prefix="rs-lua-")
os.environ["LOCALAPPDATA"] = TMP


def make_sample(path: str):
    subprocess.run(
        [shutil.which("ffmpeg"), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=navy:size=480x360:rate=30:duration=20",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", path],
        check=True, capture_output=True)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "a"
    fh_log = sys.argv[2] if len(sys.argv) > 2 else ""
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 6.0

    if mode != "c" and fh_log:
        import faulthandler
        fh = open(fh_log, "w", encoding="utf-8")
        faulthandler.enable(file=fh, all_threads=True)

    sample = os.path.join(TMP, "clip.mp4")
    make_sample(sample)

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

    app = QApplication([])
    win = QMainWindow()
    central = QWidget()
    win.setCentralWidget(central)
    lay = QVBoxLayout(central)
    lay.setContentsMargins(0, 0, 0, 0)
    host = QWidget()
    host.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
    host.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
    lay.addWidget(host)
    win.resize(900, 620)
    win.show()

    def pump(sec: float):
        end = time.time() + sec
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    pump(0.3)

    from replaystudio.media.player import ensure_mpv_dll

    ensure_mpv_dll()
    import mpv

    opts = dict(
        wid=str(int(host.winId())),
        hwdec="auto",
        keep_open="always",
        osc="no",
        ytdl="no",
        input_default_bindings=False,
        input_vo_keyboard=False,
    )
    if mode == "b":
        opts["load_scripts"] = False

    msgs: list[str] = []
    opts["log_handler"] = lambda lv, comp, msg: msgs.append(f"{comp}|{msg}")

    player = mpv.MPV(**opts)
    player.play(sample)
    pump(seconds)

    # make the engine do property work, the way the UI does
    for _ in range(6):
        try:
            _ = player.duration
            _ = player.time_pos
            _ = player.track_list
            player.volume = 65
        except Exception:
            pass
        pump(0.4)

    hits: dict[str, int] = {}
    for m in msgs:
        low = m.lower()
        if "lua" in low or "script" in low:
            key = m.split("|", 1)[1][:90]
            hits[key] = hits.get(key, 0) + 1
    print(f"--- mode={mode} lua/script log lines: {len(hits)} ---", flush=True)
    for k, c in sorted(hits.items(), key=lambda kv: -kv[1])[:14]:
        print(f"   {c:>2}x {k}", flush=True)
    print(f"mode={mode} survived", flush=True)
    player.terminate()
    pump(0.2)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from replaystudio.core import diagnostics

    code = main()
    # our own teardown path; otherwise libmpv falls over in DllMain
    diagnostics.hard_exit(code)
