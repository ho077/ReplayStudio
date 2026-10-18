"""Read-only adapter for ACLOS ("无畏时刻") local highlight databases.

Everything here reads. Nothing here writes, renames, moves or deletes ACLOS
files. On any inconsistency we keep the raw value and let the UI decide how to
display it, instead of guessing a nicer-looking label.

Account layout (see docs/ACLOS_FORMAT.md):
    %APPDATA%\\ACLOS\\WonderfulDb\\<openid>          main match list
    %APPDATA%\\ACLOS\\WonderfulDb\\snapshot<openid>  optional nickname / MVP data
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

from . import taxonomy as T
from .crypto import decrypt_account_file

MONTAGE_TYPES = T.MONTAGE_TYPES

# Weapon class -> plain Chinese category. Only the weapon *class* is mapped;
# individual skins are deliberately not translated because we cannot verify them.
WEAPON_CLASS_CN = {
    "AssaultRifle": "步枪",
    "BasePistol": "手枪",
    "DS_Gun": "狙击枪",
    "Sniper": "狙击枪",
    "Shotgun": "霰弹枪",
    "SMG": "冲锋枪",
    "LMG": "轻机枪",
    "Melee": "近战",
    "Bomb": "爆能器",
    "Ability": "技能",
}


def default_db_dir() -> str:
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    return os.path.join(appdata, "ACLOS", "WonderfulDb")


@dataclass
class ClipEvent:
    """One kill/death rendered on the video timeline."""
    ms: int
    etype: str                 # "kill" | "death"
    killer: str = ""
    killed: str = ""
    agent: str = ""
    weapon: str = ""
    weapon_class: str = ""
    round_id: str = ""
    event_time: str = ""
    is_mine: bool = False      # the local player is the killer (kill) or victim (death)

    @property
    def is_kill(self) -> bool:
        return self.etype == "kill"

    @property
    def title(self) -> str:
        if self.is_kill:
            return f"击杀 {self.killed}" if self.killed else "击杀"
        return f"被 {self.killer} 击杀" if self.killer else "死亡"


@dataclass
class Video:
    video_id: str
    match_id: str
    openid: str
    path: str = ""
    poster: str = ""
    duration_ms: int = 0
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    level: str = ""
    vtype: str = ""
    vname: str = ""
    created_ms: int = 0
    is_montage: bool = False
    events: list[ClipEvent] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.duration_ms / 1000.0

    @property
    def resolution(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return ""

    @property
    def display_name(self) -> str:
        return self.vname or self.vtype or os.path.basename(self.path)


@dataclass
class Match:
    match_id: str
    openid: str
    map_id: str = ""
    map_cn: str = ""
    agent_id: str = ""
    agent_cn: str = ""
    mode: str = ""
    mode_cn: str = ""
    start_ms: int = 0
    end_ms: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    score: int = 0
    has_won: bool = False
    rounds_won: int = 0
    rounds_lost: int = 0
    videos: list[Video] = field(default_factory=list)

    @property
    def kda(self) -> str:
        return f"{self.kills}/{self.deaths}/{self.assists}"

    @property
    def result(self) -> str:
        return "胜利" if self.has_won else "失败"


@dataclass
class SnapshotInfo:
    nick: str = ""
    tag: str = ""
    achievements: dict[str, str] = field(default_factory=dict)   # match_id -> "MVP"/"SVP"


@dataclass
class Account:
    openid: str
    db_path: str
    snapshot_path: str = ""
    nick: str = ""
    tag: str = ""

    @property
    def display(self) -> str:
        return f"{self.nick}#{self.tag}" if self.nick else f"账号 {self.openid}"


def list_accounts(db_dir: str | None = None) -> list[Account]:
    """Enumerate account files. A valid account file name is pure decimal digits."""
    db_dir = db_dir or default_db_dir()
    if not os.path.isdir(db_dir):
        return []
    out = []
    for name in sorted(os.listdir(db_dir)):
        if not name.isdigit() or name.startswith("snapshot"):
            continue
        p = os.path.join(db_dir, name)
        if not os.path.isfile(p):
            continue
        snap = os.path.join(db_dir, f"snapshot{name}")
        out.append(Account(openid=name, db_path=p,
                           snapshot_path=snap if os.path.isfile(snap) else ""))
    return out


def _to_ms(text: str | None) -> int:
    """'2025-09-27 20:59:38.294' -> epoch milliseconds. Returns 0 when unparsable."""
    if not text:
        return 0
    try:
        return int(datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S.%f").timestamp() * 1000)
    except ValueError:
        pass
    try:
        return int(datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
    except ValueError:
        return 0


def _parse_resolution(raw: str) -> tuple[int, int]:
    """ACLOS writes '1920\\rx1080\\r' — strip control chars before splitting."""
    if not raw:
        return 0, 0
    clean = re.sub(r"[\r\n\t]", "", raw)
    m = re.match(r"(\d+)\s*[xX*]\s*(\d+)", clean)
    if not m:
        return 0, 0
    return int(m.group(1)), int(m.group(2))


def _weapon_of(ext: dict) -> tuple[str, str]:
    wid = ext.get("WeaponID", "") or ""
    cls = ""
    for key in WEAPON_CLASS_CN:
        if f"/{key}_" in wid or wid.endswith(key):
            cls = key
            break
    if not cls:
        m = re.search(r"PersistentLevel\.([A-Za-z0-9_]+)_C_", wid)
        cls = m.group(1) if m else ""
    return cls, WEAPON_CLASS_CN.get(cls, cls)


def _events_for_video(vtype: str, rounds: list) -> list[ClipEvent]:
    """Shared event state machine (see docs/ACLOS_FORMAT.md).

    montage 击杀集锦 -> kill events at event_sTime
    montage 死亡集锦 -> death events at event_sTime
    any other moment -> kill events at clip_sTime + event_sTime

    Only EventName == "Shot" rows are trustworthy seek targets; anything else is
    dropped rather than shown as a fake marker.
    """
    out: list[ClipEvent] = []
    montage = vtype in MONTAGE_TYPES
    for rnd in rounds or []:
        for clip in (rnd.get("round_clips") or []):
            clip_s = int(clip.get("clip_sTime") or 0)
            for ev in (clip.get("clip_events") or []):
                ext = ev.get("event_ext") or {}
                if ext.get("EventName") != "Shot":
                    continue
                etype = ev.get("event_type") or ""
                ev_s = int(ev.get("event_sTime") or 0)
                if montage:
                    if (vtype == "击杀集锦" and etype != "kill") or \
                       (vtype == "死亡集锦" and etype != "death"):
                        continue
                    ms = ev_s
                else:
                    if etype != "kill":
                        continue
                    ms = clip_s + ev_s
                cls, cls_cn = _weapon_of(ext)
                killer = ext.get("KillerPlayerName") or ""
                killed = ext.get("KilledPlayerName") or ""
                out.append(ClipEvent(
                    ms=ms, etype=etype,
                    killer=killer, killed=killed,
                    agent=T.agent_display(ext.get("AgentID", ""), ext.get("AgentName", "")),
                    weapon=cls, weapon_class=cls_cn,
                    round_id=str(ext.get("RoundID", "")),
                    event_time=ext.get("EventTime", ""),
                    is_mine=bool(ext.get("KillerIsMe")) if etype == "kill"
                    else bool(ext.get("KilledIsMe")),
                ))
    out.sort(key=lambda e: e.ms)
    return out


def parse_account_file(db_path: str, openid: str) -> list[Match]:
    """Decrypt + parse one WonderfulDb account file into Match objects."""
    with open(db_path, "rb") as f:
        raw = f.read()
    plain = decrypt_account_file(raw, openid)
    obj = json.loads(plain.decode("utf-8", errors="replace"))
    if not isinstance(obj, dict):
        raise ValueError(f"{db_path}: top level JSON is not an object")
    arr = obj.get(f"key_wonderful_list_{openid}")
    if arr is None:
        arr = next((v for v in obj.values() if isinstance(v, list)), None)
    if arr is None:
        raise ValueError(f"{db_path}: no match list found")

    matches: list[Match] = []
    for m in arr:
        if not isinstance(m, dict):
            continue
        stats = m.get("stats") or {}
        map_id = (m.get("map") or {}).get("map_id", "")
        agent = m.get("agent") or {}
        mode = m.get("mode") or ""
        mode_name = stats.get("mode_name") or ""
        match = Match(
            match_id=m.get("matches_id") or "",
            openid=openid,
            map_id=map_id,
            map_cn=T.map_display(map_id),
            agent_id=agent.get("agent_id", ""),
            agent_cn=T.agent_display(agent.get("agent_id", ""), agent.get("agent_name", "")),
            mode=mode,
            mode_cn=T.mode_display(mode, mode_name),
            start_ms=int(m.get("matches_time") or 0),
            end_ms=_to_ms(m.get("gameEndTime")),
            kills=int(stats.get("kills") or 0),
            deaths=int(stats.get("deaths") or 0),
            assists=int(stats.get("assists") or 0),
            score=int(stats.get("score") or 0),
            has_won=bool(stats.get("has_won")),
            rounds_won=int(stats.get("rounds_won") or 0),
            rounds_lost=int(stats.get("rounds_lost") or 0),
        )
        for v in (m.get("videos") or []):
            if not isinstance(v, dict):
                continue
            w, h = _parse_resolution(v.get("video_resolution", ""))
            vtype = v.get("video_type") or ""
            vid = Video(
                video_id=v.get("video_id") or "",
                match_id=match.match_id,
                openid=openid,
                path=v.get("video_src") or "",
                poster=v.get("video_poster") or "",
                duration_ms=int(v.get("video_duration") or 0),
                size_bytes=int(v.get("video_size") or 0),
                width=w, height=h,
                fps=float(v.get("video_fps") or 0),
                level=str(v.get("video_level") or ""),
                vtype=vtype,
                vname=v.get("video_name") or "",
                created_ms=int(v.get("video_time") or 0),
                is_montage=vtype in MONTAGE_TYPES,
                events=_events_for_video(vtype, v.get("rounds") or []),
            )
            match.videos.append(vid)
        matches.append(match)
    return matches


def parse_snapshot_file(path: str, openid: str) -> SnapshotInfo:
    """Nickname / tag / MVP-SVP from snapshot<openid>. Never raises for bad data."""
    info = SnapshotInfo()
    if not path or not os.path.isfile(path):
        return info
    try:
        with open(path, "rb") as f:
            raw = f.read()
        plain = decrypt_account_file(raw, openid)
        obj = json.loads(plain.decode("utf-8", errors="replace"))
    except Exception:
        return info
    if not isinstance(obj, dict):
        return info
    arr = obj.get(f"key_snapshot_list{openid}")
    if arr is None:
        arr = next((v for v in obj.values() if isinstance(v, list)), None)
    if not isinstance(arr, list):
        return info

    best_time = float("-inf")
    for rec in arr:
        if not isinstance(rec, dict):
            continue
        snap = rec.get("snapshot") or {}
        if not isinstance(snap, dict):
            continue
        t = rec.get("matches_time") or snap.get("ss_time") or 0
        try:
            t = int(t)
        except (TypeError, ValueError):
            t = 0
        nick = (snap.get("ss_nick") or "").strip()
        tag = (snap.get("ss_nick_id") or "").strip()
        if (nick or tag) and t >= best_time:
            best_time = t
            if nick:
                info.nick = nick
            if tag:
                info.tag = tag
        ach = (snap.get("ss_achieve_type") or "").lower()
        mid = rec.get("matches_id") or ""
        if ach in ("mvp", "svp") and mid:
            info.achievements[mid] = (snap.get("ss_type_str") or ach).strip()
    return info
