"""探测 Advanced 输出模式下编码器到底能不能换。

背景：Simple 模式试过了 —— RecEncoder 设成 av1_nvenc / hevc_nvenc 之后录出来
依然是 h264，说明 Simple 的 RecQuality="Stream"（录制复用直播设置）会让
RecEncoder 完全不起作用；而 RecQuality 改成 High / Lossless 直接让 StartRecord
失败（501）。所以想要真正能选编码器，只能切到 Advanced 模式。

Advanced 用的键名是 AdvOut.*，编码器是 obs-ffmpeg 的编码器 ID。

输出 -> tools/_adv_out.txt
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

OUT = os.path.join(ROOT, "tools", "_adv_out.txt")
REC_DIR = r"D:\WorkSpace\WorkBuddySpace\temp\_obs_adv"
log: list[str] = []

# obs-ffmpeg 注册的编码器 ID（OBS 32.2.2）
ENCODERS = ["h264_nvenc", "hevc_nvenc", "av1_nvenc", "obs_x264",
            "h264_qsv", "h264_amf"]
FORMATS = ["mp4", "mkv", "hybrid_mp4", "mov"]


def say(s: str):
    log.append(s)
    print(s, flush=True)


def codec_of(path: str) -> str:
    try:
        done = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace")
        return (done.stdout or "").strip() or "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


def set_param(client, cat, name, value):
    client.call_sync("SetProfileParameter", {"parameterCategory": cat,
                                             "parameterName": name,
                                             "parameterValue": value})
    got = client.call_sync("GetProfileParameter", {"parameterCategory": cat,
                                                   "parameterName": name})
    return (got[0] or {}).get("parameterValue") if got else None


def try_record(client, app, label: str) -> str:
    r, err = client.call_sync("StartRecord", timeout=8.0)
    if err:
        say(f"  {label:26s} StartRecord 失败: {err}")
        return ""
    time.sleep(1.5)
    for _ in range(30):
        app.processEvents()
        time.sleep(0.05)
    stop, serr = client.call_sync("StopRecord", timeout=8.0)
    path = (stop or {}).get("outputPath") or ""
    if not path or not os.path.isfile(path):
        say(f"  {label:26s} 无产出 ({serr or 'outputPath 为空'})")
        return ""
    size = os.path.getsize(path)
    say(f"  {label:26s} OK  {size/1024:7.0f} KB  codec={codec_of(path)}")
    try:
        os.remove(path)
    except OSError:
        pass
    return path


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
    while time.time() < end and not box["ok"]:
        app.processEvents()
        time.sleep(0.05)
    if not box["ok"]:
        say("[FAIL] 握手失败")
        obs_launcher.stop(home=home)
        return 2

    client.call_sync("SetRecordDirectory", {"recordDirectory": REC_DIR})
    say(f"当前 Output.Mode = {set_param(client, 'Output', 'Mode', 'Advanced')!r}")

    say("")
    say("== AdvOut 基础键（读回，看 OBS 认哪些）==")
    for key in ("RecType", "RecEncoder", "RecFormat", "RecFilePath",
                "RecBitrate", "RecMuxerCustom", "TrackIndex", "RecQuality"):
        got = client.call_sync("GetProfileParameter",
                               {"parameterCategory": "AdvOut", "parameterName": key})
        say(f"  AdvOut.{key:16s} = {(got[0] or {}).get('parameterValue') if got else None!r}")

    say("")
    say("== 编码器候选（Advanced）==")
    set_param(client, "AdvOut", "RecType", "Standard")
    set_param(client, "AdvOut", "RecFormat", "mp4")
    good: list[str] = []
    for enc in ENCODERS:
        stored = set_param(client, "AdvOut", "RecEncoder", enc)
        got_codec = ""
        path = try_record(client, app, f"{enc} (存为 {stored!r})")
        if path:
            got_codec = codec_of(path)
            good.append(f"{enc}->{got_codec}")
        say(f"      → 实际 codec={got_codec}")

    say("")
    say("== 容器候选（Advanced, RecEncoder=上次成功值）==")
    for fmt in FORMATS:
        set_param(client, "AdvOut", "RecFormat", fmt)
        try_record(client, app, f"RecFormat={fmt}")

    say("")
    say("== 恢复 Simple ==")
    set_param(client, "Output", "Mode", "Simple")
    say(f"  Output.Mode 读回 = {set_param(client, 'Output', 'Mode', 'Simple')!r}")
    try_record(client, app, "Simple 回归验证")

    say("")
    say(f"Advanced 编码器实测结果: {good}")

    obs_launcher.stop(client, home)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
