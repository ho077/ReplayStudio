"""Quitting with OBS actually running — must not take 25 seconds.

The quit path now passes budget=3.0 to obs_launcher.stop(). This starts our
portable OBS for real, then measures how long it takes to shut it down and
whether it is actually gone afterwards.
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

BUDGET = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0


def main() -> int:
    from replaystudio.core import obs_launcher, obs_paths

    home = obs_paths.obs_home()
    print(f"obs home: {home}")
    if not home:
        print("no OBS available here")
        return 0

    print("starting OBS …", flush=True)
    ok, msg = obs_launcher.start(home, obs_paths.DEFAULT_PORT, timeout=90)
    print(f"  start -> {ok}: {msg}", flush=True)
    if not ok:
        return 1

    print(f"  running before stop: {obs_launcher.is_running()}", flush=True)

    t0 = time.perf_counter()
    ok, msg = obs_launcher.stop(None, home, budget=BUDGET)
    dt = (time.perf_counter() - t0)
    print(f"  stop(budget={BUDGET}) -> {ok}: {msg}", flush=True)
    print(f"  elapsed {dt:.2f}s", flush=True)

    # Give OBS a moment; the check itself is authoritative.
    time.sleep(0.5)
    gone = not obs_launcher.is_running()
    print(f"  OBS gone: {gone}", flush=True)

    fails = []
    if dt > BUDGET + 2.0:
        fails.append(f"stop took {dt:.1f}s, budget was {BUDGET}s")
    if not gone:
        fails.append("OBS still running after stop()")
    for f in fails:
        print(f"  FAIL {f}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
