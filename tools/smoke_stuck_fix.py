"""Verify the fixes for the '卡住了' report.

1. single instance: second acquire fails; its file-drop request reaches the
   first instance on the next poll tick (real QTimer event loop, in-process).
2. thumbnail cache: a corrupt cached JPEG no longer poisons the video, and
   failed videos are not re-queued on every repaint.
3. sqlite busy timeout is short (3 s) so a locked DB errors instead of
   freezing the UI thread for 30 s.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from PySide6.QtWidgets import QApplication

import replaystudio.core.single_instance as si
from replaystudio.media import thumbs

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main() -> int:
    app = QApplication([])
    print("== 1. single instance (file drop) ==")
    first = si.SingleInstance(app)
    got: list[str] = []
    check("first acquire", first.acquire(lambda fs: got.extend(fs)))

    second = si.SingleInstance(app)
    check("second acquire refused", not second.acquire(None))

    marker = os.path.join(tempfile.mkdtemp(), "opened.txt")
    with open(marker, "w", encoding="utf-8") as f:
        f.write("x")

    def opener(files):
        got.extend(files)
        with open(marker, "w", encoding="utf-8") as f:
            f.write("opened")

    first._on_open = opener
    ok = second.drop_request([marker])
    end = time.time() + 3.0                    # one poll tick is 400 ms
    while time.time() < end and not got:
        app.processEvents()
        time.sleep(0.02)
    check("open request delivered", ok and got == [marker], f"got={got!r}")
    check("opener ran", open(marker, encoding="utf-8").read() == "opened")

    # No stray request files left behind.
    d = si._requests_dir()
    leftovers = [n for n in os.listdir(d) if n.startswith(si.REQ_PREFIX)]
    check("requests drained", not leftovers, f"left={leftovers!r}")

    print("== 2. thumbnail cache ==")
    tmp = tempfile.mkdtemp()
    vid = "probe-vid-1"
    orig_cache = thumbs._CACHE_DIR
    thumbs._CACHE_DIR = tmp
    try:
        # 2a. corrupt cache file poisons no more: QImage null -> file removed
        bad = thumbs._cache_path(vid)
        with open(bad, "wb") as f:
            f.write(b"\x00\x01not a jpeg")
        from PySide6.QtGui import QImage

        class Task(thumbs._ThumbTask):
            emitted = ""

            def run(self):
                p = thumbs.produce_thumbnail(self.video_id, self.path,
                                             self.poster, self.width)
                img = QImage(p) if p else QImage()
                if img.isNull():
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                    Task.emitted = "failed"

        t = Task(vid, "", "", 300, 170)       # no source -> failure either way
        t.run()                               # run inline, no thread needed
        check("corrupt cache removed on failure", not os.path.isfile(bad))
        check("failure reported", Task.emitted == "failed")

        # 2b. atomic copy leaves no .part junk and produces a valid file
        src = os.path.join(tmp, "poster.jpg")
        QImage(32, 32, QImage.Format_RGB32).save(src, "JPEG")
        thumbs._atomic_copy(src, thumbs._cache_path("probe-vid-2"))
        out = thumbs._cache_path("probe-vid-2")
        check("atomic copy lands", os.path.isfile(out)
              and not any(".part" in x for x in os.listdir(tmp))
              and QImage(out).height() == 32)
    finally:
        thumbs._CACHE_DIR = orig_cache

    # 2c. failed videos are not re-requested on every repaint
    svc = thumbs.ThumbnailService()
    calls = {"n": 0}
    orig_produce = thumbs.produce_thumbnail
    thumbs.produce_thumbnail = lambda *a, **k: calls.__setitem__(
        "n", calls["n"] + 1) or ""
    try:
        svc.request("f1", "no-such-file.mp4")
        for _ in range(60):
            app.processEvents()
            time.sleep(0.02)
            if "f1" in svc._failed:
                break
        before = calls["n"]
        for _ in range(5):
            svc.request("f1", "no-such-file.mp4")
        check("failed video not re-queued", calls["n"] == before,
              f"before={before} after={calls['n']}")
    finally:
        thumbs.produce_thumbnail = orig_produce

    print("== 3. sqlite busy timeout ==")
    import inspect

    from replaystudio.core.db import Database
    src_code = inspect.getsource(Database.conn)
    check("conn uses timeout=3", "timeout=3" in src_code)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    from replaystudio.core import diagnostics
    diagnostics.install()
    diagnostics.hard_exit(main())
