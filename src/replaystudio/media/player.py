"""libmpv playback engine, wrapped as a Qt-friendly object.

Why mpv: it gives us hardware decoding (verified: `hwdec-current = d3d11va`),
SRT/ASS subtitle rendering, multiple audio tracks, frame-accurate seeking and a
native window handle (`--wid`) so the video is drawn *into* our Qt widget
instead of a floating child window.

The mpv event thread calls our callbacks, so every callback only emits a Qt
signal — never touches widgets directly.
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QObject, Signal

_MPV_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "vendor", "mpv")


def ensure_mpv_dll(extra_dir: str | None = None) -> str:
    """Put libmpv-2.dll on %PATH%. python-mpv resolves the library via %PATH%."""
    for d in (extra_dir, _MPV_DIR):
        if d and os.path.isfile(os.path.join(d, "libmpv-2.dll")):
            if d not in os.environ.get("PATH", ""):
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(d)
                except OSError:
                    pass
            return d
    raise RuntimeError(f"未找到 libmpv-2.dll，请确认 {_MPV_DIR} 存在")


ERROR_TEXT = {
    1: "加载失败：文件不存在、已损坏，或编码/封装格式不受支持。",
    2: "格式无法识别。",
    3: "不支持的媒体类型。",
    4: "解码失败（缺少解码器或数据损坏）。",
}


class MpvPlayer(QObject):
    """Thin, signal-emitting wrapper around one libmpv instance."""

    positionChanged = Signal(float)
    durationChanged = Signal(float)
    pausedChanged = Signal(bool)
    fileLoaded = Signal(str)
    ended = Signal()
    errorOccurred = Signal(str)
    volumeChanged = Signal(int)
    mutedChanged = Signal(bool)
    speedChanged = Signal(float)
    tracksChanged = Signal()
    hwdecChanged = Signal(str)

    def __init__(self, wid: int | None = None, hwdec: str = "auto", parent=None):
        super().__init__(parent)
        ensure_mpv_dll()
        import mpv

        opts = dict(
            hwdec=hwdec,
            keep_open="always",
            idle="yes",
            osc="no",
            input_default_bindings=False,
            input_vo_keyboard=False,
            force_window="no" if wid else "yes",
            sub_auto="all",
            audio_file_auto="all",
            ytdl="no",
            log_handler=self._log,
        )
        if wid:
            opts["wid"] = str(int(wid))
        self._mpv = mpv.MPV(**opts)
        self._wid = wid
        self._path = ""
        self._muted = False

        self._mpv.observe_property("time-pos", self._on_time_pos)
        self._mpv.observe_property("duration", self._on_duration)
        self._mpv.observe_property("pause", self._on_pause)
        self._mpv.observe_property("volume", self._on_volume)
        self._mpv.observe_property("mute", self._on_mute)
        self._mpv.observe_property("speed", self._on_speed)
        self._mpv.observe_property("path", self._on_path)
        self._mpv.observe_property("hwdec-current", self._on_hwdec)
        self._mpv.register_event_callback(self._on_event)

    # ------------------------------------------------------------- plumbing
    @staticmethod
    def _log(level, component, message):
        if level in ("error", "fatal"):
            sys.stderr.write(f"[mpv:{component}] {message}\n")

    def _on_event(self, event):
        try:
            eid = getattr(event, "id", None)
            names = getattr(event, "name", "") or ""
            if "end-file" in str(names) or eid == 7:
                data = getattr(event, "data", None)
                reason = getattr(data, "reason", None)
                err = getattr(data, "error", None)
                if err:
                    try:
                        err = int(err)
                    except (TypeError, ValueError):
                        pass
                    msg = ERROR_TEXT.get(err if isinstance(err, int) else -1,
                                         f"mpv 报告错误：{err}")
                    self.errorOccurred.emit(msg)
                else:
                    self.ended.emit()
            elif "file-loaded" in str(names) or eid == 6:
                self.fileLoaded.emit(self._path)
                self.tracksChanged.emit()
        except Exception:
            pass

    def _on_time_pos(self, _name, value):
        if value is not None:
            self.positionChanged.emit(float(value))

    def _on_duration(self, _name, value):
        if value is not None:
            self.durationChanged.emit(float(value))

    def _on_pause(self, _name, value):
        self.pausedChanged.emit(bool(value))

    def _on_volume(self, _name, value):
        if value is not None:
            self.volumeChanged.emit(int(value))

    def _on_mute(self, _name, value):
        self._muted = bool(value)
        self.mutedChanged.emit(self._muted)

    def _on_speed(self, _name, value):
        if value is not None:
            self.speedChanged.emit(float(value))

    def _on_path(self, _name, value):
        if value:
            self._path = value

    def _on_hwdec(self, _name, value):
        self.hwdecChanged.emit(str(value or "no"))

    # -------------------------------------------------------------- controls
    def play(self, path: str, start_pos: float | None = None):
        """Load and play. Optionally resume at `start_pos` seconds."""
        self._path = path
        try:
            # `start` is applied to the next loaded file — no race with loadfile.
            self._mpv.start = f"{float(start_pos)}" if start_pos else "none"
            self._mpv.play(path)
        except Exception as exc:
            self.errorOccurred.emit(f"无法播放 {os.path.basename(path)}：{exc}")

    def stop(self):
        try:
            self._mpv.command("stop")
        except Exception:
            pass

    @property
    def path(self) -> str:
        return self._path

    @property
    def duration(self) -> float:
        try:
            return float(self._mpv.duration or 0.0)
        except Exception:
            return 0.0

    @property
    def position(self) -> float:
        try:
            return float(self._mpv.time_pos or 0.0)
        except Exception:
            return 0.0

    def seek(self, seconds: float, precise: bool = True):
        try:
            d = self.duration
            if d:
                seconds = max(0.0, min(seconds, d))
            self._mpv.seek(seconds, reference="absolute",
                           precision="exact" if precise else "keyframes")
        except Exception:
            pass

    def seek_relative(self, delta: float):
        self.seek(self.position + delta)

    @property
    def paused(self) -> bool:
        try:
            return bool(self._mpv.pause)
        except Exception:
            return True

    def set_paused(self, paused: bool):
        try:
            self._mpv.pause = bool(paused)
        except Exception:
            pass

    def toggle_pause(self):
        self.set_paused(not self.paused)

    @property
    def volume(self) -> int:
        try:
            return int(self._mpv.volume or 0)
        except Exception:
            return 0

    def set_volume(self, value: int):
        try:
            self._mpv.volume = max(0, min(130, int(value)))
        except Exception:
            pass

    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool):
        try:
            self._mpv.mute = bool(muted)
        except Exception:
            pass

    @property
    def speed(self) -> float:
        try:
            return float(self._mpv.speed or 1.0)
        except Exception:
            return 1.0

    def set_speed(self, value: float):
        try:
            self._mpv.speed = float(value)
        except Exception:
            pass

    # ---------------------------------------------------------------- tracks
    @property
    def tracks(self):
        try:
            return list(self._mpv.track_list) or []
        except Exception:
            return []

    def audio_tracks(self):
        return [t for t in self.tracks if t.get("type") == "audio"]

    def subtitle_tracks(self):
        return [t for t in self.tracks if t.get("type") == "sub"]

    def select_audio(self, track_id: int):
        try:
            self._mpv.aid = int(track_id)
        except Exception:
            pass

    def select_subtitle(self, track_id):
        """track_id None/False disables subtitles."""
        try:
            if not track_id:
                self._mpv.sid = "no"
            else:
                self._mpv.sid = int(track_id)
        except Exception:
            pass

    def add_subtitle(self, path: str):
        try:
            self._mpv.command("sub-add", path, "select")
            self.tracksChanged.emit()
        except Exception as exc:
            self.errorOccurred.emit(f"字幕加载失败：{exc}")

    def set_fullscreen(self, on: bool):
        try:
            self._mpv.fullscreen = bool(on)
        except Exception:
            pass

    def screenshot(self, path: str):
        try:
            self._mpv.command("screenshot-to-file", path, "video")
            return True
        except Exception:
            return False

    def hwdec_current(self) -> str:
        try:
            return str(self._mpv.hwdec_current or "")
        except Exception:
            return ""

    def shutdown(self):
        try:
            self._mpv.terminate()
        except Exception:
            pass
