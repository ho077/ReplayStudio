"""Why does QApplication.setStyleSheet() fault after a tab has been closed?

tools/probe_theme_leak.py 1 12 reproduces it deterministically: open a tab,
close it, switch the palette once → access violation inside
`QApplication::setStyleSheet`. Neither half alone crashes (4 open/close cycles
with no theme switch is fine, 12 theme switches with no tab is fine), so the
damage is done by the *page teardown* and only detonates on the next repolish.

Each case below removes one ingredient, so the one that stops crashing is the
one to fix. Run one case per process — a fault takes the whole interpreter down.

Usage: probe_qss_crash.py <case>
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

QSS_A = "QWidget { background: #16161A; color: #EDEDF0; }"
QSS_B = "QWidget { background: #111820; color: #E6F4F8; }"
CHILD_QSS = "background: #000; border-radius: 10px;"

CASE = int(sys.argv[1]) if len(sys.argv) > 1 else 0


class Page(QWidget):
    """The smallest stand-in for PlayerPage that still has the ingredients."""

    def __init__(self, parent=None, native=False, styled_child=False):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.host = QWidget(self)
        lay.addWidget(self.host)
        if native:
            self.host.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
            self.host.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors,
                                   True)
        if styled_child:
            self.host.setStyleSheet(CHILD_QSS)
        self.label = QLabel("x", self)
        self.label.setStyleSheet(CHILD_QSS)
        lay.addWidget(self.label)


def spin(app, seconds: float):
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def main() -> int:
    app = QApplication([])
    app.setStyleSheet(QSS_A)

    win = QWidget()
    win.setStyleSheet(QSS_A)
    win.resize(600, 400)
    win.show()
    spin(app, 0.3)

    if CASE == 0:
        print("case 0: nothing is ever destroyed (baseline)")
    elif CASE == 1:
        print("case 1: plain widget, create + deleteLater, no native child")
        p = Page(win)
        p.show()
        spin(app, 0.2)
        p.deleteLater()
        spin(app, 0.4)
    elif CASE == 2:
        print("case 2: native-window child, create + deleteLater")
        p = Page(win, native=True)
        p.show()
        spin(app, 0.2)
        p.deleteLater()
        spin(app, 0.4)
    elif CASE == 3:
        print("case 3: native child + per-widget stylesheet on the children")
        p = Page(win, native=True, styled_child=True)
        p.show()
        spin(app, 0.2)
        p.deleteLater()
        spin(app, 0.4)
    elif CASE == 4:
        print("case 4: native child, unpolish before the widget goes away")
        p = Page(win, native=True, styled_child=True)
        p.show()
        spin(app, 0.2)
        app.style().unpolish(p)
        app.style().unpolish(p.host)
        app.style().unpolish(p.label)
        p.deleteLater()
        spin(app, 0.4)
    elif CASE == 5:
        print("case 5: native child, clear the stylesheets before deleting")
        p = Page(win, native=True, styled_child=True)
        p.show()
        spin(app, 0.2)
        p.host.setStyleSheet("")
        p.label.setStyleSheet("")
        p.setStyleSheet("")
        p.deleteLater()
        spin(app, 0.4)
    elif CASE == 6:
        print("case 6: native child removed from the parent tree first")
        p = Page(win, native=True, styled_child=True)
        p.show()
        spin(app, 0.2)
        p.host.setParent(None)
        p.label.setParent(None)
        spin(app, 0.2)
        p.deleteLater()
        spin(app, 0.4)
    else:
        print(f"unknown case {CASE}")
        return 2

    spin(app, 0.2)
    print("  -> switching the app stylesheet")
    app.setStyleSheet(QSS_B)
    spin(app, 0.3)
    print("  -> survived")
    return 0


if __name__ == "__main__":
    sys.exit(main())
