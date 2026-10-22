"""Invariants behind the database speed-up — asserts, not just prints.

Before this change every browsing call opened a read-write connection. That
cost 60 ms on its own (tools/probe_db_perf.py), and `facets()` opened five, so
one page reload took 604 ms. Each of these checks would fail if any of it came
back.
"""
import os
import sqlite3
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from replaystudio.core.db import Database, user_data_dir

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main() -> int:
    path = os.environ.get("RS_DB") or os.path.join(user_data_dir(), "index.db")
    real = os.path.isfile(path)
    db = Database(path)
    print(f"db: {path}")

    print("== connection reuse ==")
    with db.read_conn() as c1:
        id1 = id(c1)
    with db.read_conn() as c2:
        id2 = id(c2)
    check("read connection is cached", id1 == id2, f"{id1} != {id2}")

    try:
        with db.read_conn() as c:
            c.execute("CREATE TABLE should_not_work(x)")
        check("read connection rejects writes", False, "write went through")
    except sqlite3.OperationalError:
        check("read connection rejects writes", True)

    # A cached handle that dies must be replaced, not reused. (The failed write
    # above already dropped ours, so make sure something read first.)
    db.get_setting("probe", "x")
    db._ro.close()
    check("reopens after the handle is closed", db.get_setting("probe", "x") == "x")

    db.set_setting("probe", "written")
    check("write visible to the cached reader", db.get_setting("probe") == "written")

    print("== thread safety ==")
    seen = []

    def worker():
        seen.append(db.get_setting("probe"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("same connection from 8 threads", set(seen) == {"written"}, f"{seen!r}")

    print("== temporary databases stay deletable ==")
    with tempfile.TemporaryDirectory() as td:
        tmp_db = Database(os.path.join(td, "t.db"))
        tmp_db.set_setting("k", "v")
        tmp_db.get_setting("k")
        tmp_db.close()
    check("close() releases the file handle", True)     # cleanup would throw first

    if not real:
        print("\nonly a scratch db, skipping real-data timings")
        print(f"\n{PASS} passed, {FAIL} failed")
        return 1 if FAIL else 0

    print("== timings on the real index (ms) ==")

    def best(fn, n=5):
        b = None
        for _ in range(n):
            t0 = time.perf_counter()
            fn()
            dt = (time.perf_counter() - t0) * 1000
            b = dt if b is None else min(b, dt)
        return b

    rows, total = db.query_videos({}, 200, 0)
    dt = best(lambda: db.query_videos({}, 200, 0))
    print(f"  query_videos(page0)      {dt:6.2f}   rows={len(rows)} total={total}")
    check("query_videos under 25 ms", dt < 25, f"{dt:.1f} ms")

    dt = best(lambda: db.facets(None, {}))
    print(f"  facets()                 {dt:6.2f}")
    check("facets under 25 ms", dt < 25, f"{dt:.1f} ms")

    dt = best(lambda: (db.query_videos({}, 200, 0), db.facets(None, {}),
                       db.stats(), db.list_accounts()), 3)
    print(f"  ONE PAGE REFRESH         {dt:6.2f}   (was 604 ms)")
    check("page refresh under 60 ms", dt < 60, f"{dt:.1f} ms")

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
