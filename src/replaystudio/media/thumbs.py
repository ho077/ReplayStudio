"""Thumbnail pipeline: lazily produced, cached on disk, loaded off the UI thread.

Two sources, in priority order:

1. **ACLOS's own cover JPEG** (`video_poster`) — already on disk, zero cost.
   Roughly 70 % of the user's highlights have one.
2. **FFmpeg frame extraction** — only for videos without a usable cover. One
   frame at `-ss 3`, scaled down, written into the thumbnail cache directory.

Everything is produced at most once and then reused; the grid never decodes a
full-size frame while scrolling.
"""
from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QImage

from ..core.db import user_data_dir

_CACHE_DIR = os.path.join(user_data_dir(), "thumbs")


def cache_dir() -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return _CACHE_DIR


def _cache_path(video_id: str) -> str:
    safe = "".join(c for c in (video_id or "x") if c.isalnum() or c in "-_")[:64] or "x"
    return os.path.join(cache_dir(), f"{safe}.jpg")


def find_ffmpeg() -> str:
    """Locate ffmpeg: bundled copy first, then whatever is on PATH."""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for cand in (os.path.join(here, "vendor", "ffmpeg", "ffmpeg.exe"), "ffmpeg", "ffmpeg.exe"):
        if os.path.isabs(cand):
            if os.path.isfile(cand):
                return cand
        else:
            from shutil import which
            found = which(cand)
            if found:
                return found
    return ""


def produce_thumbnail(video_id: str, path: str, poster: str = "",
                      width: int = 360) -> str:
    """Return a JPEG path for this video, generating it if needed. Blocking."""
    out = _cache_path(video_id)
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        return out

    # 1. ACLOS's own cover — preferred, it is what the game actually rendered.
    if poster and os.path.isfile(poster):
        try:
            with open(poster, "rb") as src, open(out, "wb") as dst:
                dst.write(src.read())
            if os.path.getsize(out) > 0:
                return out
        except OSError:
            pass

    # 2. Fall back to extracting a frame.
    if not path or not os.path.isfile(path):
        return ""
    ff = find_ffmpeg()
    if not ff:
        return ""
    try:
        subprocess.run(
            [ff, "-y", "-ss", "3", "-i", path, "-frames:v", "1",
             "-vf", f"scale={width}:-2", "-q:v", "5", out],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out if os.path.isfile(out) and os.path.getsize(out) > 0 else ""


class _Signals(QObject):
    done = Signal(str, QImage)
    failed = Signal(str)


class _ThumbTask(QRunnable):
    def __init__(self, video_id: str, path: str, poster: str, width: int, height: int):
        super().__init__()
        self.video_id = video_id
        self.path = path
        self.poster = poster
        self.width = width
        self.height = height
        self.signals = _Signals()

    def run(self):
        try:
            p = produce_thumbnail(self.video_id, self.path, self.poster, self.width)
            if not p:
                self.signals.failed.emit(self.video_id)
                return
            img = QImage(p)
            if img.isNull():
                self.signals.failed.emit(self.video_id)
                return
            img = img.scaled(self.width, self.height,
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
            self.signals.done.emit(self.video_id, img)
        except Exception:
            self.signals.failed.emit(self.video_id)


class ThumbnailService(QObject):
    """Async thumbnail producer with in-flight de-duplication."""

    done = Signal(str, QImage)
    failed = Signal(str)

    def __init__(self, parent=None, max_workers: int = 4):
        super().__init__(parent)
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max_workers)
        self._inflight: set[str] = set()
        self._ready: set[str] = set()

    def is_cached(self, video_id: str) -> bool:
        return video_id in self._ready

    def request(self, video_id: str, path: str, poster: str = "",
                width: int = 360, height: int = 203):
        if not video_id or video_id in self._inflight or video_id in self._ready:
            return
        self._inflight.add(video_id)
        task = _ThumbTask(video_id, path, poster, width, height)
        task.signals.done.connect(self._on_done)
        task.signals.failed.connect(self._on_failed)
        self.pool.start(task)

    def _on_done(self, video_id: str, image: QImage):
        self._inflight.discard(video_id)
        self._ready.add(video_id)
        self.done.emit(video_id, image)

    def _on_failed(self, video_id: str):
        self._inflight.discard(video_id)
        self.failed.emit(video_id)

    def clear_ready(self):
        self._ready.clear()
