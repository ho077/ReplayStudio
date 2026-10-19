"""WM_CLOSE 发了，OBS 为什么不走？把发送结果和发送后的窗口状态都打出来。

输出 -> tools/_obs_exit.txt
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from replaystudio.core import obs_launcher, obs_paths  # noqa: E402

OUT = os.path.join(ROOT, "tools", "_obs_exit.txt")
log: list[str] = []

u = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
u.IsWindowVisible.argtypes = [wintypes.HWND]
u.IsIconic.argtypes = [wintypes.HWND]


def say(s: str):
    log.append(s)
    print(s, flush=True)


def title_of(hwnd: int) -> str:
    n = u.GetWindowTextLengthW(wintypes.HWND(hwnd))
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(wintypes.HWND(hwnd), buf, n + 1)
    return buf.value


def snapshot(tag: str):
    pids = obs_launcher.pids()
    say(f"  [{tag}] pids={pids}")
    for pid in pids:
        wins = obs_launcher._top_windows(pid)
        titled = [(h, t) for h, t in wins if t]
        say(f"    pid {pid}: {len(wins)} 个顶层窗口，{len(titled)} 个有标题")
        for h, t in titled[:6]:
            say(f"      hwnd={h} visible={bool(u.IsWindowVisible(wintypes.HWND(h)))} "
                f"iconic={bool(u.IsIconic(wintypes.HWND(h)))} title={t[:80]!r}")


def main() -> int:
    home = obs_paths.obs_home()
    port = int(obs_paths.read_ws_config(home).get("server_port") or 4455)

    ok, msg = obs_launcher.start(home, port)
    say(f"[{'PASS' if ok else 'FAIL'}] {msg}")
    if not ok:
        return 2

    snapshot("启动后")
    hwnd = obs_launcher.main_window()
    say(f"  选中的主窗口 hwnd={hwnd} title={title_of(hwnd)[:80]!r}")

    from ctypes import wintypes as wt
    res = wt.DWORD()
    u.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
                                      wt.UINT, wt.UINT, ctypes.POINTER(wt.DWORD)]
    u.SendMessageTimeoutW.restype = ctypes.c_size_t
    sent = u.SendMessageTimeoutW(wt.HWND(hwnd), wt.UINT(0x0010), wt.WPARAM(0),
                                 wt.LPARAM(0), wt.UINT(0x0002), wt.UINT(4000),
                                 ctypes.byref(res))
    say(f"  SendMessageTimeout 返回={sent} result={res.value} "
        f"GetLastError={ctypes.get_last_error()}")

    for i in (1, 3, 6, 10):
        time.sleep(i if i == 1 else 2)
        snapshot(f"{i}s")

    if obs_launcher.pids():
        ok2, msg2 = obs_launcher.stop(home=home)
        say(f"  兜底：{msg2}")
    say(f"  .sentinel: {os.listdir(obs_paths.sentinel_dir(home))}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
