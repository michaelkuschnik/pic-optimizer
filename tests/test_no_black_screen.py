"""
Testcase: Kein schwarzes Ghost-Fenster beim Start
Prüft, dass _kill_existing_instances() alte Fenster graceful schließt,
sodass kein schwarzes Restfenster auf dem Desktop verbleibt.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_venv  = Path(__file__).parent.parent / ".venv" / "Scripts"
PYTHON = str(_venv / "pythonw.exe")   # kein Konsolenfenster → CloseMainWindow trifft Qt-Fenster
MAIN   = "main.py"   # relativ → cwd=WORK → Commandline enthält " main.py"
WORK   = str(Path(__file__).parent.parent)


def _kill_all_optimizer():
    """Killt alle laufenden Optimizer-Instanzen (für sauberen Testzustand)."""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | "
         "Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and "
         "$_.ExecutablePath -like '*pic_optimizer*' -and "
         "($_.CommandLine -like '* main.py*' -or $_.CommandLine -match 'main\\.py[\\s\"]*$') -and "
         "$_.CommandLine -notlike '*-c *' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
        capture_output=True, timeout=10,
    )
    time.sleep(0.5)


def _optimizer_pids() -> list[int]:
    """Gibt alle laufenden Optimizer-PIDs zurück."""
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
            pids.append(int(line))
        except ValueError:
            pass
    return pids


def _start_app() -> subprocess.Popen:
    return subprocess.Popen(
        [PYTHON, MAIN], cwd=WORK,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def test_main_uses_graceful_close():
    """main.py muss CloseMainWindow() und time.sleep() vor taskkill verwenden."""
    main_path = Path(WORK) / "main.py"
    content = main_path.read_text(encoding="utf-8")
    assert "CloseMainWindow" in content, "CloseMainWindow fehlt – kein graceful close"
    assert "time.sleep" in content,      "time.sleep fehlt – kein Warten vor force-kill"
    assert "taskkill" in content,        "taskkill fehlt – kein force-kill als Fallback"
    assert "/T" in content,              "/T fehlt – Kind-Prozesse (Qt) werden nicht mitgekilled"


def test_filter_uses_executable_path():
    """PowerShell-Filter muss ExecutablePath nutzen, nicht CommandLine für pic_optimizer-Check.
    So werden auch Prozesse mit relativem Startpfad erkannt."""
    main_path = Path(WORK) / "main.py"
    content = main_path.read_text(encoding="utf-8")
    assert "ExecutablePath" in content, \
        "Filter nutzt CommandLine statt ExecutablePath – relative Startpfade werden nicht erkannt"
    assert "pythonw.exe" in content, \
        "pythonw.exe fehlt im Filter"


def test_only_one_instance_after_second_start():
    """Nach dem Start einer zweiten Instanz darf nur noch eine Optimizer-Instanz laufen."""
    _kill_all_optimizer()

    # Erste Instanz starten
    _start_app()
    time.sleep(3)
    pids_1 = _optimizer_pids()
    assert len(pids_1) >= 1, f"Erste Instanz nicht gefunden: {pids_1}"

    # Zweite Instanz starten – soll erste killen
    _start_app()
    time.sleep(4)  # Zeit für graceful close + neues Fenster
    pids_2 = _optimizer_pids()

    assert len(pids_2) == 1, \
        f"Erwartet 1 laufende Instanz nach zweitem Start, gefunden: {pids_2}"

    _kill_all_optimizer()


def _optimizer_qt_pid() -> int | None:
    """Gibt die PID des echten Qt-Prozesses zurück (Fenster-Titel 'Optimizer')."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and "
         "($_.Name -eq 'python' -or $_.Name -eq 'pythonw') -and "
         "$_.MainWindowTitle -eq 'Optimizer' } | "
         "Select-Object -ExpandProperty Id -First 1"],
        capture_output=True, text=True, timeout=10,
    )
    for line in result.stdout.splitlines():
        line = line.strip()
        try:
            return int(line)
        except ValueError:
            pass
    return None


def test_graceful_close_responds_to_close_window():
    """CloseMainWindow() soll den echten Qt-Prozess innerhalb von 3 s schließen."""
    _kill_all_optimizer()

    _start_app()
    time.sleep(4)  # App vollständig starten lassen

    qt_pid = _optimizer_qt_pid()
    assert qt_pid is not None, "Qt-Fenster 'Optimizer' nicht gefunden – App nicht gestartet?"

    # Graceful close direkt an den Qt-Prozess (nicht den UV-Shim)
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"$p = Get-Process -Id {qt_pid} -ErrorAction SilentlyContinue; "
         f"if ($p) {{ $p.CloseMainWindow() | Out-Null }}"],
        capture_output=True, timeout=5,
    )
    time.sleep(3)  # 3 s Zeit zum sauberen Schließen

    still_alive = _optimizer_qt_pid()
    assert still_alive is None, \
        f"Qt-Prozess (PID {qt_pid}) reagiert nicht auf CloseMainWindow – black screen möglich!"


if __name__ == "__main__":
    test_main_uses_graceful_close()
    print("PASS: test_main_uses_graceful_close")

    test_filter_uses_executable_path()
    print("PASS: test_filter_uses_executable_path")

    test_only_one_instance_after_second_start()
    print("PASS: test_only_one_instance_after_second_start")

    test_graceful_close_responds_to_close_window()
    print("PASS: test_graceful_close_responds_to_close_window")
