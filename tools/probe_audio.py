"""Check the audio path actually used, and look for underruns.

Background: playback crackled ("锯齿感") on some clips. The working theory is
an audio underrun caused by a too-small buffer (mpv defaults to 0.2 s) plus a
disk stall — the recordings are ~35 Mbit/s and the first playback usually
overlaps thumbnail extraction. Mitigation: bigger `audio-buffer` and a small
demuxer cache.

This probe does not "prove" the crackle is gone (that needs ears), but it does
prove the options took effect and that a normal 8 s playback logs no underrun
and no audio errors.

Run:  .venv\\Scripts\\python.exe tools\\probe_audio.py [video]
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="rs-audio-")
MEDIA = os.path.join(ROOT, "tests", "_media")

OUT = os.path.join(ROOT, "tools", "_audio.txt")
lines: list[str] = []


def say(s: str):
    lines.append(s)
    print(s, flush=True)


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from replaystudio.media.player import MpvPlayer
    from replaystudio.ui.main_window import MainWindow
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    win = MainWindow()
    win.resize(1200, 800)
    win.show()

    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        for name in ("phase2-sample.mkv", "container.mp4"):
            cand = os.path.join(MEDIA, name)
            if os.path.isfile(cand):
                path = cand
                break
    if not path:
        say("缺少样本，先运行 tools/smoke_containers.py")
        return 1

    # Capture every mpv log line, not just errors (MpvPlayer._log filters).
    captured: list[str] = []
    original = MpvPlayer._log

    @staticmethod
    def _all(level, component, message):
        captured.append(f"{level}/{component}: {message}")
        if level in ("error", "fatal"):
            captured[-1] = "ERROR " + captured[-1]

    MpvPlayer._log = _all

    host = QWidget()
    host.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
    host.resize(640, 360)
    host.show()
    player = MpvPlayer(wid=int(host.winId()))

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    player.play(path, 0.0)
    pump(8.0)

    eng = player._mpv
    say(f"file            : {os.path.basename(path)}")
    say(f"duration        : {player.duration:.2f}s  pos={player.position:.2f}s")
    for attr in ("audio_out_params", "audio_params", "audio_codec_name",
                 "audio_bitrate", "audio_samplerate", "audio_channels",
                 "current_ao", "audio_device", "audio_buffer"):
        try:
            say(f"{attr:16s}: {getattr(eng, attr)}")
        except Exception as exc:
            say(f"{attr:16s}: <{exc}>")
    try:
        cs = eng.demuxer_cache_state
        if isinstance(cs, dict):
            say(f"demuxer_cache   : ranges={len(cs.get('seekable-ranges') or [])} "
                f"bytes={cs.get('total-bytes')} eof={cs.get('eof')}")
        else:
            say(f"demuxer_cache   : {cs}")
    except Exception as exc:
        say(f"demuxer_cache   : <{exc}>")

    bad = [ln for ln in captured
           if "underrun" in ln.lower() or ln.startswith("ERROR")]
    say(f"\nmpv log lines   : {len(captured)}")
    say(f"underrun/errors : {len(bad)}")
    for ln in bad[:10]:
        say("  " + ln)
    # Keep a sample of the audio-related lines: they show which AO was opened.
    for ln in captured:
        low = ln.lower()
        if any(k in low for k in ("ao/", "audio", "wasapi")):
            say("  audio> " + ln)

    player.shutdown()
    host.close()
    win.close()

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())
