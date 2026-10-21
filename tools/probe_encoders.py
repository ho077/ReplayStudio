"""把 OBS 简单输出模式下真正认的取值试出来。

SetProfileParameter 是直接往 ini 里写，不做任何校验 —— 写个非法值它也返回成功，
只有真的去录一次才知道 OBS 认不认。所以这里对每个候选值：写进去 -> 录 1.5 秒 ->
停 -> 看 StopRecord 有没有给出 outputPath、文件在不在、ffprobe 认不认。

测完把 RecEncoder 恢复成探测前的值。

输出 -> tools/_encoders.txt
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

OUT = os.path.join(ROOT, "tools", "_encoders.txt")
REC_DIR = r"D:\WorkSpace\WorkBuddySpace\temp\_obs_enc"
log: list[str] = []

ENCODERS = ["nvenc", "x264", "qsv", "amd", "nvenc_hevc", "av1_nvenc", "hevc_nvenc"]
FORMATS = ["hybrid_mp4", "mp4", "mkv", "mov", "flv", "ts"]
QUALITIES = ["Stream", "High", "Lossless"]


def say(s: str):
    log.append(s)
    print(s, flush=True)


def probe(client, app, category: str, name: str, value: str) -> bool:
    client.call_sync("SetProfileParameter",
                     {"parameterCategory": category, "parameterName": name,
                      "parameterValue": value})
    read = client.call_sync("GetProfileParameter",
                            {"parameterCategory": category, "parameterName": name})
    stored = (read[0] or {}).get("parameterValue") if read else None
    r, err = client.call_sync("StartRecord", timeout=8.0)
    if err:
        say(f"  {value:14s} 存为={stored!r}  StartRecord 失败: {err}")
        client.call_sync("StopRecord")
        return False

    time.sleep(1.5)
    for _ in range(30):                     # pump so events/responses land
        app.processEvents()
        time.sleep(0.05)
    stop, serr = client.call_sync("StopRecord", timeout=8.0)
    path = (stop or {}).get("outputPath") or ""
    if not path or not os.path.isfile(path):
        say(f"  {value:14s} 存为={stored!r}  无产出文件 ({serr or 'outputPath 为空'})")
        return False

    size = os.path.getsize(path)
    codec = _codec_of(path)
    say(f"  {value:14s} 存为={stored!r}  OK  {size/1024:.0f} KB  codec={codec}")
    try:
        os.remove(path)
    except OSError:
        pass
    return True


def _codec_of(path: str) -> str:
    try:
        done = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace")
        return (done.stdout or "").strip() or "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


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
    box = {"ok": False}
    client.identified.connect(lambda _i: box.__setitem__("ok", True))
    client.open()
    end = time.time() + 15
    while time.time() < end and box["ok"] is False:
        app.processEvents()
        time.sleep(0.05)
    if not box["ok"]:
        say("[FAIL] 握手失败")
        obs_launcher.stop(home=home)
        return 2

    client.call_sync("SetRecordDirectory", {"recordDirectory": REC_DIR})
    cur = client.call_sync("GetProfileParameter",
                           {"parameterCategory": "SimpleOutput",
                            "parameterName": "RecEncoder"})
    original = ((cur[0] or {}).get("parameterValue") if cur else "") or "nvenc"
    say(f"原始 RecEncoder = {original!r}")

    say("")
    say("== RecEncoder 候选 ==")
    good: list[str] = []
    for enc in ENCODERS:
        if probe(client, app, "SimpleOutput", "RecEncoder", enc):
            good.append(enc)

    client.call_sync("SetProfileParameter",
                     {"parameterCategory": "SimpleOutput",
                      "parameterName": "RecEncoder", "parameterValue": original})

    say("")
    say("== RecFormat2（容器）候选 ==")
    for fmt in FORMATS:
        probe(client, app, "SimpleOutput", "RecFormat2", fmt)

    client.call_sync("SetProfileParameter",
                     {"parameterCategory": "SimpleOutput",
                      "parameterName": "RecFormat2", "parameterValue": "hybrid_mp4"})

    say("")
    say("== RecQuality 候选 ==")
    for q in QUALITIES:
        probe(client, app, "SimpleOutput", "RecQuality", q)

    client.call_sync("SetProfileParameter",
                     {"parameterCategory": "SimpleOutput",
                      "parameterName": "RecQuality", "parameterValue": "Stream"})

    say("")
    say(f"可用的 RecEncoder: {good}")

    obs_launcher.stop(client, home)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
