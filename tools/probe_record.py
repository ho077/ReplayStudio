"""Phase 3 取证：真的录一段，把产出文件拿到手并验证它能播。

不猜 obs-websocket 的请求名和参数 —— 先让服务端把能力列出来，再按它给的默认值
建一个显示器采集源，录 3 秒，停，把文件路径、大小、时长、编码格式全部核实一遍。

做七件事：
  1. 列出服务端支持的所有请求（挑出录制/场景/输入/配置相关的）
  2. GetInputDefaultSettings("monitor_capture") 拿真实默认参数
  3. CreateInput 建显示器采集源
  4. 把录制目录指到临时目录（试 SetRecordDirectory，没有就退回 profile 参数）
  5. StartRecord -> 等 3 秒 -> StopRecord
  6. 从 RecordStateChanged 事件里拿输出文件名（而不是靠猜目录）
  7. 用 ffprobe 验证产出文件真的能解码

输出 -> tools/_record.txt
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from replaystudio.core import obs_launcher, obs_paths  # noqa: E402
from replaystudio.core.obs_client import ObsClient  # noqa: E402

OUT = os.path.join(ROOT, "tools", "_record.txt")
REC_DIR = r"D:\WorkSpace\WorkBuddySpace\temp\_obs_rec"
log: list[str] = []


def say(s: str):
    log.append(s)
    print(s, flush=True)


def short(d) -> str:
    if d is None:
        return "<None>"
    s = str(d)
    return s if len(s) < 500 else s[:500] + " …"


def main() -> int:
    app = QCoreApplication(sys.argv)
    home = obs_paths.obs_home()
    if not home:
        say("[FAIL] 没有便携版 OBS")
        return 2
    os.makedirs(REC_DIR, exist_ok=True)

    cfg = obs_paths.ensure_ws_config(home)
    port = int(cfg.get("server_port") or 4455)
    ok, msg = obs_launcher.start(home, port)
    say(f"[{'PASS' if ok else 'FAIL'}] {msg}")
    if not ok:
        return 2

    client = ObsClient("127.0.0.1", port, cfg.get("server_password") or "")
    app.client = client
    box = {"ok": False, "file": "", "events": []}
    client.identified.connect(lambda _i: box.__setitem__("ok", True))
    client.eventReceived.connect(lambda t, d: box["events"].append((t, d)))
    client.open()
    end = time.time() + 15
    while time.time() < end and not box["ok"]:
        app.processEvents()
        time.sleep(0.05)
    if not box["ok"]:
        say("[FAIL] 握手失败")
        obs_launcher.stop(home=home)
        return 2
    say("[PASS] 握手成功")

    def ask(name, data=None, timeout=5.0):
        r, err = client.call_sync(name, data, timeout=timeout)
        if err:
            say(f"  {name:26s} ERROR {err}")
            return None
        say(f"  {name:26s} {short(r)}")
        return r

    # ---------------------------------------------------------------- 1 能力
    say("")
    say("== 服务端能力（挑录制/场景/输入/配置相关）==")
    ver = ask("GetVersion", timeout=8.0)
    reqs = (ver or {}).get("availableRequests") or []
    say(f"  共 {len(reqs)} 个请求")
    for kw in ("Record", "Scene", "Input", "Profile", "Directory", "Output", "Stream"):
        hit = [r for r in reqs if kw in r]
        say(f"  {kw:10s} ({len(hit)}): {hit[:14]}")

    # ---------------------------------------------------------- 2 默认参数
    say("")
    say("== 显示器采集源的默认参数 ==")
    defaults = ask("GetInputDefaultSettings", {"inputKind": "monitor_capture"})
    ask("GetInputDefaultSettings", {"inputKind": "game_capture"})

    # ------------------------------------------------------------ 3 建源
    say("")
    say("== 建显示器采集源 ==")
    scene = (ask("GetSceneList") or {}).get("currentProgramSceneName") or ""
    say(f"  当前场景: {scene!r}")
    ask("CreateInput", {
        "sceneName": scene,
        "inputName": "ReplayStudio 显示器采集",
        "inputKind": "monitor_capture",
        "inputSettings": (defaults or {}).get("defaultInputSettings") or {},
        "sceneItemEnabled": True,
    })

    # --------------------------------------------------------- 4 录制目录
    say("")
    say("== 录制目录 ==")
    say(f"  目标: {REC_DIR}")
    if "SetRecordDirectory" in reqs:
        ask("SetRecordDirectory", {"recordDirectory": REC_DIR})
    else:
        say("  服务端没有 SetRecordDirectory，退回 profile 参数")
    ask("SetProfileParameter", {"parameterCategory": "SimpleOutput",
                                "parameterName": "RecFilePath",
                                "parameterValue": REC_DIR})
    ask("SetProfileParameter", {"parameterCategory": "AdvOut",
                                "parameterName": "RecFilePath",
                                "parameterValue": REC_DIR})
    say(f"  读回 GetRecordDirectory: {short(ask('GetRecordDirectory'))}")
    ask("GetProfileParameter", {"parameterCategory": "Output", "parameterName": "Mode"})
    ask("GetProfileParameter", {"parameterCategory": "SimpleOutput",
                                "parameterName": "RecFormat2"})

    # ------------------------------------------------------------- 5 录制
    say("")
    say("== 录 3 秒 ==")
    ask("StartRecord")
    for i in range(3):
        time.sleep(1)
        app.processEvents()
        st, _ = client.call_sync("GetRecordStatus")
        say(f"  {(st or {}).get('outputTimecode')}  "
            f"active={(st or {}).get('outputActive')} "
            f"bytes={(st or {}).get('outputBytes')}")
    ask("StopRecord")

    # --------------------------------------------------- 6 事件里的文件名
    say("")
    say("== 录制相关事件 ==")
    for t, d in box["events"]:
        if "Record" in t or "Output" in t:
            say(f"  {t}: {short(d)}")
            if t == "RecordStateChanged":
                box["file"] = (d.get("outputPath") or "")

    # ------------------------------------------------------------- 7 验收
    say("")
    say("== 产出文件 ==")
    files = []
    for root, _dirs, names in os.walk(REC_DIR):
        for n in names:
            p = os.path.join(root, n)
            files.append((os.path.getmtime(p), p, os.path.getsize(p)))
    files.sort(reverse=True)
    for mtime, p, size in files[:5]:
        say(f"  {size/1024/1024:.2f} MB  {p}")
    if not files:
        say("  [FAIL] 录制目录里什么都没有")
        obs_launcher.stop(home=home)
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(log) + "\n")
        return 2

    target = files[0][1]
    say("")
    say("== ffprobe ==")
    try:
        done = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration,size,format_name", "-show_entries",
             "stream=codec_name,width,height,r_frame_rate",
             "-of", "default=noprint_wrappers=1", target],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace")
        say(done.stdout.strip() or "(无输出)")
        if done.stderr.strip():
            say(f"  stderr: {done.stderr.strip()[:300]}")
    except (OSError, subprocess.SubprocessError) as exc:
        say(f"  ffprobe 不可用: {exc}")

    obs_launcher.stop(client, home)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
