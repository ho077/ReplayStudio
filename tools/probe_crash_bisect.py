"""Is the post-close theme crash caused by mpv, or by our own teardown?

tools/probe_theme_leak.py 1 12 crashes every time: open a tab, close it, switch
the palette once. The fault lands inside QApplication::setStyleSheet. Removing
mpv from the picture tells us which side to fix — and tools/probe_qss_crash.py
already showed that plain widgets (native windows included) survive the same
sequence, so mpv is the prime suspect.

Usage: probe_crash_bisect.py [real|dummy]
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

MODE = sys.argv[1] if len(sys.argv) > 1 else "real"
# Comma separated teardown steps to switch off, e.g. "purge,disconnect".
OFF = [s for s in (sys.argv[2] if len(sys.argv) > 2 else "").split(",") if s]

TMP = tempfile.mkdtemp(prefix="rs-bisect-")
os.environ["LOCALAPPDATA"] = TMP
MEDIA = os.path.join(TMP, "media")
os.makedirs(MEDIA, exist_ok=True)
CLIP = os.path.join(MEDIA, "clip.mp4")

subprocess.run(
    [shutil.which("ffmpeg"), "-y", "-loglevel", "error",
     "-f", "lavfi", "-i", "color=c=navy:size=320x240:rate=15:duration=2",
     "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
     "-c:a", "aac", "-shortest", CLIP],
    check=True, capture_output=True)


class DummyPlayer:
    """Same surface as MpvPlayer, no libmpv underneath."""

    def __init__(self, wid=None, hwdec="auto", volume=None, parent=None):
        self._wid = wid
        self._pos = 0.0
        self._dur = 0.0
        self._paused = True
        self._vol = volume or 80
        self._speed = 1.0
        self._tracks: list = []
        self.path = ""
        self.muted = False
        self._alive = True

    positionChanged = None          # replaced below; only the names matter here

    def play(self, path, start_pos=None):
        self.path = path
        self._paused = False

    def stop(self):
        pass

    @property
    def duration(self):
        return self._dur

    @property
    def position(self):
        return self._pos

    def seek(self, *_a, **_k):
        pass

    @property
    def paused(self):
        return self._paused

    def set_paused(self, v):
        self._paused = bool(v)

    def toggle_pause(self):
        self._paused = not self._paused

    @property
    def volume(self):
        return self._vol

    def set_volume(self, v):
        self._vol = v

    def set_muted(self, v):
        self.muted = bool(v)

    def suspend_audio(self):
        return ""

    def resume_audio(self, _aid=""):
        pass

    @property
    def speed(self):
        return self._speed

    def set_speed(self, v):
        self._speed = v

    @property
    def tracks(self):
        return []

    def audio_tracks(self):
        return []

    def subtitle_tracks(self):
        return []

    def select_audio(self, *_a):
        pass

    def select_subtitle(self, *_a):
        pass

    def current_audio(self):
        return None

    def current_subtitle(self):
        return None

    def add_subtitle(self, _p):
        pass

    @property
    def sub_delay(self):
        return 0.0

    def set_sub_delay(self, _v):
        pass

    def set_aspect(self, _v):
        pass

    def set_fullscreen(self, _v):
        pass

    def screenshot(self, _p):
        return False

    def hwdec_current(self):
        return "no"

    def disconnect_all(self):
        pass

    def shutdown(self):
        self._alive = False


def main() -> int:
    from PySide6.QtCore import QEventLoop, QObject, QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from replaystudio.core import diagnostics
    from replaystudio.ui import theme as T
    from replaystudio.ui.main_window import MainWindow
    from replaystudio.ui import player_page as PP

    diagnostics.install()
    diagnostics.install_qt(app)

    if MODE == "dummy":
        # Signals are class attributes on MpvPlayer; the dummy needs real ones
        # because PlayerPage connects to them.
        from PySide6.QtCore import Signal

        class QtDummy(QObject, DummyPlayer):
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

            def __init__(self, wid=None, hwdec="auto", volume=None, parent=None):
                QObject.__init__(self, parent)
                DummyPlayer.__init__(self, wid, hwdec, volume, parent)

        PP.MpvPlayer = QtDummy
        print("mode: dummy engine (no libmpv at all)")
    else:
        print("mode: real libmpv engine")

    from replaystudio.ui import theme as _theme
    from replaystudio.ui import qt_teardown as _qt

    _real_tree = _qt.disconnect_tree
    if "purge" in OFF:
        _qt.purge_children = lambda _w: 0
    if "disconnect" in OFF:
        _qt.disconnect_tree = lambda _w: 0
    if "d-root" in OFF:
        # Only the page's own signals; leave every child alone.
        _qt.disconnect_tree = lambda root: _qt.disconnect_object(root)
    if "d-custom" in OFF:
        # Only the handful PlayerPage declares for the outside world.
        def _only_declared(root):
            cut = 0
            for name in ("backRequested", "prevRequested", "nextRequested",
                         "positionReported", "playlistIndexSelected",
                         "fileDropped", "rowChanged"):
                sig = getattr(root, name, None)
                if sig is not None:
                    try:
                        sig.disconnect()
                        cut += 1
                    except (RuntimeError, TypeError):
                        pass
            return cut

        _qt.disconnect_tree = _only_declared
    if "d-no-destroyed" in OFF:
        # Everything except the inherited QObject::destroyed / objectNameChanged
        # hooks, which is where Qt (and shiboken) keep their own bookkeeping.
        def _skip_destroyed(root):
            cut = 0
            for obj in [root] + list(root.findChildren(QObject)):
                for sig in _qt.signal_instances(obj):
                    try:
                        if sig.signal in ("destroyed(QObject*)",
                                          "destroyed()",
                                          "objectNameChanged(QString)"):
                            continue
                        sig.disconnect()
                        cut += 1
                    except (RuntimeError, TypeError):
                        pass
            return cut

        _qt.disconnect_tree = _skip_destroyed
    if "unregister" in OFF:
        _theme.unregister = lambda _w: 0
    if OFF:
        print(f"teardown steps disabled: {OFF}")

    win = MainWindow()
    empty = os.path.join(TMP, "empty-aclos")
    os.makedirs(empty, exist_ok=True)
    win.settings["aclos_dir"] = empty
    win.show()

    def pump(seconds: float):
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    pump(0.8)
    tabs = win.player_tabs

    print("opening a tab", flush=True)
    win.open_paths([CLIP])
    for _ in range(60):
        pump(0.05)
        page = tabs.current_page()
        if page is not None and page.player:
            break
    pump(0.5)

    if MODE == "keep":
        # The way the bug was actually reported: a video is playing and the
        # user cycles the palette over the top of it.
        print("keeping the tab open", flush=True)
    else:
        print("closing the tab", flush=True)
        tabs.close_current()
        pump(0.8)

    # Same sequence as tools/probe_theme_leak.py: the crash in the real app
    # arrived on the *second* switch, so one switch is not a fair test.
    keys = list(T.PALETTES.keys())
    for n in range(len(keys) * 2):
        key = keys[n % len(keys)]
        win.apply_theme(key)
        pump(0.25)
        print(f"  theme {n}: {key}", flush=True)
    print("survived", flush=True)
    return 0


if __name__ == "__main__":
    from replaystudio.core import diagnostics

    diagnostics.install()
    diagnostics.hard_exit(main())
