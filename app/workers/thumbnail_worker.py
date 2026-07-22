from pathlib import Path
from PyQt6.QtCore import QRunnable, QObject, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage
from PIL import Image
from app.utils.image_loader import is_video, is_image, make_thumbnail
from app.utils.video_thumb import extract_video_thumbnail

THUMB_SIZE = (180, 180)


class ThumbnailSignals(QObject):
    ready = pyqtSignal(int, QPixmap)
    error = pyqtSignal(int)


class ThumbnailWorker(QRunnable):
    """Generiert einen Thumbnail im Hintergrund und emittiert ihn via Signal."""

    def __init__(self, index: int, path: str | Path):
        super().__init__()
        self.index = index
        self.path = Path(path)
        self.signals = ThumbnailSignals()

    def run(self):
        try:
            from app.utils.thumb_cache import get_cached_thumbnail, save_cached_thumbnail

            # Cache-Check zuerst
            cached = get_cached_thumbnail(self.path, THUMB_SIZE)
            if cached is not None:
                pixmap = _pil_to_pixmap(cached)
                self.signals.ready.emit(self.index, pixmap)
                return

            if is_video(self.path):
                img = extract_video_thumbnail(self.path, THUMB_SIZE)
            elif is_image(self.path):
                img = make_thumbnail(self.path, THUMB_SIZE)
            else:
                self.signals.error.emit(self.index)
                return

            if img is None:
                self.signals.error.emit(self.index)
                return

            # In Cache speichern
            save_cached_thumbnail(self.path, img)

            pixmap = _pil_to_pixmap(img)
            self.signals.ready.emit(self.index, pixmap)
        except Exception:
            self.signals.error.emit(self.index)


def _pil_to_pixmap(img: Image.Image) -> QPixmap:
    if img.mode == "RGBA":
        # BGRA-Byte-Reihenfolge für Qt Format_ARGB32 (little-endian: B G R A)
        r, g, b, a = img.split()
        img_bgra = Image.merge("RGBA", (b, g, r, a))
        data = img_bgra.tobytes("raw", "RGBA")
        qimg = QImage(data, img.width, img.height, img.width * 4, QImage.Format.Format_ARGB32)
    else:
        img = img.convert("RGB")
        data = img.tobytes("raw", "RGB")
        qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
    # .copy() stellt sicher, dass Qt die Pixeldaten besitzt (kein Dangling-Pointer)
    return QPixmap.fromImage(qimg.copy())
