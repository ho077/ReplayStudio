"""验证一个假设：OBS 的录制输出在启动时就建好了，所以运行中改编码器不生效，
必须重启 OBS。

前两轮探测里，Simple 和 Advanced 两种模式各试了 7/6 个编码器候选，录出来
全是 h264 —— 连 av1_nvenc、hevc_nvenc 也一样。如果只是"参数名写错"，OBS 多半
会直接录失败；它稳定地录出 h264，更像是 output 对象根本没重建。

所以这里：设成 hevc_nvenc -> 重启 OBS -> 录一段 -> 看 codec。
再设回 h264_nvenc -> 重启 -> 录一段 -> 看 codec。两者不同就证明假设成立。

输出 -> tools/_encoder_restart.txt
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

OUT = os.path.join(ROOT, "tools", "_encoder_restart.txt")
REC_DIR = r"D:\WorkSpace\WorkBuddySpace\temp\_obs_restart"
log: list[str] = []


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


def record_once(app, home, port, password, label: str) -> str:
    """Start OBS, record ~1.5s, return the codec ('' on failure)."""
    ok, msg = obs_launcher.start(home, port)
    say(f"  启动：{msg}")
    if not ok:
        return ""
    client = ObsClient("127.0.0.1", port, password)
    app.client = client
    box = {"ok": False}
    client.identified.connect(lambda _i: box.__setitem__("ok", True))
    client.open()
    end = time.time() + 20
    while time.time() < end and not box["ok"]:
        app.processEvents()
        time.sleep(0.05)
    if not box["ok"]:
        say("  握手失败")
        obs_launcher.stop(home=home)
        return ""

    client.call_sync("SetRecordDirectory", {"recordDirectory": REC_DIR})
    r, err = client.call_sync("StartRecord", timeout=8.0)
    if err:
        say(f"  {label}: StartRecord 失败 {err}")
        obs_launcher.stop(client, home)
        return "FAILED"
    time.sleep(1.5)
    for _ in range(30):
        app.processEvents()
        time.sleep(0.05)
    stop, serr = client.call_sync("StopRecord", timeout=8.0)
    path = (stop or {}).get("outputPath") or ""
    codec = ""
    if path and os.path.isfile(path):
        codec = codec_of(path)
        say(f"  {label}: {os.path.getsize(path)/1024:.0f} KB  codec={codec}")
        try:
            os.remove(path)
        except OSError:
            pass
    else:
        say(f"  {label}: 无产出 ({serr or 'outputPath 为空'})")
    obs_launcher.stop(client, home)
    return codec


def main() -> int:
    app = QCoreApplication(sys.argv)
    home = obs_paths.obs_home()
    if not home:
        say("[FAIL] 没有便携版 OBS")
        return 2
    os.makedirs(REC_DIR, exist_ok=True)
    cfg = obs_paths.ensure_ws_config(home)
    port = int(cfg.get("server_port") or 4455)
    password = cfg.get("server_password") or ""

    # OBS 得先跑起来才能通过 websocket 改参数
    obs_launcher.start(home, port)
    client = ObsClient("127.0.0.1", port, password)
    app.client = client
    box = {"ok": False}
    client.identified.connect(lambda _i: box.__setitem__("ok", True))
    client.open()
    end = time.time() + 20
    while time.time() < end and not box["ok"]:
        app.processEvents()
        time.sleep(0.05)
    if not box["ok"]:
        say("[FAIL] 首次握手失败")
        obs_launcher.stop(home=home)
        return 2

    def set_param(cat, name, value):
        client.call_sync("SetProfileParameter", {"parameterCategory": cat,
                                                 "parameterName": name,
                                                 "parameterValue": value})
        got = client.call_sync("GetProfileParameter", {"parameterCategory": cat,
                                                       "parameterName": name})
        return (got[0] or {}).get("parameterValue") if got else None

    say("切到 Advanced 并写参数")
    say(f"  Output.Mode   = {set_param('Output', 'Mode', 'Advanced')!r}")
    say(f"  RecType       = {set_param('AdvOut', 'RecType', 'Standard')!r}")
    say(f"  RecFormat     = {set_param('AdvOut', 'RecFormat', 'mp4')!r}")
    say(f"  RecBitrate    = {set_param('AdvOut', 'RecBitrate', '10000')!r}")
    obs_launcher.stop(client, home)

    say("")
    say("== 第一轮：不重启，直接设 hevc_nvenc 然后录 ==")
    set_hevc = None
    obs_launcher.start(home, port)
    c2 = ObsClient("127.0.0.1", port, password)
    app.client = c2
    b2 = {"ok": False}
    c2.identified.connect(lambda _i: b2.__setitem__("ok", True))
    c2.open()
    end = time.time() + 20
    while time.time() < end and not b2["ok"]:
        app.processEvents()
        time.sleep(0.05)
    c2.call_sync("SetProfileParameter", {"parameterCategory": "AdvOut",
                                         "parameterName": "RecEncoder",
                                         "parameterValue": "hevc_nvenc"})
    got = c2.call_sync("GetProfileParameter", {"parameterCategory": "AdvOut",
                                               "parameterName": "RecEncoder"})
    set_hevc = (got[0] or {}).get("parameterValue")
    say(f"  写入后读回 RecEncoder = {set_hevc!r}")
    c2.call_sync("SetRecordDirectory", {"recordDirectory": REC_DIR})
    r, err = c2.call_sync("StartRecord", timeout=8.0)
    if err:
        say(f"  StartRecord 失败：{err}")
    else:
        time.sleep(1.5)
        for _ in range(30):
            app.processEvents()
            time.sleep(0.05)
        stop, _s = c2.call_sync("StopRecord", timeout=8.0)
        p = (stop or {}).get("outputPath") or ""
        say(f"  不重启录出来 codec = {codec_of(p) if p and os.path.isfile(p) else '无产出'}")
        if p and os.path.isfile(p):
            os.remove(p)
    obs_launcher.stop(c2, home)

    say("")
    say("== 第二轮：hevc_nvenc 已写入，重启 OBS 后再录 ==")
    hevc = record_once(app, home, port, password, "重启后(hevc_nvenc)")

    say("")
    say("== 第三轮：改回 h264_nvenc，重启后再录 ==")
    obs_launcher.start(home, port)
    c3 = ObsClient("127.0.0.1", port, password)
    app.client = c3
    b3 = {"ok": False}
    c3.identified.connect(lambda _i: b3.__setitem__("ok", True))
    c3.open()
    end = time.time() + 20
    while time.time() < end and not b3["ok"]:
        app.processEvents()
        time.sleep(0.05)
    c3.call_sync("SetProfileParameter", {"parameterCategory": "AdvOut",
                                         "parameterName": "RecEncoder",
                                         "parameterValue": "h264_nvenc"})
    obs_launcher.stop(c3, home)
    h264 = record_once(app, home, port, password, "重启后(h264_nvenc)")

    say("")
    say("== 结论 ==")
    say(f"  不重启 = {set_hevc!r} 录出 h264（见上）")
    say(f"  重启后 hevc_nvenc -> {hevc or '?'}")
    say(f"  重启后 h264_nvenc -> {h264 or '?'}")
    if hevc and h264 and hevc != h264:
        say("  => 编码器确实生效，只是必须重启 OBS 才会重建 output")
    else:
        say("  => 即使重启也没换掉，Advanced 的 RecEncoder 在本机不起效")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
