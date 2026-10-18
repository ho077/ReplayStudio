"""Core smoke test: ACLOS parse -> SQLite index -> query. No GUI required."""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from replaystudio.aclos import reader  # noqa: E402
from replaystudio.core.db import Database  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_smoke_core.txt")
lines = []


def log(*a):
    s = " ".join(str(x) for x in a)
    lines.append(s)
    print(s, flush=True)


def main():
    db_dir = reader.default_db_dir()
    log("db dir:", db_dir, "exists:", os.path.isdir(db_dir))
    accounts = reader.list_accounts(db_dir)
    log("accounts:", len(accounts))
    assert accounts, "no accounts found"

    db_path = os.path.join(os.path.dirname(OUT), "_smoke.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db = Database(db_path)

    t0 = time.time()
    total_m = total_v = total_e = 0
    for acc in accounts:
        snap = reader.parse_snapshot_file(acc.snapshot_path, acc.openid)
        acc.nick, acc.tag = snap.nick, snap.tag
        db.upsert_account(acc.openid, acc.nick, acc.tag, acc.db_path)
        log(f"\naccount {acc.openid} -> {acc.display}")
        matches = reader.parse_account_file(acc.db_path, acc.openid)
        with db.conn() as c:
            for m in matches:
                db.upsert_match(m, _conn=c)
                for v in m.videos:
                    db.upsert_video(v, _conn=c)
                    if v.events:
                        db.replace_events(v.video_id, v.events, _conn=c)
                        total_e += len(v.events)
        total_m += len(matches)
        total_v += sum(len(m.videos) for m in matches)
        log(f"  matches={len(matches)} videos={sum(len(m.videos) for m in matches)}")
    log(f"\nparse+index seconds: {time.time()-t0:.2f}")
    log(f"total matches={total_m} videos={total_v} events={total_e}")

    # ---- queries
    fac = db.facets()
    log("\nmaps:", fac["maps"][:8])
    log("agents:", fac["agents"])
    log("types:", fac["types"])
    log("modes:", fac["modes"][:6])
    log("total:", fac["total"], "missing:", fac["missing"])

    rows, n = db.query_videos({"sort": "最新"}, 5, 0)
    log(f"\nquery newest: n={n}")
    for r in rows:
        log(f"  {r.get('created_ms')}\t{r.get('vname')}\t{r.get('map_cn')}\t"
            f"{r.get('agent_cn')}\t{r.get('vtype')}\tevents={r.get('event_count')}")

    rows, n = db.query_videos({"vtype": "五杀时刻", "sort": "最新"}, 5, 0)
    log(f"\nfilter 五杀时刻: n={n}")

    # pick one video with events and show them
    vid = None
    for r in db.query_videos({"sort": "最新"}, 200, 0)[0]:
        if r.get("event_count"):
            vid = r["video_id"]
            break
    if vid:
        evs = db.get_events(vid)
        row = db.get_video(vid)
        log(f"\nevents for {row['vname']} ({vid}) duration={row['duration_ms']}ms:")
        for e in evs[:8]:
            log(f"  {e['ms']:>7}ms  {e['etype']:<5} {e['killer']} -> {e['killed']} "
                f"{e['weapon_class']} mine={e['is_mine']}")
        bad = [e for e in evs if e["ms"] > row["duration_ms"]]
        log(f"  events beyond duration: {len(bad)}")

    # ---- incremental marker
    st = db.stats()
    log("\nstats:", st)
    log("SMOKE OK")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        lines.append("FAILED\n" + traceback.format_exc())
        print(traceback.format_exc(), flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
