"""Phase 0 probe: decrypt real ACLOS WonderfulDb files and dump structure.

Read-only. Never writes into the ACLOS directory.
Output goes to tools/_probe_out.txt because Bash shim sometimes swallows stdout.
"""
import hashlib
import json
import os
import sys

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DB_DIR = r"C:\Users\Admin\AppData\Roaming\ACLOS\WonderfulDb"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_probe_out.txt")

lines = []


def log(*a):
    s = " ".join(str(x) for x in a)
    lines.append(s)


def derive_key_iv(openid: str):
    hexdigest = hashlib.sha256(openid.encode("utf-8")).hexdigest()
    return hexdigest[:32].encode(), hexdigest[:16].encode()


def decrypt_file(path: str, openid: str) -> str:
    raw = open(path, "rb").read()
    text = raw.decode("utf-8", errors="strict").strip()
    cipher = bytes.fromhex(text)
    key, iv = derive_key_iv(openid)
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = dec.update(cipher) + dec.finalize()
    pad = plain[-1]
    if 1 <= pad <= 16:
        plain = plain[:-pad]
    return plain.decode("utf-8", errors="replace")


def main():
    entries = sorted(os.listdir(DB_DIR))
    accounts = [e for e in entries if e.isdigit() and not e.startswith("snapshot")]
    snapshots = [e for e in entries if e.startswith("snapshot")]
    log("db dir:", DB_DIR)
    log("accounts:", accounts)
    log("snapshots:", snapshots)

    for acc in accounts:
        path = os.path.join(DB_DIR, acc)
        size = os.path.getsize(path)
        log("\n" + "=" * 70)
        log("ACCOUNT", acc, "size", size)
        try:
            text = decrypt_file(path, acc)
        except Exception as exc:  # noqa
            log("  DECRYPT FAILED:", type(exc).__name__, exc)
            continue
        log("  plaintext chars:", len(text))
        try:
            obj = json.loads(text)
        except Exception as exc:
            log("  JSON FAILED:", exc, "head:", text[:200])
            continue
        if not isinstance(obj, dict):
            log("  top-level is", type(obj))
            continue
        log("  top-level keys:", list(obj.keys())[:5])
        list_key = f"key_wonderful_list_{acc}"
        arr = obj.get(list_key)
        if arr is None:
            for v in obj.values():
                if isinstance(v, list):
                    arr = v
                    break
        log("  matches:", 0 if arr is None else len(arr))
        if not arr:
            continue
        m = arr[0]
        log("  match keys:", sorted(m.keys()))
        log("  --- first match raw (truncated) ---")
        log(json.dumps(m, ensure_ascii=False)[:3000])
        # video structure
        vids = m.get("videos") or []
        log("  videos in first match:", len(vids))
        if vids:
            v = vids[0]
            log("  video keys:", sorted(v.keys()))
            log("  --- first video (truncated) ---")
            log(json.dumps(v, ensure_ascii=False)[:2500])
        # aggregate video count across all matches
        total = sum(len(x.get("videos") or []) for x in arr if isinstance(x, dict))
        log("  TOTAL videos in account:", total)
        # collect a few video paths
        paths = []
        for x in arr:
            for v in (x.get("videos") or []):
                for k, val in v.items():
                    if isinstance(val, str) and (".mp4" in val.lower() or ".mkv" in val.lower()):
                        paths.append((k, val))
                if len(paths) >= 8:
                    break
            if len(paths) >= 8:
                break
        log("  sample path fields:", paths[:8])

    # snapshot
    for snap in snapshots:
        openid = snap[len("snapshot"):]
        path = os.path.join(DB_DIR, snap)
        log("\n" + "-" * 70)
        log("SNAPSHOT", snap, "openid", openid, "size", os.path.getsize(path))
        try:
            text = decrypt_file(path, openid)
            obj = json.loads(text)
            key = f"key_snapshot_list{openid}"
            arr = obj.get(key) if isinstance(obj, dict) else None
            if arr is None and isinstance(obj, dict):
                for v in obj.values():
                    if isinstance(v, list):
                        arr = v
                        break
            log("  records:", 0 if arr is None else len(arr))
            if arr:
                rec = arr[0]
                log("  rec keys:", sorted(rec.keys()))
                sn = rec.get("snapshot") or {}
                log("  ss_nick:", sn.get("ss_nick"), "ss_nick_id:", sn.get("ss_nick_id"))
        except Exception as exc:
            log("  snapshot decrypt failed:", type(exc).__name__, exc)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("WROTE", OUT, len(lines), "lines")


if __name__ == "__main__":
    main()
