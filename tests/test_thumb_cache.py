"""
Testcase: Thumbnail-Cache – Persistenter Cache für Gallery-Thumbnails
"""
import sys
import os
import time
import tempfile
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_test_jpeg(path: Path, w=200, h=150, color=(255, 0, 0)):
    img = Image.new("RGB", (w, h), color)
    img.save(str(path), "JPEG")


# ── Tests ────────────────────────────────────────────────────────────────────

def test_cache_miss_returns_none():
    """Bei fehlendem Cache muss None zurückkommen."""
    from app.utils.thumb_cache import get_cached_thumbnail
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.jpg"
        _make_test_jpeg(p)
        result = get_cached_thumbnail(p, (180, 180))
        assert result is None


def test_cache_save_and_load():
    """Gespeicherter Thumbnail muss korrekt geladen werden."""
    from app.utils.thumb_cache import get_cached_thumbnail, save_cached_thumbnail
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.jpg"
        _make_test_jpeg(p)
        thumb = Image.new("RGB", (180, 135), (0, 255, 0))
        save_cached_thumbnail(p, thumb)
        loaded = get_cached_thumbnail(p, (180, 180))
        assert loaded is not None
        assert loaded.size == (180, 135)
        assert loaded.mode == "RGB"


def test_cache_invalidation_on_mtime_change():
    """Geändertes mtime muss Cache-Miss auslösen."""
    from app.utils.thumb_cache import get_cached_thumbnail, save_cached_thumbnail
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.jpg"
        _make_test_jpeg(p)
        thumb = Image.new("RGB", (180, 135), (0, 255, 0))
        save_cached_thumbnail(p, thumb)
        # mtime ändern
        time.sleep(0.1)
        _make_test_jpeg(p, color=(0, 0, 255))  # Datei neu schreiben
        loaded = get_cached_thumbnail(p, (180, 180))
        assert loaded is None, "Cache sollte nach mtime-Änderung invalid sein"


def test_cache_key_deterministic():
    """Gleiche Datei muss gleichen Cache-Key ergeben."""
    from app.utils.thumb_cache import _cache_key
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.jpg"
        _make_test_jpeg(p)
        k1 = _cache_key(p)
        k2 = _cache_key(p)
        assert k1 == k2


def test_cache_dir_created():
    """Cache-Verzeichnis muss automatisch erstellt werden."""
    from app.utils.thumb_cache import save_cached_thumbnail, CACHE_DIR_NAME, THUMB_SUBDIR
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.jpg"
        _make_test_jpeg(p)
        thumb = Image.new("RGB", (180, 135), (0, 255, 0))
        save_cached_thumbnail(p, thumb)
        cache_dir = Path(d) / CACHE_DIR_NAME / THUMB_SUBDIR
        assert cache_dir.exists()


def test_corrupt_cache_handled():
    """Korrupte Cache-Datei darf keinen Fehler werfen."""
    from app.utils.thumb_cache import get_cached_thumbnail, _cache_key, _cache_dir
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.jpg"
        _make_test_jpeg(p)
        # Korrupte Datei in den Cache-Ordner schreiben
        cache = _cache_dir(p.parent)
        cache.mkdir(parents=True, exist_ok=True)
        key = _cache_key(p)
        corrupt_file = cache / f"{key}.jpg"
        corrupt_file.write_bytes(b"not a valid image")
        loaded = get_cached_thumbnail(p, (180, 180))
        assert loaded is None


def test_rgba_thumbnail_saved_as_rgb():
    """RGBA-Thumbnails müssen als RGB-JPEG gespeichert werden."""
    from app.utils.thumb_cache import get_cached_thumbnail, save_cached_thumbnail
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.jpg"
        _make_test_jpeg(p)
        thumb = Image.new("RGBA", (180, 135), (0, 255, 0, 128))
        save_cached_thumbnail(p, thumb)
        loaded = get_cached_thumbnail(p, (180, 180))
        assert loaded is not None
        assert loaded.mode == "RGB"


if __name__ == "__main__":
    for name, obj in list(globals().items()):
        if name.startswith("test_") and callable(obj):
            obj()
            print(f"PASS: {name}")
