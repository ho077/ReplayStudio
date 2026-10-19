"""Phase 3 回归：OBS 便携版能起来、能握手、能干净退出。

这一组检查专门盯住三个踩过的坑，任何一个退化都会让录屏功能整体失效：

  1. 工作目录必须是 exe 所在目录。放到便携版根目录，OBS 弹一个标题叫
     "Error" 的框然后自杀，日志一行都不写 —— 现象上完全看不出原因。
  2. `.sentinel` 残留会让 OBS 下次启动时判定"上次异常退出"并弹对话框拦住
     启动；顺着对话框点了安全模式，obs-websocket 就不加载了。
  3. 退出前必须先让 websocket 的关闭帧发出去，否则 WM_CLOSE 被无视，
     最后只能强杀 —— 强杀又会留下 sentinel，回到问题 2。

没有 vendor/obs 时整组跳过（返回 0），因为 OBS 有 466MB，不进版本库。

输出 -> tools/_smoke_obs.txt
"""
from __future__ import annotations

import glob
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from replaystudio.core import obs_launcher, obs_paths  # noqa: E402
from replaystudio.core.obs_client import ObsClient  # noqa: E402

OUT = os.path.join(ROOT, "tools", "_smoke_obs.txt")
lines: list[str] = []
passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> bool:
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
    mark = "PASS" if ok else "FAIL"
    lines.append(f"[{mark}] {name}" + (f"  {detail}" if detail else ""))
    print(f"[{mark}] {name}" + (f"  {detail}" if detail else ""), flush=True)
    return ok


def wait_identified(app, client, seconds: float = 15.0) -> bool:
    box = {"ok": False}
    client.identified.connect(lambda _i: box.__setitem__("ok", True))
    client.open()
    end = time.time() + seconds
    while time.time() < end and not box["ok"]:
        app.processEvents()
        time.sleep(0.05)
    return box["ok"]


def newest_log_text(home: str, head: int = 6000) -> str:
    files = sorted(glob.glob(os.path.join(obs_paths.config_root(home), "logs", "*.txt")),
                   key=os.path.getmtime)
    if not files:
        return ""
    try:
        with open(files[-1], "r", encoding="utf-8", errors="replace") as f:
            return f.read(head)
    except OSError:
        return ""


def main() -> int:
    app = QCoreApplication(sys.argv)
    home = obs_paths.obs_home()
    if not home:
        print("SKIP: 没有 vendor/obs 便携版，跳过 OBS 回归（不算失败）")
        return 0
    exe = obs_paths.obs_exe(home)
    print(f"OBS  : {exe}")
    print(f"port : {obs_paths.DEFAULT_PORT}")
    print("")

    port = obs_paths.DEFAULT_PORT
    client: ObsClient | None = None

    # ---- 1. 核心文件齐全
    check(os.path.isfile(exe), "便携版 obs64.exe 存在")
    ws_cfg = obs_paths.ensure_ws_config(home, port)
    check(bool(ws_cfg.get("server_enabled")) and bool(ws_cfg.get("server_password")),
          "obs-websocket 配置可用（已启用且有密码）",
          f"port={ws_cfg.get('server_port')}")

    # ---- 2. sentinel 残留能被清掉（在没有真实崩溃记录的前提下）
    left_before = len([n for n in os.listdir(obs_paths.sentinel_dir(home))
                       if n.startswith("run_")]) if os.path.isdir(
        obs_paths.sentinel_dir(home)) else 0
    with open(os.path.join(obs_paths.sentinel_dir(home), "run_smoketest"), "w") as f:
        f.write("")
    removed = obs_paths.clear_sentinel(home)
    left_after = len([n for n in os.listdir(obs_paths.sentinel_dir(home))
                      if n.startswith("run_")])
    check(removed >= 1 and left_after == 0,
          "clear_sentinel 能清掉自己造的残留标记",
          f"原有 {left_before} 个，清掉 {removed} 个，剩余 {left_after} 个")

    # ---- 3. 启动
    ok, msg = obs_launcher.start(home, port)
    check(ok, "OBS 启动并等到端口就绪", msg)
    if not ok:
        _write()
        return 2

    try:
        # ---- 4. 握手
        client = ObsClient("127.0.0.1", port, ws_cfg.get("server_password") or "")
        app.client = client
        check(wait_identified(app, client), "obs-websocket v5 握手成功")

        # ---- 5/6. 真实请求
        ver, err = client.call_sync("GetVersion")
        check(bool(ver) and not err, "GetVersion 可用",
              f"obsWebSocketVersion={ver.get('obsWebSocketVersion') if ver else err}")
        st, err2 = client.call_sync("GetRecordStatus")
        check(bool(st) and not err2, "GetRecordStatus 可用",
              f"outputActive={(st or {}).get('outputActive')}")

        # ---- 7. 干净退出
        ok2, msg2 = obs_launcher.stop(client, home)
        check(ok2 and "正常退出" in msg2, "OBS 走 WM_CLOSE 干净退出（不是强杀）", msg2)
        client = None
    finally:
        if client is not None:
            obs_launcher.stop(client, home)
        if obs_launcher.pids():          # 兜底，绝不留下后台进程
            obs_launcher.stop(home=home)

    # ---- 8. 收尾状态
    left = [n for n in os.listdir(obs_paths.sentinel_dir(home))
            ] if os.path.isdir(obs_paths.sentinel_dir(home)) else []
    check(not left, "退出后 .sentinel 无残留", f"{left}")
    check(not obs_launcher.pids(), "退出后没有 obs64.exe 残留")

    text = newest_log_text(home)
    check(bool(text) and "Safe Mode enabled" not in text,
          "本次启动日志未进入安全模式")

    _write()
    print(f"\n合计 {passed}/{passed + failed} 项通过")
    return 0 if failed == 0 else 2


def _write():
    lines.append(f"\n合计 {passed}/{passed + failed} 项通过")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
