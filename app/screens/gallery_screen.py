from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListView, QPushButton,
    QLabel, QSizePolicy, QStyledItemDelegate, QStyle, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QThreadPool, QRunnable, QObject, QRect, QTimer
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QIcon, QPixmap, QColor, QPainter, QFont

from app.utils.image_loader import is_supported, is_video
from app.workers.thumbnail_worker import ThumbnailWorker

THUMB_SIZE = 180
CELL_SIZE  = 210
LABEL_H    = 44          # Höhe des Textbereichs unter dem Thumbnail (2 Zeilen)
DATE_ROLE  = Qt.ItemDataRole.UserRole + 1


class _DateSignals(QObject):
    ready = pyqtSignal(int, str)


class _DateWorker(QRunnable):
    def __init__(self, index: int, path: Path):
        super().__init__()
        self.signals = _DateSignals()
        self._index  = index
        self._path   = path

    def run(self):
        date_str = _read_date(self._path)
        self.signals.ready.emit(self._index, date_str)


def _read_date(path: Path) -> str:
    """Liest das Aufnahmedatum aus EXIF — unterstützt JPEG/PNG/WEBP, RAW und HEIC."""
    ext = path.suffix.lower()
    raw_str = ""

    # ── RAW-Dateien: EXIF via rawpy ───────────────────────────────────────────
    if ext in {".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".pef"}:
        try:
            import rawpy
            with rawpy.imread(str(path)) as raw:
                # rawpy liefert EXIF als Dict (rawpy >= 0.18)
                if hasattr(raw, "metadata") and hasattr(raw.metadata, "timestamp"):
                    import datetime
                    ts = raw.metadata.timestamp
                    if ts:
                        dt = datetime.datetime.fromtimestamp(ts)
                        return dt.strftime("%d.%m.%Y")
        except Exception:
            pass
        # Fallback: PIL öffnet RAW manchmal als Thumbnail + EXIF
        try:
            from PIL import Image as _Img
            with _Img.open(path) as img:
                raw_str = _exif_date_from_pil(img)
        except Exception:
            pass
        return raw_str or _date_from_filename(path)

    # ── HEIC ─────────────────────────────────────────────────────────────────
    if ext in {".heic", ".heif"}:
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
            from PIL import Image as _Img
            with _Img.open(path) as img:
                raw_str = _exif_date_from_pil(img)
        except Exception:
            pass
        return raw_str or _date_from_filename(path)

    # ── Video-Dateien (MOV, MP4 …) via ffprobe, Fallback: mtime, Dateiname ──
    if ext in {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv"}:
        return _date_from_ffprobe(path) or _date_from_mtime(path) or _date_from_filename(path)

    # ── Standard-Formate (JPEG, PNG, WEBP, TIFF …) ───────────────────────────
    try:
        from PIL import Image as _Img
        with _Img.open(path) as img:
            raw_str = _exif_date_from_pil(img)
    except Exception:
        pass
    if raw_str:
        return raw_str

    # ── Letzter Fallback: Datum aus Dateiname ─────────────────────────────────
    return _date_from_filename(path)


def _date_from_ffprobe(path: Path) -> str:
    """Liest creation_time aus Video-Container-Metadaten via ffprobe."""
    import subprocess, json
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return ""
        data = json.loads(result.stdout)
        tags = data.get("format", {}).get("tags", {})
        # MOV/MP4 speichern das Datum unter verschiedenen Schlüsseln
        raw = (tags.get("creation_time")
               or tags.get("date")
               or tags.get("com.apple.quicktime.creationdate")
               or "")
        if not raw:
            return ""
        # Format: "2024-06-15T14:32:11.000000Z" oder "2024-06-15"
        date_part = raw[:10]               # "2024-06-15"
        parts = date_part.split("-")
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            return f"{parts[2]}.{parts[1]}.{parts[0]}"
    except Exception:
        pass
    return ""


def _date_from_mtime(path: Path) -> str:
    """Liest das Datum aus dem Dateisystem-Timestamp (mtime) — gesetzt z.B. von transfer_media."""
    try:
        import datetime
        ts = path.stat().st_mtime
        dt = datetime.datetime.fromtimestamp(ts)
        # Plausibilitätsprüfung: kein zukünftiges Datum, nicht vor 1990
        if 1990 <= dt.year <= 2100:
            return dt.strftime("%d.%m.%Y")
    except Exception:
        pass
    return ""


def _date_from_filename(path: Path) -> str:
    """Extrahiert ein Datum aus dem Dateinamen per Regex (letzter Fallback)."""
    import re
    name = path.stem
    # YYYYMMDD mit optionalem Trennzeichen + Uhrzeit: 20240615, 20240615_143211
    m = re.search(r"(\d{4})[._-]?(\d{2})[._-]?(\d{2})", name)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        # Plausibilitätsprüfung
        if 1990 <= int(y) <= 2100 and 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{d}.{mo}.{y}"
    return ""


def _exif_date_from_pil(img) -> str:
    """Extrahiert DateTimeOriginal aus einem geöffneten PIL-Image (inkl. Sub-IFD)."""
    try:
        from PIL.ExifTags import TAGS
        exif = img.getexif() if hasattr(img, "getexif") else None
        if not exif:
            return ""

        # 1) Direkt im Haupt-IFD (DateTime, Tag 306)
        tag_map = {TAGS.get(k, k): v for k, v in exif.items()}
        raw = tag_map.get("DateTimeOriginal") or tag_map.get("DateTime")

        # 2) EXIF-Sub-IFD (Tag 34865 = 0x8769) — enthält DateTimeOriginal (36867)
        if not raw and hasattr(exif, "get_ifd"):
            exif_ifd = exif.get_ifd(0x8769)
            if exif_ifd:
                sub_map = {TAGS.get(k, k): v for k, v in exif_ifd.items()}
                raw = sub_map.get("DateTimeOriginal") or sub_map.get("DateTime")

        if raw:
            parts = str(raw).split(" ")[0].split(":")
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                return f"{parts[2]}.{parts[1]}.{parts[0]}"
    except Exception:
        pass
    return ""


class _ThumbDelegate(QStyledItemDelegate):
    """Zeichnet Thumbnail + Text-Label zuverlässig unter jedes Bild."""

    MODE_NAME = "name"
    MODE_DATE = "date"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = self.MODE_NAME

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setClipRect(option.rect)   # kein Text-Overflow außerhalb der Zelle

        # Hintergrund
        painter.fillRect(option.rect, QColor("#f8f9fa"))
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(52, 152, 219, 60))

        # Thumbnail
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon and not icon.isNull():
            pix = icon.pixmap(THUMB_SIZE, THUMB_SIZE)
            ix  = option.rect.x() + (option.rect.width() - pix.width()) // 2
            iy  = option.rect.y() + 4
            painter.drawPixmap(ix, iy, pix)

        # Text-Label (unterhalb des Thumbnails, strikt auf LABEL_H begrenzt)
        name = index.data(Qt.ItemDataRole.UserRole)
        date = index.data(DATE_ROLE)

        if self.mode == self.MODE_DATE and date:
            label = date
        else:
            label = Path(name).name if name else ""

        text_rect = QRect(
            option.rect.x() + 2,
            option.rect.y() + THUMB_SIZE + 6,
            option.rect.width() - 4,
            LABEL_H,
        )
        painter.setClipRect(text_rect)     # Text strikt auf Label-Bereich begrenzen
        painter.setPen(QColor(40, 40, 40))
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
            | Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere,
            label,
        )

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(CELL_SIZE, THUMB_SIZE + LABEL_H + 8)


class GalleryScreen(QWidget):
    back_requested = pyqtSignal()
    image_selected = pyqtSignal(str)
    video_selected = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._files: list[Path] = []
        self._model    = QStandardItemModel()
        self._pool     = QThreadPool.globalInstance()
        self._delegate = _ThumbDelegate()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Kopfzeile
        header = QHBoxLayout()

        self._folder_label = QLabel("Kein Ordner ausgewählt")
        self._folder_label.setStyleSheet("color: #7f8c8d; font-size: 13px;")
        self._folder_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: #95a5a6; font-size: 12px;")

        self._toggle_btn = QPushButton("📅 Datum anzeigen")
        self._toggle_btn.setFixedHeight(28)
        self._toggle_btn.setToolTip("Zwischen Dateiname und Aufnahmedatum umschalten")
        self._toggle_btn.clicked.connect(self._toggle_mode)
        self._toggle_btn.setStyleSheet(
            "QPushButton { background: #ecf0f1; color: #555; border-radius: 6px;"
            "  font-size: 12px; padding: 2px 10px; }"
            "QPushButton:hover { background: #bdc3c7; }"
        )

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(32, 32)
        close_btn.setToolTip("Schließen – zurück zur Ordnerauswahl")
        close_btn.clicked.connect(self.back_requested.emit)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #7f8c8d; border-radius: 16px;"
            "  font-size: 16px; font-weight: bold; }"
            "QPushButton:hover { background: #e74c3c; color: white; }"
        )

        refresh_btn = QPushButton("🔄")
        refresh_btn.setFixedSize(32, 32)
        refresh_btn.setToolTip("Galerie aktualisieren")
        refresh_btn.clicked.connect(self._refresh)
        refresh_btn.setStyleSheet(
            "QPushButton { background: #ecf0f1; color: #555; border-radius: 6px; font-size: 14px; }"
            "QPushButton:hover { background: #bdc3c7; }"
        )

        self._delete_btn = QPushButton("🗑 Löschen")
        self._delete_btn.setFixedHeight(28)
        self._delete_btn.setToolTip("Ausgewählte Datei löschen")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._delete_selected)
        self._delete_btn.setStyleSheet(
            "QPushButton { background: #ecf0f1; color: #555; border-radius: 6px;"
            "  font-size: 12px; padding: 2px 10px; }"
            "QPushButton:hover { background: #e74c3c; color: white; }"
            "QPushButton:disabled { color: #bbb; }"
        )

        header.addWidget(self._folder_label)
        header.addWidget(self._count_label)
        header.addSpacing(8)
        header.addWidget(self._toggle_btn)
        header.addSpacing(4)
        header.addWidget(self._delete_btn)
        header.addSpacing(4)
        header.addWidget(refresh_btn)
        header.addSpacing(4)
        header.addWidget(close_btn)

        # Grid-Ansicht
        self._list_view = QListView()
        self._list_view.setModel(self._model)
        self._list_view.setItemDelegate(self._delegate)
        self._list_view.setViewMode(QListView.ViewMode.IconMode)
        self._list_view.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self._list_view.setGridSize(QSize(CELL_SIZE, THUMB_SIZE + LABEL_H + 16))
        self._list_view.setResizeMode(QListView.ResizeMode.Adjust)
        self._list_view.setUniformItemSizes(True)
        self._list_view.setSpacing(6)
        self._list_view.setMovement(QListView.Movement.Static)
        self._list_view.setStyleSheet(
            "QListView { background: #f8f9fa; border: none; padding-bottom: 60px; }"
        )
        self._list_view.doubleClicked.connect(self._on_double_click)
        self._list_view.selectionModel().selectionChanged.connect(self._on_selection_changed)

        layout.addLayout(header)
        layout.addWidget(self._list_view)

    @property
    def files(self) -> list:
        return self._files

    def _refresh(self):
        if self._files:
            folder = self._files[0].parent
            self.load_folder(str(folder))

    def load_folder(self, folder_path: str):
        self._model.clear()
        self._files = []

        folder = Path(folder_path)
        self._folder_label.setText(str(folder))

        files = sorted(
            [f for f in folder.iterdir() if f.is_file() and is_supported(f)],
            key=lambda f: f.name.lower(),
        )
        self._files = files
        self._count_label.setText(f"{len(files)} Dateien")

        placeholder = _placeholder_pixmap(THUMB_SIZE)

        for i, f in enumerate(files):
            item = QStandardItem()
            item.setText(f.name)   # nötig für Qt-Hit-Detection beim Klick
            item.setIcon(QIcon(placeholder))
            item.setData(str(f), Qt.ItemDataRole.UserRole)
            item.setData("", DATE_ROLE)
            item.setSizeHint(QSize(CELL_SIZE, THUMB_SIZE + LABEL_H + 8))
            self._model.appendRow(item)

        # Sichtbare Thumbnails zuerst laden (~30 im Viewport), Rest verzögert
        visible_count = min(len(files), 30)
        for i in range(visible_count):
            self._schedule_thumbnail(i, files[i])
            self._schedule_date(i, files[i])
        if len(files) > visible_count:
            self._pending_batch = list(range(visible_count, len(files)))
            QTimer.singleShot(100, self._schedule_remaining_batch)

    def _schedule_remaining_batch(self):
        """Lädt Thumbnails für Dateien außerhalb des sichtbaren Bereichs."""
        if not hasattr(self, '_pending_batch'):
            return
        for i in self._pending_batch:
            if i < len(self._files):
                self._schedule_thumbnail(i, self._files[i])
                self._schedule_date(i, self._files[i])
        del self._pending_batch

    def _schedule_thumbnail(self, index: int, path: Path):
        worker = ThumbnailWorker(index, path)
        worker.signals.ready.connect(self._on_thumbnail_ready)
        self._pool.start(worker)

    def _schedule_date(self, index: int, path: Path):
        worker = _DateWorker(index, path)
        worker.signals.ready.connect(self._on_date_ready)
        self._pool.start(worker)

    def _on_thumbnail_ready(self, index: int, pixmap: QPixmap):
        item = self._model.item(index)
        if item:
            item.setIcon(QIcon(pixmap))

    def _on_date_ready(self, index: int, date_str: str):
        item = self._model.item(index)
        if item:
            item.setData(date_str, DATE_ROLE)
            # Neu zeichnen damit Datum sofort erscheint wenn Datum-Modus aktiv
            idx = self._model.index(index, 0)
            self._list_view.update(idx)

    def _toggle_mode(self):
        if self._delegate.mode == _ThumbDelegate.MODE_NAME:
            self._delegate.mode = _ThumbDelegate.MODE_DATE
            self._toggle_btn.setText("📄 Dateiname anzeigen")
        else:
            self._delegate.mode = _ThumbDelegate.MODE_NAME
            self._toggle_btn.setText("📅 Datum anzeigen")
        self._list_view.viewport().update()

    def _on_selection_changed(self, selected, deselected):
        self._delete_btn.setEnabled(bool(self._list_view.currentIndex().isValid()))

    def _delete_selected(self):
        idx = self._list_view.currentIndex()
        if not idx.isValid():
            return
        item = self._model.itemFromIndex(idx)
        if not item:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        reply = QMessageBox.question(
            self,
            "Datei löschen",
            f"Soll diese Datei wirklich gelöscht werden?\n\n{path.name}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
        except Exception as e:
            QMessageBox.warning(self, "Fehler", f"Löschen fehlgeschlagen:\n{e}")
            return
        row = idx.row()
        self._model.removeRow(row)
        if row < len(self._files):
            self._files.pop(row)
        self._count_label.setText(f"{len(self._files)} Dateien")
        self._delete_btn.setEnabled(self._list_view.currentIndex().isValid())

    def refresh_item(self, old_path: str, new_path: str):
        for row in range(self._model.rowCount()):
            item = self._model.item(row)
            if item and item.data(Qt.ItemDataRole.UserRole) == old_path:
                item.setData(new_path, Qt.ItemDataRole.UserRole)
                item.setData("", DATE_ROLE)
                item.setIcon(QIcon(_placeholder_pixmap(THUMB_SIZE)))
                new = Path(new_path)
                self._schedule_thumbnail(row, new)
                self._schedule_date(row, new)
                break

    def keyPressEvent(self, event):
        sel = self._list_view.currentIndex()
        row = sel.row() if sel.isValid() else -1
        n   = self._model.rowCount()
        if event.key() == Qt.Key.Key_Home and row > 0:
            new_idx = self._model.index(row - 1, 0)
            self._list_view.setCurrentIndex(new_idx)
        elif event.key() == Qt.Key.Key_End and row < n - 1:
            new_idx = self._model.index(row + 1, 0)
            self._list_view.setCurrentIndex(new_idx)
        else:
            super().keyPressEvent(event)

    def _on_double_click(self, index):
        item = self._model.itemFromIndex(index)
        if item:
            path = item.data(Qt.ItemDataRole.UserRole)
            if not path:
                return
            if is_video(path):
                self.video_selected.emit(path)
            else:
                self.image_selected.emit(path)


def _placeholder_pixmap(size: int) -> QPixmap:
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.lightGray)
    return px
