"""Phase 2 screenshots: playlist tab, subtitle menu, playback settings.

Runs a real window (libmpv needs one) and grabs:
  01-player-playlist.png  – player page, playlist tab active
  02-player-submenu.png   – subtitle track menu open (non-blocking popup)
  03-settings-playback.png– settings scrolled to playback + association
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

SHOT_DIR = os.path.join(ROOT, "docs", "screenshots", "phase2")
SAMPLE = os.path.join(ROOT, "tests", "_media", "phase2-sample.mkv")
SRT = os.path.join(ROOT, "tests", "_media", "sample.srt")

os.makedirs(SHOT_DIR, exist_ok=True)


def pump(app, seconds: float):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    app = QApplication([])
    from replaystudio.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    pump(app, 0.8)

    # a 3-item context so the playlist tab has something to show
    base = win.make_row(SAMPLE)
    rows = []
    for i, name in enumerate(["四杀时刻 · 盐海矿镇", "残局 1v3 · 霓虹町", "ACE 回合 · 亚述"]):
        r = dict(base)
        r["video_id"] = base["video_id"] + f"-{i}"
        r["vname"] = name
        rows.append(r)
    win.open_player(rows[0], context=rows)
    pump(app, 3.5)
    pp = win.player_page
    pp.player.add_subtitle(SRT)
    pump(app, 1.0)
    pp.seek_to(4.0)
    pump(app, 0.8)

    # --- 01: playlist tab
    pp._switch_tab("list")
    pump(app, 0.8)
    win.grab().save(os.path.join(SHOT_DIR, "01-player-playlist.png"))
    print("shot 01", flush=True)

    # --- 02: subtitle menu. A popup is a separate top-level window, so
    # win.grab() would miss it and a full-screen grab would capture unrelated
    # desktop content. Instead we build the menu without exec()ing it, render
    # it standalone, and paint it onto the window grab at the button anchor.
    pp._switch_tab("info")
    pump(app, 0.5)
    base_img = win.grab()
    menu = pp._build_sub_menu()
    if menu is not None:
        menu.ensurePolished()
        menu.adjustSize()
        mimg = menu.grab()
        if mimg.isNull():
            menu.show()
            app.processEvents()
            mimg = menu.grab()
            menu.hide()
        if not mimg.isNull():
            anchor = pp.btn_sub.mapTo(win, pp.btn_sub.rect().topLeft())
            mw, mh = mimg.width() / mimg.devicePixelRatio(), \
                mimg.height() / mimg.devicePixelRatio()
            mx = max(6, min(anchor.x(), base_img.width() - mw - 6))
            my = max(6, anchor.y() - mh - 6)
            from PySide6.QtCore import QPoint
            from PySide6.QtGui import QPainter
            p = QPainter(base_img)
            p.drawPixmap(QPoint(int(mx), int(my)), mimg)
            p.end()
        menu.deleteLater()
    base_img.save(os.path.join(SHOT_DIR, "02-player-submenu.png"))
    print("shot 02", flush=True)

    # --- 04: error overlay (before any shutdown, so mpv is still alive)
    garbage = os.path.join(ROOT, "tests", "_media", "broken-garbage.mp4")
    if os.path.isfile(garbage):
        win.open_paths([garbage])
        pump(app, 3.0)
        win.grab().save(os.path.join(SHOT_DIR, "04-error-overlay.png"))
        print("shot 04", flush=True)

    # --- 03: settings → playback + association
    win.navigate("settings")
    pump(app, 0.5)
    sp = win.settings_page
    sp.scroll.ensureWidgetVisible(sp.b_assoc)
    pump(app, 0.8)
    win.grab().save(os.path.join(SHOT_DIR, "03-settings-playback.png"))
    print("shot 03", flush=True)

    pp.shutdown()
    win.thumbs.pool.waitForDone(3000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
