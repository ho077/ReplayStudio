"""Do closed tabs actually go away?

Track pages with *weakrefs* — walking gc.get_objects() and holding the result
makes the probe itself the strongest reference there is, which is exactly the
kind of measurement error that hides a leak (it showed "3 alive" even when
nothing else referred to them).

What is measured:
  * the theme registry (_STYLED), which used to pin every widget strongly
  * the tab signal slots, which used to close over their page
  * whether a closed PlayerPage can be collected at all

Usage: probe_theme_leak.py [cycles]
"""

from __future__ import annotations

import gc
import os
import shutil
import subprocess
import sys
import tempfile

import shiboken6 as shiboken

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

TMP = tempfile.mkdtemp(prefix="rs-leak-")
os.environ["LOCALAPPDATA"] = TMP
MEDIA = os.path.join(TMP, "media")
os.makedirs(MEDIA, exist_ok=True)


def make_sample(path: str, freq: int = 440):
    subprocess.run(
        [shutil.which("ffmpeg"), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=navy:size=320x240:rate=15:duration=3",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", path],
        check=True, capture_output=True)


def main() -> int:
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    # 0 = never touch the theme, which is how you tell "opening and closing
    # tabs breaks it" apart from "switching the palette breaks it".
    themes = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    v = os.path.join(MEDIA, "clip.mp4")
    make_sample(v)

    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from replaystudio.core import diagnostics
    from replaystudio.ui import theme as T
    from replaystudio.ui.main_window import MainWindow

    diagnostics.install()
    diagnostics.install_qt(app)

    win = MainWindow()
    empty = os.path.join(TMP, "empty-aclos")
    os.makedirs(empty, exist_ok=True)
    win.settings["aclos_dir"] = empty
    win.show()

    def pump(seconds: float):
        """Run a real event loop — see the note in tools/probe_page_refs.py.

        With `app.processEvents()` nothing is ever deleted, so every run of this
        probe reported pages as immortal whether or not they were.
        """
        from PySide6.QtCore import QEventLoop, QTimer

        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    def alive(tracked) -> int:
        """How many tracked pages are still real widgets?

        The Python wrapper lingers for a moment after a tab closes; what must
        actually go away is the C++ QWidget, because that is what owns the
        native window mpv renders into.
        """
        gc.collect()
        n = 0
        for r in tracked:
            page = r()
            if page is None:
                continue
            try:
                if shiboken.isValid(page):
                    n += 1
            except RuntimeError:
                pass
        return n

    pump(0.8)
    tabs = win.player_tabs
    print(f"crash log dir: {diagnostics.log_dir()}", flush=True)
    print(f"{'step':<22} {'pages':>6} {'alive':>6} {'_STYLED':>8}", flush=True)

    tracked: list = []
    print(f"{'boot':<22} {0:>6} {alive(tracked):>6} {len(T._STYLED):>8}", flush=True)

    for i in range(cycles):
        win.open_paths([v])
        for _ in range(60):
            pump(0.05)
            if tabs.current_page() is not None and tabs.current_page().player:
                break
        pump(0.4)
        import weakref
        tracked.append(weakref.ref(tabs.current_page()))
        print(f"{f'open  {i}':<22} {len(tracked):>6} "
              f"{alive(tracked):>6} {len(T._STYLED):>8}", flush=True)

        tabs.close_current()
        pump(0.6)
        print(f"{f'close {i}':<22} {len(tracked):>6} "
              f"{alive(tracked):>6} {len(T._STYLED):>8}", flush=True)

    keys = list(T.PALETTES.keys())
    for n in range(themes):
        key = keys[n % len(keys)]
        win.apply_theme(key)
        pump(0.25)
        print(f"{f'theme {n}: {key}':<22} {len(tracked):>6} "
              f"{alive(tracked):>6} {len(T._STYLED):>8}", flush=True)
    print(f"{f'after {themes} theme switches':<22} {len(tracked):>6} "
          f"{alive(tracked):>6} {len(T._STYLED):>8}", flush=True)

    survivors = alive(tracked)
    print(f"\n=== {survivors}/{len(tracked)} closed pages still alive ===", flush=True)
    if survivors:
        import types
        p = tracked[0]()
        print("referrers of the first survivor (frames excluded):", flush=True)
        for r in gc.get_referrers(p):
            if isinstance(r, types.FrameType) or r is tracked:
                continue
            if isinstance(r, (list, tuple)):
                kinds = sorted({type(x).__name__ for x in r})
                print(f"   {type(r).__name__}[{len(r)}] of {kinds}", flush=True)
            elif hasattr(r, "cell_contents"):
                try:
                    c = type(r.cell_contents).__name__
                except ValueError:
                    c = "empty"
                owners = []
                for rr in gc.get_referrers(r):
                    if isinstance(rr, types.FrameType) or rr is tracked:
                        continue
                    name = getattr(rr, "__qualname__", None) or type(rr).__name__
                    mod = getattr(rr, "__module__", "")
                    owners.append(f"{name}@{mod.split('.')[-1]}")
                print(f"   cell -> {c}  held by {owners}", flush=True)
            else:
                print(f"   {type(r).__name__}: {repr(r)[:130]}", flush=True)
    win.close()
    pump(0.5)
    return 0 if survivors == 0 else 1


if __name__ == "__main__":
    from replaystudio.core import diagnostics

    diagnostics.install()
    code = main()
    # libmpv dies in DllMain(PROCESS_DETACH) on a normal exit
    diagnostics.hard_exit(code)
