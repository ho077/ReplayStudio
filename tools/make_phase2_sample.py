"""Build a small real sample that exercises Phase 2 features.

The highlights library has no multi-audio / subtitled files, so we derive one
from a real recording with FFmpeg (stream copy — no re-encode, ~1 s):

  out.mkv = 25 s of a real game clip
            + 2 audio tracks (the original stream mapped twice)
            + 1 SRT subtitle track (Chinese + English lines)
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "tests", "_media")
DB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ReplayStudio", "index.db")

SRT = """1
00:00:01,000 --> 00:00:04,000
中文测试字幕 · 第一条
English subtitle line one

2
00:00:06,000 --> 00:00:09,000
第二条：验证 ASS/SRT 渲染与延迟调整

3
00:00:12,000 --> 00:00:16,000
第三条：点击字幕轨道菜单可切换
"""


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    raise RuntimeError("未找到 ffmpeg")


def pick_source() -> str:
    c = sqlite3.connect(DB)
    rows = c.execute("SELECT path FROM videos WHERE file_state=1 "
                     "ORDER BY size_bytes DESC LIMIT 20").fetchall()
    c.close()
    for (p,) in rows:
        if os.path.isfile(p):
            return p
    raise RuntimeError("数据库中没有可用的真实视频")


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    src = pick_source()
    srt = os.path.join(OUT_DIR, "sample.srt")
    out = os.path.join(OUT_DIR, "phase2-sample.mkv")
    with open(srt, "w", encoding="utf-8") as f:
        f.write(SRT)

    cmd = [find_ffmpeg(), "-y", "-ss", "2", "-t", "25", "-i", src,
           "-i", srt,
           "-map", "0:v:0", "-map", "0:a:0", "-map", "0:a:0", "-map", "1:s:0",
           "-c:v", "copy", "-c:a", "copy", "-c:s", "srt",
           "-metadata:s:a:0", "title=音轨A-原声",
           "-metadata:s:a:1", "title=音轨B-副本",
           "-metadata:s:s:0", "title=中英字幕",
           out]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    ok = os.path.isfile(out) and os.path.getsize(out) > 100_000
    msg = {
        "source": src,
        "output": out,
        "size_mb": round(os.path.getsize(out) / 1024 / 1024, 2) if ok else 0,
        "ffmpeg_rc": r.returncode,
        "stderr_tail": (r.stderr or "")[-300:],
        "ok": ok,
    }
    with open(os.path.join(ROOT, "tools", "_mk_sample.txt"), "w",
              encoding="utf-8") as f:
        for k, v in msg.items():
            f.write(f"{k}: {v}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
