"""SQLite index for ReplayStudio.

Design rules that come straight from the 100 GB+ requirement:

* every public call opens its own short-lived connection (SQLite connection
  creation is cheap, and this keeps worker threads safe without a global lock);
* WAL mode so the UI thread can read while a scan thread writes;
* no full-file hashing during scan — identity is (path, size, mtime);
* queries are always LIMIT/OFFSET paginated so the UI never materialises 1471
  rows into widgets at once.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS accounts(
  openid     TEXT PRIMARY KEY,
  nick       TEXT DEFAULT '',
  tag        TEXT DEFAULT '',
  db_path    TEXT DEFAULT '',
  db_mtime   REAL DEFAULT 0,
  db_size    INTEGER DEFAULT 0,
  last_scan  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS matches(
  match_id    TEXT PRIMARY KEY,
  openid      TEXT NOT NULL,
  map_id      TEXT DEFAULT '',
  map_cn      TEXT DEFAULT '',
  agent_id    TEXT DEFAULT '',
  agent_cn    TEXT DEFAULT '',
  mode        TEXT DEFAULT '',
  mode_cn     TEXT DEFAULT '',
  start_ms    INTEGER DEFAULT 0,
  end_ms      INTEGER DEFAULT 0,
  kills       INTEGER DEFAULT 0,
  deaths      INTEGER DEFAULT 0,
  assists     INTEGER DEFAULT 0,
  score       INTEGER DEFAULT 0,
  has_won     INTEGER DEFAULT 0,
  rounds_won  INTEGER DEFAULT 0,
  rounds_lost INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_matches_openid ON matches(openid);
CREATE INDEX IF NOT EXISTS idx_matches_map    ON matches(map_cn);
CREATE INDEX IF NOT EXISTS idx_matches_agent  ON matches(agent_cn);
CREATE INDEX IF NOT EXISTS idx_matches_time   ON matches(start_ms);

CREATE TABLE IF NOT EXISTS videos(
  video_id    TEXT PRIMARY KEY,
  match_id    TEXT DEFAULT '',
  openid      TEXT DEFAULT '',
  path        TEXT DEFAULT '',
  poster      TEXT DEFAULT '',
  duration_ms INTEGER DEFAULT 0,
  size_bytes  INTEGER DEFAULT 0,
  width       INTEGER DEFAULT 0,
  height      INTEGER DEFAULT 0,
  fps         REAL DEFAULT 0,
  level       TEXT DEFAULT '',
  vtype       TEXT DEFAULT '',
  vname       TEXT DEFAULT '',
  created_ms  INTEGER DEFAULT 0,
  is_montage  INTEGER DEFAULT 0,
  source      TEXT DEFAULT 'aclos',
  file_state  INTEGER DEFAULT 0,   -- 0 unknown, 1 present, 2 missing
  file_mtime  REAL DEFAULT 0,
  file_size   INTEGER DEFAULT 0,
  added_ts    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_videos_match ON videos(match_id);
CREATE INDEX IF NOT EXISTS idx_videos_openid ON videos(openid);
CREATE INDEX IF NOT EXISTS idx_videos_type  ON videos(vtype);
CREATE INDEX IF NOT EXISTS idx_videos_time  ON videos(created_ms);
CREATE INDEX IF NOT EXISTS idx_videos_source ON videos(source);

CREATE TABLE IF NOT EXISTS events(
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id     TEXT NOT NULL,
  ms           INTEGER NOT NULL,
  etype        TEXT NOT NULL,
  killer       TEXT DEFAULT '',
  killed       TEXT DEFAULT '',
  agent        TEXT DEFAULT '',
  weapon       TEXT DEFAULT '',
  weapon_class TEXT DEFAULT '',
  round_id     TEXT DEFAULT '',
  is_mine      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_video ON events(video_id, ms);

CREATE TABLE IF NOT EXISTS tags(
  video_id TEXT NOT NULL,
  tag      TEXT NOT NULL,
  PRIMARY KEY(video_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);

CREATE TABLE IF NOT EXISTS favorites(
  video_id TEXT PRIMARY KEY,
  ts       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS play_history(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id   TEXT NOT NULL,
  ts         INTEGER DEFAULT 0,
  pos_s      REAL DEFAULT 0,
  duration_s REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_history_video ON play_history(video_id);

CREATE TABLE IF NOT EXISTS roots(
  path     TEXT PRIMARY KEY,
  kind     TEXT DEFAULT 'local',
  added_ts INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings(
  key   TEXT PRIMARY KEY,
  value TEXT DEFAULT ''
);
"""

SORT_COLUMNS = {
    "最新": "v.created_ms DESC",
    "最早": "v.created_ms ASC",
    "时长": "v.duration_ms DESC",
    "大小": "v.size_bytes DESC",
    "击杀数": "m.kills DESC",
}


def user_data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    d = os.path.join(base, "ReplayStudio")
    os.makedirs(d, exist_ok=True)
    return d


class Database:
    def __init__(self, path: str | None = None, show_missing: bool = False):
        self.path = path or os.path.join(user_data_dir(), "index.db")
        # Videos whose source file is gone (the game client purges old clips,
        # or the user deleted them on purpose) are excluded from every listing
        # by default. They stay in the index — re-appearing automatically if the
        # file comes back — but they no longer clutter the grids.
        self.show_missing = show_missing
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._init_schema()

    @property
    def missing_clause(self) -> str:
        """SQL fragment for the current visibility policy ('' = show them)."""
        return "" if self.show_missing else "v.file_state != 2"

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    @contextmanager
    def _tx(self, existing=None):
        """Reuse a caller-supplied connection (bulk scans) or open a fresh one."""
        if existing is not None:
            yield existing
        else:
            with self.conn() as c:
                yield c

    def _init_schema(self):
        with self.conn() as c:
            c.executescript(SCHEMA)

    # ------------------------------------------------------------------ accounts
    def upsert_account(self, openid, nick="", tag="", db_path="", db_mtime=0.0, db_size=0, _conn=None):
        with self._tx(_conn) as c:
            c.execute("""INSERT INTO accounts(openid,nick,tag,db_path,db_mtime,db_size,last_scan)
                         VALUES(?,?,?,?,?,?,?)
                         ON CONFLICT(openid) DO UPDATE SET
                           nick=excluded.nick, tag=excluded.tag, db_path=excluded.db_path,
                           db_mtime=excluded.db_mtime, db_size=excluded.db_size,
                           last_scan=excluded.last_scan""",
                      (openid, nick, tag, db_path, db_mtime, db_size, int(time.time())))

    def get_account_meta(self, openid: str):
        with self.conn() as c:
            r = c.execute("SELECT db_mtime, db_size FROM accounts WHERE openid=?", (openid,)).fetchone()
            return (r["db_mtime"], r["db_size"]) if r else (0.0, 0)

    def list_accounts(self):
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM accounts ORDER BY nick, openid").fetchall()]

    # ------------------------------------------------------------------- matches
    def upsert_match(self, m, _conn=None):
        with self._tx(_conn) as c:
            c.execute("""INSERT INTO matches(match_id,openid,map_id,map_cn,agent_id,agent_cn,
                         mode,mode_cn,start_ms,end_ms,kills,deaths,assists,score,has_won,
                         rounds_won,rounds_lost)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(match_id) DO UPDATE SET
                           map_cn=excluded.map_cn, agent_cn=excluded.agent_cn,
                           mode_cn=excluded.mode_cn, start_ms=excluded.start_ms,
                           end_ms=excluded.end_ms, kills=excluded.kills,
                           deaths=excluded.deaths, assists=excluded.assists,
                           score=excluded.score, has_won=excluded.has_won,
                           rounds_won=excluded.rounds_won, rounds_lost=excluded.rounds_lost""",
                      (m.match_id, m.openid, m.map_id, m.map_cn, m.agent_id, m.agent_cn,
                       m.mode, m.mode_cn, m.start_ms, m.end_ms, m.kills, m.deaths,
                       m.assists, m.score, int(m.has_won), m.rounds_won, m.rounds_lost))

    # -------------------------------------------------------------------- videos
    def upsert_video(self, v, file_state: int | None = None, _conn=None):
        if file_state is not None:
            fs, fmtime, fsize = file_state, 0.0, v.size_bytes
        else:
            fs, fmtime, fsize = self._probe_state(v.path, v.size_bytes)
        with self._tx(_conn) as c:
            c.execute("""INSERT INTO videos(video_id,match_id,openid,path,poster,duration_ms,
                         size_bytes,width,height,fps,level,vtype,vname,created_ms,is_montage,
                         source,file_state,file_mtime,file_size,added_ts)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(video_id) DO UPDATE SET
                           match_id=excluded.match_id, path=excluded.path, poster=excluded.poster,
                           duration_ms=excluded.duration_ms, size_bytes=excluded.size_bytes,
                           width=excluded.width, height=excluded.height, fps=excluded.fps,
                           level=excluded.level, vtype=excluded.vtype, vname=excluded.vname,
                           created_ms=excluded.created_ms, is_montage=excluded.is_montage,
                           file_state=excluded.file_state, file_mtime=excluded.file_mtime,
                           file_size=excluded.file_size""",
                      (v.video_id, v.match_id, v.openid, v.path, v.poster, v.duration_ms,
                       v.size_bytes, v.width, v.height, v.fps, v.level, v.vtype, v.vname,
                       v.created_ms, int(v.is_montage), getattr(v, "source", "aclos"),
                       fs, fmtime, fsize, int(time.time())))

    @staticmethod
    def _probe_state(path: str, size: int = 0):
        try:
            st = os.stat(path)
            return 1, st.st_mtime, st.st_size
        except OSError:
            return 2, 0.0, 0

    def replace_events(self, video_id: str, events, _conn=None):
        with self._tx(_conn) as c:
            c.execute("DELETE FROM events WHERE video_id=?", (video_id,))
            c.executemany("""INSERT INTO events(video_id,ms,etype,killer,killed,agent,
                             weapon,weapon_class,round_id,is_mine)
                             VALUES(?,?,?,?,?,?,?,?,?,?)""",
                          [(video_id, e.ms, e.etype, e.killer, e.killed, e.agent,
                            e.weapon, e.weapon_class, e.round_id, int(e.is_mine)) for e in events])

    def get_events(self, video_id: str):
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM events WHERE video_id=? ORDER BY ms", (video_id,)).fetchall()]

    # ------------------------------------------------------------------- queries
    def _build_where(self, f: dict, skip: tuple[str, ...] = ()) -> tuple[list[str], list]:
        """Shared WHERE builder for the grid *and* for the facet counts.

        ``skip`` names filter keys to leave out. A facet count must be taken
        with every *other* filter applied but never with its own: "霓虹町 (154)"
        has to mean "154 with the filters you already picked", otherwise the
        label promises the unfiltered total and the grid delivers less. That
        discrepancy is exactly the "实际数量不到括号里的数量" report.
        """
        skip = set(skip)
        where, args = [], []
        if f.get("source") and "source" not in skip:
            where.append("v.source = ?"); args.append(f["source"])
        if f.get("openid") and "openid" not in skip:
            where.append("v.openid = ?"); args.append(f["openid"])
        if f.get("map") and "map" not in skip:
            where.append("m.map_cn = ?"); args.append(f["map"])
        if f.get("agent") and "agent" not in skip:
            where.append("m.agent_cn = ?"); args.append(f["agent"])
        if f.get("vtype") and "vtype" not in skip:
            vals = f["vtype"] if isinstance(f["vtype"], (list, tuple)) else [f["vtype"]]
            where.append("v.vtype IN (%s)" % ",".join("?" * len(vals))); args += list(vals)
        if f.get("mode") and "mode" not in skip:
            where.append("m.mode_cn = ?"); args.append(f["mode"])
        if f.get("result") == "win" and "result" not in skip:
            where.append("m.has_won = 1")
        elif f.get("result") == "lose" and "result" not in skip:
            where.append("m.has_won = 0")
        if f.get("favorite") and "favorite" not in skip:
            where.append("v.video_id IN (SELECT video_id FROM favorites)")
        if f.get("tag") and "tag" not in skip:
            where.append("v.video_id IN (SELECT video_id FROM tags WHERE tag=?)"); args.append(f["tag"])
        if f.get("mine_only") and "mine_only" not in skip:
            where.append("EXISTS(SELECT 1 FROM events e WHERE e.video_id=v.video_id AND e.is_mine=1)")
        if f.get("state") == "missing":
            # Explicit request ("仅缺失"): show *only* the missing ones, which
            # overrides the default hiding.
            where.append("v.file_state = 2")
        elif f.get("state") == "ok":
            where.append("v.file_state = 1")
        elif self.missing_clause:
            where.append(self.missing_clause)
        q = (f.get("q") or "").strip()
        if q and "q" not in skip:
            where.append("(v.vname LIKE ? OR v.vtype LIKE ? OR v.path LIKE ? OR m.map_cn LIKE ?"
                         " OR m.agent_cn LIKE ? OR m.match_id LIKE ?"
                         " OR v.video_id IN (SELECT video_id FROM tags WHERE tag LIKE ?))")
            args += [f"%{q}%"] * 7
        return where, args

    def query_videos(self, f: dict, limit: int = 200, offset: int = 0):
        where, args = self._build_where(f)
        order = SORT_COLUMNS.get(f.get("sort", "最新"), SORT_COLUMNS["最新"])
        sql = f"""
            SELECT v.*, m.map_cn, m.agent_cn, m.mode_cn, m.kills, m.deaths, m.assists,
                   m.has_won, m.start_ms, m.end_ms, m.rounds_won, m.rounds_lost,
                   a.nick, a.tag,
                   EXISTS(SELECT 1 FROM favorites f2 WHERE f2.video_id=v.video_id) AS fav,
                   (SELECT COUNT(*) FROM events e2 WHERE e2.video_id=v.video_id) AS event_count
            FROM videos v
            LEFT JOIN matches m ON m.match_id = v.match_id
            LEFT JOIN accounts a ON a.openid = v.openid
            {"WHERE " + " AND ".join(where) if where else ""}
            ORDER BY {order}
            LIMIT ? OFFSET ?"""
        args += [limit, offset]
        with self.conn() as c:
            rows = [dict(r) for r in c.execute(sql, args).fetchall()]
            count_sql = """
                SELECT COUNT(*) AS n FROM videos v
                LEFT JOIN matches m ON m.match_id = v.match_id
                """ + (f"WHERE {' AND '.join(where)}" if where else "")
            n = c.execute(count_sql, args[:-2]).fetchone()["n"]
        return rows, n

    # Dimension -> (grouped column, filter key it must ignore)
    FACET_DIMENSIONS = {
        "maps": ("m.map_cn", "map"),
        "agents": ("m.agent_cn", "agent"),
        "types": ("v.vtype", "vtype"),
        "modes": ("m.mode_cn", "mode"),
    }

    def facets(self, openid: str | None = None, filters: dict | None = None) -> dict:
        """Facet values with counts, under every *other* active filter.

        ``filters`` is the same dict the grid uses. Each dimension is counted
        with that dict minus its own key, so the number in parentheses is
        literally how many rows you get after picking it — including the
        account, the search box, 仅收藏 and 仅我的击杀/死亡.
        """
        f = dict(filters or {})
        if openid:
            f["openid"] = openid

        def group(col: str, own_key: str):
            where, args = self._build_where(f, skip=(own_key,))
            pre = ("WHERE " + " AND ".join(where)) if where else ""
            sql = f"""SELECT {col} AS k, COUNT(*) AS n FROM videos v
                     LEFT JOIN matches m ON m.match_id = v.match_id
                     {pre} GROUP BY {col} ORDER BY n DESC"""
            with self.conn() as c:
                return [(r["k"], r["n"]) for r in c.execute(sql, args).fetchall() if r["k"]]

        where, args = self._build_where(f)
        pre = ("WHERE " + " AND ".join(where)) if where else ""
        with self.conn() as c:
            total = c.execute(
                f"""SELECT COUNT(*) n FROM videos v
                    LEFT JOIN matches m ON m.match_id = v.match_id {pre}""",
                args).fetchone()["n"]
            # Purged files are a property of the index, not of the filter.
            missing = c.execute("SELECT COUNT(*) n FROM videos WHERE file_state=2").fetchone()["n"]
        out = {key: group(col, own) for key, (col, own) in self.FACET_DIMENSIONS.items()}
        out["total"] = total
        out["missing"] = missing
        return out

    def get_video(self, video_id: str):
        with self.conn() as c:
            r = c.execute("""SELECT v.*, m.map_cn, m.agent_cn, m.mode_cn, m.kills, m.deaths,
                             m.assists, m.has_won, m.start_ms, m.end_ms, a.nick, a.tag
                             FROM videos v
                             LEFT JOIN matches m ON m.match_id=v.match_id
                             LEFT JOIN accounts a ON a.openid=v.openid
                             WHERE v.video_id=?""", (video_id,)).fetchone()
            return dict(r) if r else None

    def get_video_by_path(self, path: str):
        """Look up an indexed row by absolute path (used for drag&drop / argv)."""
        with self.conn() as c:
            r = c.execute("""SELECT v.*, m.map_cn, m.agent_cn, m.mode_cn, m.kills, m.deaths,
                             m.assists, m.has_won, m.start_ms, m.end_ms, a.nick, a.tag
                             FROM videos v
                             LEFT JOIN matches m ON m.match_id=v.match_id
                             LEFT JOIN accounts a ON a.openid=v.openid
                             WHERE v.path=?""", (path,)).fetchone()
            return dict(r) if r else None

    # ------------------------------------------------------------- tags/fav/hist
    def set_tags(self, video_id: str, tags):
        with self.conn() as c:
            c.execute("DELETE FROM tags WHERE video_id=?", (video_id,))
            c.executemany("INSERT OR IGNORE INTO tags(video_id,tag) VALUES(?,?)",
                          [(video_id, t) for t in tags if t.strip()])

    def get_tags(self, video_id: str):
        with self.conn() as c:
            return [r["tag"] for r in c.execute(
                "SELECT tag FROM tags WHERE video_id=? ORDER BY tag", (video_id,)).fetchall()]

    def all_tags(self):
        with self.conn() as c:
            return [(r["tag"], r["n"]) for r in c.execute(
                "SELECT tag, COUNT(*) n FROM tags GROUP BY tag ORDER BY n DESC").fetchall()]

    def toggle_favorite(self, video_id: str) -> bool:
        with self.conn() as c:
            r = c.execute("SELECT 1 FROM favorites WHERE video_id=?", (video_id,)).fetchone()
            if r:
                c.execute("DELETE FROM favorites WHERE video_id=?", (video_id,))
                return False
            c.execute("INSERT INTO favorites(video_id,ts) VALUES(?,?)", (video_id, int(time.time())))
            return True

    def add_history(self, video_id: str, pos_s: float, duration_s: float):
        with self.conn() as c:
            c.execute("INSERT INTO play_history(video_id,ts,pos_s,duration_s) VALUES(?,?,?,?)",
                      (video_id, int(time.time()), pos_s, duration_s))
            c.execute("""DELETE FROM play_history WHERE id NOT IN
                         (SELECT id FROM play_history ORDER BY ts DESC LIMIT 500)""")

    def recent(self, limit: int = 60):
        extra = f"WHERE {self.missing_clause}" if self.missing_clause else ""
        with self.conn() as c:
            return [dict(r) for r in c.execute(f"""
                SELECT v.*, m.map_cn, m.agent_cn, h.ts, h.pos_s,
                       MAX(h.ts) AS last_ts
                FROM play_history h
                JOIN videos v ON v.video_id = h.video_id
                LEFT JOIN matches m ON m.match_id = v.match_id
                {extra}
                GROUP BY h.video_id
                ORDER BY last_ts DESC LIMIT ?""", (limit,)).fetchall()]

    def last_position(self, video_id: str):
        with self.conn() as c:
            r = c.execute("""SELECT pos_s FROM play_history WHERE video_id=?
                             ORDER BY ts DESC LIMIT 1""", (video_id,)).fetchone()
            return r["pos_s"] if r else 0.0

    # -------------------------------------------------------------------- roots
    def add_root(self, path: str, kind: str = "local"):
        with self.conn() as c:
            c.execute("INSERT OR IGNORE INTO roots(path,kind,added_ts) VALUES(?,?,?)",
                      (path, kind, int(time.time())))

    def remove_root(self, path: str):
        with self.conn() as c:
            c.execute("DELETE FROM roots WHERE path=?", (path,))

    def list_roots(self, kind: str | None = None):
        with self.conn() as c:
            if kind:
                rows = c.execute("SELECT * FROM roots WHERE kind=? ORDER BY path", (kind,)).fetchall()
            else:
                rows = c.execute("SELECT * FROM roots ORDER BY kind,path").fetchall()
            return [dict(r) for r in rows]

    # ----------------------------------------------------------------- settings
    def get_setting(self, key: str, default: str = ""):
        with self.conn() as c:
            r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return r["value"] if r else default

    def set_setting(self, key: str, value: str):
        with self.conn() as c:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # ------------------------------------------------------------------ utility
    def refresh_file_states(self, paths: list[str] | None = None) -> int:
        """Re-stat indexed files. Cheap (no hashing); marks moved/deleted files."""
        changed = 0
        with self.conn() as c:
            rows = c.execute("SELECT video_id, path FROM videos").fetchall()
            for r in rows:
                state, mtime, size = self._probe_state(r["path"])
                cur = c.execute("SELECT file_state FROM videos WHERE video_id=?",
                                (r["video_id"],)).fetchone()
                if cur and cur["file_state"] != state:
                    c.execute("""UPDATE videos SET file_state=?, file_mtime=?, file_size=?
                                 WHERE video_id=?""", (state, mtime, size, r["video_id"]))
                    changed += 1
        return changed

    def stats(self) -> dict:
        with self.conn() as c:
            v = c.execute("SELECT COUNT(*) n, COALESCE(SUM(size_bytes),0) s FROM videos").fetchone()
            present = c.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(size_bytes),0) s FROM videos "
                "WHERE file_state != 2").fetchone()
            m = c.execute("SELECT COUNT(*) n FROM matches").fetchone()
            e = c.execute("SELECT COUNT(*) n FROM events").fetchone()
            miss = c.execute("SELECT COUNT(*) n FROM videos WHERE file_state=2").fetchone()
            # `videos`/`bytes` describe what the UI lists (missing ones hidden by
            # default); `all_videos`/`all_bytes` keep the raw index totals so the
            # settings page can still report how many clips were purged.
            return {"videos": present["n"], "bytes": present["s"],
                    "all_videos": v["n"], "all_bytes": v["s"],
                    "matches": m["n"], "events": e["n"], "missing": miss["n"]}
