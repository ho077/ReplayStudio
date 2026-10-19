"""Print raw mpv event ids while a short file plays to EOF."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

SHORT = os.path.join(ROOT, "tests", "_media", "container.mp4")


def main() -> int:
    app = QApplication([])
    from replaystudio.media.player import MpvPlayer

    p = MpvPlayer(wid=None, hwdec="auto")
    seen: list[str] = []
    p.ended.connect(lambda: seen.append("ENDED"))
    p.fileLoaded.connect(lambda x: seen.append("FILE_LOADED"))
    p.errorOccurred.connect(lambda m: seen.append(f"ERROR:{m}"))
    p._mpv.register_event_callback(
        lambda e: seen.append(f"raw={p._event_id(e)}"))
    p.play(SHORT)
    end = time.time() + 9
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    print("duration:", p.duration, "pos:", p.position, "paused:", p.paused)
    print("keep_open:", p._mpv.keep_open)
    print("events:", seen)
    with open(os.path.join(ROOT, "tools", "_events_dbg.txt"), "w",
              encoding="utf-8") as f:
        f.write(repr(seen) + f"\nduration={p.duration} pos={p.position}\n")
    p.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
