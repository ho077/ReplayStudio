"""Which teardown call actually breaks the page ⇄ widget closure cycle?

Three separate things have to go right for a closed tab to disappear, and it is
worth knowing which one is failing rather than patching all of them blind:

  1. the page's own signals must stop reaching outward slots
  2. every child's slots must stop reaching back into the page
  3. the engine's slots must stop reaching into the page

Usage: probe_disconnect.py
"""
from __future__ import annotations

import gc
import sys
import weakref

ROOT = sys.argv[0]
from PySide6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

app = QApplication([])


def survives(make) -> bool:
    """Does anything still point at the object after we tear it down?"""
    obj = make()
    ref = weakref.ref(obj)
    del obj
    gc.collect()
    return ref() is not None


def case(title: str, fn) -> None:
    try:
        print(f"{title:<46} {'SURVIVES' if fn() else 'released'}", flush=True)
    except Exception as exc:                      # noqa: BLE001
        print(f"{title:<46} raised {exc!r}", flush=True)


class Holder(QWidget):
    """Mimics PlayerPage: `self` captured inside a child's slot."""

    def __init__(self):
        super().__init__()
        self.btn = QPushButton("x", self)
        self.btn.clicked.connect(lambda _=False: self.marker())
        self.value = 0

    def marker(self):
        self.value += 1


def raw():
    h = Holder()
    return h


def after_delete_later():
    h = Holder()
    h.deleteLater()
    app.processEvents()
    return "see below"


# ---- 1. nothing at all
case("plain Holder (baseline)", lambda: survives(Holder))


# ---- 2. only deleteLater, i.e. what the old code did
def only_delete_later():
    h = Holder()
    h.deleteLater()
    app.processEvents()
    return True                      # object is gone from Python, but see 3


print("\n-- what release() does today --", flush=True)


# ---- 3. disconnect on every child, then drop the last Python reference
def disconnect_children():
    from PySide6.QtCore import QObject
    h = Holder()
    ref = weakref.ref(h)
    for child in h.findChildren(QObject):
        try:
            child.disconnect()
        except (RuntimeError, TypeError) as exc:
            print(f"      child.disconnect() -> {exc!r}", flush=True)
    try:
        h.disconnect()
    except (RuntimeError, TypeError) as exc:
        print(f"      self.disconnect() -> {exc!r}", flush=True)
    del h
    gc.collect()
    return ref() is not None


case("weakref after child.disconnect()", disconnect_children)


# ---- 4. same, but the wrapper is what we check after deleteLater too
def disconnect_then_delete():
    from PySide6.QtCore import QObject
    h = Holder()
    ref = weakref.ref(h)
    for child in h.findChildren(QObject):
        try:
            child.disconnect()
        except (RuntimeError, TypeError):
            pass
    h.deleteLater()
    app.processEvents()
    del h
    gc.collect()
    gc.collect()
    return ref() is not None


case("weakref after children.disconnect + deleteLater", disconnect_then_delete)


# ---- 5. explicit signal.disconnect() instead of the blanket call
def explicit_signal_disconnect():
    h = Holder()
    ref = weakref.ref(h)
    try:
        h.btn.clicked.disconnect()
    except (RuntimeError, TypeError) as exc:
        print(f"      clicked.disconnect() -> {exc!r}", flush=True)
    del h
    gc.collect()
    return ref() is not None


case("weakref after btn.clicked.disconnect()", explicit_signal_disconnect)


# ---- 6. how many receivers are actually left?
def receivers():
    h = Holder()
    before = h.btn.receivers(h.btn.clicked)
    try:
        h.btn.disconnect()
    except (RuntimeError, TypeError):
        pass
    after = h.btn.receivers(h.btn.clicked)
    print(f"      receivers before={before} after={after}", flush=True)
    return after > 0


case("receivers() still non-zero after disconnect", receivers)


# ---- 7. does dropping the widgets help? (remove + deleteLater the children)
def drop_children():
    h = Holder()
    ref = weakref.ref(h)
    h.btn.setParent(None)
    h.btn.deleteLater()
    app.processEvents()
    del h
    gc.collect()
    return ref() is not None


case("weakref after reparenting the child away", drop_children)

print("\n(exit)", flush=True)
