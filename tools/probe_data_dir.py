"""Where does the app keep its own data, and does the C: -> D: move work?

Three checks, in order of how much they touch:

1. ``REPLAYSTUDIO_DATA_DIR`` wins over everything (that is what keeps the smoke
   tests out of the real index).
2. migration copies an existing ``%LOCALAPPDATA%\\ReplayStudio`` into the new
   folder, and never deletes the source.
3. the real thing: what the default resolves to on this machine, whether
   index.db landed there, and whether it is on C:.

    .venv\\Scripts\\python.exe tools\\probe_data_dir.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from replaystudio.core import db as core_db  # noqa: E402
from replaystudio.core import diagnostics  # noqa: E402

FAILS: list[str] = []


def check(label: str, ok: bool, detail: str = ""):
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(label)


def _reset():
    core_db._data_dir_cache = None


print("== 1. env override ==")
tmp = tempfile.mkdtemp(prefix="rs-data-")
os.environ[core_db.DATA_DIR_ENV] = tmp
_reset()
got = core_db.user_data_dir()
check("env wins", os.path.normcase(got) == os.path.normcase(tmp), got)
check("no migration under an explicit override",
      not os.path.exists(os.path.join(tmp, "index.db")))
del os.environ[core_db.DATA_DIR_ENV]
_reset()
shutil.rmtree(tmp, ignore_errors=True)

print("== 2. migration from a fake legacy folder ==")
sandbox = tempfile.mkdtemp(prefix="rs-mig-")
old = os.path.join(sandbox, "old")
new = os.path.join(sandbox, "new")
os.makedirs(os.path.join(old, "thumbs"))
with open(os.path.join(old, "index.db"), "wb") as f:
    f.write(b"fake")
with open(os.path.join(old, "thumbs", "a.jpg"), "wb") as f:
    f.write(b"jpeg")
with open(os.path.join(old, "open_req_123.json"), "wb") as f:
    f.write(b"scratch")
os.makedirs(new)

real_legacy = core_db.legacy_data_dir
try:
    core_db.legacy_data_dir = lambda: old
    moved = core_db._migrate_legacy_data(old, new)
    check("reported a move", moved)
    check("index.db copied", os.path.isfile(os.path.join(new, "index.db")))
    check("subfolder copied", os.path.isfile(os.path.join(new, "thumbs", "a.jpg")))
    check("hand-off scratch skipped",
          not os.path.exists(os.path.join(new, "open_req_123.json")))
    check("source left alone", os.path.isfile(os.path.join(old, "index.db")))
    # second run must be a no-op, otherwise we would re-copy on every start
    again = core_db._migrate_legacy_data(old, new)
    check("second run is a no-op", not again)
finally:
    core_db.legacy_data_dir = real_legacy
shutil.rmtree(sandbox, ignore_errors=True)

print("== 3. the real default on this machine ==")
_reset()
default = core_db.user_data_dir()
legacy = core_db.legacy_data_dir()
print(f"  default : {default}")
print(f"  legacy  : {legacy}")
check("default is not the legacy folder",
      os.path.normcase(os.path.abspath(default)) != os.path.normcase(os.path.abspath(legacy)))
check("default is off C:", os.path.splitdrive(default)[0].upper() != "C:",
      os.path.splitdrive(default)[0] or "(no drive)")
check("data folder exists", os.path.isdir(default))
idx = os.path.join(default, "index.db")
print(f"  index.db: {idx}  ({os.path.getsize(idx) if os.path.isfile(idx) else '-'} bytes)")
if os.path.isfile(idx):
    d = core_db.Database()
    try:
        print(f"  videos  : {d.stats().get('videos')}")
    finally:
        d.close()
else:
    print("  videos  : (no index yet — the next start will build one)")
logs = str(diagnostics.log_dir())
print(f"  logs    : {logs}")
check("logs follow the data folder",
      os.path.normcase(logs).startswith(os.path.normcase(default)))

print()
if FAILS:
    print(f"FAIL ({len(FAILS)}): {'; '.join(FAILS)}")
    sys.exit(1)
print("all good")
