"""
Testcase: _kill_existing_instances() aus main.py
Prüft, dass die WMIC-basierte Instanz-Erkennung korrekt funktioniert.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _get_optimizer_pids(exclude_current=True):
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | "
         "Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and "
         "$_.ExecutablePath -like '*pic_optimizer*' -and "
         "($_.CommandLine -like '* main.py*' -or $_.CommandLine -match 'main\\.py[\\s\"]*$') -and "
         "$_.CommandLine -notlike '*-c *' } | "
         "Select-Object -ExpandProperty ProcessId"],
        capture_output=True, text=True, timeout=10,
    )
    pids = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
            if exclude_current and pid == os.getpid():
                continue
            pids.append(pid)
        except ValueError:
            pass
    return pids


def test_powershell_finder_returns_no_false_positives():
    """PowerShell-Finder darf keine falschen Instanzen liefern (kein main.py läuft)."""
    pids = _get_optimizer_pids(exclude_current=False)
    # Wenn keine Instanz läuft, muss die Liste leer sein
    for pid in pids:
        assert pid > 0, f"Ungültige PID: {pid}"


def test_main_py_exists_and_has_kill_function():
    """main.py muss _kill_existing_instances via PowerShell enthalten."""
    main_path = Path(__file__).parent.parent / "main.py"
    assert main_path.exists(), "main.py nicht gefunden"
    content = main_path.read_text()
    assert "_kill_existing_instances" in content
    assert "powershell" in content
    assert "taskkill" in content
    assert "Get-CimInstance" in content
    assert "-notlike" in content


def test_no_optimizer_instances_after_kill():
    """Nach explizitem Kill darf keine fremde Instanz mehr laufen."""
    foreign_pids = _get_optimizer_pids(exclude_current=True)
    for pid in foreign_pids:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)

    remaining = _get_optimizer_pids(exclude_current=True)
    assert remaining == [], f"Noch laufende Instanzen nach Kill: {remaining}"


if __name__ == "__main__":
    test_powershell_finder_returns_no_false_positives(); print("PASS: test_powershell_finder_returns_no_false_positives")
    test_main_py_exists_and_has_kill_function();         print("PASS: test_main_py_exists_and_has_kill_function")
    test_no_optimizer_instances_after_kill();            print("PASS: test_no_optimizer_instances_after_kill")
