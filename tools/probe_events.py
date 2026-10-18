"""Dump complete event_ext objects so the event model can be built on facts."""
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DB_DIR = r"C:\Users\Admin\AppData\Roaming\ACLOS\WonderfulDb"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_events_out.txt")


def dec(path, oid):
    raw = open(path, "rb").read()
    cipher = bytes.fromhex(raw.decode("utf-8").strip())
    h = hashlib.sha256(oid.encode()).hexdigest()
    k, iv = h[:32].encode(), h[:16].encode()
    d = Cipher(algorithms.AES(k), modes.CBC(iv)).decryptor()
    p = d.update(cipher) + d.finalize()
    return p[: -p[-1]].decode("utf-8", errors="replace")


def main():
    out = []
    wanted = {"击杀集锦", "死亡集锦", "三杀时刻"}
    seen = set()
    for name in sorted(os.listdir(DB_DIR)):
        if not name.isdigit() or name.startswith("snapshot"):
            continue
        arr = json.loads(dec(os.path.join(DB_DIR, name), name)).get(f"key_wonderful_list_{name}") or []
        for m in arr:
            for v in (m.get("videos") or []):
                vt = v.get("video_type")
                if vt in wanted and vt not in seen:
                    rounds = v.get("rounds") or []
                    if not rounds:
                        continue
                    clips = rounds[0].get("round_clips") or []
                    if not clips:
                        continue
                    evs = clips[0].get("clip_events") or []
                    if not evs:
                        continue
                    seen.add(vt)
                    out.append(f"### video_type={vt}  video_name={v.get('video_name')}")
                    out.append("round keys: " + json.dumps(list(rounds[0].keys()), ensure_ascii=False))
                    out.append("clip keys : " + json.dumps(list(clips[0].keys()), ensure_ascii=False))
                    out.append("clip: " + json.dumps({k: c for k, c in clips[0].items() if k != "clip_events"},
                                                     ensure_ascii=False))
                    out.append("event keys: " + json.dumps(list(evs[0].keys()), ensure_ascii=False))
                    out.append("event_sTime=" + str(evs[0].get("event_sTime")) + " type=" + str(evs[0].get("event_type")))
                    out.append("event_ext: " + json.dumps(evs[0].get("event_ext"), ensure_ascii=False, indent=2))
                    out.append("")
        if len(seen) == len(wanted):
            break
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("WROTE", OUT, "types:", sorted(seen))


if __name__ == "__main__":
    main()
