"""Phase 3 高风险点验证：OBS 便携版能否启动并监听 obs-websocket 端口。

做法：
  1. 在 vendor/obs/config 下写 obs-websocket 的配置，启用服务并设本地密码；
  2. 以 --portable 启动 obs64.exe（不安装、不写系统目录、不动用户现有 OBS 配置）；
  3. 轮询 127.0.0.1:4455 的 TCP 连接，确认服务真的起来了；
  4. 结束后关掉 OBS，不留后台进程。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBS_HOME = os.path.join(ROOT, "vendor", "obs")
OBS_EXE = os.path.join(OBS_HOME, "bin", "64bit", "obs64.exe")
CFG_DIR = os.path.join(OBS_HOME, "config", "obs-studio", "plugin_config", "obs-websocket")
CFG = os.path.join(CFG_DIR, "config.json")
PORT = 4455
PASSWORD = "replaystudio-local"
OUT = os.path.join(ROOT, "tools", "_obs_probe.txt")

log: list[str] = []


def say(s: str):
    log.append(s)
    print(s, flush=True)


def write_config():
    os.makedirs(CFG_DIR, exist_ok=True)
    cfg = {
        "alerts_enabled": False,
        "auth_required": True,
        "first_load": False,
        "server_enabled": True,
        "server_password": PASSWORD,
        "server_port": PORT,
    }
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    # 跳过 OBS 首次运行的自动配置向导，否则会卡在弹窗
    global_ini = os.path.join(OBS_HOME, "config", "obs-studio", "global.ini")
    os.makedirs(os.path.dirname(global_ini), exist_ok=True)
    if not os.path.isfile(global_ini):
        with open(global_ini, "w", encoding="utf-8") as f:
            f.write("[Basic]\nAutoConfigCompleted=true\n")
    say(f"config written: {CFG}")


def port_open(host="127.0.0.1", port=PORT, timeout=0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    if not os.path.isfile(OBS_EXE):
        say(f"[FAIL] 找不到 {OBS_EXE}")
        return 2
    say(f"OBS exe: {OBS_EXE}")
    write_config()

    if port_open():
        say(f"[WARN] {PORT} 已被占用，可能已有 OBS 在运行；跳过启动")
        return 0

    cwd = os.path.dirname(OBS_EXE)
    proc = subprocess.Popen(
        [OBS_EXE, "--portable", "--minimize-to-tray",
         "--disable-shutdown-check", "--disable-missing-files-check"],
        cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    say(f"obs64.exe started pid={proc.pid}")

    deadline = time.time() + 45
    ok = False
    while time.time() < deadline:
        if port_open():
            ok = True
            break
        if proc.poll() is not None:
            say(f"[FAIL] OBS 进程提前退出，exit={proc.returncode}")
            break
        time.sleep(1.0)

    if ok:
        say(f"[PASS] obs-websocket 正在监听 127.0.0.1:{PORT}")
    else:
        say(f"[FAIL] 45s 内未在 {PORT} 上监听")

    # 收尾：不要留下后台 OBS
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            say("OBS 已关闭")
    except Exception as exc:  # noqa: BLE001
        say(f"关闭 OBS 时出错: {exc}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
