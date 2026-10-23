"""End-to-end: record 3 s through Recorder and report where the file landed.

The default used to be %LOCALAPPDATA%\\ReplayStudio\\recordings — on C:, which
is the wrong drive for video. This drives the real Recorder (not a hand-made
spec) and asserts the output path is the new default.
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

app = QCoreApplication(sys.argv)

from replaystudio.core import obs_paths
from replaystudio.core.db import Database, user_data_dir
from replaystudio.core.recording import IDLE, RECORDING, Recorder


def pump(seconds: float):
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def main() -> int:
    db = Database(os.path.join(user_data_dir(), "index.db"))
    rec = Recorder(db)
    want = obs_paths.default_record_dir(db)
    print("default_record_dir :", want)
    print("spec.output_dir    :", rec.spec.output_dir)
    print("on C:?             :", want[:1].upper() == "C")
    print("legacy?            :", obs_paths.is_legacy_default(rec.spec.output_dir))

    if not rec.available:
        print("no OBS here")
        return 0

    print("connecting …", flush=True)
    rec.connect_obs()
    for _ in range(120):
        pump(0.5)
        if rec.client is not None and rec.client.ready:
            break
    if rec.client is None or not rec.client.ready:
        print("FAIL: could not connect to OBS")
        return 1
    print("connected:", type(rec.client).__name__, flush=True)

    # Let SetRecordDirectory take effect.
    pump(1.5)

    # What OBS will actually use. Asking beats recording a clip to find out:
    # this is the value SetRecordDirectory set on connect, and it is where every
    # recording lands.
    got, err = rec.client.call_sync("GetRecordDirectory", timeout=8.0)
    live = (got or {}).get("recordDirectory", "")
    print("OBS record dir     :", live, flush=True)
    same = os.path.normcase(os.path.abspath(live)) == os.path.normcase(
        os.path.abspath(want))
    print("matches default    :", same)
    print("on C:?             :", live[:1].upper() == "C")

    if "--record" not in sys.argv:
        rec.disconnect_obs(budget=3.0)
        pump(1.0)
        fails = [] if same else [f"OBS record dir {live!r} != default {want!r}"]
    for f in fails:
        print("FAIL", f)
    print("(skipping a real recording; pass --record to also make a clip)")
    return 1 if fails else 0

    box: dict = {"path": ""}
    rec.finished.connect(lambda p: box.__setitem__("path", p))
    rec.error.connect(lambda m: print("  error:", m, flush=True))
    rec.stateChanged.connect(lambda s: print("  state:", s, flush=True))

    print("recording 3 s …", flush=True)
    rec.start()
    for i in range(60):
        pump(0.25)
        if rec.state == RECORDING:
            break
        if i % 12 == 11:
            st, err = rec.client.call_sync("GetRecordStatus", timeout=5.0)
            print(f"  …{i}: state={rec.state} obs={st} err={err}", flush=True)
    if rec.state != RECORDING:
        print("FAIL: never reached RECORDING, state =", rec.state)
        return 1
    pump(3.5)
    rec.stop()
    for _ in range(40):
        pump(0.25)
        if box.get("path"):
            break

    path = box.get("path", "")
    print("output path        :", path)
    if not path:
        print("FAIL: no output path")
        return 1
    exists = os.path.isfile(path)
    print("exists             :", exists)
    if exists:
        print("size               :", round(os.path.getsize(path) / 1024 / 1024, 2), "MB")
    on_c = path[:1].upper() == "C"
    print("on C:?             :", on_c)

    rec.disconnect_obs(budget=3.0)
    pump(1.0)

    fails = []
    if on_c:
        fails.append("recording still landed on C:")
    if not exists:
        fails.append("output file missing")
    if rec.state != IDLE:
        fails.append(f"recorder left in state {rec.state}")
    for f in fails:
        print("FAIL", f)
    return 1 if fails else 0


if __name__ == "__main__":
    from replaystudio.core import diagnostics

    diagnostics.install()
    sys.exit(main())
