"""Compare what the facet labels promise against what the grid actually lists.

Reported: pick a map or agent in the filter bar and the grid shows fewer rows
than the number in parentheses. Two candidate causes:

  A. the facet COUNT uses a different join/filter than query_videos COUNT
  B. the facet counts rows, the query counts *visible* rows (missing files
     hidden), and the two disagree at the edges

Run against the real index in %LOCALAPPDATA%.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from replaystudio.core.db import Database  # noqa: E402


def main() -> int:
    db = Database()
    print("index:", db.path)
    if not os.path.exists(db.path):
        print("MISSING index")
        return 1

    fac = db.facets()
    print("total:", fac["total"], " missing:", fac["missing"])

    # Scenario 1: nothing else selected — the counts should match exactly.
    bad = 0
    for key, flt in (("maps", "map"), ("agents", "agent"),
                     ("types", "vtype"), ("modes", "mode")):
        print(f"\n--- {key} (no other filter) ---")
        for name, n in fac[key][:40]:
            rows, total = db.query_videos({flt: name}, 1, 0)
            flag = "" if total == n else "   <<< MISMATCH"
            if total != n:
                bad += 1
            print(f"  {name:<24} facet={n:<6} query={total:<6}{flag}")
    print(f"\nmismatched facets (bare): {bad}")

    # Scenario 2: another filter is also active. The facet labels are computed
    # without it, so they promise the unfiltered total — this is what the user
    # sees as "the grid shows fewer than the number in parentheses".
    extra = sys.argv[1] if len(sys.argv) > 1 else "mine"
    base = {"mine_only": True} if extra == "mine" else (
        {"favorite": True} if extra == "fav" else {"result": "win"})
    print(f"\n### with {base} also active — labels counted the way the UI does")
    fac = db.facets(filters=base)      # <- the fix: honour the other filters
    bad2 = 0
    for key, flt in (("maps", "map"), ("agents", "agent")):
        print(f"\n--- {key} ---")
        for name, n in fac[key][:8]:
            f = dict(base); f[flt] = name
            rows, total = db.query_videos(f, 1, 0)
            flag = "" if total == n else "   <<< MISMATCH"
            if total != n:
                bad2 += 1
            print(f"  {name:<24} facet={n:<6} query={total:<6}{flag}")
    print(f"\nmismatched facets (with {extra}): {bad2}")
    return 1 if (bad or bad2) else 0


if __name__ == "__main__":
    from replaystudio.core import diagnostics

    diagnostics.install()
    diagnostics.hard_exit(main())
