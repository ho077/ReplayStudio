"""Cutting Qt connections so a widget can actually be freed.

Why this module exists
----------------------
The obvious way to drop every connection is ``obj.disconnect()``. In PySide6
that overload does not exist: it raises

    TypeError: QObject.disconnect(): not enough arguments

``tools/probe_disconnect.py`` measures this against the shipped PySide6. Our
teardown code wrapped the call in ``except (RuntimeError, TypeError)``, so the
error disappeared and nothing was ever disconnected. A tab could be "closed"
and its whole widget tree — including the native mpv surface — stayed alive for
the rest of the session, which is what ``tools/probe_theme_leak.py`` sees.

The zero-argument form *does* exist on a **SignalInstance** (``btn.clicked``),
so each signal has to be disarmed one at a time. There is no public "list my
connections" API, so we walk the meta-object and ask each declared signal for
its instance.
"""
from __future__ import annotations

import warnings

from PySide6.QtCore import QMetaMethod, QObject, SignalInstance
from PySide6.QtWidgets import QWidget


def signal_instances(obj: QObject) -> list[SignalInstance]:
    """Every signal `obj` declares, as a bound SignalInstance.

    Includes the ones inherited from QWidget/QObject (`destroyed`,
    `objectNameChanged`, …) because those are exactly where a long-lived parent
    tends to be connected into a dead child.
    """
    out: list[SignalInstance] = []
    meta = obj.metaObject()
    for i in range(meta.methodCount()):
        method = meta.method(i)
        if method.methodType() is not QMetaMethod.MethodType.Signal:
            continue
        try:
            name = method.name().data().decode("utf-8", "replace")
        except Exception:                                  # noqa: BLE001
            continue
        if not name:
            continue
        try:
            sig = getattr(obj, name, None)
        except RuntimeError:            # C++ half already destroyed
            continue
        if isinstance(sig, SignalInstance):
            out.append(sig)
    return out


def disconnect_object(obj: QObject) -> int:
    """Drop every slot listening to any of `obj`'s signals.

    Returns how many signals were cleared, which the diagnostics log uses to
    prove the teardown ran.
    """
    cut = 0
    for sig in signal_instances(obj):
        try:
            with warnings.catch_warnings():
                # A signal nobody ever connected reports
                # "Failed to disconnect (None)". That is not a problem, it is
                # just PySide6 being chatty about a no-op we asked for.
                warnings.simplefilter("ignore", RuntimeWarning)
                sig.disconnect()
            cut += 1
        except (RuntimeError, TypeError):
            pass
    return cut


def disconnect_tree(root: QObject) -> int:
    """Disconnect `root` and every QObject below it — **do not use on a page**.

    Kept because it is the reproducible form of a bug, not because anything
    ships it. Disarming every child widget also removes the connections Qt
    makes *inside* its own composite widgets (scroll areas, item views, …), and
    the process then dies of an access violation at the next
    `QApplication::setStyleSheet`. tools/probe_crash_bisect.py shows it:
    close a tab with this, switch the palette, crash. Same run with
    `disconnect_object(root)` instead, no crash.

    Destroying children (`purge_children`) is both enough and safe — a
    connection cannot outlive its sender — so that is what PlayerPage uses.
    """
    cut = disconnect_object(root)
    try:
        children = root.findChildren(QObject)
    except RuntimeError:
        return cut
    for child in children:
        cut += disconnect_object(child)
    return cut


def purge_children(widget: QWidget) -> int:
    """Delete `widget`'s immediate children.

    Layouts own widgets, so letting ``deleteLater()`` wait for the parent is
    normally right. It is not right when the parent is the thing we are trying
    to get rid of: Qt only destroys a QWidget's C++ object when the Python
    wrapper goes away, and the wrapper stays alive for as long as any lambda
    below it still points at the page. Deleting the children is what lets the
    cycle break even if some connection slipped through.
    """
    cut = 0
    for child in widget.children():
        if isinstance(child, QObject):
            cut += 1
            try:
                child.deleteLater()
            except RuntimeError:
                pass
    return cut
