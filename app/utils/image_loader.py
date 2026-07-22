"""
Format-Routing: lädt Bilder je nach Extension via Pillow, rawpy oder pillow-heif.
Gibt immer ein PIL.Image zurück.
"""
from pathlib import Path
from PIL import Image

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
RAW_EXTENSIONS = {".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".pef"}
HEIC_EXTENSIONS = {".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv"}

ALL_SUPPORTED = IMAGE_EXTENSIONS | RAW_EXTENSIONS | HEIC_EXTENSIONS | VIDEO_EXTENSIONS


def load_image(path: str | Path) -> Image.Image:
    """Lädt eine Bilddatei und gibt ein PIL.Image zurück (EXIF-Orientierung korrigiert)."""
    from PIL import ImageOps
    path = Path(path)
    ext = path.suffix.lower()

    if ext in IMAGE_EXTENSIONS:
        with Image.open(path) as _img:
            img = ImageOps.exif_transpose(_img)
            if img.mode == "RGBA":
                return img.copy()
            return img.convert("RGB")

    if ext in RAW_EXTENSIONS:
        return _load_raw(path)

    if ext in HEIC_EXTENSIONS:
        return _load_heic(path)

    raise ValueError(f"Nicht unterstütztes Format: {ext}")


def _load_raw(path: Path) -> Image.Image:
    try:
        import rawpy
        import numpy as np
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=False, output_bps=8)
        return Image.fromarray(rgb)
    except ImportError:
        raise RuntimeError("rawpy ist nicht installiert. Bitte: pip install rawpy")


def _load_heic(path: Path) -> Image.Image:
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
        return Image.open(path).convert("RGB")
    except ImportError:
        raise RuntimeError("pillow-heif ist nicht installiert. Bitte: pip install pillow-heif")


def make_thumbnail(path: str | Path, size: tuple[int, int] = (200, 200)) -> Image.Image:
    """Erstellt ein Thumbnail für Bilder. Videos werden separat behandelt."""
    img = load_image(path)
    img.thumbnail(size, Image.LANCZOS)
    return img


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in (IMAGE_EXTENSIONS | RAW_EXTENSIONS | HEIC_EXTENSIONS)


def is_video(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def is_supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in ALL_SUPPORTED
