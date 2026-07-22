# Pic Optimizer — Claude Code Anweisungen

## Kommunikations-Konvention (PERMANENT)

**`FRAGE:`** — Wenn der User dieses Schlüsselwort verwendet, bittet er **nur um eine Auskunft**.
Keine Umsetzung vornehmen! Die Implementierung wird separat angestoßen.


## Backup-Regel (PFLICHT)

Vor jeder Änderung an einer Bilddatei wird automatisch ein Backup im Ordner `.optimizer_originals/` im selben Verzeichnis angelegt:
- Backup wird nur beim **ersten Laden** erstellt – nie überschrieben
- Restore ersetzt die bearbeitete Datei mit dem Original
- Bei Extension-Wechsel (z. B. JPG → PNG) wird die alte Datei gelöscht und beim Restore die neue Extension-Datei entfernt

## Benutzeranleitung (PFLICHT)

Sobald eine sichtbare oder funktionale Änderung an der App vorgenommen wird, muss zwingend auch die `docs/USER_MANUAL.md` aktualisiert werden, um sie auf dem neuesten Stand zu halten. Dies ist Teil jedes Features oder Bugfixes, der die Benutzerinteraktion betrifft.

## Test Suite (PFLICHT)

Vor JEDER Code-Änderung und vor JEDEM Commit alle Tests ausführen und prüfen, dass alle grün sind:

```powershell
.venv\Scripts\python.exe -m pytest tests\ -v
```

**Bug Fix Regel:** Bei jedem Bug Fix zuerst einen Test Case schreiben, der den Bug reproduziert (schlägt fehl), dann den Bug fixen (Test wird grün), dann committen. Test Cases niemals löschen.

**Neue Features:** Für jedes neue Feature einen passenden Test Case in `tests/` anlegen (Dateiname: `test_<feature>.py`).

## App-Neustart nach Änderungen

Nach jeder Code-Änderung die App neu starten (ohne Konsolenfenster):

```powershell
.venv\Scripts\python.exe main.py
```

Die App wird über `Optimizer.vbs` mit `WindowStyle=0` gestartet – kein sichtbares PowerShell-Fenster.

## Git-Workflow (PFLICHT)

Nach **jeder Code-Änderung** muss committed und gepusht werden:

```bash
git add <geänderte Dateien>
git commit -m "Kurze Beschreibung der Änderung"
git push
```

- Immer spezifische Dateien stagen (kein `git add -A`)
- Commit-Message auf Deutsch oder Englisch, kurz und aussagekräftig
- Direkt nach dem Commit pushen — nie mehrere Änderungen ansammeln

## Tech Stack

- GUI: PyQt6
- Bildverarbeitung: Pillow, OpenCV, rawpy, pillow-heif
- Video: ffmpeg-python
- Hintergrundentfernung: rembg
- Geometrie: shapely, numpy
- Tests: pytest

## Projektstruktur

```
pic_optimizer/
├── main.py                  # Einstiegspunkt, Qt-App-Start
├── Optimizer.vbs            # Startskript (kein Konsolenfenster)
├── app/
│   ├── main_window.py       # Hauptfenster / Navigation
│   ├── screens/
│   │   ├── editor_screen.py         # Bild-Editor (Kernlogik)
│   │   ├── gallery_screen.py        # Galerie-Ansicht
│   │   ├── folder_screen.py         # Ordner-Auswahl
│   │   └── video_editor_screen.py   # Video-Editor
│   ├── utils/
│   │   ├── image_loader.py  # Bilder laden (RAW, HEIF, etc.)
│   │   └── video_thumb.py   # Video-Thumbnails
│   └── workers/
│       └── thumbnail_worker.py  # Async Thumbnail-Generierung
└── tests/                   # Pytest-Tests (niemals löschen!)
```

## Arbeitsverzeichnis

`C:\Users\micha\Desktop\claude\pic_optimizer`

## .gitignore — Was NICHT committed wird

- `.venv/`, `__pycache__/`, `.pytest_cache/`
- `error_log.txt`, `debug_*.txt`, `startup_*.log`
- `.optimizer_originals/` (Backup-Ordner in Benutzerordnern)
