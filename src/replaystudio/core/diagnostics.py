"""Crash and diagnostic logging.

Why this exists
---------------
The app once vanished mid-playback with no trace: no dialog, no console output,
nothing to debug with. A native crash (access violation inside libmpv) or an
unhandled exception inside a Qt slot leaves no evidence unless we install
handlers *before* the window is built.

Three independent channels, all best-effort — a logging failure must never be
allowed to take the app down:

* ``faulthandler``  → native faults (SIGSEGV / access violation), the only way
  to see a traceback when libmpv dies.
* ``sys.excepthook`` → unhandled Python exceptions and Qt slot exceptions.
* Qt message handler → ``qWarning`` / ``qFatal`` from Qt itself, which would
  otherwise go to a console nobody reads on a windowed build.

Logs land in ``%LOCALAPPDATA%\\ReplayStudio\\logs\\``. Files older than
``KEEP`` days are pruned on startup so the directory cannot grow forever.
"""
from __future__ import annotations

import faulthandler
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

KEEP = 7                      # days of session logs to retain
_crash_file = None            # keep the handle alive: faulthandler needs it
_log_path: Path | None = None


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "ReplayStudio" / "logs"


def _prune(directory: Path):
    cutoff = time.time() - KEEP * 86400
    try:
        for p in directory.glob("*.log"):
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
    except OSError:
        pass


def install(app=None) -> Path | None:
    """Install every handler. Returns the session log path (or None on failure).

    Call as early as possible in ``main()`` — before ``QApplication`` is built,
    so import-time crashes are captured too.
    """
    global _crash_file, _log_path

    directory = log_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    _prune(directory)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    _log_path = directory / f"session-{stamp}.log"

    # ---- 1. native faults -------------------------------------------------
    try:
        _crash_file = open(directory / f"crash-{stamp}.log", "w", encoding="utf-8")
        # Deliberately NOT all_threads=True. See the long note in
        # docs/devlog/007: libmpv raises 0xe24c4a02 (LuaJIT unwinding its stack)
        # routinely and handles it itself. faulthandler's vectored exception
        # handler sees it first and walks *every* thread's stack — including
        # the one whose stack pointer LuaJIT is in the middle of rewriting.
        # Those dumps are full of "<invalid frame>" and the process was dying
        # of an access violation right after them, which is how most of this
        # project's "random" crashes actually happened.
        #
        # Dumping only the faulting thread keeps the handler useful for real
        # faults — the faulting thread is the one you want anyway — without
        # touching the others.
        faulthandler.enable(file=_crash_file, all_threads=False)
    except (OSError, RuntimeError):
        _crash_file = None

    # ---- 2. python exceptions ---------------------------------------------
    previous_hook = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            previous_hook(exc_type, exc, tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        _write("UNHANDLED EXCEPTION\n" + text)
        previous_hook(exc_type, exc, tb)

    sys.excepthook = _excepthook

    _write(f"=== ReplayStudio session start {stamp} ===")
    _write(f"python {sys.version.split()[0]}  argv={sys.argv}")
    try:
        import PySide6
        _write(f"PySide6 {PySide6.__version__}")
    except Exception:
        pass

    return _log_path


def install_qt(app=None):
    """Hook Qt's message handler. Separate from :func:`install` because
    ``QApplication`` does not exist yet when the earliest handlers go in."""
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:
        return

    def _qt_handler(mode, context, message):
        name = {
            QtMsgType.QtDebugMsg: "debug",
            QtMsgType.QtInfoMsg: "info",
            QtMsgType.QtWarningMsg: "warning",
            QtMsgType.QtCriticalMsg: "critical",
            QtMsgType.QtFatalMsg: "fatal",
        }.get(mode, "log")
        # Debug spam from Qt internals is not worth persisting.
        if name in ("debug", "info"):
            return
        where = f"{context.file}:{context.line}" if context and context.file else ""
        _write(f"QT {name.upper()} {where} {message}")
        if mode == QtMsgType.QtFatalMsg:
            _flush()

    try:
        qInstallMessageHandler(_qt_handler)
    except Exception:
        pass


def _flush():
    if _crash_file is not None:
        try:
            _crash_file.flush()
        except (OSError, ValueError):
            pass


def _write(text: str):
    """Append one timestamped block to the session log."""
    if _log_path is None:
        return
    try:
        with open(_log_path, "a", encoding="utf-8") as f:
            for line in text.rstrip("\n").splitlines():
                f.write(f"{datetime.now():%H:%M:%S.%f}"[:-3] + f"  {line}\n")
    except OSError:
        pass


def note(text: str):
    """Public helper for coarse lifecycle breadcrumbs (open/close/stop)."""
    _write(text)


def current_log() -> Path | None:
    return _log_path


def hard_exit(code: int = 0) -> None:
    """End the process without running Python's shutdown sequence.

    Why this is necessary
    --------------------
    libmpv keeps WASAPI/COM state in DLL-global storage that is released during
    process detach. Measured on this machine: once *two* engine lifetimes have
    happened (open -> play -> terminate, twice), the process segfaults inside
    that detach — after all our code has finished and after the window has
    already closed, so nothing in Python can catch it. The same run with
    ``audio=no`` never crashes, which pins it on the audio stack rather than on
    the video surface or the event threads.

    Nothing we tried from Python helps: ``quit()`` plus a wait, joining the
    event threads (python-mpv's ``terminate()`` already does), or a
    ``gc.collect()`` and a drain before exiting. All still segfault. Leaving
    through ``_exit()`` does not, and by the time we get here everything that
    matters has already been persisted:

    * diagnostics write and close the log file on every line;
    * every engine is terminated in ``MainWindow.closeEvent``;
    * the thumbnail thread pool is joined there too.

    So the only thing skipped is Python tearing down objects we no longer need.
    """
    try:
        if _log_path is not None:
            with open(_log_path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%H:%M:%S.%f}"[:-3] + "  === exit ===\n")
    except OSError:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    if os.name == "nt":
        # `os._exit` is not enough: _exit() still runs ExitProcess, which walks
        # every loaded module's DllMain(PROCESS_DETACH) — and that is precisely
        # where libmpv faults. TerminateProcess skips that walk, so the process
        # ends without touching libmpv's global teardown.
        try:
            import ctypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            k32.TerminateProcess.restype = ctypes.c_int
            k32.TerminateProcess(k32.GetCurrentProcess(), int(code) & 0xFFFFFFFF)
        except Exception:
            pass
    os._exit(int(code))
