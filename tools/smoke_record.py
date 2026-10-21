"""Phase 3 端到端：用 Recorder 真的录一段，并确认它进了本地库。

走完整链路：启动 OBS -> 握手 -> 建采集源 -> StartRecord -> 等 STARTED ->
录 2 秒 -> StopRecord -> 等 STOPPED -> 拿到 outputPath -> 文件存在 ->
ffprobe 能解 -> 数据库里有这条记录 -> 干净退出 OBS。

每一步都有超时，任何一步卡住都会失败退出而不是干等。

输出 -> tools/_smoke_record.txt
"""
from __future__ import annotations

import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

from replaystudio.core import obs_paths, recording  # noqa: E402
from replaystudio.core.db import Database  # noqa: E402
from replaystudio.core.recording import IDLE, RECORDING  # noqa: E402

OUT = os.path.join(ROOT, "tools", "_smoke_record.txt")
TMP = r"D:\WorkSpace\WorkBuddySpace\temp\_smoke_record"
REC_DIR = os.path.join(TMP, "rec")
DB_PATH = os.path.join(TMP, "index.db")

lines: list[str] = []
passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""),
          flush=True)
    lines.append(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def pump(app, until, timeout: float, tick=0.05) -> bool:
    """Run the event loop until `until()` is true or we run out of time."""
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if until():
            return True
        time.sleep(tick)
    return until()


def main() -> int:
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(REC_DIR, exist_ok=True)

    app = QCoreApplication(sys.argv)
    if not obs_paths.obs_home():
        print("SKIP: 没有 vendor/obs 便携版，跳过录制回归（不算失败）")
        return 0

    db = Database(DB_PATH)
    rec = recording.Recorder(db, None)
    rec.spec.output_dir = REC_DIR

    states: list[str] = []
    errors: list[str] = []
    finished: list[str] = []
    conn: list[tuple[bool, str]] = []

    rec.stateChanged.connect(states.append)
    rec.error.connect(errors.append)
    rec.finished.connect(finished.append)
    rec.connectionChanged.connect(lambda ok, msg: conn.append((ok, msg)))

    check(rec.available, "便携版 OBS 可用")

    rec.connect_obs()
    check(pump(app, lambda: any(ok for ok, _ in conn), 60),
          "连接并握手成功", str(conn[-1] if conn else ""))
    if not rec.client or not rec.client.ready:
        _done(rec, app)
        return 2

    rec.start()
    check(pump(app, lambda: rec.state == RECORDING, 30),
          "进入录制状态", f"state={rec.state} states={states}")
    if rec.state != RECORDING:
        check(False, "录制未能开始", f"errors={errors}")
        _done(rec, app)
        return 2

    time.sleep(2.0)
    app.processEvents()

    rec.stop()
    check(pump(app, lambda: finished and rec.state == IDLE, 30),
          "停止并回到空闲", f"state={rec.state}")

    path = finished[0] if finished else ""
    check(bool(path), "StopRecord 给出了 outputPath", path)
    check(bool(path) and os.path.isfile(path), "产出文件存在")
    if path and os.path.isfile(path):
        size = os.path.getsize(path)
        check(size > 100_000, "产出文件不是空的", f"{size/1024:.0f} KB")
        info = recording.probe_file(path)
        check(info.get("duration_ms", 0) > 500, "ffprobe 读出时长",
              f"{info.get('duration_ms')} ms")
        check(bool(info.get("width")) and bool(info.get("height")), "ffprobe 读出分辨率",
              f"{info.get('width')}x{info.get('height')}")

    # 入库
    rows, total = db.query_videos({"source": "record", "sort": "最新"}, 50, 0) \
        if hasattr(db, "query_videos") else ([], 0)
    found = [r for r in rows if r.get("path") == path] if path else []
    check(bool(found), "录制文件已入库到本地视频", f"{len(rows)} 条 record 记录")
    if found:
        r = found[0]
        check(bool(r.get("duration_ms")), "入库记录带时长", f"{r.get('duration_ms')} ms")

    check(not errors, "过程中没有报错", str(errors[:2]))

    _done(rec, app)
    return 0 if failed == 0 else 2


def _done(rec, app):
    try:
        rec.disconnect_obs()
    except Exception:  # noqa: BLE001
        pass
    left = []
    try:
        with os.scandir(obs_paths.sentinel_dir(obs_paths.obs_home())) as it:
            left = [e.name for e in it if e.is_file()]
    except OSError:
        pass
    from replaystudio.core import obs_launcher
    check(not obs_launcher.pids() and not left,
          "收尾：OBS 已退出且无残留标记", f"pids={obs_launcher.pids()} sentinel={left}")
    lines.append(f"\n合计 {passed}/{passed + failed} 项通过")
    print(f"\n合计 {passed}/{passed + failed} 项通过")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
