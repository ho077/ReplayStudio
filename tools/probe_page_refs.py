"""Who still holds a closed PlayerPage?

tools/probe_theme_leak.py tells us *that* the page survives; this one finds the
reference chain. It distinguishes three different things that all look alike
from the outside:

  * the C++ QWidget still exists  → native video surface still allocated
  * only the Python wrapper lives → harmless for memory, still harmless
  * neither                       → actually released

Usage: probe_page_refs.py [depth]
"""
from __future__ import annotations

import gc
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

TMP = tempfile.mkdtemp(prefix="rs-refs-")
os.environ["LOCALAPPDATA"] = TMP
MEDIA = os.path.join(TMP, "media")
os.makedirs(MEDIA, exist_ok=True)


def make_sample(path: str):
    subprocess.run(
        [shutil.which("ffmpeg"), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=navy:size=320x240:rate=15:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", path],
        check=True, capture_output=True)


def describe(obj) -> str:
    if isinstance(obj, types.FunctionType):
        return f"func {obj.__qualname__}"
    if isinstance(obj, types.MethodType):
        return f"bound {obj.__self__.__class__.__name__}.{obj.__func__.__qualname__}"
    if hasattr(obj, "__self__"):
        return f"signal {type(obj).__name__} of {type(obj.__self__).__name__}"
    if isinstance(obj, dict):
        return f"dict[{len(obj)}]"
    if isinstance(obj, (list, tuple, set)):
        body = ", ".join(sorted({type(x).__name__ for x in obj})[:4])
        return f"{type(obj).__name__}[{len(obj)}] {{{body}}}"
    return f"{type(obj).__name__}: {repr(obj)[:80]}"


def walk(target, depth: int, seen: set, indent: str):
    """Print what refers to `target`, then what refers to that, and so on."""
    if depth <= 0:
        return
    for ref in gc.get_referrers(target):
        if isinstance(ref, types.FrameType) or ref is seen:
            continue
        key = id(ref)
        if key in seen:
            print(f"{indent}(cycle, already shown)", flush=True)
            continue
        seen.add(key)
        # A closure tuple on its own is useless; name the function it belongs to.
        owners = []
        for owner in gc.get_referrers(ref):
            if isinstance(owner, types.FunctionType):
                try:
                    if ref in (owner.__closure__ or ()):
                        owners.append(f"{owner.__qualname__}")
                except TypeError:
                    pass
            elif isinstance(owner, types.MethodType):
                owners.append(f"method {owner.__func__.__qualname__}")
        label = describe(ref)
        if owners:
            label += f"   <- closure of {', '.join(owners[:4])}"
        print(f"{indent}{label}", flush=True)
        walk(ref, depth - 1, seen, indent + "    ")


def main() -> int:
    depth = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    v = os.path.join(MEDIA, "clip.mp4")
    make_sample(v)

    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    from replaystudio.core import diagnostics
    from replaystudio.ui.main_window import MainWindow

    diagnostics.install()
    diagnostics.install_qt(app)

    win = MainWindow()
    empty = os.path.join(TMP, "empty-aclos")
    os.makedirs(empty, exist_ok=True)
    win.settings["aclos_dir"] = empty
    win.show()

    def pump(seconds: float):
        """Run a *real* event loop.

        `app.processEvents()` in a loop does deliver most events, but a
        DeferredDelete is only acted on once control returns to an event loop
        (Qt docs for `QObject::deleteLater`). Measuring teardown with
        processEvents therefore reports every widget as immortal, including ones
        the application frees immediately.
        """
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    pump(0.8)
    tabs = win.player_tabs

    win.open_paths([v])
    for _ in range(60):
        pump(0.05)
        page = tabs.current_page()
        if page is not None and page.player:
            break
    pump(0.4)

    import weakref
    ref = weakref.ref(tabs.current_page())
    tabs.close_current()
    pump(1.2)
    gc.collect()

    page = ref()
    print("=== ref alive?", page is not None, flush=True)
    if page is None:
        print("OK: page collected", flush=True)
        return 0

    import shiboken6
    valid = shiboken6.isValid(page)
    owned = shiboken6.ownedByPython(page)
    print(f"shiboken6.isValid={valid}  ownedByPython={owned}", flush=True)
    print(f"still parented to {page.parent()!r}", flush=True)
    print(f"tab host children: {len(tabs.body.children())}", flush=True)
    print(f"stacked widget count: {tabs.body.count()}", flush=True)
    cpp_alive = True
    try:
        page.isVisible()
    except RuntimeError:
        cpp_alive = False
    print(f"C++ QWidget still alive: {cpp_alive}", flush=True)
    print(f"player object still held: {getattr(page, 'player', 'n/a')!r}", flush=True)

    print("\n=== referrers of the page ===", flush=True)
    walk(page, depth, set(), "  ")

    print("\n=== referrers of the page.__dict__ (holds its widgets) ===", flush=True)
    d = getattr(page, "__dict__", None)
    if d is not None:
        walk(d, min(depth, 2), set(), "  ")
    # Why does `deleteLater()` not finish the job? Try each candidate fix in
    # isolation so the fix is chosen from evidence rather than from habit.
    print("\n=== teardown experiments ===", flush=True)
    p1 = ref()
    p1.deleteLater()
    app.processEvents()
    pump(0.3)
    print(f"after a second deleteLater(): valid={shiboken6.isValid(p1)}", flush=True)

    try:
        p1.setParent(None)
    except RuntimeError:
        print("   (already gone)", flush=True)
        return 0
    app.processEvents()
    pump(0.3)
    print(f"after setParent(None):         valid={shiboken6.isValid(p1)}", flush=True)

    p1.deleteLater()
    app.processEvents()
    pump(0.3)
    print(f"after deleteLater() unparented: valid={shiboken6.isValid(p1)}", flush=True)
    return 1


if __name__ == "__main__":
    from replaystudio.core import diagnostics

    diagnostics.install()
    diagnostics.hard_exit(main())
