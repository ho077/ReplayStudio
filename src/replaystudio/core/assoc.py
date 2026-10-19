"""Windows file-type association helper (Phase 2).

Scope deliberately narrow:

* Only writes to ``HKEY_CURRENT_USER\\Software\\Classes`` — no administrator
  rights needed, and it never touches the machine-wide (HKLM) hive.
* Registers ReplayStudio as an *OpenWith* candidate. It does **not** steal the
  default program from the player the user already likes, so the change is
  visible in "打开方式" but non-destructive.
* ``unregister()`` removes exactly the keys we created.

Only ``Applications\\<name>`` under HKCU is used for the command, plus
``OpenWithProgids`` entries on each extension, which is the documented way for
a per-user app to appear in the Open With list.
"""
from __future__ import annotations

import os
import sys

APP_NAME = "ReplayStudio.exe"
PROG_ID = "ReplayStudio.Video"
EXTS = [".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv",
        ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts"]

try:  # pragma: no cover - non-Windows import guard
    import winreg
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]


def project_root() -> str:
    d = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(d)))


def launch_command() -> str:
    """Command line Windows must run to open a file with us (`%1` = file)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    exe = sys.executable
    base, ext = os.path.splitext(exe)
    pythonw = base + "w" + ext
    if os.path.isfile(pythonw):      # no console window when launched by shell
        exe = pythonw
    script = os.path.join(project_root(), "run.py")
    return f'"{exe}" "{script}" "%1"'


def is_registered() -> bool:
    if not winreg:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            rf"Software\Classes\Applications\{APP_NAME}\shell\open\command"):
            return True
    except OSError:
        return False


def register(exts: list[str] | None = None) -> tuple[bool, str]:
    """Add ReplayStudio to the per-user Open With list for `exts`."""
    if not winreg:
        return False, "仅支持 Windows 平台"
    cmd = launch_command()
    exts = exts or EXTS
    try:
        root = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                rf"Software\Classes\Applications\{APP_NAME}")
        winreg.SetValueEx(root, "FriendlyAppName", 0, winreg.REG_SZ, "ReplayStudio")
        winreg.CloseKey(root)

        cmd_key = winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Classes\Applications\{APP_NAME}\shell\open\command")
        winreg.SetValue(cmd_key, "", winreg.REG_SZ, cmd)
        winreg.CloseKey(cmd_key)

        # SupportedTypes makes Windows show the app for these extensions.
        st = winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Classes\Applications\{APP_NAME}\SupportedTypes")
        for e in exts:
            winreg.SetValueEx(st, e, 0, winreg.REG_SZ, "")
        winreg.CloseKey(st)

        # A ProgId gives the shell a stable identity for the Open With list.
        prog = winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Classes\{PROG_ID}\shell\open\command")
        winreg.SetValue(prog, "", winreg.REG_SZ, cmd)
        winreg.CloseKey(prog)
        winreg.SetValue(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}",
                        winreg.REG_SZ, "ReplayStudio 视频")

        for e in exts:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                   rf"Software\Classes\{e}\OpenWithProgids")
            winreg.SetValueEx(key, PROG_ID, 0, winreg.REG_NONE, b"")
            winreg.CloseKey(key)
    except OSError as exc:
        return False, f"写入注册表失败：{exc}"
    return True, f"已为 {len(exts)} 种视频格式添加「打开方式」入口"


def unregister() -> tuple[bool, str]:
    """Undo exactly what `register()` wrote (best effort, ignores misses)."""
    if not winreg:
        return False, "仅支持 Windows 平台"

    def delete(path: str):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            pass

    delete(rf"Software\Classes\Applications\{APP_NAME}\shell\open\command")
    delete(rf"Software\Classes\Applications\{APP_NAME}\shell")
    delete(rf"Software\Classes\Applications\{APP_NAME}\SupportedTypes")
    delete(rf"Software\Classes\Applications\{APP_NAME}")
    delete(rf"Software\Classes\{PROG_ID}\shell\open\command")
    delete(rf"Software\Classes\{PROG_ID}\shell")
    delete(rf"Software\Classes\{PROG_ID}")

    for e in EXTS:
        try:  # remove only our value, keep other apps' entries
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               rf"Software\Classes\{e}\OpenWithProgids",
                               0, winreg.KEY_SET_VALUE)
            try:
                winreg.DeleteValue(k, PROG_ID)
            except OSError:
                pass
            winreg.CloseKey(k)
        except OSError:
            pass
    return True, "已移除「打开方式」关联（未改动系统默认播放器）"
