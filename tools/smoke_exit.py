"""Closing the app must not crash the process.

Real defect: after two engine lifetimes (open -> play -> terminate, twice) the
process segfaulted inside libmpv's DLL detach — i.e. the window closed normally
and then Windows saw the process die. Nothing in Python could catch it.

The app now leaves through `diagnostics.hard_exit()`. This test proves the
end-to-end exit path: it launches a child that drives the *real* `app.main()`,
opens three tabs, closes two of them, shuts the window, and checks the exit
code.

Run:  .venv\\Scripts\\python.exe tools\\smoke_exit.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHILD = os.path.join(ROOT, "tools", "_exit_child.py")
OUT = os.path.join(ROOT, "tools", "_exit.txt")

results: list[bool] = []
lines: list[str] = []


def say(s: str):
    lines.append(s)
    print(s, flush=True)


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append(bool(ok))
    say(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def run_once(label: str) -> int:
    env = dict(os.environ)
    env["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="rs-exit-")
    env["PYTHONIOENCODING"] = "utf-8"
    t0 = time.time()
    proc = subprocess.run([sys.executable, CHILD], capture_output=True, text=True,
                          env=env, timeout=180, encoding="utf-8", errors="replace")
    elapsed = time.time() - t0
    say(f"    {label}: exit={proc.returncode} 用时={elapsed:.1f}s")
    if proc.returncode != 0:
        tail = (proc.stdout or "")[-600:]
        err = (proc.stderr or "")[-600:]
        say(f"      stdout…{tail}")
        say(f"      stderr…{err}")
    return proc.returncode


def main() -> int:
    say("== 关闭应用不应崩溃 ==")
    if not os.path.isfile(CHILD):
        say("缺少驱动脚本 tools/_exit_child.py")
        return 1

    # Two independent runs: the DLL-detach fault depended on accumulated engine
    # lifetimes, so a single lucky run proves nothing.
    codes = [run_once("第 1 次"), run_once("第 2 次")]
    check("连开三个标签后关闭窗口，进程正常退出（连续两次）",
          all(c == 0 for c in codes), f"exit codes={codes}")

    # A crash would be 139 (SIGSEGV) or a big negative/positive SEH code.
    check("没有任何一次以段错误结束",
          all(c == 0 for c in codes), f"codes={codes}")

    passed = sum(results)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + f"\n\n结果：{passed}/{len(results)} 通过\n")
    print(f"\n结果：{passed}/{len(results)} 通过", flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
