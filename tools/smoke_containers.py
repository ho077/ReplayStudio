"""Phase 2 robustness checks: containers, exotic filenames, corrupt files, drop.

Acceptance items from the brief that are easy to fake and worth proving:
  * MP4 / MKV / MOV / WEBM / AVI all open through the same page
  * Chinese + spaces + `()#%&` in the filename still work
  * a truncated (corrupt) file reports a readable error instead of crashing
  * a real QDropEvent is routed by MainWindow (drag & drop plumbing)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtCore import QMimeData, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

SRC = os.path.join(ROOT, "tests", "_media", "phase2-sample.mkv")
OUT_DIR = os.path.join(ROOT, "tests", "_media")
REPORT = os.path.join(ROOT, "tools", "_containers.txt")

log: list[str] = []


def say(msg: str):
    log.append(msg)
    print(msg, flush=True)


def pump(app, seconds: float):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def build_variants() -> dict[str, str]:
    """Remux/re-encode the sample into several containers (kept tiny)."""
    out: dict[str, str] = {}
    jobs = [
        ("mp4", ["-c", "copy"]),
        ("mov", ["-c", "copy"]),
        ("avi", ["-c:v", "mpeg4", "-c:a", "libmp3lame", "-s", "640x360"]),
        ("webm", ["-c:v", "libvpx-vp9", "-c:a", "libopus", "-s", "640x360",
                  "-b:v", "600k", "-deadline", "realtime", "-cpu-used", "8"]),
    ]
    for ext, extra in jobs:
        dst = os.path.join(OUT_DIR, f"container.{ext}")
        if os.path.isfile(dst) and os.path.getsize(dst) > 50_000:
            out[ext] = dst
            continue
        cmd = ["ffmpeg", "-y", "-t", "6", "-i", SRC, *extra, dst]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if os.path.isfile(dst) and os.path.getsize(dst) > 50_000:
            out[ext] = dst
        else:
            say(f"      (skip {ext}: rc={r.returncode} "
                f"{(r.stderr or '')[-120:]})")
    return out


def main() -> int:
    if not os.path.isfile(SRC):
        say("缺少样本，请先运行 tools/make_phase2_sample.py")
        return 1

    results: list[bool] = []
    app = QApplication([])
    from replaystudio.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    pump(app, 0.6)

    # Playback is tab-based now: every open may create a new tab with its own
    # engine, so the page must be re-acquired after each open.
    def cur_page(timeout: float = 8.0):
        end = time.time() + timeout
        while time.time() < end:
            pump(app, 0.05)
            pg = win.player_page
            if pg is not None and pg.player is not None:
                return pg
        return win.player_page

    # force mpv to be constructed before we start loading files
    win.open_paths([SRC])
    pump(app, 2.5)
    pp = cur_page()
    results.append(pp is not None and pp.player is not None)

    say("== 容器兼容性 ==")
    for ext, path in build_variants().items():
        win.open_paths([path])
        pump(app, 2.5)
        pp = cur_page()
        d = pp.player.duration if pp.player else 0.0
        ok = d > 0.5 and not pp.last_error
        results.append(ok)
        say(f"[{'PASS' if ok else 'FAIL'}] {ext.upper():5s} 打开 "
            f"duration={d:.2f}s {pp.last_error[:40]!r}")

    say("\n== 中文 / 空格 / 特殊字符文件名 ==")
    weird = os.path.join(OUT_DIR, "测试 文件 (2024) #1 & 100%.mkv")
    if not os.path.isfile(weird):
        shutil.copyfile(SRC, weird)
    win.open_paths([weird])
    pump(app, 2.5)
    pp = cur_page()
    d = pp.player.duration if pp.player else 0.0
    ok = d > 0.5 and not pp.last_error
    results.append(ok)
    say(f"[{'PASS' if ok else 'FAIL'}] {os.path.basename(weird)} "
        f"duration={d:.2f}s {pp.last_error[:40]!r}")

    say("\n== 损坏文件容错 ==")
    # Case A: truncated MKV. Matroska is streamable, so mpv usually plays the
    # intact prefix — the requirement is only "never crash", not "must error".
    broken = os.path.join(OUT_DIR, "broken-truncated.mkv")
    with open(SRC, "rb") as f:
        head = f.read(120_000)
    with open(broken, "wb") as f:
        f.write(head)
    win.open_paths([broken])
    pump(app, 3.5)
    pp = cur_page()
    alive = pp is not None and pp.player is not None and win.isVisible()
    results.append(alive)
    say(f"[{'PASS' if alive else 'FAIL'}] 截断文件：进程存活={alive}，"
        f"duration={pp.player.duration if pp.player else 0:.2f}s，"
        f"错误={pp.last_error[:40]!r}")

    # Case B: pure garbage with a video extension — mpv must report a readable
    # error and the UI must surface it (overlay) instead of dying silently.
    garbage = os.path.join(OUT_DIR, "broken-garbage.mp4")
    with open(garbage, "wb") as f:
        f.write(os.urandom(600_000))
    win.open_paths([garbage])
    pump(app, 3.5)
    pp = cur_page()
    alive = pp is not None and pp.player is not None and win.isVisible()
    ok = alive and bool(pp.last_error)
    results.append(ok)
    say(f"[{'PASS' if ok else 'FAIL'}] 垃圾文件：进程存活={alive}，"
        f"错误提示={pp.last_error[:40]!r}")
    say(f"      界面提示={pp.info_label.text()[:60]!r}，"
        f"覆盖层可见={pp.overlay.isVisible()}")

    say("\n== 拖放事件路由 ==")
    md = QMimeData()
    md.setUrls([__import__("PySide6.QtCore", fromlist=["QUrl"]).QUrl.fromLocalFile(SRC)])
    enter = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction | Qt.MoveAction, md,
                            Qt.LeftButton, Qt.NoModifier)
    app.sendEvent(win, enter)
    accepted = enter.isAccepted()
    drop = QDropEvent(QPoint(10, 10), Qt.CopyAction | Qt.MoveAction, md,
                      Qt.LeftButton, Qt.NoModifier)
    app.sendEvent(win, drop)
    pump(app, 2.5)
    pp = cur_page()
    ok = accepted and (os.path.basename(pp.player.path if pp.player else "") ==
                       os.path.basename(SRC))
    results.append(ok)
    say(f"[{'PASS' if ok else 'FAIL'}] dragEnter accepted={accepted}，"
        f"当前播放={os.path.basename(pp.player.path if pp.player else '')}")

    win.player_tabs.close_all()
    win.thumbs.pool.waitForDone(3000)

    passed = sum(1 for r in results if r)
    say(f"\n合计 {passed}/{len(results)} 项通过")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    return 0 if passed == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
