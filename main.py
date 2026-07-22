import sys
import os
import time
import subprocess
import ctypes

# UV-Shim (CONSOLE-Subsystem) erzeugt sonst ein schwarzes Konsolenfenster.
# Wird per Optimizer.vbs mit WindowStyle=0 gestartet → Fenster bleibt versteckt.
# Zusätzlich: Konsolenfenster per API verstecken, falls direkt gestartet.
if os.name == "nt":
    _hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if _hwnd:
        ctypes.windll.user32.ShowWindow(_hwnd, 0)  # SW_HIDE

from PyQt6.QtWidgets import QApplication
from app.main_window import MainWindow


def _kill_existing_instances():
    """Findet alle laufenden Optimizer-Instanzen via PowerShell und killt sie.

    Ablauf:
    1. PIDs ermitteln (ExecutablePath für pic_optimizer-Check, weil CommandLine
       bei relativem Start keinen absoluten Pfad enthält)
    2. Fenster per CloseMainWindow() graceful schließen (kein schwarzes Ghost-Fenster)
    3. 400 ms warten
    4. Noch lebende Prozesse per taskkill /F abwürgen
    """
    current_pid = os.getpid()
    # Auch die Parent-PID ausschließen: UV-Shim hat andere PID als der Python-Prozess
    try:
        _ppid_result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={current_pid}').ParentProcessId"],
            capture_output=True, text=True, timeout=5,
        )
        parent_pid = int(_ppid_result.stdout.strip())
    except Exception:
        parent_pid = None
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process | "
                "Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and "
                "$_.ExecutablePath -like '*pic_optimizer*' -and "
                "($_.CommandLine -like '* main.py*' -or $_.CommandLine -match 'main\\.py[\\s\"]*$') -and "
                "$_.CommandLine -notlike '*-c *' } | "
                "Select-Object -ExpandProperty ProcessId",
            ],
            capture_output=True, text=True, timeout=10,
        )
        pids = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                pid = int(line)
            except ValueError:
                continue
            if pid == current_pid or pid == parent_pid:
                continue
            pids.append(pid)

        if not pids:
            return

        # Schritt 1: Kind-Prozesse (echte Qt-Fenster) ermitteln und graceful close senden
        # Der UV-Shim (pythonw.exe) hat kein Fenster; das Qt-Fenster gehört dem Kind-Prozess
        pid_csv = ",".join(str(p) for p in pids)
        subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f"$shimPids = @({pid_csv});"
                f"$children = Get-CimInstance Win32_Process | Where-Object {{"
                f"  $_.ParentProcessId -in $shimPids -and"
                f"  ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe')"
                f"}};"
                f"$allTargets = $shimPids + ($children | Select-Object -ExpandProperty ProcessId);"
                f"foreach ($id in $allTargets) {{"
                f"  $p = Get-Process -Id $id -ErrorAction SilentlyContinue;"
                f"  if ($p) {{ $p.CloseMainWindow() | Out-Null }}"
                f"}}",
            ],
            capture_output=True, timeout=5,
        )

        # Schritt 2: Kurz warten – Fenster haben Zeit sauber zu verschwinden
        time.sleep(0.4)

        # Schritt 3: Prozessbaum hart killen (/T = inkl. Kind-Prozesse)
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    except Exception:
        pass


def _setup_error_log():
    """Alle Exceptions (auch aus Qt-Slots) in eine Log-Datei schreiben."""
    import traceback
    log_path = os.path.join(os.path.dirname(__file__), "error_log.txt")

    def _hook(exc_type, exc_value, exc_tb):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            import datetime
            f.write(str(datetime.datetime.now()) + "\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook

    # Qt-interne Exceptions aus Slots abfangen
    try:
        from PyQt6.QtCore import qInstallMessageHandler, QtMsgType
        def _qt_msg(msg_type, context, message):
            if msg_type in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"[Qt {msg_type.name}] {message}\n")
        qInstallMessageHandler(_qt_msg)
    except Exception:
        pass

    return log_path


def main():
    _kill_existing_instances()

    log_path = _setup_error_log()

    app = QApplication(sys.argv)
    app.setApplicationName("Optimizer")
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
