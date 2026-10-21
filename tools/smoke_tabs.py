"""Verify the tab-per-video lifecycle — the fix for two reported bugs.

Bugs under test
---------------
1. "按返回后后台还有声音" — leaving the player area used to only *hide* the
   page; mpv kept decoding. Now a background tab must be paused AND muted.
2. "程序自己退出了" — a tab closing must fully terminate its engine (mpv
   handle detached) instead of leaving a decoder attached to a dead surface.

Every check runs against a real libmpv instance with real (synthesised) media.
The user's own recordings and index are never touched: LOCALAPPDATA is
redirected to a temp dir before the app is constructed.

Run:  .venv\\Scripts\\python.exe tools\\smoke_tabs.py
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

# Redirect *all* app state (index.db, thumbs, logs) into a throwaway dir.
TMP = tempfile.mkdtemp(prefix="rs-tabs-")
os.environ["LOCALAPPDATA"] = TMP
MEDIA = os.path.join(TMP, "media")
os.makedirs(MEDIA, exist_ok=True)

# Deliberately NOT offscreen: libmpv needs a real --wid window to open a video
# output. A headless run silently fails to load anything.
from PySide6.QtWidgets import QApplication, QTabBar  # noqa: E402

REPORT = os.path.join(ROOT, "tools", "_tabs.txt")
log: list[str] = []


def win_rect(hwnd: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) of a window in physical pixels."""
    import ctypes
    from ctypes import wintypes

    r = wintypes.RECT()
    ctypes.WinDLL("user32", use_last_error=True).GetWindowRect(
        wintypes.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Client origin (screen coords) + size of a window, physical pixels."""
    import ctypes
    from ctypes import wintypes

    u = ctypes.WinDLL("user32", use_last_error=True)
    pt = wintypes.POINT(0, 0)
    u.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt))
    r = wintypes.RECT()
    u.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return pt.x, pt.y, r.right, r.bottom


def say(msg: str):
    log.append(msg)
    print(msg, flush=True)


def check(name: str, cond: bool, detail: str = "") -> bool:
    say(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(cond)


def pump(app, seconds: float):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def wait_for(app, cond, timeout: float = 6.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def make_sample(path: str, freq: int, colour: str):
    """3s H.264 + AAC clip; a distinct tone per file makes cross-talk audible."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg")
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=c={colour}:size=320x240:rate=15:duration=3",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", path],
        check=True, capture_output=True)


def main() -> int:
    v1 = os.path.join(MEDIA, "tab-a.mp4")
    v2 = os.path.join(MEDIA, "tab-b.mp4")
    make_sample(v1, 440, "navy")
    make_sample(v2, 880, "darkgreen")
    say(f"样本已生成于 {MEDIA}")

    app = QApplication([])
    from replaystudio.core import diagnostics
    from replaystudio.ui.main_window import MainWindow
    from replaystudio.ui.player_tabs import MAX_TABS

    diagnostics.install()
    diagnostics.install_qt(app)

    win = MainWindow()
    # Keep the startup bootstrap from scanning the real ACLOS library.
    empty = os.path.join(TMP, "empty-aclos")
    os.makedirs(empty, exist_ok=True)
    win.settings["aclos_dir"] = empty
    win.show()
    pump(app, 0.8)

    tabs = win.player_tabs
    results: list[bool] = []

    # ------------------------------------------------------------ 1 open one
    win.open_paths([v1])
    ok = wait_for(app, lambda: tabs.count() == 1 and tabs.current_page()
                  and tabs.current_page().player is not None, 8.0)
    page1 = tabs.current_page()
    results.append(check("打开视频后创建 1 个标签且引擎就绪", ok))
    if page1 is None or page1.player is None:
        say("引擎未就绪，后续用例跳过")
        _dump(results)
        return 1

    playing = wait_for(app, lambda: page1.player.position > 0.4, 6.0)
    results.append(check("标签内真实播放（位置前进）", playing,
                         f"pos={page1.player.position:.2f}s"))
    results.append(check("标签标题取自视频名", "tab-a" in tabs.bar.tabText(0),
                         tabs.bar.tabText(0)))

    # ------------------------------------- 1b mpv surface pinned to its host
    # Reported: the *first* video was drawn off-centre. Root cause was the mpv
    # host's native HWND sitting somewhere other than where Qt thinks the
    # widget is, because Qt created it before layout settled.
    #
    # The invariant is "the HWND sits where Qt says the widget is", and it has
    # to be measured *without* touching the parent: calling
    # `parentWidget().winId()` forces Qt to give that widget a real HWND, and
    # re-parenting the surface underneath the measurement is exactly how this
    # assertion ended up reporting a 283 px offset that nobody could see on
    # screen. `mapToGlobal` never creates a window, so it is safe to ask.
    def surface_offsets() -> list[tuple[str, int, int, int, int]]:
        out = []
        for i, pg in enumerate(tabs.pages()):
            host = pg.video_host
            if not host.isVisible():
                continue
            hx, hy, hw, hh = win_rect(int(host.winId()))
            dpr = host.devicePixelRatioF() or 1.0
            want = host.mapToGlobal(host.rect().topLeft())
            out.append((f"tab{i}",
                        hx - int(round(want.x() * dpr)),
                        hy - int(round(want.y() * dpr)),
                        int(round(host.width() * dpr)) - hw,
                        int(round(host.height() * dpr)) - hh))
        return out

    pump(app, 0.4)
    offs = surface_offsets()
    results.append(check("首个标签的画面窗口与舞台完全重合（不偏移）",
                         bool(offs) and all(dx == 0 and dy == 0
                                            for _n, dx, dy, _dw, _dh in offs),
                         f"{offs}"))
    results.append(check("画面窗口尺寸等于舞台客户区（不缩放/不裁切）",
                         bool(offs) and all(dw == 0 and dh == 0
                                            for _n, _dx, _dy, dw, dh in offs),
                         f"{offs}"))

    # Moving and resizing the window must not reintroduce the offset.
    win.move(win.x() + 60, win.y() + 40)
    win.resize(1180, 780)
    pump(app, 0.8)
    offs = surface_offsets()
    results.append(check("移动/缩放窗口后画面窗口仍重合",
                         bool(offs) and all(dx == 0 and dy == 0
                                            for _n, dx, dy, _dw, _dh in offs),
                         f"{offs}"))

    # ------------------------------------- 1c close button inside its tab
    # Reported: the ✕ badge overflowed the tab's rounded corner. We now place a
    # fixed-size QToolButton; Qt must keep it inside the tab rect.
    bad = []
    for i in range(tabs.count()):
        btn = tabs.bar.tabButton(i, QTabBar.RightSide)
        if btn is None:
            bad.append(f"tab{i}: no button")
            continue
        tr, br = tabs.bar.tabRect(i), btn.geometry()
        if not tr.contains(br):
            bad.append(f"tab{i}: tab={tr.getRect()} btn={br.getRect()}")
    results.append(check("关闭按钮完全落在标签范围内（不越界）", not bad,
                         "; ".join(bad) or "all inside"))

    # --------------------------------------------------------- 2 second tab
    engine1 = page1.player
    win.open_paths([v2])
    ok = wait_for(app, lambda: tabs.count() == 2 and tabs.current_page().player is not None, 8.0)
    page2 = tabs.current_page()
    results.append(check("打开第二个视频后有两个标签", ok))
    results.append(check("两个标签使用不同的引擎实例",
                         page2 is not None and page2.player is not engine1))
    wait_for(app, lambda: page2.player.position > 0.3, 6.0)

    # ------------------------------------------------- 3 background silenced
    results.append(check("后台标签被暂停", engine1.paused,
                         f"paused={engine1.paused}"))
    results.append(check("后台标签被静音（关键：不再出声）", engine1.muted,
                         f"muted={engine1.muted}"))
    results.append(check("前台标签仍在播放", not page2.player.paused,
                         f"paused={page2.player.paused}"))

    # ------------------------------------------------------- 4 leave player
    win.navigate("moments")
    pump(app, 0.6)
    results.append(check("离开播放区后前台标签也被静音",
                         page2.player is None or page2.player.muted,
                         f"muted={getattr(page2.player, 'muted', None)}"))
    results.append(check("离开后侧边栏出现「正在播放」入口（可回来）",
                         win.sidebar.buttons["player"].isVisible()))
    win.navigate("player")
    pump(app, 0.6)
    # Coming back should resume what the user was watching, not restart it.
    results.append(check("回到播放区后恢复播放", page2.player is not None
                         and not page2.player.paused,
                         f"paused={getattr(page2.player, 'paused', None)}"))
    results.append(check("回到播放区后取消静音", page2.player is not None
                         and not page2.player.muted))

    # ------------------------------------------------------- 5 close a tab
    engine2 = page2.player                 # keep a reference: the page drops it
    tabs.close_current()                   # same path as the 返回 button
    pump(app, 0.8)
    results.append(check("关闭标签后标签数减 1", tabs.count() == 1,
                         f"count={tabs.count()}"))
    results.append(check("被关闭标签的页面已从栈中移除", tabs.body.indexOf(page2) == -1))
    results.append(check("关闭后页面已脱离引擎", page2.player is None))
    results.append(check("关闭后 mpv 句柄已断开（引擎真正终止）",
                         getattr(engine2._mpv, "handle", "missing") is None,
                         f"handle={getattr(engine2._mpv, 'handle', 'missing')!r}"))

    # --------------------------------------------- 6 reuse existing tab
    win.open_paths([v1])
    pump(app, 0.6)
    results.append(check("再次打开同一视频复用已有标签（不新建）",
                         tabs.count() == 1, f"count={tabs.count()}"))

    # ---------------------------------------------------- 7 tab cap
    for i in range(MAX_TABS + 2):
        win.open_paths([v1 if i % 2 else v2])
        pump(app, 0.25)
    results.append(check(f"标签数量不超过上限 {MAX_TABS}",
                         tabs.count() <= MAX_TABS, f"count={tabs.count()}"))

    # ------------------------------------------------ 8 close all + teardown
    closed: list[bool] = []
    tabs.empty.connect(lambda: closed.append(True))
    tabs.close_all()
    pump(app, 0.6)
    results.append(check("全部关闭后标签清空", tabs.count() == 0))
    results.append(check("全部关闭后发出 empty 信号", bool(closed)))
    results.append(check("全部关闭后界面回到媒体库（不卡在空播放器）",
                         win.stack.currentWidget() is not tabs))

    win.close()
    pump(app, 0.3)
    say("窗口关闭未崩溃")

    _dump(results)
    return 0 if all(results) else 2


def _dump(results: list[bool]):
    total, passed = len(results), sum(1 for r in results if r)
    say(f"\n结果：{passed}/{total} 通过")
    try:
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(log) + f"\n\n{passed}/{total}\n")
    except OSError:
        pass


if __name__ == "__main__":
    _code = main()
    shutil.rmtree(TMP, ignore_errors=True)
    from replaystudio.core import diagnostics
    # Six engines were created and destroyed here; libmpv faults during DLL
    # detach, so leave the way the app does (see diagnostics.hard_exit).
    diagnostics.hard_exit(_code)
