"""Phase 2 end-to-end verification with a real multi-audio / subtitled file.

Checks (all against a real libmpv instance, not mocks):
  1. external file opened via drag&drop path (`open_paths`) actually loads
  2. duration / audio tracks / subtitle tracks are reported by mpv
  3. audio track switching + subtitle track switching take effect
  4. subtitle delay, aspect override, speed are applied and read back
  5. screenshot writes a real PNG
  6. playlist context + play_index switch the loaded file
  7. configurable seek step (settings) is honoured
  8. file association register → is_registered → unregister round-trips
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
# NOTE: deliberately NOT offscreen — libmpv needs a real window handle (--wid)
# to create its vo/gpu-next output. A headless run silently fails to open files.

from PySide6.QtWidgets import QApplication  # noqa: E402

SAMPLE = os.path.join(ROOT, "tests", "_media", "phase2-sample.mkv")
SRT = os.path.join(ROOT, "tests", "_media", "sample.srt")
REPORT = os.path.join(ROOT, "tools", "_phase2.txt")

log: list[str] = []


def say(msg: str):
    log.append(msg)
    print(msg, flush=True)


def pump(app, seconds: float):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def check(name: str, cond: bool, detail: str = "") -> bool:
    say(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def main() -> int:
    if not os.path.isfile(SAMPLE):
        say("样本不存在，请先运行 tools/make_phase2_sample.py")
        return 1

    app = QApplication([])
    from replaystudio.core import assoc
    from replaystudio.ui.main_window import MainWindow

    win = MainWindow()
    win.show()
    pump(app, 0.6)

    results: list[bool] = []

    # ---------------------------------------------------------------- 1 open
    win.open_paths([SAMPLE])
    pump(app, 3.0)
    p = win.player_page.player
    results.append(check("外部文件通过 open_paths 载入", p is not None))
    if p is None:
        return _finish()
    d = p.duration
    results.append(check("获取到真实时长", d > 1.0, f"duration={d:.2f}s"))

    # ------------------------------------------------------------- 2 tracks
    auds = p.audio_tracks()
    subs = p.subtitle_tracks()
    results.append(check("识别到多音轨", len(auds) >= 2, f"audio={len(auds)}"))
    results.append(check("识别到字幕轨道", len(subs) >= 1, f"sub={len(subs)}"))
    titles = [t.get("title") for t in auds]
    say(f"      音轨: {titles}")
    say(f"      字幕: {[t.get('title') or t.get('lang') for t in subs]}")

    # ------------------------------------------------------- 3 switching
    first_a = p.current_audio()
    p.cycle_audio(True)
    pump(app, 0.5)
    second_a = p.current_audio()
    results.append(check("切换音轨生效", first_a != second_a, f"{first_a} -> {second_a}"))

    p.select_subtitle(None)
    pump(app, 0.3)
    off = p.current_subtitle()
    sid = subs[0]["id"]
    p.select_subtitle(sid)
    pump(app, 0.4)
    results.append(check("字幕开关与选择生效",
                         off is None and p.current_subtitle() == sid,
                         f"off={off} on={p.current_subtitle()}"))

    p.add_subtitle(SRT)
    pump(app, 0.6)
    results.append(check("外部 SRT 可挂载", len(p.subtitle_tracks()) >= 2,
                         f"sub={len(p.subtitle_tracks())}"))

    # ------------------------------------------------- 4 delay / aspect / speed
    p.set_sub_delay(-1.5)
    pump(app, 0.3)
    results.append(check("字幕延迟可写回", abs(p.sub_delay + 1.5) < 0.01,
                         f"sub_delay={p.sub_delay}"))
    p.set_sub_delay(0.0)

    p.set_aspect("4:3")
    pump(app, 0.3)
    a43 = p._mpv.video_aspect_override
    p.set_aspect("-1")
    pump(app, 0.3)
    results.append(check("画面比例可覆盖", str(a43).startswith("1.3"),
                         f"override={a43}"))

    p.set_speed(1.5)
    pump(app, 0.3)
    results.append(check("倍速可设置", abs(p.speed - 1.5) < 0.01, f"speed={p.speed}"))
    p.set_speed(1.0)

    # ------------------------------------------------------------- 5 seek step
    win.settings["seek_step"] = 8
    p.seek(2.0)
    pump(app, 0.6)
    before = p.position
    win.player_page.seek_relative(win.settings["seek_step"])
    pump(app, 0.8)
    after = p.position
    results.append(check("按设置的步长跳转", after - before > 5.0,
                         f"{before:.2f} -> {after:.2f} (step=8)"))
    win.settings["seek_step"] = 5

    # ----------------------------------------------------------- 6 screenshot
    out_png = win.player_page.take_screenshot.__name__  # ensure method exists
    win.player_page.take_screenshot()
    pump(app, 1.0)
    pic_dir = os.path.join(os.path.expanduser("~"), "Pictures", "ReplayStudio")
    pngs = [f for f in os.listdir(pic_dir) if f.endswith(".png")] if os.path.isdir(pic_dir) else []
    results.append(check("截图生成真实 PNG", bool(pngs), f"{len(pngs)} 个文件, 最新: "
                         f"{(max(pngs, key=lambda f: os.path.getmtime(os.path.join(pic_dir, f))) if pngs else '-')}"))

    # ------------------------------------------------------------ 7 playlist
    row_b = win.make_row(SAMPLE)
    row_b["vname"] = "列表第二项"
    row_b["video_id"] = row_b["video_id"] + "b"
    win.open_player(row_b, context=[row_b, win.make_row(SAMPLE)])
    pump(app, 1.0)
    n_items = win.player_page.playlist.count()
    results.append(check("播放列表填充上下文", n_items == 2, f"items={n_items}"))
    win.play_index(1)
    pump(app, 2.0)
    results.append(check("点击播放列表可切换",
                         (win.player_page._current or {}).get("vname") != "列表第二项",
                         f"current={(win.player_page._current or {}).get('vname')}"))

    # ------------------------------------------------------------ 8 autoplay
    short = os.path.join(ROOT, "tests", "_media", "container.mp4")
    if os.path.isfile(short):
        row_a, row_b2 = win.make_row(short), win.make_row(SAMPLE)
        row_a["vname"], row_b2["vname"] = "A-短片", "B-长片"
        row_a["video_id"], row_b2["video_id"] = "ROW-A", "ROW-B"
        win.open_player(row_a, context=[row_a, row_b2])

        # Wait for the engine to report THIS clip's duration. Right after a
        # file switch the property can still hold the previous clip's value
        # (or 0); seeking against a stale value jumps past the real end and
        # the EOF never looks the way the test expects. That race — not the
        # product code — is what made this check flaky.
        dur = wait_duration(p, app, below=12.0)
        say(f"      A 片时长 = {dur:.2f}s（切文件后已稳定）")

        ended: list[str] = []

        # NOTE: PySide6 may drop a bare lambda slot, so keep a real reference.
        def _on_end():
            ended.append("ended")

        p.ended.connect(_on_end)
        target = max(0.0, dur - 1.0)
        say(f"      seek -> {target:.2f}s，之后轮询等待结束事件")
        p.seek(target)

        # Poll instead of a fixed sleep: EOF on a 6s clip is not instant and
        # the exact latency depends on mpv's decode pipeline.
        deadline = time.time() + 8.0
        while time.time() < deadline and not ended:
            pump(app, 0.1)
        pump(app, 0.6)          # let the auto-advance handler run
        results.append(check("播放结束事件触发", bool(ended),
                            f"events={ended}"))
        try:
            p.ended.disconnect(_on_end)
        except (RuntimeError, TypeError):
            pass
        results.append(check("自动连播切换到下一项",
                             (win.player_page._current or {}).get("video_id") == "ROW-B",
                             f"current={(win.player_page._current or {}).get('vname')}"))

        # Regression: EOF arrives through two channels (end-file event and the
        # eof-reached property). Without the `_ended_fired` latch both fire and
        # auto-play jumps TWO items. Verify it advances exactly one position.
        rows3 = []
        for i, vid in enumerate(("ROW-1", "ROW-2", "ROW-3")):
            r = dict(row_a)
            r["video_id"] = vid
            r["vname"] = f"第{vid[-1]}段"
            rows3.append(r)
        win.open_player(rows3[0], context=rows3)
        dur3 = wait_duration(p, app, below=12.0)
        say(f"      3 段场景时长 = {dur3:.2f}s")
        p.seek(max(0.0, dur3 - 1.0))
        deadline = time.time() + 8.0
        diag = []
        while time.time() < deadline:
            pump(app, 0.1)
            diag.append(f"pos={p.position:.1f} latch={getattr(p, '_ended_fired', '?')} "
                        f"cur={(win.player_page._current or {}).get('video_id')}")
            if (win.player_page._current or {}).get("video_id") == "ROW-2":
                break
        say("      诊断: " + " | ".join(diag[::12][:6]))
        pump(app, 1.2)   # give a (buggy) second advance a chance to happen
        jumped = (win.player_page._current or {}).get("video_id")
        results.append(check("连播只前进一项（不跳过）", jumped == "ROW-2",
                             f"current={jumped}"))
    else:
        say("      (跳过自动连播：缺少短样本，先运行 tools/smoke_containers.py)")

    # --------------------------------------------------------- 9 association
    ok1, m1 = assoc.register([".mkv", ".mp4"])
    registered = assoc.is_registered()
    ok2, m2 = assoc.unregister()
    cleaned = not assoc.is_registered()
    results.append(check("文件关联注册/撤销闭环",
                         ok1 and registered and ok2 and cleaned, f"{m1} | {m2}"))

    win.player_page.shutdown()
    win.thumbs.pool.waitForDone(3000)
    return _finish(results)


def wait_duration(player, app, below: float = 12.0, seconds: float = 8.0) -> float:
    """Wait until the player reports a duration for the *current* clip.

    `below` guards against reading the previous clip's length right after a
    switch: our test clips are short, so a value under `below` means the
    property has caught up.
    """
    deadline = time.time() + seconds
    d = player.duration
    while time.time() < deadline and not (0.1 < d < below):
        pump(app, 0.1)
        d = player.duration
    return d


def _finish(results: list[bool] | None = None) -> int:
    results = results or []
    passed = sum(1 for r in results if r)
    say(f"\n合计 {passed}/{len(results)} 项通过")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    return 0 if passed == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
