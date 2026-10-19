"""Missing source files must not be listed anywhere.

The user deleted those clips on purpose; showing them in every grid is noise.
Policy under test:

* `file_state = 2` rows are excluded from `query_videos`, `recent`, `facets`
  and `stats` by default;
* the explicit "仅缺失" filter still works (it overrides the hiding);
* `show_missing = True` restores the old behaviour;
* the rows stay in the index, so a returning file re-appears on the next scan.

Runs against a throwaway SQLite file — the real index is never touched.
Run:  .venv\\Scripts\\python.exe tools\\smoke_hidden_missing.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from replaystudio.core.db import Database  # noqa: E402

results: list[bool] = []
lines: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append(bool(ok))
    line = f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else "")
    lines.append(line)
    print(line, flush=True)
    return ok


def make_match(mid: str, map_cn: str) -> SimpleNamespace:
    return SimpleNamespace(
        match_id=mid, openid="o1", map_id="m1", map_cn=map_cn,
        agent_id="a1", agent_cn="捷风", mode="competitive", mode_cn="竞技模式",
        start_ms=0, end_ms=1, kills=10, deaths=2, assists=3, score="13:5",
        has_won=True, rounds_won=13, rounds_lost=5)


def make_video(vid: str, mid: str, path: str) -> SimpleNamespace:
    return SimpleNamespace(
        video_id=vid, match_id=mid, openid="o1", path=path, poster="",
        duration_ms=25_000, size_bytes=1024, width=1920, height=1080, fps=60,
        level=0, vtype="击杀", vname=f"片段{vid}", created_ms=0, is_montage=False)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="rs-miss-")
    media = os.path.join(tmp, "media")
    os.makedirs(media)

    def touch(name: str) -> str:
        p = os.path.join(media, name)
        with open(p, "wb") as f:
            f.write(b"\0" * 64)
        return p

    present = [touch("a.mp4"), touch("b.mp4")]
    gone = os.path.join(media, "deleted-by-user.mp4")   # never created

    db = Database(os.path.join(tmp, "index.db"))
    db.upsert_match(make_match("M1", "亚海悬城"))
    db.upsert_match(make_match("M2", "霓虹町"))
    db.upsert_video(make_video("V1", "M1", present[0]), file_state=1)
    db.upsert_video(make_video("V2", "M2", present[1]), file_state=1)
    db.upsert_video(make_video("V3", "M2", gone), file_state=2)
    for vid in ("V1", "V2", "V3"):
        db.add_history(vid, 5.0, 25.0)

    # ---- default: hidden -------------------------------------------------
    rows, total = db.query_videos({})
    ids = {r["video_id"] for r in rows}
    check("默认查询不返回缺失片段", ids == {"V1", "V2"}, f"ids={sorted(ids)} total={total}")
    check("总数同步为可见数量", total == 2, f"total={total}")

    rec = {r["video_id"] for r in db.recent()}
    check("最近播放不含缺失片段", rec == {"V1", "V2"}, f"ids={sorted(rec)}")

    f = db.facets()
    maps = dict(f["maps"])
    check("筛选计数不含缺失片段", maps.get("霓虹町") == 1,
          f"maps={maps}")

    s = db.stats()
    check("统计区分可见/全部", (s["videos"], s["all_videos"], s["missing"]) == (2, 3, 1),
          f"videos={s['videos']} all={s['all_videos']} missing={s['missing']}")

    # ---- explicit "仅缺失" -----------------------------------------------
    rows, _ = db.query_videos({"state": "missing"})
    ids = {r["video_id"] for r in rows}
    check("显式筛选「仅缺失」仍可用", ids == {"V3"}, f"ids={sorted(ids)}")

    # ---- opt-in show_missing --------------------------------------------
    db.show_missing = True
    rows, total = db.query_videos({})
    ids = {r["video_id"] for r in rows}
    check("开启开关后恢复显示全部", ids == {"V1", "V2", "V3"} and total == 3,
          f"ids={sorted(ids)} total={total}")
    check("开启后最近播放也含缺失片段",
          {r["video_id"] for r in db.recent()} == {"V1", "V2", "V3"})
    check("开启后筛选计数恢复",
          dict(db.facets()["maps"]).get("霓虹町") == 2,
          f"maps={dict(db.facets()['maps'])}")

    # ---- the row is still there -----------------------------------------
    db.show_missing = False
    check("片段仍在索引中（文件回来后自动恢复）",
          db.get_video("V3") is not None)
    with open(gone, "wb") as fh:
        fh.write(b"\0" * 64)
    db.refresh_file_states()
    rows, _ = db.query_videos({})
    check("文件恢复后自动回到列表",
          {r["video_id"] for r in rows} == {"V1", "V2", "V3"},
          f"ids={sorted({r['video_id'] for r in rows})}")

    passed = sum(results)
    out = os.path.join(ROOT, "tools", "_missing.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + f"\n\n结果：{passed}/{len(results)} 通过\n")
    print(f"\n结果：{passed}/{len(results)} 通过", flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
