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


# mpv's MPV_ERROR_* codes are negative; we key them by absolute value.
ERROR_TEXT = {
    12: "播放命令执行失败。",
    13: "加载失败：文件不存在、已损坏，或编码/封装格式不受支持。",
    14: "无法初始化音频输出设备（检查默认播放设备是否被占用）。",
    15: "无法初始化视频输出（尝试在设置中切换硬件解码方式）。",
    16: "没有可播放的内容。",
    17: "无法识别的格式：文件不是视频，或文件头已损坏。",
    18: "不支持的媒体类型或编码。",
    19: "该功能未实现（通常是缺少对应解码器）。",
    20: "未知错误，请查看日志。",
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
    aspectDetected = Signal(float)
    osdText = Signal(str)

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
        # Guards every callback the engine thread can invoke. Once we start
        # tearing the instance down, late events must become no-ops: a
        # RuntimeError ("Signal source has been deleted") raised on mpv's event
        # thread is *not* caught by Qt and has been seen to kill the process.
        self._alive = True
        # EOF can arrive through two independent channels (the `end-file`
        # event and the `eof-reached` property observer). Without a latch both
        # would fire `ended` for the same clip and auto-play would skip one
        # video. The latch is cleared every time a new file is loaded.
        self._ended_fired = False

        self._mpv.observe_property("time-pos", self._guard(self._on_time_pos))
        self._mpv.observe_property("duration", self._guard(self._on_duration))
        self._mpv.observe_property("pause", self._guard(self._on_pause))
        self._mpv.observe_property("volume", self._guard(self._on_volume))
        self._mpv.observe_property("mute", self._guard(self._on_mute))
        self._mpv.observe_property("speed", self._guard(self._on_speed))
        self._mpv.observe_property("path", self._guard(self._on_path))
        self._mpv.observe_property("hwdec-current", self._guard(self._on_hwdec))
        self._mpv.observe_property("track-list", self._guard(self._on_track_list))
        # `keep_open=always` pauses on the last frame and never sends end-file,
        # so EOF is detected through this property instead (autoplay depends on it).
        self._mpv.observe_property("eof-reached", self._guard(self._on_eof))
        self._mpv.observe_property("video-params/aspect",
                                   self._guard(self._on_aspect_prop))
        self._mpv.register_event_callback(self._guard(self._on_event))

    # ------------------------------------------------------------- plumbing
    def _guard(self, fn):
        """Make one engine callback safe to call after shutdown.

        mpv's event thread keeps delivering property changes while we tear the
        instance down. Emitting a Qt signal from a wrapper that is already gone
        raises RuntimeError *outside* any Qt handler, which Python reports as an
        unhandled exception — and in this process that meant a silent exit.
        """
        def wrapper(*args, **kwargs):
            if not self._alive:
                return
            try:
                fn(*args, **kwargs)
            except RuntimeError:
                self._alive = False
            except Exception:
                pass
        wrapper.__name__ = getattr(fn, "__name__", "callback")
        return wrapper
    @staticmethod
    def _log(level, component, message):
        if level in ("error", "fatal"):
            sys.stderr.write(f"[mpv:{component}] {message}\n")

    # mpv event ids we care about (mpv_event_id enum)
    _EID_START_FILE = 6
    _EID_END_FILE = 7
    _EID_FILE_LOADED = 8

    def _event_id(self, event) -> int:
        """python-mpv hands us a ctypes `MpvEvent`, whose id field is
        `event_id` (a c_int) — plain `event.id` silently returns None, which is
        exactly how the whole handler used to be dead code."""
        eid = getattr(event, "event_id", None)
        if eid is not None:
            try:
                return int(eid.value)
            except AttributeError:
                return int(eid)
        return int(getattr(event, "id", -1) or -1)

    def _on_event(self, event):
        try:
            eid = self._event_id(event)
            if eid == self._EID_END_FILE:
                data = getattr(event, "data", None)
                reason = int(getattr(data, "reason", 0) or 0)
                err = getattr(data, "error", 0) or 0
                try:
                    err = int(err)
                except (TypeError, ValueError):
                    err = 0
                # reason 4 == MPV_END_FILE_REASON_ERROR; mpv codes are negative
                if reason == 4 or err < 0:
                    code = abs(err)
                    msg = ERROR_TEXT.get(code, f"mpv 报告错误（代码 {err}）")
                    self.errorOccurred.emit(msg)
                elif reason in (0, 1):     # eof / restarted
                    self._emit_ended()
            elif eid == self._EID_FILE_LOADED:
                self._ended_fired = False
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

    def _on_track_list(self, _name, _value):
        self.tracksChanged.emit()

    def _on_eof(self, _name, value):
        if value:
            self._emit_ended()

    def _emit_ended(self):
        """Fire `ended` at most once per loaded file (see `_ended_fired`)."""
        if self._ended_fired:
            return
        self._ended_fired = True
        self.ended.emit()

    def _on_aspect_prop(self, _name, value):
        if value:
            try:
                self.aspectDetected.emit(float(value))
            except (TypeError, ValueError):
                pass

    # -------------------------------------------------------------- controls
    def play(self, path: str, start_pos: float | None = None):
        """Load and play. Optionally resume at `start_pos` seconds."""
        self._path = path
        self._ended_fired = False
        try:
            # `start` is applied to the next loaded file — no race with loadfile.
            self._mpv.start = f"{float(start_pos)}" if start_pos else "none"
            self._mpv.play(path)
            # With `keep_open=always`, mpv sets `pause=yes` when a clip reaches
            # its end and KEEPS that state for the next loadfile. Without this
            # reset, auto-play would open the next video frozen on its first
            # frame and never advance — the user sees "自动连播没反应".
            self._mpv.pause = False
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

    def cycle_subtitle(self, forward: bool = True):
        """Step through subtitle tracks (… → off → track1 → track2 → …)."""
        subs = self.subtitle_tracks()
        if not subs:
            self.osdText.emit("无字幕轨道")
            return
        cur = self.current_subtitle()
        ids = [t["id"] for t in subs]
        try:
            i = ids.index(cur)
        except ValueError:
            i = -1 if cur else len(ids) - 1
        nxt = i + (1 if forward else -1)
        if nxt >= len(ids):
            self.select_subtitle(None)
            self.osdText.emit("字幕：关闭")
        elif nxt < -1:
            self.select_subtitle(ids[-1])
        else:
            tid = ids[nxt]
            self.select_subtitle(tid)
            self.osdText.emit(f"字幕：{self._track_label(tid)}")
        self.tracksChanged.emit()

    def cycle_audio(self, forward: bool = True):
        auds = self.audio_tracks()
        if len(auds) < 2:
            self.osdText.emit("仅有一条音轨")
            return
        ids = [t["id"] for t in auds]
        cur = self.current_audio()
        try:
            i = ids.index(cur)
        except ValueError:
            i = 0
        tid = ids[(i + (1 if forward else -1)) % len(ids)]
        self.select_audio(tid)
        self.osdText.emit(f"音轨：{self._track_label(tid)}")
        self.tracksChanged.emit()

    def _track_label(self, track_id) -> str:
        for t in self.tracks:
            if t.get("id") == track_id:
                title = t.get("title") or ""
                lang = t.get("lang") or ""
                return f"#{track_id} {title or lang}".strip()
        return f"#{track_id}"

    def current_audio(self):
        try:
            v = self._mpv.aid
            return None if v in (None, False, "no") else int(v)
        except Exception:
            return None

    def current_subtitle(self):
        try:
            v = self._mpv.sid
            return None if v in (None, False, "no") else int(v)
        except Exception:
            return None

    def add_subtitle(self, path: str):
        try:
            self._mpv.command("sub-add", path, "select")
            self.tracksChanged.emit()
        except Exception as exc:
            self.errorOccurred.emit(f"字幕加载失败：{exc}")

    # ------------------------------------------------------- subtitle geometry
    @property
    def sub_delay(self) -> float:
        try:
            return float(self._mpv.sub_delay or 0.0)
        except Exception:
            return 0.0

    def set_sub_delay(self, seconds: float):
        try:
            self._mpv.sub_delay = float(seconds)
            self.osdText.emit(f"字幕延迟 {seconds:+.1f}s")
        except Exception:
            pass

    @property
    def audio_delay(self) -> float:
        try:
            return float(self._mpv.audio_delay or 0.0)
        except Exception:
            return 0.0

    def set_audio_delay(self, seconds: float):
        try:
            self._mpv.audio_delay = float(seconds)
            self.osdText.emit(f"音轨延迟 {seconds:+.1f}s")
        except Exception:
            pass

    def set_sub_visible(self, visible: bool):
        try:
            self._mpv.sub_visibility = bool(visible)
        except Exception:
            pass

    def set_sub_scale(self, scale: float):
        try:
            self._mpv.sub_scale = float(scale)
        except Exception:
            pass

    # ------------------------------------------------------------------ aspect
    def set_aspect(self, value: str):
        """`value` is '-1' (auto/source), 'no' (fit window) or e.g. '16:9'."""
        try:
            self._mpv.video_aspect_override = value
            label = {"-1": "原始比例", "no": "拉伸填满"}.get(value, value)
            self.osdText.emit(f"画面比例：{label}")
        except Exception:
            pass

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
        """Stop decoding and tear the instance down.

        `command("stop")` first releases the audio device and the video output
        *before* the event thread is joined, which avoids the engine being torn
        down mid-frame. `_alive` goes down first so any callback that already
        queued up becomes a no-op. Safe to call more than once.
        """
        self._alive = False
        try:
            self._mpv.command("stop")
        except Exception:
            pass
        try:
            self._mpv.terminate()
        except Exception:
            pass
