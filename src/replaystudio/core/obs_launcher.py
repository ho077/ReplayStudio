"""Start and stop OBS, and always leave through a door OBS considers clean.

Why this module exists
----------------------
OBS marks "I am running" by dropping a zero-byte `run_<uuid>` file into
`config/obs-studio/.sentinel/` and deleting it again on exit. Kill the process
the hard way and the marker survives, so the *next* launch decides the previous
run crashed, blocks on a modal dialog and offers safe mode — and in safe mode
obs-websocket is never loaded ("Skipping module 'obs-websocket', not on safe
list"), so nothing can ever connect.

Our probe scripts used to end OBS with TerminateProcess, which is exactly the
hard way. The dialog then came back on every launch: 01:46:34 "Crash or unclean
shutdown detected", 13 seconds later "Safe mode launch selected".

So this module does two things: clear stale markers before launching (unless a
real crash dump is on file, so we never hide an actual crash), and stop OBS
through a path that lets it clean up after itself.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time

from . import obs_paths

OBS_IMAGE = "obs64.exe"


# ------------------------------------------------------------------ observation
def pids() -> list[int]:
    """PIDs of running obs64.exe processes (portable copy or installed one)."""
    try:
        done = subprocess.run(
            ["tasklist", "/fi", f"imagename eq {OBS_IMAGE}", "/nh"],
            capture_output=True, text=True, timeout=15,
            encoding="gbk", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return []
    found: list[int] = []
    for line in (done.stdout or "").splitlines():
        line = line.strip()
        if not line.lower().startswith(OBS_IMAGE.lower()):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            found.append(int(parts[1]))
    return found


def is_running() -> bool:
    return bool(pids())


# --------------------------------------------------------------- window plumbing
# Everything below is Windows-only; on other platforms OBS is simply left alone.
SW_MINIMIZE = 6
WM_CLOSE = 0x0010
SMTO_ABORTIFHUNG = 0x0002


def _user32():
    import ctypes
    from ctypes import wintypes
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.EnumWindows.argtypes = [
        ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                      wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                      ctypes.POINTER(wintypes.DWORD)]
    u.SendMessageTimeoutW.restype = ctypes.c_size_t
    return u


def _top_windows(pid: int) -> list[tuple[int, str]]:
    """[(hwnd, title)] of every top-level window owned by `pid`."""
    import ctypes
    from ctypes import wintypes
    try:
        u = _user32()
    except OSError:
        return []
    got = wintypes.DWORD()
    out: list[tuple[int, str]] = []

    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def walk(hwnd, _lp):
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(got))
        if got.value != pid:
            return True
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        out.append((int(hwnd), buf.value))
        return True

    u.EnumWindows(enum_proc(walk), 0)
    return out


def main_window() -> int:
    """HWND of OBS's main window — the one with the long title bar."""
    best, best_len = 0, 0
    for pid in pids():
        for hwnd, title in _top_windows(pid):
            if len(title) > best_len:
                best, best_len = hwnd, len(title)
    return best


def minimize() -> bool:
    """Get OBS out of the way without hiding it (hiding breaks shutdown)."""
    hwnd = main_window()
    if not hwnd:
        return False
    try:
        from ctypes import wintypes
        _user32().ShowWindow(wintypes.HWND(hwnd), SW_MINIMIZE)
        return True
    except OSError:
        return False


def ask_close() -> bool:
    """Send WM_CLOSE to the main window and report whether it was delivered."""
    import ctypes
    from ctypes import wintypes
    hwnd = main_window()
    if not hwnd:
        return False
    try:
        u = _user32()
        res = wintypes.DWORD()
        sent = u.SendMessageTimeoutW(wintypes.HWND(hwnd), wintypes.UINT(WM_CLOSE),
                                     wintypes.WPARAM(0), wintypes.LPARAM(0),
                                     wintypes.UINT(SMTO_ABORTIFHUNG),
                                     wintypes.UINT(4000), ctypes.byref(res))
        return bool(sent)
    except OSError:
        return False


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------- launching
def start(home: str | None = None, port: int = obs_paths.DEFAULT_PORT,
          wait: bool = True, timeout: float = 90.0) -> tuple[bool, str]:
    """Launch our portable OBS and wait until its websocket answers."""
    home = home or obs_paths.obs_home()
    if not home:
        return False, "没找到 OBS：既没有 vendor/obs 便携版，也没检测到已安装版本"

    exe = obs_paths.obs_exe(home)
    if not os.path.isfile(exe):
        return False, f"OBS 主程序不存在：{exe}"

    if is_running():
        return True, "OBS 已经在运行，复用它（不重复启动）"

    cfg = obs_paths.ensure_ws_config(home, port)
    obs_paths.ensure_unattended_config(home)
    cleared = obs_paths.clear_sentinel(home)
    note = f"，清理了 {cleared} 个残留运行标记" if cleared else ""

    # The working directory has to be the folder the exe sits in, NOT the
    # portable root. OBS resolves its runtime data (locale, themes, ...) relative
    # to the cwd; launch it from vendor/obs and it puts up a bare "Error" box and
    # kills itself before writing a single log line. Measured both ways.
    try:
        subprocess.Popen([exe, *obs_paths.LAUNCH_ARGS], cwd=os.path.dirname(exe),
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError as exc:
        return False, f"启动 OBS 失败：{exc}"

    if not wait:
        return True, f"已启动 OBS{note}（未等待端口）"

    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            time.sleep(0.6)        # let the main window finish painting
            minimize()
            return True, f"OBS 已就绪，端口 {port}{note}"
        if not is_running():
            return False, "OBS 启动后自行退出了（看 vendor/obs/config/obs-studio/logs/ 最新日志）"
        time.sleep(0.4)
    return False, f"等了 {timeout:.0f} 秒，端口 {port} 仍未就绪"


# ---------------------------------------------------------------------- stopping
def _taskkill(force: bool) -> bool:
    args = ["taskkill", "/im", OBS_IMAGE]
    if force:
        args.insert(1, "/f")
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=20,
                              encoding="gbk", errors="replace")
        return done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _gone(timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_running():
            return True
        time.sleep(0.3)
    return False


def stop(client=None, home: str | None = None, timeout: float = 25.0) -> tuple[bool, str]:
    """Shut OBS down, cleanest method first.

    1. Post WM_CLOSE to OBS's main window. It saves its profile and removes its
       own `.sentinel` marker on the way out, which is the whole point.
    2. `taskkill` without `/f`, the same idea through a different door.
    3. Still alive: force it, then sweep the marker by hand so the next launch
       does not get blamed for this one.

    obs-websocket has no `Exit` request in 5.7.4 (checked: 151 requests, none of
    them Exit) and OBS registers no exit hotkey either, so a window message is
    what is left.
    """
    if not is_running():
        obs_paths.clear_sentinel(home or obs_paths.obs_home())
        return True, "OBS 未在运行"

    if client is not None:
        try:
            client.auto_reconnect = False      # do not resurrect a dying OBS
            client.close()
        except Exception:
            pass
        # QWebSocket.close() only *starts* the closing handshake; without letting
        # the event loop run, the close frame never reaches OBS, and OBS waits on
        # that connection before tearing itself down. Measured: with the pump the
        # process is gone in ~3s, without it WM_CLOSE is ignored for 10s+.
        try:
            from PySide6.QtCore import QCoreApplication
            app = QCoreApplication.instance()
            if app is not None:
                end = time.time() + 1.0
                while time.time() < end:
                    app.processEvents()
                    time.sleep(0.02)
        except Exception:
            pass

    sent = ask_close()
    if sent and _gone(min(10.0, timeout)):
        obs_paths.clear_sentinel(home or obs_paths.obs_home())
        return True, "OBS 已正常退出（WM_CLOSE）"

    if _taskkill(force=False) and _gone(min(8.0, timeout)):
        obs_paths.clear_sentinel(home or obs_paths.obs_home())
        return True, f"OBS 已关闭（taskkill，WM_CLOSE sent={sent}）"

    _taskkill(force=True)
    if _gone(min(7.0, timeout)):
        removed = obs_paths.clear_sentinel(home or obs_paths.obs_home())
        tail = f"，并清掉 {removed} 个残留标记" if removed else ""
        return True, f"OBS 已强制结束（WM_CLOSE sent={sent}）{tail}"

    return False, "OBS 没能退出，可能还有一个窗口在等你点确认"
