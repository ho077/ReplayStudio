"""Phase 3 第一手取证：真的连上 obs-websocket，把协议细节和 OBS 的配置键名问出来。

做五件事：
  1. 用 vendor/obs 的便携版启动 OBS（先清掉 .sentinel 残留，否则 OBS 会判定
     "上次异常退出" 并弹对话框拦住启动 —— 对话框里选了安全模式的话
     obs-websocket 根本不加载）
  2. 完成 obs-websocket v5 握手（Hello -> Identify -> Identified）
  3. 打印版本、场景、可用输入类型、视频设置、录制目录
  4. 逐个试探 profile 参数键名，把 OBS 真正认的键名和当前值列出来
  5. 正常退出 OBS，确认它把自己的运行标记收干净了、且日志里没有 safe mode

结果写入 tools/_obs_ws.txt。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from replaystudio.core import obs_launcher, obs_paths  # noqa: E402
from replaystudio.core.obs_client import ObsClient  # noqa: E402

OUT = os.path.join(ROOT, "tools", "_obs_ws.txt")
log: list[str] = []


def say(s: str):
    log.append(s)
    print(s, flush=True)


def main() -> int:
    app = QCoreApplication(sys.argv)
    home = obs_paths.obs_home()
    if not home:
        say("[FAIL] 没有便携版 OBS：vendor/obs/bin/64bit/obs64.exe")
        return 2

    cfg = obs_paths.ensure_ws_config(home)
    port = int(cfg.get("server_port") or obs_paths.DEFAULT_PORT)
    password = cfg.get("server_password") or ""
    say(f"OBS      : {obs_paths.obs_exe(home)}")
    say(f"ws config: {obs_paths.ws_config_path(home)}")
    say(f"port={port} auth_required={cfg.get('auth_required')} "
        f"password={'*' * len(password)}")

    client: ObsClient | None = None
    code = 2
    try:
        ok, msg = obs_launcher.start(home, port)
        say(f"[{'PASS' if ok else 'FAIL'}] {msg}")
        if not ok:
            return 2

        client = ObsClient("127.0.0.1", port, password)
        app.client = client
        box = {"ok": False}
        client.identified.connect(lambda _i: box.__setitem__("ok", True))
        client.failed.connect(lambda m: say(f"[client] {m}"))
        client.open()

        import time
        end = time.time() + 15
        while time.time() < end and not box["ok"]:
            app.processEvents()
            time.sleep(0.05)

        if not box["ok"]:
            say("[FAIL] 握手未成功（15s）")
            return 2
        say("[PASS] obs-websocket 握手成功（Hello -> Identify -> Identified）")

        def ask(name, *args):
            data, err = client.call_sync(name, args[0] if args else None)
            if err:
                say(f"  {name:22s} ERROR {err}")
                return None
            say(f"  {name:22s} {_short(data)}")
            return data

        say("")
        say("== 基本信息 ==")
        ask("GetVersion")
        ask("GetVideoSettings")
        ask("GetRecordDirectory")
        ask("GetRecordStatus")

        say("")
        say("== 场景与输入 ==")
        ask("GetSceneList")
        kinds = ask("GetInputKindList")
        if kinds:
            ks = kinds.get("inputKinds") or []
            say(f"  input kinds ({len(ks)}):")
            for k in ks:
                say(f"    - {k}")
        ask("GetInputList")

        say("")
        say("== profile 参数试探 ==")
        for category, keys in (
            ("Output", ["Mode"]),
            ("SimpleOutput", ["RecQuality", "RecFormat2", "RecEncoder", "RecFilePath",
                              "StreamEncoder", "VBitrate", "ABitrate"]),
            ("AdvOut", ["RecType", "RecEncoder", "RecFormat", "RecFilePath", "RecBitrate",
                        "RecMuxerCustom", "TrackIndex"]),
            ("Video", ["BaseCX", "BaseCY", "OutputCX", "OutputCY", "FPSType", "FPSInt",
                       "ScaleType", "ScaleFilter"]),
            ("Audio", ["SampleRate", "ChannelSetup"]),
        ):
            for key in keys:
                data, err = client.call_sync(
                    "GetProfileParameter",
                    {"parameterCategory": category, "parameterName": key})
                val = (data or {}).get("parameterValue", "<none>") if data else f"ERR {err}"
                say(f"  [{category}] {key:16s} = {val!r}")

        code = 0
    finally:
        ok, msg = obs_launcher.stop(client, home)
        say(f"[{'PASS' if ok else 'FAIL'}] 退出：{msg}")
        left = _sentinel_files(home)
        say(f"[{'PASS' if not left else 'FAIL'}] .sentinel 残留 {len(left)} 个"
            + (f" -> {left}" if left else "（OBS 自己收干净了）"))
        say(f"[{'PASS' if not _log_says_safe_mode(home) else 'FAIL'}] "
            f"本次启动日志未进入安全模式")
        if code == 0 and not ok:
            code = 2

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    return code


def _short(data) -> str:
    if data is None:
        return ""
    s = str(data)
    return s if len(s) < 400 else s[:400] + " …"


def _sentinel_files(home: str) -> list[str]:
    try:
        with os.scandir(obs_paths.sentinel_dir(home)) as it:
            return [e.name for e in it if e.is_file()]
    except OSError:
        return []


def _log_says_safe_mode(home: str) -> bool:
    """Did the *most recent* launch log take the safe-mode branch?"""
    import glob
    logs = sorted(glob.glob(os.path.join(obs_paths.config_root(home), "logs", "*.txt")),
                  key=os.path.getmtime)
    if not logs:
        return False
    try:
        with open(logs[-1], "r", encoding="utf-8", errors="replace") as f:
            head = f.read(6000)
    except OSError:
        return False
    return "Safe Mode enabled" in head or "Safe mode launch selected" in head


if __name__ == "__main__":
    sys.exit(main())
