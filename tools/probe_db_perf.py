"""Where does the time go? Times every call the pages make, on the real index."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from replaystudio.core.db import Database, user_data_dir


def ms(fn, n=5):
    best = None
    for _ in range(n):
        t0 = time.perf_counter()
        out = fn()
        dt = (time.perf_counter() - t0) * 1000
        best = dt if best is None else min(best, dt)
    return best, out


def main() -> int:
    path = os.environ.get("RS_DB") or os.path.join(user_data_dir(), "index.db")
    print(f"db: {path}  ({os.path.getsize(path)/1024/1024:.1f} MB)")
    db = Database(path)
    with db.conn() as c:
        for t in ("videos", "matches", "events", "tags", "favorites",
                  "play_history"):
            print(f"  {t:<14} {c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>8}")
        print("  indexes:")
        for r in c.execute("SELECT name FROM sqlite_master "
                           "WHERE type='index' AND NOT name LIKE 'sqlite_%'"):
            print(f"    {r['name']}")

    print("\n--- calls used while browsing (best of 5, ms) ---")

    def q_plain():
        return db.query_videos({}, 200, 0)

    dt, (rows, n) = ms(q_plain)
    print(f"{'query_videos(page0, no filter)':<46} {dt:8.2f}   rows={len(rows)} total={n}")

    def q_page():
        return db.query_videos({}, 200, 800)

    dt, _ = ms(q_page)
    print(f"{'query_videos(page4)':<46} {dt:8.2f}")

    def q_mine():
        return db.query_videos({"mine_only": True}, 200, 0)

    dt, (rows, n) = ms(q_mine)
    print(f"{'query_videos(mine_only)':<46} {dt:8.2f}   total={n}")

    def q_search():
        return db.query_videos({"q": "击杀"}, 200, 0)

    dt, (rows, n) = ms(q_search)
    print(f"{'query_videos(search 击杀)':<46} {dt:8.2f}   total={n}")

    def q_fav():
        return db.query_videos({"favorite": True}, 200, 0)

    dt, _ = ms(q_fav)
    print(f"{'query_videos(favorite)':<46} {dt:8.2f}")

    def f_no():
        return db.facets()

    dt, _ = ms(f_no)
    print(f"{'facets() (no filter)':<46} {dt:8.2f}")

    def f_all():
        return db.facets(None, {"mine_only": True, "favorite": True})

    dt, _ = ms(f_all)
    print(f"{'facets(mine+fav)':<46} {dt:8.2f}")

    def st():
        return db.stats()

    dt, out = ms(st) if hasattr(db, "stats") else (0, None)
    print(f"{'stats()':<46} {dt:8.2f}   {out if isinstance(out, dict) else ''}")

    def acc():
        return db.list_accounts()

    dt, a = ms(acc)
    print(f"{'list_accounts()':<46} {dt:8.2f}   n={len(a)}")

    def conn_only():
        with db.conn() as c:
            c.execute("SELECT 1").fetchone()

    dt, _ = ms(conn_only, 20)
    print(f"{'one short-lived connection':<46} {dt:8.2f}")

    # How many queries does one page load actually issue?
    def full_refresh():
        rows, n = db.query_videos({}, 200, 0)
        db.facets(None, {})
        db.stats() if hasattr(db, "stats") else None
        db.list_accounts()

    dt, _ = ms(full_refresh, 3)
    print(f"{'ONE FULL PAGE REFRESH (all of the above)':<46} {dt:8.2f}")

    print("\n--- query plans ---")
    with db.conn() as c:
        for label, sql in (
            ("main list", "SELECT v.* FROM videos v LEFT JOIN matches m "
                          "ON m.match_id=v.match_id WHERE v.file_state != 2 "
                          "ORDER BY v.created_ms DESC LIMIT 200 OFFSET 0"),
            ("count", "SELECT COUNT(*) AS n FROM videos v LEFT JOIN matches m "
                      "ON m.match_id = v.match_id WHERE v.file_state != 2"),
            ("facet group", "SELECT m.map_cn AS k, COUNT(*) AS n FROM videos v "
                            "LEFT JOIN matches m ON m.match_id = v.match_id "
                            "WHERE v.file_state != 2 GROUP BY m.map_cn"),
        ):
            plan = c.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
            detail = " | ".join(r["detail"] for r in plan)
            print(f"  {label}: {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
