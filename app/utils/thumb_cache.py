"""Persistenter Thumbnail-Cache – speichert Thumbnails als JPEG in .optimizer_cache/thumbs/."""
import hashlib
from pathlib import Path
from PIL import Image

CACHE_DIR_NAME = ".optimizer_cache"
THUMB_SUBDIR = "thumbs"
CACHE_QUALITY = 85


def _cache_key(file_path: Path) -> str:
    """Cache-Key aus Pfad + mtime + Dateigröße."""
    stat = file_path.stat()
    raw = f"{file_path}|{stat.st_mtime}|{stat.st_size}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_dir(source_folder: Path) -> Path:
    return source_folder / CACHE_DIR_NAME / THUMB_SUBDIR


def get_cached_thumbnail(file_path: Path, thumb_size: tuple[int, int]) -> "Image.Image | None":
    """Gecachten Thumbnail laden, falls vorhanden und gültig."""
    cache = _cache_dir(file_path.parent)
    key = _cache_key(file_path)
    cached = cache / f"{key}.jpg"
    if cached.exists():
        try:
            img = Image.open(cached)
            img.load()
            return img
        except Exception:
            cached.unlink(missing_ok=True)
    return None


def save_cached_thumbnail(file_path: Path, thumb: Image.Image) -> None:
    """Thumbnail in den Cache speichern."""
    cache = _cache_dir(file_path.parent)
    cache.mkdir(parents=True, exist_ok=True)
    key = _cache_key(file_path)
    target = cache / f"{key}.jpg"
    try:
        thumb.convert("RGB").save(str(target), "JPEG", quality=CACHE_QUALITY)
    except Exception:
        pass
