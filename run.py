"""Launch ReplayStudio from the project root (双击或 python run.py 均可)。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from replaystudio.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
