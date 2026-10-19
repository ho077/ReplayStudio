"""Verify the crash/diagnostic logging actually captures failures.

Two failure modes must leave evidence on disk, otherwise a "the app just
vanished" report stays undebuggable:

  1. an uncaught Python exception (e.g. raised inside a Qt slot)
  2. a native access violation (what a libmpv fault looks like)

Each case runs in a child process so a real crash does not take the test
down with it. Logs are redirected to a temp dir via LOCALAPPDATA.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = str(ROOT / "src")

CASES = {
    "python-exception": """
import sys
sys.path.insert(0, r"{src}")
from replaystudio.core import diagnostics
p = diagnostics.install()
raise RuntimeError("boom-from-regression-test")
""",
    "native-access-violation": """
import ctypes, sys
sys.path.insert(0, r"{src}")
from replaystudio.core import diagnostics
p = diagnostics.install()
# Dereference a null pointer: the OS raises an access violation, which is
# exactly the shape of a native libmpv fault. faulthandler must dump it.
ctypes.string_at(0)
""",
}


def run_case(name: str, code: str, tmp: Path) -> tuple[bool, str]:
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(tmp)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-c", code.format(src=SRC)],
        capture_output=True, text=True, env=env, timeout=120,
        encoding="utf-8", errors="replace",
    )
    logdir = tmp / "ReplayStudio" / "logs"
    session = list(logdir.glob("session-*.log"))
    crash = list(logdir.glob("crash-*.log"))

    session_text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                             for p in session)
    crash_text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                           for p in crash)

    if name == "python-exception":
        ok = ("UNHANDLED EXCEPTION" in session_text
              and "boom-from-regression-test" in session_text
              and proc.returncode != 0)
        detail = (f"exit={proc.returncode} "
                  f"session_log={len(session)} "
                  f"traceback_captured={'Traceback' in session_text}")
    else:
        ok = (proc.returncode != 0
              and ("Access violation" in crash_text
                   or "Windows fatal exception" in crash_text
                   or "Current thread" in crash_text)
              and bool(crash))
        detail = (f"exit={proc.returncode} "
                  f"crash_log={len(crash)} "
                  f"faulthandler_dump={'Current thread' in crash_text}")
    return ok, detail


def main() -> int:
    results: list[bool] = []
    print("== 崩溃日志捕获验证 ==")
    for name, code in CASES.items():
        with tempfile.TemporaryDirectory(prefix="rs-diag-") as td:
            ok, detail = run_case(name, code, Path(td))
            results.append(ok)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")

    # Rotation: a log older than KEEP days must be pruned on install.
    with tempfile.TemporaryDirectory(prefix="rs-diag-") as td:
        tmp = Path(td)
        logdir = tmp / "ReplayStudio" / "logs"
        logdir.mkdir(parents=True)
        stale = logdir / "session-19990101-000000.log"
        stale.write_text("old", encoding="utf-8")
        old = 1_000_000.0
        os.utime(stale, (old, old))
        env = dict(os.environ, LOCALAPPDATA=str(tmp))
        subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, r'{SRC}');"
             "from replaystudio.core import diagnostics;"
             "diagnostics.install()"],
            env=env, capture_output=True, timeout=60,
        )
        ok = not stale.exists()
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] 过期日志轮转清理  "
              f"stale_removed={not stale.exists()}")

    passed = sum(results)
    print(f"\n结果：{passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
