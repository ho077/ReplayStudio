"""看清楚 OBS 启动卡在哪个对话框上，并把对话框里的文字读出来。

现象：obs64.exe 起来了，弹一个标题为 "Error" 的窗口，12 秒后自己退出，
期间没有写任何新日志 —— 说明它连日志初始化都没走到。Qt 在 Windows 上把没设
标题的 QMessageBox::critical 显示成 "Error"，所以这是 OBS 自己弹的致命错误。

这个探针按不同工作目录各起一次 OBS，抓取窗口标题 + 子控件文字（错误信息就在
子控件里），把差异摆出来。

输出 -> tools/_obs_start.txt
"""
from __future__ import annotations

import ctypes
import glob
import os
import socket
import subprocess
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from replaystudio.core import obs_launcher, obs_paths  # noqa: E402

OUT = os.path.join(ROOT, "tools", "_obs_start.txt")
log: list[str] = []

user32 = ctypes.WinDLL("user32", use_last_error=True)
_ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
_CHILDPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [_ENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, _CHILDPROC, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]


def say(s: str):
    log.append(s)
    print(s, flush=True)


def _text(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def describe(pid: int) -> list[dict]:
    """Visible windows of `pid`, each with the text of its child controls."""
    got = wintypes.DWORD()
    out: list[dict] = []

    def collect(hwnd):
        if not user32.IsWindowVisible(hwnd):
            return
        children: list[str] = []

        def each(child, _lp):
            t = _text(child).strip()
            if t:
                children.append(t)
            return True

        user32.EnumChildWindows(hwnd, _CHILDPROC(each), 0)
        out.append({"title": _text(hwnd), "children": children})

    def walk(hwnd, _lp):
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(got))
        if got.value == pid:
            collect(hwnd)
        return True

    user32.EnumWindows(_ENUMPROC(walk), 0)
    return out


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def run_once(home: str, cwd: str, port: int, seconds: int = 14) -> None:
    exe = obs_paths.obs_exe(home)
    before = set(glob.glob(os.path.join(obs_paths.config_root(home), "logs", "*.txt")))
    obs_launcher.stop(home=home)
    obs_paths.clear_sentinel(home)
    say("")
    say(f"===== cwd = {cwd} =====")
    proc = subprocess.Popen([exe, "--portable"], cwd=cwd,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    say(f"pid={proc.pid}")

    for i in range(1, seconds // 2 + 1):
        time.sleep(2)
        pids = obs_launcher.pids()
        fresh = [p for p in glob.glob(os.path.join(obs_paths.config_root(home), "logs", "*.txt"))
                 if p not in before]
        wins: list[dict] = []
        for pid in pids:
            wins += describe(pid)
        say(f"[{i * 2:3d}s] pids={pids} port={port_open(port)} newlog={len(fresh)}")
        for w in wins:
            say(f"       窗口 {w['title']!r} 子控件={w['children']}")
        if port_open(port):
            say("[PASS] 端口已就绪")
            break
        if not pids and i > 1:
            say("[STOP] 进程自己退出了")
            break

    fresh = [p for p in glob.glob(os.path.join(obs_paths.config_root(home), "logs", "*.txt"))
             if p not in before]
    if fresh:
        say("--- 新日志头部 ---")
        with open(fresh[-1], "r", encoding="utf-8", errors="replace") as f:
            say("\n".join(f.read().splitlines()[:6]))
    else:
        say("--- 没有新日志 ---")

    ok, msg = obs_launcher.stop(home=home)
    say(f"收尾：{msg}")


def main() -> int:
    home = obs_paths.obs_home()
    if not home:
        say("[FAIL] 没有便携版 OBS")
        return 2
    port = int(obs_paths.read_ws_config(home).get("server_port") or 4455)
    say(f"OBS  : {obs_paths.obs_exe(home)}")
    say(f"port : {port}")

    run_once(home, home, port)                                   # vendor\obs
    run_once(home, os.path.dirname(obs_paths.obs_exe(home)), port)  # ...\bin\64bit

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
