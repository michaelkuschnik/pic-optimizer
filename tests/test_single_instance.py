"""
Testcase: Single-Instance-Mechanismus
Sicherstellt, dass beim Start alle alten Optimizer-Instanzen gekillt werden.
"""
import os
import subprocess
import sys
import time

PYTHON = sys.executable
MAIN = os.path.join(os.path.dirname(__file__), "..", "main.py")


def _running_optimizer_pids(exclude_pid=None):
    result = subprocess.run(
        ["wmic", "process", "where", "name='python.exe'",
         "get", "processid,commandline", "/format:csv"],
        capture_output=True, text=True, timeout=5,
    )
    pids = []
    for line in result.stdout.splitlines():
        if "pic_optimizer" not in line:
            continue
        parts = line.strip().split(",")
        try:
            pid = int(parts[-1].strip())
            if pid != exclude_pid:
                pids.append(pid)
        except ValueError:
            pass
    return pids


def test_no_duplicate_instances():
    """Nach dem Start darf nur eine Optimizer-Instanz laufen."""
    # Vorher: alle Instanzen killen
    for pid in _running_optimizer_pids():
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    time.sleep(1)

    # Erste Instanz starten
    p1 = subprocess.Popen(
        [PYTHON, MAIN],
        cwd=os.path.dirname(MAIN),
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)  # Fenster aufbauen lassen

    pids_after_first = _running_optimizer_pids(exclude_pid=os.getpid())
    assert len(pids_after_first) == 1, f"Erwartet 1 Instanz, gefunden: {pids_after_first}"

    # Zweite Instanz starten → soll erste killen
    p2 = subprocess.Popen(
        [PYTHON, MAIN],
        cwd=os.path.dirname(MAIN),
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)

    pids_after_second = _running_optimizer_pids(exclude_pid=os.getpid())
    assert len(pids_after_second) == 1, (
        f"Nach zweitem Start: erwartet 1 Instanz, gefunden: {pids_after_second}"
    )
    assert p1.pid not in pids_after_second, "Alte Instanz wurde nicht gekillt"

    # Aufräumen
    for pid in pids_after_second:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)


if __name__ == "__main__":
    test_no_duplicate_instances()
    print("PASS: test_no_duplicate_instances")
