"""Two real processes: A listens, B connects and sends 'open'. A must call back."""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
PY = sys.executable

A = r"""
import sys, os
sys.path.insert(0, r'{src}')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
import replaystudio.core.single_instance as si
app = QApplication([])
first = si.SingleInstance(app)
assert first.acquire(lambda fs: print('CALLBACK', fs, flush=True))
def quit():
    print('A done', flush=True)
    app.quit()
QTimer.singleShot(2500, quit)
app.exec()
""".replace("{src}", SRC)

B = r"""
import sys, os
sys.path.insert(0, r'{src}')
from PySide6.QtWidgets import QApplication
import replaystudio.core.single_instance as si
app = QApplication([])
s = si.SingleInstance(app)
print('B acquire-refused?', not s.acquire(None))
print('B forward', s.forward_or_warn([r'{marker}']), flush=True)
""".replace("{src}", SRC).replace("{marker}", sys.argv[1] if len(sys.argv) > 1 else "C:/x.txt")


def main():
    marker = os.path.join(os.environ.get("TEMP", "/tmp"), "si-probe-marker.txt")
    if os.path.isfile(marker):
        os.remove(marker)
    code_b = B.replace("C:/x.txt", marker)
    pa = subprocess.Popen([PY, "-c", A], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True)
    time.sleep(1.2)
    pb = subprocess.run([PY, "-c", code_b], stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, timeout=20)
    out_a = pa.communicate(timeout=20)[0]
    print("--- A ---"); print(out_a)
    print("--- B ---"); print(pb.stdout)
    print("marker exists:", os.path.isfile(marker))
    ok = "CALLBACK" in out_a and os.path.isfile(marker)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
