"""录屏工作台的 UI 冒烟：只搭界面，不启动 OBS，不录制。

覆盖：页面能构造、控件都在、改模式会联动窗口下拉、改选项会写进设置、
按钮在未连接时是禁用的（不能出现"点了没反应"的死按钮）。

输出 -> tools/_smoke_record_ui.txt
"""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from replaystudio.core.config import Settings  # noqa: E402
from replaystudio.core.db import Database  # noqa: E402
from replaystudio.core.recording import KIND_MONITOR, KIND_WINDOW  # noqa: E402
from replaystudio.ui.record_page import RecordPage  # noqa: E402

OUT = os.path.join(ROOT, "tools", "_smoke_record_ui.txt")
TMP = r"D:\WorkSpace\WorkBuddySpace\temp\_smoke_rec_ui"

lines: list[str] = []
passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
    msg = f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else "")
    print(msg, flush=True)
    lines.append(msg)


def main() -> int:
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)
    app = QApplication(sys.argv)          # noqa: F841  (needed for widgets)

    db = Database(os.path.join(TMP, "index.db"))
    settings = Settings(db)
    page = RecordPage(db, settings)
    page.show()

    check(page.rec is not None, "Recorder 已创建")
    check(page.rec.state == "idle", "初始状态是 idle", page.rec.state)

    # ---- 控件齐全
    for name in ("cb_kind", "cb_window", "cb_container", "ck_cursor", "ck_desktop",
                 "ck_mic", "ed_dir", "b_connect", "b_start", "b_pause", "b_stop",
                 "lbl_time", "lbl_state"):
        check(hasattr(page, name), f"控件存在: {name}")

    check(page.cb_container.count() == 6, "容器下拉有 6 个已验证选项",
          str(page.cb_container.count()))
    check(page.cb_kind.count() == 3, "采集模式有 3 种（屏幕/窗口/游戏）")

    # ---- 未连接时不能开录
    check(not page.b_start.isEnabled(), "未连接时「开始录制」是禁用的")
    check(not page.b_stop.isEnabled(), "未连接时「停止」是禁用的")
    check(page.b_connect.isEnabled(), "「连接 OBS」可点")

    # ---- 模式联动
    page.cb_kind.setCurrentIndex(page.cb_kind.findData(KIND_MONITOR))
    check(not page.cb_window.isEnabled(), "屏幕模式下窗口下拉禁用")
    page.cb_kind.setCurrentIndex(page.cb_kind.findData(KIND_WINDOW))
    check(page.cb_window.isEnabled(), "切到窗口模式后窗口下拉可用")
    check(page.cb_window.count() >= 1, "窗口列表非空", str(page.cb_window.count()))

    # ---- 设置往返
    page.cb_container.setCurrentIndex(2)          # MKV
    page.ck_mic.setChecked(True)
    page.ed_dir.setText(r"D:\WorkSpace\WorkBuddySpace\temp\_smoke_rec_ui\out")
    page._save_settings()
    check(settings.get("record_format") == page.cb_container.currentData(),
          "容器选择写进设置", str(settings.get("record_format")))
    check(int(settings.get("record_mic", 0)) == 1, "麦克风勾选写进设置")
    check(settings.get("record_dir", "").endswith("out"), "输出目录写进设置")
    check(page.rec.spec.container == page.cb_container.currentData(),
          "设置同步到了 Recorder.spec", page.rec.spec.container)
    check(page.rec.spec.mic is True, "麦克风同步到 spec")
    check(page.rec.spec.kind == KIND_WINDOW, "采集模式同步到 spec")

    # ---- 重新载入后能还原
    raw = db.get_setting("settings_json", "")
    check("mkv" in raw, "设置已落库", raw[:160])
    page2 = RecordPage(db, Settings(db))
    check(page2.cb_container.currentData() == page.cb_container.currentData(),
          "重开页面后容器选择被还原", str(page2.cb_container.currentData()))
    check(page2.ck_mic.isChecked(), "重开页面后麦克风勾选被还原")

    # ---- 计时格式
    from replaystudio.ui.record_page import _fmt
    check(_fmt(0) == "00:00:00", "计时格式 0", _fmt(0))
    check(_fmt(65_000) == "00:01:05", "计时格式 65s", _fmt(65_000))
    check(_fmt(3_661_000) == "01:01:01", "计时格式 1h1m1s", _fmt(3_661_000))

    lines.append(f"\n合计 {passed}/{passed + failed} 项通过")
    print(f"\n合计 {passed}/{passed + failed} 项通过")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
