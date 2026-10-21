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
import threading
import warnings

from PySide6.QtCore import QObject, Signal

from ..core import diagnostics

_LOG_SEEN: dict[str, int] = {}

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

    def __init__(self, wid: int | None = None, hwdec: str = "auto",
                 volume: int | None = None, parent=None):
        super().__init__(parent)
        ensure_mpv_dll()
        import mpv

        opts = dict(
            hwdec=hwdec,
            keep_open="always",
            idle="yes",
            osc="no",
            # mpv embeds LuaJIT to run a dozen built-in scripts (osc,
            # auto_profiles, ytdl_hook, stats, console, select ...). We ship our
            # own controls, so none of them are wanted. They are not just dead
            # weight: LuaJIT unwinds its stack by *raising an SEH exception*
            # (0xe24c4a02 = LJ_EXCODE + LUA_ERRRUN) and catching it again. Our
            # faulthandler installs a vectored exception handler that sees it
            # first and walks the stacks — of a thread whose stack pointer
            # LuaJIT is in the middle of rewriting. That is how crash dumps
            # ended up containing "<invalid frame>" and the process died of an
            # access violation shortly after. No scripts, no such exceptions.
            load_scripts=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            force_window="no" if wid else "yes",
            sub_auto="all",
            audio_file_auto="all",
            ytdl="no",
            # Crackling ("锯齿感") on some clips before this: mpv's default
            # audio buffer is only 0.2 s, so a single disk stall drains it and
            # the output underruns. The ACLOS clips are ~35 Mbit/s and the first
            # playback often overlaps thumbnail extraction, which makes those
            # stalls likely. A larger buffer plus a demuxer cache for local
            # files absorbs them. The cache is deliberately small: up to six
            # tabs each hold their own engine.
            audio_buffer=0.4,
            cache="yes",
            demuxer_max_bytes=32 * 1024 * 1024,
            demuxer_max_back_bytes=8 * 1024 * 1024,
            log_handler=self._log,
        )
        # Initial volume is an *option*, not a property write. Setting it after
        # `MPV()` returned put a synchronous `_set_property` on the GUI thread
        # at the exact moment the engine's event thread was spinning up — the
        # two raced and the process died there. As an option mpv applies it
        # while initialising, before the event thread exists.
        if volume is not None:
            opts["volume"] = max(0, min(130, int(volume)))
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
        # Mirror of everything the observers report. Property reads are the
        # ones the UI hammers (the position timer alone asks 10x a second), and
        # every `mpv.<prop>` is a *synchronous* round trip into the engine. If
        # the engine is busy — or already gone — that call blocks the GUI
        # thread ("点不动视频") and, in the crash reports, is exactly where the
        # process dies. So reads answer from the mirror and never cross into
        # libmpv; only writes do, and only when the value actually changes.
        self._pos = 0.0
        self._dur = 0.0
        self._paused = True
        self._vol = int(opts["volume"]) if volume is not None else 0
        self._speed = 1.0
        self._tracks: list[dict] = []
        self._hwdec = ""
        # Same mirror for the settings the UI reaches for on every file load
        # and every menu opening. `_on_file_loaded` alone used to put a
        # synchronous `sub-delay` write on the GUI thread once per clip.
        self._sub_delay = 0.0
        self._audio_delay = 0.0
        self._sub_visible = True
        self._sub_scale = 1.0

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
        """Route engine diagnostics somewhere a windowed build can find them.

        stderr is invisible in a GUI launch, so mpv errors used to vanish. They
        now land in the session log. Audio warnings are included on purpose:
        "underrun" there is the only hard evidence of the crackling some clips
        show, and without it the next report is as blind as the last one.
        """
        msg = (message or "").strip()
        interesting = level in ("error", "fatal")
        if not interesting and level == "warn":
            low = f"{component} {msg}".lower()
            interesting = ("underrun" in low or "ao/" in low or "wasapi" in low
                           or "audio" in low)
        if not interesting:
            return
        if level in ("error", "fatal"):
            try:
                sys.stderr.write(f"[mpv:{component}] {msg}\n")
            except Exception:
                pass
        key = f"{level}|{component}|{msg[:120]}"
        seen = _LOG_SEEN.get(key, 0) + 1
        _LOG_SEEN[key] = seen
        # Underruns come in bursts; log the first few, then summarise.
        if seen <= 3 or seen % 50 == 0:
            diagnostics.note(f"mpv[{level}] {component}: {msg} (x{seen})")

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
            self._pos = float(value)
            self.positionChanged.emit(self._pos)

    def _on_duration(self, _name, value):
        if value is not None:
            self._dur = float(value)
            self.durationChanged.emit(self._dur)

    def _on_pause(self, _name, value):
        self._paused = bool(value)
        self.pausedChanged.emit(self._paused)

    def _on_volume(self, _name, value):
        if value is not None:
            self._vol = int(value)
            self.volumeChanged.emit(self._vol)

    def _on_mute(self, _name, value):
        self._muted = bool(value)
        self.mutedChanged.emit(self._muted)

    def _on_speed(self, _name, value):
        if value is not None:
            self._speed = float(value)
            self.speedChanged.emit(self._speed)

    def _on_path(self, _name, value):
        if value:
            self._path = value

    def _on_hwdec(self, _name, value):
        self._hwdec = str(value or "no")
        self.hwdecChanged.emit(self._hwdec)

    def _on_track_list(self, _name, value):
        # Value arrives as a list of dicts; keep it so `tracks` never has to
        # ask the engine (see the mirror note in __init__).
        self._tracks = [dict(t) for t in (value or [])]
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

    # --------------------------------------------------------- writing to mpv
    def _write(self, name: str, value) -> None:
        """Change a property **without** waiting for the engine to answer.

        `mpv.<prop> = value` is `mpv_set_property`, i.e. a synchronous round
        trip into the core, and every one of these is called from the GUI
        thread: pausing, parking a tab, moving the volume slider. If the core is
        busy — finishing a load, or being torn down by another thread — the
        caller blocks inside libmpv. That is the "点不动视频" report, and the
        crash logs back it up: the faulting runs all have the GUI thread parked
        in python-mpv's `_set_property` while an engine thread is unwinding.

        `set` through `command_async` posts the change and returns at once.
        Booleans have to become mpv's `yes`/`no`; everything else stringifies.
        Falls back to the synchronous form only if the async path is unusable
        (core already gone), where the write would be a no-op anyway.
        """
        if not self._alive:
            return
        text = ("yes" if value else "no") if isinstance(value, bool) else str(value)
        try:
            self._mpv.command_async("set", name, text)
            return
        except Exception:
            pass
        try:
            setattr(self._mpv, name.replace("-", "_"), value)
        except Exception:
            pass

    # -------------------------------------------------------------- controls
    def play(self, path: str, start_pos: float | None = None):
        """Load and play. Optionally resume at `start_pos` seconds."""
        if not self._alive:
            return
        self._path = path
        self._ended_fired = False
        # Clear the mirror: the observers only report once mpv has the new
        # file, so stale duration/position/tracks otherwise stay on screen.
        self._pos = 0.0
        self._dur = 0.0
        self._tracks = []
        try:
            # `start` is applied to the next loaded file — no race with loadfile.
            self._write("start", f"{float(start_pos)}" if start_pos else "none")
            self._mpv.play(path)
            # With `keep_open=always`, mpv sets `pause=yes` when a clip reaches
            # its end and KEEPS that state for the next loadfile. Without this
            # reset, auto-play would open the next video frozen on its first
            # frame and never advance — the user sees "自动连播没反应".
            self._write("pause", False)
        except Exception as exc:
            self.errorOccurred.emit(f"无法播放 {os.path.basename(path)}：{exc}")

    def stop(self):
        if not self._alive:
            return
        try:
            self._mpv.command_async("stop")
        except Exception:
            pass

    @property
    def path(self) -> str:
        return self._path

    @property
    def duration(self) -> float:
        return self._dur

    @property
    def position(self) -> float:
        return self._pos

    def seek(self, seconds: float, precise: bool = True):
        if not self._alive:
            return
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
        return self._paused

    def set_paused(self, paused: bool):
        if not self._alive:
            return
        want = bool(paused)
        if want == self._paused:
            return                      # already there: skip the round trip
        self._paused = want
        self._write("pause", want)

    def toggle_pause(self):
        self.set_paused(not self.paused)

    @property
    def volume(self) -> int:
        return self._vol

    def set_volume(self, value: int):
        if not self._alive:
            return
        want = max(0, min(130, int(value)))
        # The engine reports its volume back, the UI mirrors it into the
        # slider, and the slider's signal used to set it *again*. Echoing it
        # straight back to mpv is what put a synchronous write on the GUI
        # thread for every tick; a no-op write now stops at the mirror.
        if want == self._vol:
            return
        self._vol = want
        self._write("volume", want)

    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool):
        if not self._alive:
            return
        want = bool(muted)
        if want == self._muted:
            return
        self._muted = want
        self._write("mute", want)

    def suspend_audio(self) -> str:
        """Turn the audio track off and return the id that was playing.

        Measured on this machine: two engines that each hold a real audio
        output *and* a real video output crash the process (access violation,
        3 of 10 runs) while the second one is starting up. A parked tab has no
        reason to keep an audio device open — it is muted anyway — so we drop
        the track entirely and let mpv release the AO. The id is restored on
        resume, which keeps a manually chosen track intact.
        """
        if not self._alive:
            return ""
        aid = self.current_audio()
        self._write("aid", "no")
        return "" if aid is None else str(aid)

    def resume_audio(self, aid: str = ""):
        self._write("aid", aid or "auto")

    @property
    def speed(self) -> float:
        return self._speed

    def set_speed(self, value: float):
        if not self._alive:
            return
        want = float(value)
        if abs(want - self._speed) < 1e-6:
            return
        self._speed = want
        self._write("speed", want)

    # ---------------------------------------------------------------- tracks
    @property
    def tracks(self):
        return list(self._tracks)

    def audio_tracks(self):
        return [t for t in self.tracks if t.get("type") == "audio"]

    def subtitle_tracks(self):
        return [t for t in self.tracks if t.get("type") == "sub"]

    def select_audio(self, track_id: int):
        self._write("aid", int(track_id))

    def select_subtitle(self, track_id):
        """track_id None/False disables subtitles."""
        self._write("sid", int(track_id) if track_id else "no")

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
        return self._selected_track("audio")

    def current_subtitle(self):
        return self._selected_track("sub")

    def _selected_track(self, kind: str):
        """Which track of `kind` mpv has selected, straight from the mirror.

        Reading `aid`/`sid` off the engine instead put a synchronous call on
        the GUI thread inside `_on_tracks`, i.e. on every single file load.
        """
        if not self._alive:
            return None
        for t in self._tracks:
            if t.get("type") == kind and t.get("selected"):
                tid = t.get("id")
                if tid is None:
                    return None
                try:
                    return int(tid)
                except (TypeError, ValueError):
                    return None
        return None

    def add_subtitle(self, path: str):
        if not self._alive:
            return
        try:
            self._mpv.command_async("sub-add", path, "select")
            self.tracksChanged.emit()
        except Exception as exc:
            self.errorOccurred.emit(f"字幕加载失败：{exc}")

    # ------------------------------------------------------- subtitle geometry
    # Every one of these used to read or write the property straight through to
    # the engine. A write is a synchronous round trip on whichever thread calls
    # it — and these are called from the GUI thread (menu building, file load),
    # which is what makes the interface stop responding while mpv is busy.
    @property
    def sub_delay(self) -> float:
        return self._sub_delay

    def set_sub_delay(self, seconds: float):
        want = float(seconds)
        if abs(want - self._sub_delay) < 1e-4:
            return
        self._sub_delay = want
        self._write("sub-delay", want)
        self.osdText.emit(f"字幕延迟 {want:+.1f}s")

    @property
    def audio_delay(self) -> float:
        return self._audio_delay

    def set_audio_delay(self, seconds: float):
        want = float(seconds)
        if abs(want - self._audio_delay) < 1e-4:
            return
        self._audio_delay = want
        self._write("audio-delay", want)
        self.osdText.emit(f"音轨延迟 {want:+.1f}s")

    def set_sub_visible(self, visible: bool):
        want = bool(visible)
        if want == self._sub_visible:
            return
        self._sub_visible = want
        self._write("sub-visibility", want)

    def set_sub_scale(self, scale: float):
        want = float(scale)
        if abs(want - self._sub_scale) < 1e-4:
            return
        self._sub_scale = want
        self._write("sub-scale", want)

    # ------------------------------------------------------------------ aspect
    def set_aspect(self, value: str):
        """`value` is '-1' (auto/source), 'no' (fit window) or e.g. '16:9'."""
        self._write("video-aspect-override", value)
        label = {"-1": "原始比例", "no": "拉伸填满"}.get(value, value)
        self.osdText.emit(f"画面比例：{label}")

    def set_fullscreen(self, on: bool):
        self._write("fullscreen", bool(on))

    def screenshot(self, path: str):
        if not self._alive:
            return False
        try:
            self._mpv.command_async("screenshot-to-file", path, "video")
            return True
        except Exception:
            return False

    def hwdec_current(self) -> str:
        return self._hwdec

    def disconnect_all(self):
        """Cut every signal this engine exposes.

        PlayerPage connects `lambda _p: self._on_file_loaded()` to `fileLoaded`,
        so the engine ends up holding a closure over the page that owns it —
        a cycle the garbage collector cannot break, because the C++ side is
        part of it too. The page then never gets collected and PySide6 never
        destroys it. Disconnecting is what actually releases it.
        """
        for name in ("positionChanged", "durationChanged", "pausedChanged",
                     "fileLoaded", "ended", "errorOccurred", "volumeChanged",
                     "mutedChanged", "speedChanged", "tracksChanged",
                     "hwdecChanged", "aspectDetected", "osdText"):
            signal = getattr(self, name, None)
            if signal is None:
                continue
            try:
                with warnings.catch_warnings():
                    # PySide6 logs "Failed to disconnect (None)" for a signal
                    # nobody ever connected. That is not worth filling the
                    # console — we are clearing all of them unconditionally.
                    warnings.simplefilter("ignore", RuntimeWarning)
                    signal.disconnect()
            except (RuntimeError, TypeError):
                pass

    def shutdown(self):
        """Stop decoding and tear the instance down.

        Order matters, and the naive order crashes. python-mpv's `terminate()`
        calls `mpv_terminate_destroy()` first and only *then* joins the event
        thread — so whenever that thread happened to be mid-callback (dispatching
        a property change, unwinding a LuaJIT trace) the handle was freed underneath
        it, and the process died with an access violation right there. Closing a
        tab was a coin flip; measured at roughly one crash in three.

        So: stop playback, then ask mpv to shut itself down and *wait for its
        event thread to leave the loop*, and only then destroy. By the time
        `terminate()` runs there is nobody left holding the handle.
        `_alive` goes down first so any callback that already queued up becomes
        a no-op. Safe to call more than once.
        """
        self._alive = False
        try:
            self._mpv.command_async("stop")
        except Exception:
            pass
        # 1. Ask mpv to shut itself down. Its event thread then handles
        #    SHUTDOWN, which ends with it destroying its own event handle.
        try:
            self._mpv.quit()
        except Exception:
            pass
        # 2. Wait for *that thread*, not for the event. `wait_for_shutdown()`
        #    also reads from the event handle, so it races the thread that is
        #    destroying it — which crashed here too. Joining touches nothing.
        #    Bounded: a wedged engine must not stall the GUI thread.
        thread = getattr(self._mpv, "_event_thread", None)
        if thread is not None and thread.is_alive() \
                and thread is not threading.current_thread():
            thread.join(1.5)
        # 3. Now nothing is inside the handle any more.
        try:
            self._mpv.terminate()
        except Exception:
            pass
