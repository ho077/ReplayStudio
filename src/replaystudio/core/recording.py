"""Screen recording sessions: pick a source, drive OBS, collect the file.

Everything here was measured against the bundled OBS 32.2.2 / obs-websocket
5.7.4 rather than taken from the docs:

* `StopRecord` answers with the `outputPath`, so the produced file never has to
  be guessed from a directory listing.
* `RecordStateChanged` walks STARTING -> STARTED -> STOPPING -> STOPPED, which is
  what actually drives the state machine below (a `StartRecord` reply alone only
  means "request accepted").
* Source settings are seeded from `GetInputDefaultSettings`; writing settings we
  invented ourselves makes OBS create a source that silently captures nothing.
* `GetRecordStatus` is polled for the elapsed time only — it is the one number
  the events do not carry.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

from ..aclos.reader import Video
from . import obs_launcher, obs_paths
from .obs_client import ObsClient

IDLE = "idle"
STARTING = "starting"
RECORDING = "recording"
PAUSED = "paused"
STOPPING = "stopping"

SCENE_NAME = "ReplayStudio"
SOURCE_NAME = "ReplayStudio 采集"
DESKTOP_AUDIO = "桌面音频"
MIC_AUDIO = "麦克风/辅助音频"

# window_capture / game_capture / monitor_capture are the three kinds OBS
# actually offers here (from GetInputKindList).
KIND_MONITOR = "monitor_capture"
KIND_WINDOW = "window_capture"
KIND_GAME = "game_capture"


# Containers OBS actually produced a playable file with (each one recorded and
# ffprobe'd, see tools/probe_encoders.py). SimpleOutput/RecFormat2.
CONTAINERS = [
    ("hybrid_mp4", "MP4（混合，中断也不丢文件）"),
    ("mp4", "MP4"),
    ("mkv", "MKV"),
    ("mov", "MOV"),
    ("flv", "FLV"),
    ("ts", "TS"),
]


@dataclass
class CaptureSpec:
    """What to record. Mirrors what the settings page can set."""
    kind: str = KIND_MONITOR
    monitor_id: str = "DUMMY"      # OBS's default monitor token = primary
    window_title: str = ""
    game_mode: str = "any_fullscreen"
    capture_cursor: bool = True
    desktop_audio: bool = True
    mic: bool = False
    output_dir: str = ""
    container: str = "hybrid_mp4"

    def label(self) -> str:
        if self.kind == KIND_MONITOR:
            return "整个屏幕"
        if self.kind == KIND_WINDOW:
            return f"窗口 · {self.window_title or '未选择'}"
        return "游戏画面"


def list_windows() -> list[str]:
    """Titles of windows worth offering as a capture target.

    Filters out tool windows, IME popups and other invisible-ish helpers that
    EnumWindows happily reports but that nobody would ever pick.
    """
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    u = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    u.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetWindowLongW.restype = ctypes.c_long

    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    out: list[str] = []

    def walk(hwnd, _lp):
        if not u.IsWindowVisible(hwnd):
            return True
        if u.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if title:
            out.append(title)
        return True

    u.EnumWindows(enum_proc(walk), 0)
    # de-duplicate while keeping order
    seen: set[str] = set()
    return [t for t in out if not (t in seen or seen.add(t))]


def probe_file(path: str) -> dict:
    """Duration / size / codec of a finished recording, via ffprobe.

    Returns {} when ffprobe is unavailable — the recording is still valid, we
    just cannot fill in the metadata.
    """
    info: dict = {}
    try:
        st = os.stat(path)
        info["size_bytes"] = st.st_size
    except OSError:
        return info
    try:
        done = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-show_entries",
             "stream=codec_name,width,height,r_frame_rate,codec_type",
             "-of", "default=noprint_wrappers=1", path],
            capture_output=True, text=True, timeout=45,
            encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return info

    for line in (done.stdout or "").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k == "duration" and v not in ("N/A", ""):
            try:
                info["duration_ms"] = int(float(v) * 1000)
            except ValueError:
                pass
        elif k == "width" and v.isdigit():
            info["width"] = int(v)
        elif k == "height" and v.isdigit():
            info["height"] = int(v)
        elif k == "r_frame_rate" and "/" in v:
            num, _, den = v.partition("/")
            try:
                if float(den):
                    info["fps"] = round(float(num) / float(den), 3)
            except ValueError:
                pass
        elif k == "codec_name" and info.get("codec_type") != "audio":
            info.setdefault("codec", v)
        elif k == "codec_type":
            info["codec_type"] = v
    info.pop("codec_type", None)
    return info


class Recorder(QObject):
    """Owns one OBS connection and one recording session at a time."""

    stateChanged = Signal(str)                 # one of the state constants
    elapsed = Signal(int)                      # milliseconds
    error = Signal(str)
    finished = Signal(str)                     # path of the finished file
    connectionChanged = Signal(bool, str)      # (ok, human readable)

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.spec = CaptureSpec(output_dir=obs_paths.default_record_dir(db))
        self.state = IDLE
        self.client: ObsClient | None = None

        self._home = obs_paths.obs_home()
        self._port = obs_paths.DEFAULT_PORT
        self._input_uuid = ""
        # Did this session touch OBS at all? Quitting used to run the OBS
        # shutdown path unconditionally, and "is it running?" is answered by
        # spawning tasklist — a quarter of a second spent on every exit of a
        # session that never opened the record page.
        self._obs_touched = False

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------- connection
    @property
    def available(self) -> bool:
        return bool(self._home)

    def connect_obs(self):
        """Start the portable OBS if needed and complete the handshake."""
        if self.client is not None and self.client.ready:
            self.connectionChanged.emit(True, "已连接")
            return
        if not self._home:
            self.connectionChanged.emit(False, "没有 OBS：vendor/obs 便携版不存在")
            return
        ok, msg = obs_launcher.start(self._home, self._port)
        if not ok:
            self.connectionChanged.emit(False, msg)
            return
        cfg = obs_paths.read_ws_config(self._home) or obs_paths.ensure_ws_config(self._home)
        self._obs_touched = True
        client = ObsClient("127.0.0.1", self._port, cfg.get("server_password") or "", self)
        client.identified.connect(self._on_identified)
        client.eventReceived.connect(self._on_event)
        client.disconnected.connect(lambda r: self.connectionChanged.emit(False, r))
        self.client = client
        self.connectionChanged.emit(False, "正在连接 OBS…")
        client.open()

    def disconnect_obs(self, budget: float = 3.0):
        """Leave OBS alone. `budget` is how many seconds quitting may spend on it.

        We only shut OBS down if this session actually started or connected to
        it. An OBS the user launched themselves is theirs, and a session that
        never touched OBS has nothing to clean: the run marker is swept on the
        next launch anyway.
        """
        if not self._obs_touched:
            self.client = None
            return
        if self.state != IDLE:
            self.stop()
        obs_launcher.stop(self.client, self._home, budget=budget)
        self.client = None
        self._obs_touched = False
        self.connectionChanged.emit(False, "已断开")

    def _on_identified(self, info):
        self.connectionChanged.emit(True, "已连接")

        # Identified does not carry the software version; ask for it so the UI
        # can show something real instead of the negotiated RPC number.
        def got(data, err):
            if data:
                self.connectionChanged.emit(
                    True, f"已连接 · OBS {data.get('obsVersion', '')} "
                          f"/ ws {data.get('obsWebSocketVersion', '')}".strip())
        self.client.call("GetVersion", cb=got)

        d = self.spec.output_dir or obs_paths.default_record_dir(self.db)
        if obs_paths.is_legacy_default(d):
            # Never send the old C: default to OBS, even if a stale setting
            # still carries it.
            d = obs_paths.default_record_dir(self.db)
            self.spec.output_dir = d
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        self.client.call("SetRecordDirectory", {"recordDirectory": d})

    # ------------------------------------------------------------------ source
    def _ensure_scene(self) -> bool:
        scenes, err = self.client.call_sync("GetSceneList")
        if err:
            self.error.emit(f"读取场景列表失败：{err}")
            return False
        names = [s.get("sceneName", "") for s in (scenes or {}).get("scenes", [])]
        if SCENE_NAME not in names:
            _, err = self.client.call_sync("CreateScene", {"sceneName": SCENE_NAME})
            if err:
                self.error.emit(f"创建场景失败：{err}")
                return False
        _, err = self.client.call_sync("SetCurrentProgramScene", {"sceneName": SCENE_NAME})
        return not err

    def _source_settings(self) -> dict:
        defaults, err = self.client.call_sync(
            "GetInputDefaultSettings", {"inputKind": self.spec.kind})
        settings = dict((defaults or {}).get("defaultInputSettings") or {})
        settings["capture_cursor"] = bool(self.spec.capture_cursor)
        if self.spec.kind == KIND_WINDOW:
            settings["window"] = self.spec.window_title
        elif self.spec.kind == KIND_GAME:
            settings["capture_mode"] = self.spec.game_mode
        return settings

    def _ensure_source(self) -> bool:
        """Rebuild the capture input so a changed spec actually takes effect."""
        if self._input_uuid:
            self.client.call_sync("RemoveInput", {"inputName": SOURCE_NAME})
            self._input_uuid = ""
        created, err = self.client.call_sync("CreateInput", {
            "sceneName": SCENE_NAME,
            "inputName": SOURCE_NAME,
            "inputKind": self.spec.kind,
            "inputSettings": self._source_settings(),
            "sceneItemEnabled": True,
        })
        if err:
            self.error.emit(f"创建采集源失败：{err}")
            return False
        self._input_uuid = (created or {}).get("inputUuid", "")
        return True

    def _apply_audio(self):
        for name, wanted in ((DESKTOP_AUDIO, self.spec.desktop_audio),
                             (MIC_AUDIO, self.spec.mic)):
            self.client.call("SetInputMute", {"inputName": name, "inputMuted": not wanted})

    def _apply_container(self):
        self.client.call("SetProfileParameter", {
            "parameterCategory": "SimpleOutput",
            "parameterName": "RecFormat2",
            "parameterValue": self.spec.container or "hybrid_mp4"})

    # -------------------------------------------------------------- transport
    def start(self):
        if self.client is None or not self.client.ready:
            self.error.emit("还没连上 OBS，先点「连接 OBS」")
            return
        if self.state not in (IDLE,):
            return
        if not self._ensure_scene() or not self._ensure_source():
            return
        self._apply_container()
        self._apply_audio()
        self._set_state(STARTING)
        _, err = self.client.call_sync("StartRecord", timeout=8.0)
        if err:
            self._set_state(IDLE)
            self.error.emit(f"开始录制失败：{err}")
            return
        self._timer.start()

    def stop(self):
        if self.state not in (RECORDING, PAUSED, STARTING):
            return
        self._set_state(STOPPING)
        self.client.call_sync("StopRecord", timeout=8.0)

    def toggle_pause(self):
        if self.state == RECORDING:
            self.client.call_sync("PauseRecord")
        elif self.state == PAUSED:
            self.client.call_sync("ResumeRecord")

    def _set_state(self, state: str):
        if state == self.state:
            return
        self.state = state
        self.stateChanged.emit(state)

    def _tick(self):
        if self.state not in (RECORDING, PAUSED) or self.client is None:
            return
        self.client.call("GetRecordStatus", cb=self._on_status, timeout=2.0)

    def _on_status(self, data, err):
        if err or not data:
            return
        if self.state in (RECORDING, PAUSED):
            self.elapsed.emit(int(data.get("outputDuration") or 0))

    def _on_event(self, etype: str, data: dict):
        if etype != "RecordStateChanged":
            return
        st = (data or {}).get("outputState", "")
        if st == "OBS_WEBSOCKET_OUTPUT_STARTED":
            self._set_state(RECORDING)
        elif st == "OBS_WEBSOCKET_OUTPUT_RESUMED":
            self._set_state(RECORDING)
        elif st == "OBS_WEBSOCKET_OUTPUT_PAUSED":
            self._set_state(PAUSED)
        elif st == "OBS_WEBSOCKET_OUTPUT_STOPPED":
            self._timer.stop()
            self._set_state(IDLE)
            self.elapsed.emit(0)
            path = (data or {}).get("outputPath") or ""
            if path:
                self._collect(path)
            self.finished.emit(path)

    # ----------------------------------------------------------------- harvest
    def _collect(self, path: str):
        """Put the finished file into the local library."""
        if self.db is None or not os.path.isfile(path):
            return
        info = probe_file(path)
        try:
            st = os.stat(path)
            mtime, size = st.st_mtime, st.st_size
        except OSError:
            mtime, size = time.time(), info.get("size_bytes", 0)
        vid = Video(
            video_id="L" + _md5(path),
            match_id="",
            openid="",
            path=path,
            duration_ms=int(info.get("duration_ms") or 0),
            size_bytes=int(info.get("size_bytes") or size),
            width=int(info.get("width") or 0),
            height=int(info.get("height") or 0),
            fps=float(info.get("fps") or 0.0),
            vtype="record",
            vname=os.path.splitext(os.path.basename(path))[0],
            created_ms=int(mtime * 1000),
        )
        vid.source = "record"
        try:
            self.db.upsert_video(vid)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"入库失败（文件仍在磁盘上）：{exc}")


def _md5(path: str) -> str:
    import hashlib
    return hashlib.md5(path.encode("utf-8", "replace")).hexdigest()[:31]
