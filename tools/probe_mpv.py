"""Phase 0 high-risk validation #2: real video playback with libmpv embedded in Qt.

Checks:
  1. libmpv-2.dll loads from vendor/mpv
  2. a Qt window can host mpv via --wid
  3. a real 1080p60 MP4 from the user's Valorant library decodes and advances
  4. seeking works (time actually moves to the requested position)
  5. hardware decode status is reported

Closes itself after ~8 s. Result -> tools/_mpv_out.txt
"""
import glob
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DLL_DIR = os.path.join(ROOT, "vendor", "mpv")
OUT = os.path.join(ROOT, "tools", "_mpv_out.txt")

lines = []


def log(*a):
    s = " ".join(str(x) for x in a)
    lines.append(s)
    print(s, flush=True)


def pick_video():
    roots = [
        r"D:\Document\Video\Valorant\wonderfulVideos8649867190760599601",
        r"D:\Document\Video\Valorant\wonderfulVideos3760884353974449366",
        r"D:\Document\Video\Valorant\wonderfulVideos17324315400230230370",
    ]
    best = None
    for r in roots:
        if not os.path.isdir(r):
            continue
        for p in glob.glob(os.path.join(r, "*", "*.mp4")):
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            if sz < 20 * 1024 * 1024:
                continue
            if best is None or sz < best[1]:
                best = (p, sz)
        if best:
            break
    return best


def main():
    log("python:", sys.version.split()[0])
    log("dll dir:", DLL_DIR, "exists:", os.path.isdir(DLL_DIR))
    log("libmpv-2.dll:", os.path.isfile(os.path.join(DLL_DIR, "libmpv-2.dll")))

    # python-mpv resolves the library through %PATH%, not add_dll_directory.
    os.add_dll_directory(DLL_DIR)
    os.environ["PATH"] = DLL_DIR + os.pathsep + os.environ.get("PATH", "")
    try:
        import mpv
    except Exception as exc:
        log("IMPORT MPV FAILED:", type(exc).__name__, exc)
        raise

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMainWindow

    vid = pick_video()
    if not vid:
        log("NO VIDEO FOUND")
        return
    path, size = vid
    log("video:", path)
    log("size :", size / 1024 / 1024, "MB")

    app = QApplication(sys.argv)
    win = QMainWindow()
    win.setWindowTitle("ReplayStudio mpv probe")
    win.resize(960, 540)
    win.show()
    app.processEvents()
    wid = int(win.winId())
    log("widget winId:", wid)

    player = mpv.MPV(
        wid=str(wid),
        hwdec="auto",
        vo="gpu",
        keep_open="always",
        idle="yes",
        osc="no",
        input_default_bindings=False,
        log_handler=lambda lvl, comp, msg: None,
    )
    log("mpv version:", player.mpv_version)

    player.play(path)
    log("command dispatched: loadfile")

    start = time.time()
    duration = None
    while time.time() - start < 6.0:
        app.processEvents()
        duration = player.duration
        if duration:
            break
        time.sleep(0.05)
    log("duration:", duration)

    time.sleep(1.0)
    app.processEvents()
    t1 = player.time_pos
    log("time_pos @1s:", t1)
    time.sleep(1.5)
    app.processEvents()
    t2 = player.time_pos
    log("time_pos @2.5s:", t2)
    log("playback advancing:", bool(t1 and t2 and t2 > t1))

    # seek test -> 60% of duration
    if duration:
        target = duration * 0.6
        player.seek(target, reference="absolute")
        log("seek target:", target)
        time.sleep(1.2)
        app.processEvents()
        t3 = player.time_pos
        log("time_pos after seek:", t3)
        log("seek ok:", bool(t3 and abs(t3 - target) < 3.0))

    for prop in ("video-params/w", "video-params/h", "hwdec-current",
                 "container-fps", "video-codec", "audio-codec", "track-list/count"):
        try:
            log(f"  {prop} =", player.__getattr__(prop))
        except Exception as exc:
            log(f"  {prop} -> ERR {type(exc).__name__}: {exc}")

    # fps sample: count rendered frames is not exposed; use avsync + time-pos growth
    log("paused:", player.pause)
    player.terminate()
    win.close()
    log("DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa
        import traceback
        lines.append("FAILED: " + traceback.format_exc())
        print("FAILED", exc, flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
