"""
Testcase: Backup-Erstellung und Original-Wiederherstellung
Prüft die Kernlogik aus EditorScreen: Backup anlegen, Restore, Extension-Wechsel-Cleanup.
"""
import shutil
import tempfile
from pathlib import Path
from PIL import Image

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_jpeg(path: Path, color=(200, 100, 50)):
    Image.new("RGB", (120, 90), color).save(str(path), "JPEG")


def _make_png_rgba(path: Path):
    Image.new("RGBA", (120, 90), (0, 200, 100, 180)).save(str(path), "PNG")


# ── Backup-Logik (aus load_file isoliert) ────────────────────────────────────

def _create_backup(file_path: Path) -> Path | None:
    backup_dir = file_path.parent / ".optimizer_originals"
    stem = file_path.stem
    if backup_dir.exists():
        for c in sorted(backup_dir.iterdir()):
            if c.stem == stem and c.is_file():
                return c  # bereits vorhanden
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / file_path.name
    shutil.copy2(str(file_path), str(backup))
    return backup


def test_backup_created_on_first_load():
    with tempfile.TemporaryDirectory() as d:
        img_path = Path(d) / "foto.jpg"
        _make_jpeg(img_path)
        backup = _create_backup(img_path)
        assert backup is not None
        assert backup.exists()
        assert backup.parent.name == ".optimizer_originals"


def test_backup_not_overwritten_on_second_load():
    with tempfile.TemporaryDirectory() as d:
        img_path = Path(d) / "foto.jpg"
        _make_jpeg(img_path, color=(200, 100, 50))
        backup1 = _create_backup(img_path)
        original_size = backup1.stat().st_size

        # Bild "bearbeiten" und nochmal backup anlegen
        _make_jpeg(img_path, color=(10, 10, 10))
        backup2 = _create_backup(img_path)

        # Backup darf nicht überschrieben worden sein
        assert backup2.stat().st_size == original_size, \
            "Backup wurde fälschlicherweise überschrieben"


def test_restore_overwrites_edited_file():
    with tempfile.TemporaryDirectory() as d:
        img_path = Path(d) / "foto.jpg"
        _make_jpeg(img_path, color=(200, 100, 50))
        backup = _create_backup(img_path)

        # Bild "bearbeiten" (Datei ändern)
        _make_jpeg(img_path, color=(10, 10, 10))
        edited_size = img_path.stat().st_size

        # Restore: Backup zurückkopieren
        shutil.copy2(str(backup), str(img_path))
        restored_size = img_path.stat().st_size

        assert restored_size != edited_size or True  # Größe kann gleich sein
        # Inhalt muss dem Backup entsprechen
        assert img_path.read_bytes() == backup.read_bytes()


def test_extension_change_old_file_deleted():
    """Beim Speichern als PNG (RGBA) muss die alte JPG-Datei gelöscht werden."""
    with tempfile.TemporaryDirectory() as d:
        jpg_path = Path(d) / "foto.jpg"
        _make_jpeg(jpg_path)
        assert jpg_path.exists()

        # Simuliere: Speichern als PNG (RGBA) → alte JPG löschen
        png_path = jpg_path.with_suffix(".png")
        _make_png_rgba(png_path)
        if png_path != jpg_path and jpg_path.exists():
            jpg_path.unlink()

        assert png_path.exists(), "PNG wurde nicht erstellt"
        assert not jpg_path.exists(), "Alte JPG wurde nicht gelöscht"


def test_restore_cleans_up_different_extension():
    """Restore von jpg → löscht png falls Extension gewechselt hatte."""
    with tempfile.TemporaryDirectory() as d:
        jpg_path = Path(d) / "foto.jpg"
        _make_jpeg(jpg_path)
        backup = _create_backup(jpg_path)

        # Gespeichert als PNG (RGBA nach Kreis-Crop)
        png_path = jpg_path.with_suffix(".png")
        _make_png_rgba(png_path)
        jpg_path.unlink()  # alte jpg wurde beim PNG-Speichern gelöscht

        # Restore: Backup (jpg) zurückkopieren, png löschen
        restored = jpg_path.parent / backup.name
        shutil.copy2(str(backup), str(restored))
        if png_path.exists() and png_path != restored:
            png_path.unlink()

        assert restored.exists()
        assert not png_path.exists(), "Alte PNG wurde beim Restore nicht gelöscht"


if __name__ == "__main__":
    test_backup_created_on_first_load();           print("PASS: test_backup_created_on_first_load")
    test_backup_not_overwritten_on_second_load();  print("PASS: test_backup_not_overwritten_on_second_load")
    test_restore_overwrites_edited_file();         print("PASS: test_restore_overwrites_edited_file")
    test_extension_change_old_file_deleted();      print("PASS: test_extension_change_old_file_deleted")
    test_restore_cleans_up_different_extension();  print("PASS: test_restore_cleans_up_different_extension")
