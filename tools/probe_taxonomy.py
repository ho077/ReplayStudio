"""Phase 0 probe: enumerate every taxonomy value present in the real ACLOS data.

Answers: which maps / agents / modes / video types must ReplayStudio handle,
and how many videos actually exist on disk (vs. only in the index).
Read-only.
"""
import hashlib
import json
import os
import random
from collections import Counter

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DB_DIR = r"C:\Users\Admin\AppData\Roaming\ACLOS\WonderfulDb"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_taxonomy.txt")


def derive_key_iv(openid):
    h = hashlib.sha256(openid.encode("utf-8")).hexdigest()
    return h[:32].encode(), h[:16].encode()


def decrypt_file(path, openid):
    raw = open(path, "rb").read()
    cipher = bytes.fromhex(raw.decode("utf-8").strip())
    key, iv = derive_key_iv(openid)
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = dec.update(cipher) + dec.finalize()
    return plain[: -plain[-1]].decode("utf-8", errors="replace")


def main():
    maps = Counter()
    agents = {}          # agent_id -> (agent_name, count)
    agent_counts = Counter()
    modes = Counter()
    mode_names = Counter()
    vtypes = Counter()
    vnames = Counter()
    levels = Counter()
    nicks = {}
    total_matches = 0
    total_videos = 0
    video_samples = []

    for name in sorted(os.listdir(DB_DIR)):
        if not name.isdigit() or name.startswith("snapshot"):
            continue
        obj = json.loads(decrypt_file(os.path.join(DB_DIR, name), name))
        arr = obj.get(f"key_wonderful_list_{name}") or []
        total_matches += len(arr)
        for m in arr:
            maps[m.get("map", {}).get("map_id", "?")] += 1
            ag = m.get("agent", {})
            aid = ag.get("agent_id", "?")
            agents.setdefault(aid, ag.get("agent_name", "?"))
            agent_counts[aid] += 1
            modes[m.get("mode", "?")] += 1
            mode_names[(m.get("stats") or {}).get("mode_name", "?")] += 1
            for v in (m.get("videos") or []):
                total_videos += 1
                vtypes[v.get("video_type", "?")] += 1
                vnames[v.get("video_name", "?")] += 1
                levels[v.get("video_level", "?")] += 1
                video_samples.append(v)

    # snapshots -> nick
    for name in sorted(os.listdir(DB_DIR)):
        if not name.startswith("snapshot"):
            continue
        oid = name[len("snapshot"):]
        try:
            obj = json.loads(decrypt_file(os.path.join(DB_DIR, name), oid))
        except Exception:
            continue
        arr = obj.get(f"key_snapshot_list{oid}") or []
        for rec in arr:
            sn = rec.get("snapshot") or {}
            if sn.get("ss_nick"):
                nicks[oid] = (sn.get("ss_nick"), sn.get("ss_nick_id"))

    # existence check on a sample
    random.seed(7)
    sample = random.sample(video_samples, min(300, len(video_samples)))
    exist_v = miss_v = 0
    exist_p = miss_p = 0
    exist_cover = 0
    for v in sample:
        p = v.get("video_src", "")
        if p and os.path.isfile(p):
            exist_v += 1
        else:
            miss_v += 1
        c = v.get("video_poster", "")
        if c:
            if os.path.isfile(c):
                exist_p += 1
                exist_cover += 1
            else:
                miss_p += 1

    lines = []
    A = lines.append
    A(f"total matches: {total_matches}")
    A(f"total videos : {total_videos}")
    A("")
    A("=== nicks ===")
    for k, v in nicks.items():
        A(f"  {k}: {v}")
    A("")
    A("=== maps (%d unique) ===" % len(maps))
    for k, c in maps.most_common():
        A(f"  {c:5d}  {k}")
    A("")
    A("=== agents (%d unique) ===" % len(agents))
    for aid, c in agent_counts.most_common():
        A(f"  {c:5d}  {aid}  {agents.get(aid)}")
    A("")
    A("=== modes ===")
    for k, c in modes.most_common():
        A(f"  {c:5d}  mode={k}")
    A("=== stats.mode_name ===")
    for k, c in mode_names.most_common():
        A(f"  {c:5d}  {k}")
    A("")
    A("=== video_type ===")
    for k, c in vtypes.most_common():
        A(f"  {c:5d}  {k}")
    A("")
    A("=== video_name (top 40) ===")
    for k, c in vnames.most_common(40):
        A(f"  {c:5d}  {k}")
    A("")
    A("=== video_level ===")
    for k, c in levels.most_common():
        A(f"  {c:5d}  {k}")
    A("")
    A(f"=== file existence (sample {len(sample)}) ===")
    A(f"  mp4 exists  : {exist_v}   missing: {miss_v}")
    A(f"  cover exists: {exist_p}   missing/missing-field: {miss_p}")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("WROTE", OUT)


if __name__ == "__main__":
    main()
