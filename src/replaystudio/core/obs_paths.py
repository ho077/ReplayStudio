"""Where OBS lives, where its config lives, and where recordings go.

Two very different setups have to work:

1. **Our portable copy** in `vendor/obs/` (the default). We own its config, so
   we may generate the websocket password ourselves and read it back later —
   no password is ever stored in *our* database.
2. **An OBS the user already installed**. We never write into its config; if it
   is already running we just connect, and the password (if any) comes from our
   own settings field that the user fills in once.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil

DEFAULT_PORT = 4455


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))


def obs_home() -> str:
    """`vendor/obs` when the portable copy is present, else ''."""
    home = os.path.join(project_root(), "vendor", "obs")
    if os.path.isfile(os.path.join(home, "bin", "64bit", "obs64.exe")):
        return home
    return ""


def obs_exe(home: str | None = None) -> str:
    home = home or obs_home()
    if home:
        return os.path.join(home, "bin", "64bit", "obs64.exe")
    return _installed_obs() or ""


def _installed_obs() -> str:
    """Best-effort: a normal OBS installation (we cannot configure its ws)."""
    cands = []
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 r"D:\Program", r"D:\Program Files"):
        cands.append(os.path.join(base, "obs-studio", "bin", "64bit", "obs64.exe"))
    w = shutil.which("obs64") or shutil.which("obs")
    if w:
        cands.append(w)
    for c in cands:
        if os.path.isfile(c):
            return c
    return ""


def config_root(home: str) -> str:
    """`--portable` keeps everything under <home>/config/obs-studio."""
    return os.path.join(home, "config", "obs-studio")


def ws_config_path(home: str) -> str:
    return os.path.join(config_root(home), "plugin_config", "obs-websocket", "config.json")


def read_ws_config(home: str) -> dict:
    path = ws_config_path(home)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def ensure_ws_config(home: str, port: int = DEFAULT_PORT) -> dict:
    """Make sure obs-websocket is enabled and we know its password.

    Writes the config only when it is missing or unusable; if OBS already
    generated one we respect it and simply read the password back.
    """
    cfg = read_ws_config(home)
    if cfg.get("server_enabled") and (cfg.get("server_password") or not cfg.get("auth_required")):
        return cfg

    cfg = {
        "alerts_enabled": False,
        "auth_required": True,
        "first_load": False,
        "server_enabled": True,
        "server_password": cfg.get("server_password") or secrets.token_urlsafe(16),
        "server_port": port,
    }
    path = ws_config_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    # Without this OBS shows the first-run auto-config wizard and sits there
    # waiting for a click — the websocket never comes up.
    global_ini = os.path.join(config_root(home), "global.ini")
    if not os.path.isfile(global_ini):
        os.makedirs(os.path.dirname(global_ini), exist_ok=True)
        with open(global_ini, "w", encoding="utf-8") as f:
            f.write("[Basic]\nAutoConfigCompleted=true\n")
    return cfg


# The game's own highlight folder, e.g. `D:\Document\Video\Valorant`. Recordings
# belong next to it: the user asked for one place for both, and C: is exactly
# the wrong answer for video (it is also the drive that keeps running out).
_GAME_DIR_HINT = "Valorant"
_RECORD_SUBDIR = "ReplayStudio"


def _cut_at(path: str, hint: str) -> str:
    """`D:\\Doc\\Vid\\Valorant\\wonderfulVideos12\\x.mp4` -> `...\\Valorant`."""
    parts = (path or "").replace("/", "\\").split("\\")
    for i, seg in enumerate(parts):
        if hint.lower() in seg.lower():
            return "\\".join(parts[:i + 1])
    return ""


def game_video_root(db=None) -> str:
    """Directory the game stores its own highlights in, or '' if we cannot tell.

    Preferred source is the index we already built: 1472 of the user's clips
    live under one Valorant folder, so asking beats guessing drive letters.
    `db` is duck-typed (anything with `.read_conn()`), which keeps this module
    free of a dependency on core.db.
    """
    from collections import Counter

    cands: list[str] = []
    if db is not None and hasattr(db, "read_conn"):
        try:
            with db.read_conn() as c:
                rows = c.execute(
                    "SELECT path FROM videos WHERE path LIKE ? LIMIT 3000",
                    (f"%{_GAME_DIR_HINT}%",)).fetchall()
            for (path,) in rows:
                cut = _cut_at(path, _GAME_DIR_HINT)
                if cut:
                    cands.append(cut)
        except Exception:
            pass
    for drive in "DEFG":
        for mid in ("Document", "Documents"):
            cands.append(f"{drive}:\\{mid}\\Video\\{_GAME_DIR_HINT}")
    for path, _n in Counter(cands).most_common():
        if path and os.path.isdir(path):
            return path
    return ""


def default_record_dir(db=None) -> str:
    """Where recordings go when the user has not chosen one.

    Deliberately not on C:. Order: next to the game's own highlights, then any
    other fixed drive, and only if there is nothing else at all do we fall back
    to the local app-data folder (which is what put video on C: before).
    """
    root = game_video_root(db)
    if root:
        return os.path.join(root, _RECORD_SUBDIR)
    for drive in "DEFG":
        if os.path.isdir(f"{drive}:\\"):
            return os.path.join(f"{drive}:\\ReplayStudio", "recordings")
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ReplayStudio", "recordings")


def legacy_default_record_dir() -> str:
    """The old default, which lived on C:. Recognised so it can be migrated."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ReplayStudio", "recordings")


def is_legacy_default(path: str) -> bool:
    """True when `path` is empty or still the old C:-based default."""
    if not (path or "").strip():
        return True
    return os.path.normcase(os.path.abspath(path)) == os.path.normcase(
        os.path.abspath(legacy_default_record_dir()))


def ensure_unattended_config(home: str) -> bool:
    """Turn off OBS's "really quit?" prompt. Returns True if the file changed.

    `ConfirmOnExit=true` makes OBS sit on a modal dialog when something asks it
    to close, which is exactly what our shutdown path does — the request then
    times out and we fall back to killing the process, which is the thing that
    leaves the stale sentinel behind in the first place. Only that one key is
    touched; every other line is written back untouched.
    """
    path = os.path.join(config_root(home), "user.ini")
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except OSError:
        return False
    if "ConfirmOnExit=false" in text:
        return False

    new, n = re.subn(r"(?m)^ConfirmOnExit=.*$", "ConfirmOnExit=false", text)
    if not n:
        new, n = re.subn(r"(?m)^\[General\][ \t]*$", "[General]\nConfirmOnExit=false",
                         text, count=1)
        if not n:
            return False
    try:
        with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(new)
    except OSError:
        return False
    return True


# --------------------------------------------------------------------- sentinel
# OBS writes a zero-byte `run_<uuid>` marker into `.sentinel/` when it starts and
# deletes it again on a clean exit. Anything left behind is read as "the last run
# died", and OBS answers by blocking startup on a modal dialog that offers safe
# mode. We used to kill OBS with TerminateProcess from the probe scripts, which
# never lets it clean up, so the dialog came back on every single launch — and
# picking safe mode there disables obs-websocket entirely ("Skipping module
# 'obs-websocket', not on safe list").
#
# `--disable-shutdown-check` does NOT suppress this check (measured: the log still
# said "Crash or unclean shutdown detected" with that flag on the command line),
# so the only reliable fix is to clear the marker ourselves before launching.
# We refuse to do that while a real crash dump is on file, so we never hide one.

# `--minimize-to-tray` is deliberately absent: OBS then has no addressable main
# window, and neither WM_CLOSE nor taskkill-without-/f can make it quit — every
# attempt times out and we end up killing it. Instead we launch normally and
# minimise the window ourselves, which keeps it reachable while staying off screen.
LAUNCH_ARGS = ["--portable", "--disable-missing-files-check"]


def sentinel_dir(home: str) -> str:
    return os.path.join(config_root(home), ".sentinel")


def crash_dir(home: str) -> str:
    return os.path.join(config_root(home), "crashes")


def has_crash_reports(home: str) -> bool:
    try:
        with os.scandir(crash_dir(home)) as it:
            return any(e.is_file() for e in it)
    except OSError:
        return False


def clear_sentinel(home: str) -> int:
    """Drop stale run markers. Returns how many were removed.

    Returns 0 without touching anything when `crashes/` holds a real report.
    """
    if has_crash_reports(home):
        return 0
    removed = 0
    try:
        with os.scandir(sentinel_dir(home)) as it:
            for e in it:
                if e.is_file() and e.name.startswith("run_"):
                    try:
                        os.remove(e.path)
                        removed += 1
                    except OSError:
                        pass
    except OSError:
        pass
    return removed
