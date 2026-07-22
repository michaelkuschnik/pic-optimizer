from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QToolBar, QSizePolicy, QStackedWidget, QFrame,
    QSlider, QSpinBox, QFormLayout, QComboBox,
    QScrollArea, QCheckBox, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QRect, QRectF, QPoint, QThread
from PyQt6.QtGui import QPixmap, QImage, QFont, QAction, QPainter, QPen, QColor, QCursor, QPainterPath, QBrush, QShortcut, QKeySequence

from PIL import Image, ImageEnhance
from app.utils.image_loader import load_image
from app.workers.thumbnail_worker import _pil_to_pixmap


# ── Interaktiver Bild-Canvas ──────────────────────────────────────────────────

class ImageCanvas(QWidget):
    """
    Zeigt das Bild zentriert/skaliert an und erlaubt Maus-Interaktionen:
    - CROP:   Rechteck aufziehen → Zuschnitt-Koordinaten
    - ADJUST: Horizontales Ziehen → Deltawert für aktiven Regler
    - ROTATE: Horizontales Ziehen → Rotationswinkel
    """
    MODE_NONE   = 0
    MODE_CROP   = 1
    MODE_ADJUST = 2
    MODE_ROTATE = 3
    MODE_LASSO  = 4
    MODE_PAINT  = 5
    MODE_QUAD   = 6
    MODE_PICK_POINT = 7   # einmaliger Klick → Koordinaten zurück

    SHAPE_RECT     = 0
    SHAPE_CIRCLE   = 1
    SHAPE_ELLIPSE  = 2
    SHAPE_FREEHAND = 3

    crop_shape_ready   = pyqtSignal()                           # Form gezeichnet, bereit zum Verschieben/Resize
    adjust_delta       = pyqtSignal(int)
    rotate_delta       = pyqtSignal(float)
    lasso_committed    = pyqtSignal(list)                        # list of (x, y) in Bildkoordinaten
    paint_stroke       = pyqtSignal(int, int)                   # x, y in Bildkoordinaten
    quad_point_added   = pyqtSignal(list)                        # list of (x, y) Bildkoordinaten
    point_picked       = pyqtSignal(int, int)                   # x, y live während Drag (MODE_PICK_POINT)
    point_pick_committed = pyqtSignal(int, int)                 # x, y beim Loslassen (finales Commit)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        _tile = 12
        _checker_img = QImage(_tile * 2, _tile * 2, QImage.Format.Format_RGB32)
        _checker_img.fill(QColor(180, 180, 180))
        _cp = QPainter(_checker_img)
        _cp.fillRect(0, 0, _tile, _tile, QColor(220, 220, 220))
        _cp.fillRect(_tile, _tile, _tile, _tile, QColor(220, 220, 220))
        _cp.end()
        self._checker_brush = QBrush(QPixmap.fromImage(_checker_img))
        self._img_size: tuple[int, int] = (1, 1)
        self._mode = self.MODE_NONE
        self._crop_shape = self.SHAPE_RECT

        self._drag_start: QPoint | None = None
        self._drag_current: QPoint | None = None
        self._is_dragging = False
        self._aspect_ratio: float | None = None
        self._crop_img_rect: "QRectF | None" = None     # aktive Crop-Form in Bildkoordinaten
        self._crop_interact: str = "none"               # "none"|"draw"|"move"|"resize"
        self._crop_move_anchor: "QPointF | None" = None # Mausoffset beim Verschieben
        self._crop_resize_handle: str | None = None     # aktiver Handle-Name
        self._freehand_raw: "list | None" = None        # Chaikin-geglättetes Basis-Polygon (Bildkoord.)
        self._freehand_poly: "list | None" = None       # aktuell angezeigtes Polygon (mit Offset)
        self._lasso_points: list[QPoint] = []
        self._lasso_cursor: "QPoint | None" = None   # aktuelle Mausposition im Lasso-Modus
        self._right_paint: bool = False      # True = rechte Taste im Lasso-Modus malt
        self._paint_pos: QPoint | None = None
        self._paint_brush_size: int = 20
        self._quad_points: list[QPoint] = []
        self._quad_img_points: list[tuple] = []

        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setStyleSheet("background: #1a1a2e;")

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def set_image(self, img: Image.Image):
        self._img_size = img.size
        self._pixmap = _pil_to_pixmap(img)
        if not self._is_dragging:
            self._drag_start = None
            self._drag_current = None
        self.update()

    def set_crop_shape(self, shape: int):
        self._crop_shape = shape
        self._drag_start = None
        self._drag_current = None
        self.update()

    def set_aspect_ratio(self, ratio: float | None):
        """Seitenverhältnis (w/h) für Rechteck/Ellipse erzwingen, None = frei."""
        self._aspect_ratio = ratio

    def clear_crop_overlay(self):
        """Löscht die aktive Crop-Form."""
        self._crop_img_rect = None
        self._freehand_raw = None
        self._freehand_poly = None
        self._lasso_points = []
        self._crop_interact = "none"
        self._drag_start = None
        self._drag_current = None
        self.update()

    def get_crop_state(self):
        """Gibt (x, y, w, h, shape) in Bildkoordinaten zurück oder None."""
        if self._crop_shape == self.SHAPE_FREEHAND:
            return (0, 0, 0, 0, self.SHAPE_FREEHAND) if self._freehand_poly else None
        if self._crop_img_rect is None:
            return None
        r = self._crop_img_rect
        return (int(r.x()), int(r.y()), int(r.width()), int(r.height()), self._crop_shape)

    def get_freehand_polygon(self) -> list:
        """Gibt das aktuelle Freihand-Polygon in Bildkoordinaten zurück."""
        return self._freehand_poly or []

    def set_freehand_offset(self, pixels: float):
        """Verschiebt die Freihand-Kontur um `pixels` Pixel nach außen (>0) oder innen (<0)."""
        if self._freehand_raw is None:
            return
        if abs(pixels) < 0.5:
            self._freehand_poly = self._freehand_raw[:]
        else:
            try:
                from shapely.geometry import Polygon
                poly = Polygon(self._freehand_raw)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                buffered = poly.buffer(pixels)
                if buffered.is_empty:
                    self._freehand_poly = self._freehand_raw[:]
                else:
                    if buffered.geom_type == "MultiPolygon":
                        buffered = max(buffered.geoms, key=lambda g: g.area)
                    self._freehand_poly = [(x, y) for x, y in buffered.exterior.coords]
            except Exception:
                self._freehand_poly = self._freehand_raw[:]
        self.update()

    @staticmethod
    def _chaikin_smooth(pts: list, iterations: int = 4) -> list:
        """Chaikin-Algorithmus: glättet ein geschlossenes Polygon."""
        for _ in range(iterations):
            new_pts = []
            n = len(pts)
            for i in range(n):
                p0, p1 = pts[i], pts[(i + 1) % n]
                new_pts.append((0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1]))
                new_pts.append((0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1]))
            pts = new_pts
        return pts

    def _crop_handles_canvas(self) -> list:
        """Gibt Liste von (name, QPoint) für die Handles im Canvas-Koordinatensystem zurück."""
        if self._crop_shape == self.SHAPE_FREEHAND:
            return []
        if self._crop_img_rect is None:
            return []
        from PyQt6.QtCore import QPointF as _QPointF
        ox, oy, scale = self._img_transform()
        r = self._crop_img_rect
        cx = ox + r.x() * scale
        cy = oy + r.y() * scale
        cw = r.width() * scale
        ch = r.height() * scale
        if self._crop_shape == self.SHAPE_CIRCLE:
            return [("br", QPoint(int(cx + cw), int(cy + ch)))]
        elif self._crop_shape == self.SHAPE_ELLIPSE:
            return [
                ("t", QPoint(int(cx + cw / 2), int(cy))),
                ("r", QPoint(int(cx + cw),     int(cy + ch / 2))),
                ("b", QPoint(int(cx + cw / 2), int(cy + ch))),
                ("l", QPoint(int(cx),          int(cy + ch / 2))),
            ]
        else:  # SHAPE_RECT
            return [
                ("tl", QPoint(int(cx),          int(cy))),
                ("t",  QPoint(int(cx + cw / 2), int(cy))),
                ("tr", QPoint(int(cx + cw),     int(cy))),
                ("r",  QPoint(int(cx + cw),     int(cy + ch / 2))),
                ("br", QPoint(int(cx + cw),     int(cy + ch))),
                ("b",  QPoint(int(cx + cw / 2), int(cy + ch))),
                ("bl", QPoint(int(cx),          int(cy + ch))),
                ("l",  QPoint(int(cx),          int(cy + ch / 2))),
            ]

    def _hit_handle(self, pos: QPoint) -> str | None:
        HR = 9
        for name, hpos in self._crop_handles_canvas():
            if abs(pos.x() - hpos.x()) <= HR and abs(pos.y() - hpos.y()) <= HR:
                return name
        return None

    def _point_in_crop(self, pos: QPoint) -> bool:
        if self._crop_shape == self.SHAPE_FREEHAND:
            return False  # kein Verschieben für Freihand
        if self._crop_img_rect is None:
            return False
        ox, oy, scale = self._img_transform()
        r = self._crop_img_rect
        cx = ox + r.x() * scale
        cy = oy + r.y() * scale
        cw = r.width() * scale
        ch = r.height() * scale
        px, py = pos.x(), pos.y()
        if self._crop_shape == self.SHAPE_RECT:
            return cx <= px <= cx + cw and cy <= py <= cy + ch
        else:
            if cw == 0 or ch == 0:
                return False
            dx = (px - (cx + cw / 2)) / (cw / 2)
            dy = (py - (cy + ch / 2)) / (ch / 2)
            return dx * dx + dy * dy <= 1.0

    def _apply_crop_resize(self, canvas_pos: QPoint):
        ip = self._canvas_to_img(canvas_pos)
        ix = max(0.0, min(float(ip[0]), float(self._img_size[0])))
        iy = max(0.0, min(float(ip[1]), float(self._img_size[1])))
        r = self._crop_img_rect
        handle = self._crop_resize_handle
        iw_f, ih_f = float(self._img_size[0]), float(self._img_size[1])
        MIN = 10.0
        rx_val = r.x() + r.width()
        by_val = r.y() + r.height()
        from PyQt6.QtCore import QRectF as _QRectF
        if self._crop_shape == self.SHAPE_CIRCLE:
            new_side = max(MIN, min(ix - r.x(), iy - r.y(), iw_f - r.x(), ih_f - r.y()))
            self._crop_img_rect = _QRectF(r.x(), r.y(), new_side, new_side)
        elif self._crop_shape == self.SHAPE_ELLIPSE:
            if handle == "t":
                ny = max(0.0, min(iy, by_val - MIN))
                self._crop_img_rect = _QRectF(r.x(), ny, r.width(), by_val - ny)
            elif handle == "b":
                self._crop_img_rect = _QRectF(r.x(), r.y(), r.width(), max(MIN, min(iy - r.y(), ih_f - r.y())))
            elif handle == "l":
                nx = max(0.0, min(ix, rx_val - MIN))
                self._crop_img_rect = _QRectF(nx, r.y(), rx_val - nx, r.height())
            elif handle == "r":
                self._crop_img_rect = _QRectF(r.x(), r.y(), max(MIN, min(ix - r.x(), iw_f - r.x())), r.height())
        else:  # SHAPE_RECT
            ar = self._aspect_ratio  # w/h oder None
            if ar and ar > 0:
                # ── Aspect-Ratio gesperrt ─────────────────────────────────
                # Ecken: Breite ist treibende Größe, Höhe = w/ar
                if handle == "br":
                    nw = max(MIN, min(ix - r.x(), iw_f - r.x()))
                    nh = nw / ar
                    if r.y() + nh > ih_f:
                        nh = ih_f - r.y(); nw = nh * ar
                    self._crop_img_rect = _QRectF(r.x(), r.y(), nw, nh)
                elif handle == "bl":
                    nw = max(MIN, min(rx_val - ix, rx_val))
                    nh = nw / ar
                    if r.y() + nh > ih_f:
                        nh = ih_f - r.y(); nw = nh * ar
                    self._crop_img_rect = _QRectF(rx_val - nw, r.y(), nw, nh)
                elif handle == "tr":
                    nw = max(MIN, min(ix - r.x(), iw_f - r.x()))
                    nh = nw / ar
                    if by_val - nh < 0:
                        nh = by_val; nw = nh * ar
                    self._crop_img_rect = _QRectF(r.x(), by_val - nh, nw, nh)
                elif handle == "tl":
                    nw = max(MIN, min(rx_val - ix, rx_val))
                    nh = nw / ar
                    if by_val - nh < 0:
                        nh = by_val; nw = nh * ar
                    self._crop_img_rect = _QRectF(rx_val - nw, by_val - nh, nw, nh)
                # Obere/untere Kante: Höhe treibt, Breite zentriert
                elif handle in ("t", "b"):
                    cx = r.x() + r.width() / 2
                    if handle == "t":
                        ny = max(0.0, min(iy, by_val - MIN))
                        nh = by_val - ny
                    else:
                        nh = max(MIN, min(iy - r.y(), ih_f - r.y()))
                        ny = r.y()
                    nw = nh * ar
                    nx = cx - nw / 2
                    if nx < 0:
                        nw = cx * 2; nh = nw / ar; nx = 0.0
                    elif nx + nw > iw_f:
                        nw = (iw_f - cx) * 2; nh = nw / ar; nx = iw_f - nw
                    self._crop_img_rect = _QRectF(nx, ny, nw, nh)
                # Linke/rechte Kante: Breite treibt, Höhe zentriert
                elif handle in ("l", "r"):
                    cy = r.y() + r.height() / 2
                    if handle == "l":
                        nx = max(0.0, min(ix, rx_val - MIN))
                        nw = rx_val - nx
                    else:
                        nw = max(MIN, min(ix - r.x(), iw_f - r.x()))
                        nx = r.x()
                    nh = nw / ar
                    ny = cy - nh / 2
                    if ny < 0:
                        nh = cy * 2; nw = nh * ar; ny = 0.0
                    elif ny + nh > ih_f:
                        nh = (ih_f - cy) * 2; nw = nh * ar; ny = ih_f - nh
                    self._crop_img_rect = _QRectF(nx, ny, nw, nh)
            else:
                # ── Freies Seitenverhältnis ───────────────────────────────
                if handle == "tl":
                    nx = max(0.0, min(ix, rx_val - MIN)); ny = max(0.0, min(iy, by_val - MIN))
                    self._crop_img_rect = _QRectF(nx, ny, rx_val - nx, by_val - ny)
                elif handle == "tr":
                    ny = max(0.0, min(iy, by_val - MIN))
                    self._crop_img_rect = _QRectF(r.x(), ny, max(MIN, min(ix - r.x(), iw_f - r.x())), by_val - ny)
                elif handle == "bl":
                    nx = max(0.0, min(ix, rx_val - MIN))
                    self._crop_img_rect = _QRectF(nx, r.y(), rx_val - nx, max(MIN, min(iy - r.y(), ih_f - r.y())))
                elif handle == "br":
                    self._crop_img_rect = _QRectF(r.x(), r.y(), max(MIN, min(ix - r.x(), iw_f - r.x())), max(MIN, min(iy - r.y(), ih_f - r.y())))
                elif handle == "t":
                    ny = max(0.0, min(iy, by_val - MIN))
                    self._crop_img_rect = _QRectF(r.x(), ny, r.width(), by_val - ny)
                elif handle == "b":
                    self._crop_img_rect = _QRectF(r.x(), r.y(), r.width(), max(MIN, min(iy - r.y(), ih_f - r.y())))
                elif handle == "l":
                    nx = max(0.0, min(ix, rx_val - MIN))
                    self._crop_img_rect = _QRectF(nx, r.y(), rx_val - nx, r.height())
                elif handle == "r":
                    self._crop_img_rect = _QRectF(r.x(), r.y(), max(MIN, min(ix - r.x(), iw_f - r.x())), r.height())

    def set_mode(self, mode: int):
        self._mode = mode
        self._drag_start = None
        self._drag_current = None
        self._lasso_points = []
        self._paint_pos = None
        self._quad_points = []
        self._quad_img_points = []
        if mode == self.MODE_CROP:
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        elif mode in (self.MODE_ADJUST, self.MODE_ROTATE):
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
        elif mode in (self.MODE_LASSO, self.MODE_PAINT, self.MODE_QUAD):
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    def reset_quad_points(self):
        """Löscht alle gesetzten Quad-Punkte."""
        self._quad_points = []
        self._quad_img_points = []
        self.update()

    def set_paint_brush_size(self, size: int):
        self._paint_brush_size = size
        self.update()

    # ── Koordinaten-Hilfsmethoden ─────────────────────────────────────────────

    def _img_transform(self) -> tuple[float, float, float]:
        """Gibt (offset_x, offset_y, scale) des skalierten Bildes zurück."""
        if self._pixmap is None:
            return 0.0, 0.0, 1.0
        cw, ch = self.width(), self.height()
        iw, ih = self._img_size
        scale = min(cw / iw, ch / ih)
        ox = 8  # linksbündig mit kleinem Rand
        oy = (ch - ih * scale) / 2
        return ox, oy, scale

    def _canvas_to_img(self, p: QPoint) -> tuple[int, int]:
        ox, oy, scale = self._img_transform()
        x = int((p.x() - ox) / scale)
        y = int((p.y() - oy) / scale)
        iw, ih = self._img_size
        return max(0, min(x, iw)), max(0, min(y, ih))

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if self._pixmap:
            ox, oy, scale = self._img_transform()
            iw, ih = self._img_size
            dst = QRect(int(ox), int(oy), int(iw * scale), int(ih * scale))
            # Schachbrettmuster für transparente Bereiche (Kreis/Ellipse-Freisteller)
            if self._pixmap.hasAlphaChannel():
                painter.fillRect(dst, self._checker_brush)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawPixmap(dst, self._pixmap)

        # Crop-Vorschau zeichnen
        if self._mode == self.MODE_CROP and self._crop_shape == self.SHAPE_FREEHAND:
            full = QRectF(0, 0, self.width(), self.height())
            ox, oy, scale = self._img_transform()
            if self._freehand_poly is not None and len(self._freehand_poly) >= 3:
                # Fertiges (ggf. offset) Polygon anzeigen
                cpts = [QPointF(ox + x * scale, oy + y * scale) for x, y in self._freehand_poly]
                poly_path = QPainterPath()
                poly_path.moveTo(cpts[0])
                for cp in cpts[1:]:
                    poly_path.lineTo(cp)
                poly_path.closeSubpath()
                outer = QPainterPath()
                outer.setFillRule(Qt.FillRule.OddEvenFill)
                outer.addRect(full)
                outer.addPath(poly_path)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(0, 0, 0, 120))
                painter.drawPath(outer)
                painter.setPen(QPen(QColor(255, 255, 255), 2, Qt.PenStyle.SolidLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(poly_path)
            elif self._is_dragging and len(self._lasso_points) > 1:
                # Freihand gerade zeichnen
                pts_snap = list(self._lasso_points[::3]) or self._lasso_points[:1]
                path = QPainterPath()
                path.moveTo(pts_snap[0].x(), pts_snap[0].y())
                for lp in pts_snap[1:]:
                    path.lineTo(lp.x(), lp.y())
                path.closeSubpath()
                painter.setBrush(QColor(52, 152, 219, 40))
                painter.setPen(QPen(QColor(52, 152, 219), 2))
                painter.drawPath(path)

        elif self._mode == self.MODE_CROP:
            # Canvas-Rect ermitteln: entweder aus fertiger Form oder aus laufendem Draw
            canvas_rf: "QRectF | None" = None
            if self._crop_img_rect is not None:
                ox, oy, scale = self._img_transform()
                r = self._crop_img_rect
                canvas_rf = QRectF(ox + r.x() * scale, oy + r.y() * scale,
                                   r.width() * scale, r.height() * scale)
            elif self._drag_start and self._drag_current:
                raw = QRect(self._drag_start, self._drag_current).normalized()
                if self._crop_shape == self.SHAPE_CIRCLE:
                    side = min(raw.width(), raw.height())
                    canvas_rf = QRectF(raw.left(), raw.top(), side, side)
                else:
                    canvas_rf = QRectF(raw)

            if canvas_rf is not None:
                full = QRect(0, 0, self.width(), self.height())
                if self._crop_shape == self.SHAPE_RECT:
                    rect = canvas_rf.toRect()
                    painter.setBrush(QColor(0, 0, 0, 100))
                    painter.setPen(Qt.PenStyle.NoPen)
                    for sr in _subtract_rect(full, rect):
                        painter.drawRect(sr)
                    painter.setPen(QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(rect)
                else:  # CIRCLE / ELLIPSE
                    path = QPainterPath()
                    path.setFillRule(Qt.FillRule.OddEvenFill)
                    path.addRect(QRectF(full))
                    path.addEllipse(canvas_rf)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(0, 0, 0, 120))
                    painter.drawPath(path)
                    painter.setPen(QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(canvas_rf)
                    if self._crop_shape == self.SHAPE_CIRCLE:
                        painter.setPen(QPen(QColor(200, 200, 200, 80), 1, Qt.PenStyle.DotLine))
                        painter.drawRect(canvas_rf)

                # Handles zeichnen (nur wenn Form fertig gezeichnet)
                if self._crop_img_rect is not None:
                    for name, hpos in self._crop_handles_canvas():
                        painter.setPen(QPen(QColor(255, 255, 255), 2))
                        painter.setBrush(QColor(52, 152, 219))
                        painter.drawRect(QRect(hpos.x() - 5, hpos.y() - 5, 10, 10))

        # Quad-Punkte zeichnen (4-Punkt-Perspektiv-Warp)
        if self._mode == self.MODE_QUAD and self._quad_points:
            orange = QColor(255, 140, 0)
            painter.setPen(QPen(orange, 2, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # Verbindungslinien
            pts = self._quad_points
            n = len(pts)
            if n >= 2:
                for i in range(n - 1):
                    painter.drawLine(pts[i], pts[i + 1])
            if n == 4:
                painter.drawLine(pts[3], pts[0])  # geschlossenes Viereck
            # Punkte mit Nummern
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.setBrush(orange)
            for i, p in enumerate(pts):
                painter.drawEllipse(p, 6, 6)
                painter.setPen(QPen(QColor(255, 255, 255), 1))
                painter.drawText(p.x() + 8, p.y() - 4, str(i + 1))
                painter.setPen(QPen(QColor(255, 255, 255), 1))

        # Lasso-Pfad zeichnen (offen — Schließen durch Maus)
        if self._mode == self.MODE_LASSO and len(self._lasso_points) > 1:
            lasso_path = QPainterPath()
            lasso_path.moveTo(self._lasso_points[0].x(), self._lasso_points[0].y())
            for p in self._lasso_points[1:]:
                lasso_path.lineTo(p.x(), p.y())
            # kein closeSubpath – Pfad bleibt offen
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(52, 152, 219), 2, Qt.PenStyle.SolidLine))
            painter.drawPath(lasso_path)

            # Startpunkt-Indikator: gelb = weit, grün = nah genug zum Schließen
            sp = self._lasso_points[0]
            snap_r = 18
            near = (self._lasso_cursor is not None and len(self._lasso_points) > 20
                    and (self._lasso_cursor - sp).manhattanLength() < snap_r * 2)
            ring_color = QColor(0, 220, 80) if near else QColor(255, 220, 0)
            painter.setPen(QPen(ring_color, 2))
            painter.setBrush(QColor(ring_color.red(), ring_color.green(), ring_color.blue(), 60))
            painter.drawEllipse(sp, snap_r, snap_r)

        # Pinsel-Cursor anzeigen
        if (self._mode == self.MODE_PAINT or self._right_paint) and self._paint_pos:
            _, _, scale = self._img_transform()
            r = max(2, int(self._paint_brush_size * scale))
            painter.setPen(QPen(QColor(255, 255, 255), 1, Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self._paint_pos, r, r)
            painter.setPen(QPen(QColor(0, 0, 0), 1, Qt.PenStyle.DotLine))
            painter.drawEllipse(self._paint_pos, r, r)

        painter.end()

    # ── Maus-Events ───────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        # Rechte Maustaste im Lasso-Modus → Pinsel malen
        if (event.button() == Qt.MouseButton.RightButton
                and self._mode == self.MODE_LASSO):
            self._right_paint = True
            self._is_dragging = True
            self._paint_pos = event.pos()
            ix, iy = self._canvas_to_img(event.pos())
            self.paint_stroke.emit(ix, iy)
            self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._mode == self.MODE_LASSO:
            self._lasso_points = [event.pos()]
            self._is_dragging = True
            return
        if self._mode == self.MODE_PAINT:
            self._is_dragging = True
            ix, iy = self._canvas_to_img(event.pos())
            self.paint_stroke.emit(ix, iy)
            return
        if self._mode == self.MODE_PICK_POINT:
            ix, iy = self._canvas_to_img(event.pos())
            self.point_picked.emit(ix, iy)
            self._is_dragging = True          # Drag-Modus — bleibt in PICK_POINT
            return
        if self._mode == self.MODE_QUAD:
            if len(self._quad_points) < 4:
                self._quad_points.append(event.pos())
                ix, iy = self._canvas_to_img(event.pos())
                self._quad_img_points.append((ix, iy))
                self.quad_point_added.emit(list(self._quad_img_points))
                self.update()
            return
        if self._mode == self.MODE_CROP and self._crop_shape == self.SHAPE_FREEHAND:
            # Jeder Klick startet neu
            self._freehand_raw = None
            self._freehand_poly = None
            self._lasso_points = [event.pos()]
            self._crop_interact = "draw"
            self._is_dragging = True
            return
        if self._mode == self.MODE_CROP and self._crop_img_rect is not None:
            handle = self._hit_handle(event.pos())
            if handle:
                self._crop_interact = "resize"
                self._crop_resize_handle = handle
                self._is_dragging = True
                return
            if self._point_in_crop(event.pos()):
                self._crop_interact = "move"
                ip = self._canvas_to_img(event.pos())
                from PyQt6.QtCore import QPointF as _QPointF
                self._crop_move_anchor = _QPointF(ip[0] - self._crop_img_rect.x(),
                                                  ip[1] - self._crop_img_rect.y())
                self._is_dragging = True
                return
            # Klick außerhalb → neue Form zeichnen
            self._crop_img_rect = None
        self._crop_interact = "draw"
        self._drag_start = event.pos()
        self._drag_current = event.pos()
        self._is_dragging = True

    def mouseMoveEvent(self, event):
        if self._mode == self.MODE_PAINT:
            self._paint_pos = event.pos()
            self.update()
            if self._is_dragging:
                ix, iy = self._canvas_to_img(event.pos())
                self.paint_stroke.emit(ix, iy)
            return

        # Cursor-Feedback im Crop-Modus (auch ohne Dragging)
        if self._mode == self.MODE_CROP and not self._is_dragging and self._crop_img_rect is not None:
            handle = self._hit_handle(event.pos())
            if handle:
                _cur = {"tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                        "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
                        "t":  Qt.CursorShape.SizeVerCursor,   "b":  Qt.CursorShape.SizeVerCursor,
                        "l":  Qt.CursorShape.SizeHorCursor,   "r":  Qt.CursorShape.SizeHorCursor}
                self.setCursor(_cur.get(handle, Qt.CursorShape.SizeAllCursor))
            elif self._point_in_crop(event.pos()):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)

        if not self._is_dragging:
            return

        if self._mode == self.MODE_PICK_POINT:
            ix, iy = self._canvas_to_img(event.pos())
            self.point_picked.emit(ix, iy)
            return

        if self._mode == self.MODE_LASSO:
            if self._right_paint:
                self._paint_pos = event.pos()
                ix, iy = self._canvas_to_img(event.pos())
                self.paint_stroke.emit(ix, iy)
            else:
                self._lasso_points.append(event.pos())
                self._lasso_cursor = event.pos()
            self.update()
            return

        if self._mode == self.MODE_CROP:
            if self._crop_interact == "move" and self._crop_img_rect is not None:
                ip = self._canvas_to_img(event.pos())
                iw_f, ih_f = float(self._img_size[0]), float(self._img_size[1])
                from PyQt6.QtCore import QRectF as _QRectF
                nx = max(0.0, min(ip[0] - self._crop_move_anchor.x(), iw_f - self._crop_img_rect.width()))
                ny = max(0.0, min(ip[1] - self._crop_move_anchor.y(), ih_f - self._crop_img_rect.height()))
                self._crop_img_rect = _QRectF(nx, ny, self._crop_img_rect.width(), self._crop_img_rect.height())
                self.update()
                return
            if self._crop_interact == "resize" and self._crop_img_rect is not None:
                self._apply_crop_resize(event.pos())
                self.update()
                return
            # draw-Phase Freihand
            if self._crop_shape == self.SHAPE_FREEHAND:
                self._lasso_points.append(event.pos())
                self.update()
                return
            # draw-Phase Rect/Circle/Ellipse
            if self._drag_start is None:
                return
            self._drag_current = event.pos()
            dx = self._drag_current.x() - self._drag_start.x()
            dy = self._drag_current.y() - self._drag_start.y()
            if self._crop_shape == self.SHAPE_CIRCLE:
                side = min(abs(dx), abs(dy))
                self._drag_current = QPoint(
                    self._drag_start.x() + (side if dx >= 0 else -side),
                    self._drag_start.y() + (side if dy >= 0 else -side),
                )
            elif self._aspect_ratio and self._aspect_ratio > 0:
                new_h = int(abs(dx) / self._aspect_ratio)
                self._drag_current = QPoint(
                    self._drag_current.x(),
                    self._drag_start.y() + (new_h if dy >= 0 else -new_h),
                )
            self.update()
            return

        if self._drag_start is None:
            return
        self._drag_current = event.pos()

        if self._mode in (self.MODE_ADJUST, self.MODE_ROTATE):
            delta = self._drag_current.x() - self._drag_start.x()
            if self._mode == self.MODE_ADJUST:
                self.adjust_delta.emit(delta)
            else:
                self.rotate_delta.emit(delta * 0.2)
            self._drag_start = self._drag_current  # relative Deltas

    def leaveEvent(self, event):
        self._paint_pos = None
        self.update()

    def mouseReleaseEvent(self, event):
        if self._is_dragging and self._mode == self.MODE_PICK_POINT:
            ix, iy = self._canvas_to_img(event.pos())
            self._is_dragging = False
            self.set_mode(self.MODE_NONE)
            self.point_pick_committed.emit(ix, iy)
            return
        if self._mode == self.MODE_PAINT:
            self._is_dragging = False
            return
        if self._is_dragging and self._mode == self.MODE_LASSO:
            if self._right_paint:
                self._right_paint = False
                self._paint_pos = None
                self._is_dragging = False
                self.update()
                return
            # Polygon nur committen wenn Endpunkt nah am Startpunkt (manuelles Schließen)
            snap_r = 18
            if (len(self._lasso_points) > 20
                    and (self._lasso_points[-1] - self._lasso_points[0]).manhattanLength() < snap_r * 2):
                pts = [self._canvas_to_img(p) for p in self._lasso_points]
                self.lasso_committed.emit(pts)
            self._lasso_points = []
            self._lasso_cursor = None
            self._is_dragging = False
            self.update()
            return
        if self._is_dragging and self._mode == self.MODE_CROP:
            if self._crop_interact == "draw":
                if (self._crop_shape == self.SHAPE_FREEHAND
                        and len(self._lasso_points) > 10):
                    raw = [self._canvas_to_img(p) for p in self._lasso_points]
                    # Subsample auf max. 150 Punkte, damit Chaikin nicht explodiert
                    step = max(1, len(raw) // 150)
                    raw = raw[::step]
                    smoothed = self._chaikin_smooth(raw, 4)
                    self._freehand_raw = smoothed
                    self._freehand_poly = smoothed[:]
                    self.crop_shape_ready.emit()
                elif self._drag_start and self._drag_current:
                    p1 = self._canvas_to_img(self._drag_start)
                    p2 = self._canvas_to_img(self._drag_current)
                    x = min(p1[0], p2[0])
                    y = min(p1[1], p2[1])
                    w = abs(p2[0] - p1[0])
                    h = abs(p2[1] - p1[1])
                    # Seitenverhältnis beim Abschluss exakt einhalten
                    if (self._crop_shape == self.SHAPE_RECT
                            and self._aspect_ratio and self._aspect_ratio > 0):
                        h = w / self._aspect_ratio
                    if w > 2 and h > 2:
                        from PyQt6.QtCore import QRectF as _QRectF
                        self._crop_img_rect = _QRectF(x, y, w, h)
                        self.crop_shape_ready.emit()
            self._lasso_points = []
            self._drag_start = None
            self._drag_current = None
            self._crop_interact = "none"
        self._is_dragging = False


# ── Hilfsroutine für Crop-Abdunklung ─────────────────────────────────────────

def _subtract_rect(outer: QRect, inner: QRect) -> list[QRect]:
    """Gibt 4 Rechtecke zurück, die outer minus inner abdecken."""
    inner = inner.intersected(outer)
    return [
        QRect(outer.left(), outer.top(), outer.width(), inner.top() - outer.top()),
        QRect(outer.left(), inner.bottom(), outer.width(), outer.bottom() - inner.bottom()),
        QRect(outer.left(), inner.top(), inner.left() - outer.left(), inner.height()),
        QRect(inner.right(), inner.top(), outer.right() - inner.right(), inner.height()),
    ]


# ── Schieberegler mit Wert-Label ──────────────────────────────────────────────

def _labeled_slider(layout: QFormLayout, label: str,
                    min_: int, max_: int, val: int,
                    on_change=None, on_reset=None) -> QSlider:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(min_, max_)
    slider.setValue(val)
    lbl = QLabel(str(val))
    lbl.setFixedWidth(34)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lbl.setStyleSheet("font-size: 11px; color: #555;")
    slider.valueChanged.connect(lambda v, l=lbl: l.setText(str(v)))
    if on_change:
        slider.valueChanged.connect(on_change)
    reset_btn = QPushButton("↺")
    reset_btn.setFixedSize(22, 22)
    reset_btn.setToolTip("Auf Ausgangswert zurücksetzen")
    reset_btn.setStyleSheet(
        "QPushButton { background: #dde; color: #555; border-radius: 4px;"
        "  font-size: 13px; padding: 0; }"
        "QPushButton:hover { background: #bbc; color: #222; }"
    )
    reset_btn.clicked.connect(on_reset if on_reset else lambda: slider.setValue(val))
    row.addWidget(slider)
    row.addWidget(lbl)
    row.addWidget(reset_btn)
    w = QWidget(); w.setLayout(row)
    layout.addRow(label, w)
    return slider


# ── Histogramm-Widget ─────────────────────────────────────────────────────────

class HistogramWidget(QWidget):
    """Zeigt ein RGB-Histogramm des aktuellen Bildes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list[list[int]] = []   # [[R×256], [G×256], [B×256]]
        self._lum: list[float] = []        # Luminanz-Kanal
        self.setFixedHeight(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("Histogramm: Rot / Grün / Blau / Luminanz")

    def set_image(self, img):
        """Histogrammdaten aus PIL-Bild berechnen und glätten."""
        try:
            rgb = img.convert("RGB")
            raw = rgb.histogram()          # 768 Werte: R[0..255], G[0..255], B[0..255]
            r = raw[0:256]
            g = raw[256:512]
            b = raw[512:768]
            self._data = [
                self._smooth(r),
                self._smooth(g),
                self._smooth(b),
            ]
            # Luminanz-Histogramm: gewichteter Durchschnitt (Rec. 601)
            lum_raw = [0.299 * r[i] + 0.587 * g[i] + 0.114 * b[i] for i in range(256)]
            self._lum = self._smooth(lum_raw)
        except Exception:
            self._data = []
            self._lum = []
        self.update()

    @staticmethod
    def _smooth(ch: list, sigma: float = 3.0) -> list:
        """Gaußscher Weichzeichner — weiche Kurven ohne Zacken-Verbreiterung."""
        import math
        radius = int(math.ceil(sigma * 3))
        kernel = [math.exp(-0.5 * (i / sigma) ** 2) for i in range(-radius, radius + 1)]
        total = sum(kernel)
        kernel = [k / total for k in kernel]
        size = len(ch)
        out = []
        for i in range(size):
            val = 0.0
            for j, k in enumerate(kernel):
                idx = i - radius + j
                if 0 <= idx < size:
                    val += ch[idx] * k
            out.append(val)
        return out

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            self._paint(painter)
        except Exception:
            pass
        finally:
            painter.end()

    def _paint(self, painter):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        W, H = self.width(), self.height()
        pad = 2

        # Hintergrund
        painter.fillRect(0, 0, W, H, QColor(30, 30, 30))

        if not self._data:
            return

        draw_w = W - 2 * pad
        draw_h = H - 2 * pad

        # Maximum über alle Kanäle (ohne Wert 0 und 255 um Clipping zu dämpfen)
        inner = [ch[1:255] for ch in self._data if len(ch) > 255]
        if not inner:
            return
        peak = max(max(ch) for ch in inner) or 1

        from PyQt6.QtGui import QPainterPath

        colors = [QColor(220, 60, 60, 160), QColor(60, 200, 60, 160), QColor(60, 100, 220, 160)]

        for ch_data, color in zip(self._data, colors):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)

            path = QPainterPath()
            path.moveTo(pad, pad + draw_h)

            for i, val in enumerate(ch_data):
                x = pad + i * draw_w / 255
                bar_h = min(val / peak, 1.0) * draw_h
                y = pad + draw_h - bar_h
                path.lineTo(x, y)

            path.lineTo(pad + draw_w, pad + draw_h)
            path.closeSubpath()
            painter.drawPath(path)

        # Luminanz-Kurve (weiße Linie, kein Fill)
        if self._lum:
            lum_pen = QPen(QColor(220, 220, 220, 200))
            lum_pen.setWidth(1)
            painter.setPen(lum_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            lum_path = QPainterPath()
            first = True
            for i, val in enumerate(self._lum):
                x = pad + i * draw_w / 255
                bar_h = min(val / peak, 1.0) * draw_h
                y = pad + draw_h - bar_h
                if first:
                    lum_path.moveTo(x, y)
                    first = False
                else:
                    lum_path.lineTo(x, y)
            painter.drawPath(lum_path)

        # Legende
        painter.setFont(QFont("Arial", 8))
        for i, (label, color) in enumerate(zip(["R", "G", "B"], colors)):
            color.setAlpha(255)
            painter.setPen(color)
            painter.drawText(pad + 4 + i * 20, pad + 12, label)
        painter.setPen(QColor(220, 220, 220))
        painter.drawText(pad + 4 + 3 * 20, pad + 12, "L")


# ── Editor-Screen ─────────────────────────────────────────────────────────────

class EditorScreen(QWidget):
    back_requested     = pyqtSignal()
    image_saved        = pyqtSignal(str, str)   # (alter Pfad, neuer Pfad)
    nav_prev_requested = pyqtSignal()
    nav_next_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._original: Image.Image | None = None
        self._original_at_load: Image.Image | None = None   # echtes Ur-Original (aus Backup)
        self._original_file_path: Path | None = None        # Ziel-Dateipfad (für Restore)
        self._backup_path: Path | None = None               # Backup-Pfad (.optimizer_originals/)
        self._history: list[Image.Image] = []               # Undo-Stack
        self._file_path: Path | None = None
        self._active_adjust_slider: QSlider | None = None
        self._fine_rotate_base: Image.Image | None = None  # Basis für akkumulierte Feinrotation
        self._fine_rotate_total: float = 0.0               # Bisher akkumulierter Winkel
        self._paint_erase_mode: bool = True                # True=Entfernen, False=Wiederherstellen
        self._hg_mask  = None                               # numpy H×W uint8, 255=Vordergrund
        self._hg_base: Image.Image | None = None            # RGB-Basis zum Zeitpunkt der Maske
        self._focus_mask = None                             # numpy H×W uint8, 255=ausgewählte Person
        self._focus_base: Image.Image | None = None         # RGB-Basis für Personen-Fokus
        self._person_focus_selecting: bool = False          # True = Rechteck-Auswahl aktiv
        self._ent_base: Image.Image | None = None           # Basis für Entzerren-Live-Preview
        self._distortion_base: Image.Image | None = None   # Basis vor erster Objektivverzerrung (für Reset)
        self._quad_base: Image.Image | None = None          # Basis vor 4-Punkt-Warp (für Zurücksetzen)
        self._crop_base: Image.Image | None = None          # Basis vor letztem Crop (für Zurücksetzen)
        self._ent_base_small: Image.Image | None = None     # Skaliertes Bild für Schnellvorschau
        self._adj_base: Image.Image | None = None           # Basis für Anpassen-Live-Preview
        self._quad_img_points: list[tuple] = []             # 4-Punkt-Perspektiv-Warp Bildkoordinaten
        self._geo_worker: "_GeoWorker | None" = None
        self._old_geo_workers: list = []   # verhindert GC während Thread läuft
        self._build_ui()
        self._setup_shortcuts()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Kopfzeile ─────────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setContentsMargins(12, 2, 12, 2)

        self._file_label = QLabel("")
        self._file_label.setStyleSheet("color: #7f8c8d; font-size: 13px;")
        self._file_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self._hint_label = QLabel("")
        self._hint_label.setStyleSheet("color: #3498db; font-size: 12px; font-style: italic;")

        _hdr_style = (
            "QPushButton { background: #34495e; color: white; border-radius: 4px;"
            "  padding: 2px 10px; font-size: 12px; }"
            "QPushButton:hover:enabled { background: #2c3e50; }"
            "QPushButton:disabled { background: #3a3a3a; color: #666; }"
        )

        self._undo_btn = QPushButton("↺ Rückgängig")
        self._undo_btn.setFixedHeight(20)
        self._undo_btn.setToolTip("Letzte Änderung rückgängig machen")
        self._undo_btn.setEnabled(False)
        self._undo_btn.clicked.connect(self._undo)
        self._undo_btn.setStyleSheet(_hdr_style)

        self._save_btn = QPushButton("💾 Speichern")
        self._save_btn.setFixedHeight(20)
        self._save_btn.setToolTip("Bild speichern (Original überschreiben)")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save_file)
        self._save_btn.setStyleSheet(
            "QPushButton { background: #27ae60; color: white; border-radius: 4px;"
            "  padding: 2px 10px; font-size: 12px; }"
            "QPushButton:hover:enabled { background: #1e8449; }"
            "QPushButton:disabled { background: #555; color: #999; }"
        )

        self._restore_btn = QPushButton("⟲ Original")
        self._restore_btn.setFixedHeight(20)
        self._restore_btn.setToolTip("Zum unbearbeiteten Original zurückkehren")
        self._restore_btn.setEnabled(False)
        self._restore_btn.clicked.connect(self._restore_original)
        self._restore_btn.setStyleSheet(
            "QPushButton { background: #e67e22; color: white; border-radius: 4px;"
            "  padding: 2px 10px; font-size: 12px; }"
            "QPushButton:hover:enabled { background: #ca6f1e; }"
            "QPushButton:disabled { background: #555; color: #999; }"
        )

        _nav_style = (
            "QPushButton { background: #34495e; color: white; border-radius: 4px;"
            "  padding: 2px 10px; font-size: 15px; }"
            "QPushButton:hover:enabled { background: #2c3e50; }"
            "QPushButton:disabled { background: #ccc; color: #888; }"
        )
        self._nav_prev_btn = QPushButton("‹")
        self._nav_prev_btn.setFixedHeight(20)
        self._nav_prev_btn.setToolTip("Vorheriges Bild")
        self._nav_prev_btn.setEnabled(False)
        self._nav_prev_btn.clicked.connect(lambda: self._try_navigate("prev"))
        self._nav_prev_btn.setStyleSheet(_nav_style)

        self._nav_next_btn = QPushButton("›")
        self._nav_next_btn.setFixedHeight(20)
        self._nav_next_btn.setToolTip("Nächstes Bild")
        self._nav_next_btn.setEnabled(False)
        self._nav_next_btn.clicked.connect(lambda: self._try_navigate("next"))
        self._nav_next_btn.setStyleSheet(_nav_style)

        close_btn = QPushButton("← Galerie")
        close_btn.setFixedHeight(20)
        close_btn.setToolTip("Zurück zur Bildübersicht")
        close_btn.clicked.connect(self.back_requested.emit)
        close_btn.setStyleSheet(
            "QPushButton { background: #34495e; color: white; border-radius: 4px;"
            "  padding: 2px 10px; font-size: 12px; }"
            "QPushButton:hover { background: #2c3e50; }"
        )

        header.addWidget(self._file_label)
        header.addWidget(self._hint_label)
        header.addStretch()
        header.addWidget(self._undo_btn)
        header.addSpacing(4)
        header.addWidget(self._save_btn)
        header.addSpacing(4)
        header.addWidget(self._restore_btn)
        header.addSpacing(8)
        header.addWidget(self._nav_prev_btn)
        header.addWidget(self._nav_next_btn)
        header.addSpacing(4)
        header.addWidget(close_btn)

        # ── Menüleiste ────────────────────────────────────────────────────────
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setStyleSheet(
            "QToolBar { background: #2c3e50; border: none; spacing: 4px; padding: 4px 8px; }"
            "QToolButton { color: white; padding: 8px 14px; border-radius: 6px; font-size: 13px; }"
            "QToolButton:hover { background: #34495e; }"
            "QToolButton:checked { background: #3498db; }"
        )
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        self._group_actions: dict[str, QAction] = {}
        self._groups = [
            ("bewegen",        "Bewegen",        ImageCanvas.MODE_ROTATE, "← → ziehen zum Drehen"),
            ("zuschneiden",    "Zuschneiden",    ImageCanvas.MODE_CROP,   "Bereich im Bild aufziehen"),
            ("freistellen",    "Freistellen",    ImageCanvas.MODE_LASSO,  "Objekt mit Maus umfahren → loslassen"),
            ("hintergrund",    "Hintergrund",    ImageCanvas.MODE_NONE,   ""),
            ("entzerren",      "Entzerren",      ImageCanvas.MODE_NONE,   ""),
            ("anpassen",       "Anpassen",       ImageCanvas.MODE_ADJUST, "← → ziehen zum Anpassen"),
            ("bildgroesse",    "Bildgröße",      ImageCanvas.MODE_NONE,   ""),
            ("bildinfos",      "Bildinfos",      ImageCanvas.MODE_NONE,   ""),
        ]
        for key, label, _, _ in self._groups:
            action = QAction(label, self)
            action.setCheckable(True)
            action.toggled.connect(lambda checked, k=key: self._on_group_toggled(k, checked))
            toolbar.addAction(action)
            self._group_actions[key] = action

        # ── Inhaltsbereich ────────────────────────────────────────────────────
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)

        # Canvas
        self._canvas = ImageCanvas()
        self._canvas.crop_shape_ready.connect(self._on_crop_shape_ready)
        self._canvas.adjust_delta.connect(self._on_canvas_adjust)
        self._canvas.rotate_delta.connect(self._on_canvas_rotate)
        self._canvas.lasso_committed.connect(self._on_lasso_committed)
        self._canvas.paint_stroke.connect(self._on_paint_stroke)
        self._canvas.quad_point_added.connect(self._on_quad_point_added)
        self._canvas.point_picked.connect(self._on_distortion_center_picked)
        self._canvas.point_pick_committed.connect(self._on_distortion_center_committed)

        # Seitenleiste
        self._sidebar = QStackedWidget()
        self._sidebar.setFixedWidth(0)
        self._sidebar.setStyleSheet(
            "QStackedWidget { background: #f0f2f5; border-left: 1px solid #dde; }"
        )

        self._panels: dict[str, QWidget] = {}
        for key, label, _, _ in self._groups:
            panel = self._build_panel(key, label)
            self._panels[key] = panel
            self._sidebar.addWidget(panel)

        content.addWidget(self._canvas, 1)
        content.addWidget(self._sidebar)

        root.addLayout(header)
        root.addWidget(toolbar)
        root.addLayout(content, 1)

    # ── Panel-Fabrik ──────────────────────────────────────────────────────────

    def _build_panel(self, key: str, title: str) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        scroll.setWidget(inner)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        heading = QLabel(title)
        f = QFont(); f.setBold(True); f.setPointSize(13)
        heading.setFont(f)
        layout.addWidget(heading)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #ccc;")
        layout.addWidget(sep)

        if key == "bewegen":
            layout.addWidget(QLabel("Im Bild ziehen um zu drehen, oder Buttons nutzen:"))
            layout.addWidget(self._btn("90° Uhrzeigersinn", lambda: self._rotate(90)))
            layout.addWidget(self._btn("90° gegen Uhrzeigersinn", lambda: self._rotate(-90)))
            layout.addWidget(self._btn("Horizontal spiegeln", lambda: self._flip("h")))
            layout.addWidget(self._btn("Vertikal spiegeln",   lambda: self._flip("v")))
            form = QFormLayout()
            self._fine_rotate_slider = _labeled_slider(
                form, "Feinrotation (°):", -45, 45, 0,
                on_reset=self._reset_fine_rotate,
            )
            self._fine_rotate_slider.sliderPressed.connect(self._on_fine_rotate_press)
            self._fine_rotate_slider.valueChanged.connect(self._on_fine_rotate_drag)
            self._fine_rotate_slider.sliderReleased.connect(self._on_fine_rotate_release)
            # action == 7 ist SliderMove (Maus-Drag) → nicht committen, nur Tastatur-Aktionen
            self._fine_rotate_slider.actionTriggered.connect(
                lambda action: self._apply_fine_rotate() if action != 7 else None
            )
            layout.addLayout(form)

        elif key == "zuschneiden":
            layout.addWidget(QLabel("Form im Bild aufziehen:"))

            _shape_style = (
                "QPushButton {{ background: {bg}; color: white; border-radius: 6px;"
                "  padding: 7px 12px; font-size: 13px; }}"
                "QPushButton:hover {{ background: {hover}; }}"
            )
            _inactive = _shape_style.format(bg="#3498db", hover="#2980b9")
            _active   = _shape_style.format(bg="#27ae60", hover="#1e8449")

            self._btn_circle  = QPushButton("⬤ Kreis")
            self._btn_ellipse = QPushButton("⬭ Ellipse")
            self._btn_rect    = QPushButton("▬ Rechteck")
            for b in (self._btn_circle, self._btn_ellipse, self._btn_rect):
                b.setCheckable(True)
                b.setStyleSheet(_inactive)
            self._btn_rect.setChecked(True)
            self._btn_rect.setStyleSheet(_active)

            self._btn_freehand = QPushButton("✏ Freihand")
            self._btn_freehand.setCheckable(True)
            self._btn_freehand.setStyleSheet(_inactive)

            # Seitenverhältnis sperren (nur für Rechteck und Ellipse)
            self._aspect_lock_btn = QPushButton("Seitenverhältnis sperren: AUS")
            self._aspect_lock_btn.setCheckable(True)
            self._aspect_lock_btn.toggled.connect(self._on_aspect_lock_toggled)
            self._aspect_lock_btn.setStyleSheet(
                "QPushButton { background: #ecf0f1; color: #333; border-radius: 6px;"
                "  padding: 7px 12px; font-size: 12px; }"
                "QPushButton:checked { background: #27ae60; color: white; }"
                "QPushButton:hover { background: #bdc3c7; }"
            )

            # Freihand-Offset-Widget (nur sichtbar wenn Freihand-Form gezeichnet)
            self._freehand_offset_w = QWidget()
            fh_layout = QVBoxLayout(self._freehand_offset_w)
            fh_layout.setContentsMargins(0, 0, 0, 0)
            fh_layout.setSpacing(4)
            fh_lbl = QLabel("Kontur-Offset (+ = nach außen, − = nach innen):")
            fh_lbl.setStyleSheet("font-size: 11px; color: #555;")
            fh_lbl.setWordWrap(True)
            fh_layout.addWidget(fh_lbl)
            fh_form = QFormLayout()
            self._freehand_offset_slider = _labeled_slider(fh_form, "Pixel:", -200, 200, 0)
            self._freehand_offset_slider.valueChanged.connect(
                lambda v: self._canvas.set_freehand_offset(float(v)))
            fh_layout.addLayout(fh_form)
            self._freehand_offset_w.setVisible(False)

            all_btns = (self._btn_circle, self._btn_ellipse, self._btn_rect, self._btn_freehand)

            def _set_shape(shape, btn):
                for b in all_btns:
                    b.setChecked(b is btn)
                    b.setStyleSheet(_active if b is btn else _inactive)
                self._canvas.set_crop_shape(shape)
                is_freehand = (shape == ImageCanvas.SHAPE_FREEHAND)
                is_circle   = (shape == ImageCanvas.SHAPE_CIRCLE)
                self._aspect_lock_btn.setVisible(not is_freehand and not is_circle)
                self._freehand_offset_w.setVisible(False)  # erst nach Zeichnen sichtbar
                if is_circle or is_freehand:
                    self._aspect_lock_btn.setChecked(False)
                hints = {ImageCanvas.SHAPE_CIRCLE:   "Quadrat aufziehen → Kreis",
                         ImageCanvas.SHAPE_ELLIPSE:  "Ellipse im Bild aufziehen",
                         ImageCanvas.SHAPE_RECT:     "Rechteck im Bild aufziehen",
                         ImageCanvas.SHAPE_FREEHAND: "Umriss mit der Maus umfahren"}
                self._hint_label.setText(hints[shape])
                self._crop_apply_btn.setEnabled(False)

            self._btn_circle.clicked.connect(  lambda: _set_shape(ImageCanvas.SHAPE_CIRCLE,   self._btn_circle))
            self._btn_ellipse.clicked.connect( lambda: _set_shape(ImageCanvas.SHAPE_ELLIPSE,  self._btn_ellipse))
            self._btn_rect.clicked.connect(    lambda: _set_shape(ImageCanvas.SHAPE_RECT,     self._btn_rect))
            self._btn_freehand.clicked.connect(lambda: _set_shape(ImageCanvas.SHAPE_FREEHAND, self._btn_freehand))

            layout.addWidget(self._btn_circle)
            layout.addWidget(self._btn_ellipse)
            layout.addWidget(self._btn_rect)
            layout.addWidget(self._btn_freehand)
            layout.addWidget(self._aspect_lock_btn)
            layout.addWidget(self._freehand_offset_w)

            sep_apply = QFrame(); sep_apply.setFrameShape(QFrame.Shape.HLine)
            sep_apply.setStyleSheet("color: #ccc;"); layout.addWidget(sep_apply)
            self._crop_apply_btn = QPushButton("✂ Anwenden")
            self._crop_apply_btn.setEnabled(False)
            self._crop_apply_btn.setStyleSheet(
                "QPushButton { background: #27ae60; color: white; border-radius: 6px;"
                "  padding: 7px 12px; font-size: 13px; }"
                "QPushButton:hover:enabled { background: #1e8449; }"
                "QPushButton:disabled { background: #ccc; color: #888; }"
            )
            self._crop_apply_btn.clicked.connect(self._apply_pending_crop)
            layout.addWidget(self._crop_apply_btn)

            sep_reset = QFrame(); sep_reset.setFrameShape(QFrame.Shape.HLine)
            sep_reset.setStyleSheet("color: #ccc;"); layout.addWidget(sep_reset)
            crop_reset_btn = QPushButton("↺ Letzten Zuschnitt zurücksetzen")
            crop_reset_btn.setStyleSheet(
                "QPushButton { background: #e74c3c; color: white; border-radius: 6px;"
                "  padding: 7px 12px; font-size: 13px; }"
                "QPushButton:hover { background: #c0392b; }"
            )
            crop_reset_btn.clicked.connect(self._reset_crop)
            layout.addWidget(crop_reset_btn)

        elif key == "freistellen":
            # ── KI-Automatik ───────────────────────────────────────────────
            lbl_ki = QLabel("KI-Vollautomatik (empfohlen):")
            lbl_ki.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(lbl_ki)
            self._rembg_btn = self._btn("KI: Hintergrund automatisch entfernen", self._remove_background)
            layout.addWidget(self._rembg_btn)
            self._rembg_error_lbl = QLabel("")
            self._rembg_error_lbl.setWordWrap(True)
            self._rembg_error_lbl.setStyleSheet("font-size: 11px; color: #e74c3c;")
            layout.addWidget(self._rembg_error_lbl)

            sep_lasso = QFrame(); sep_lasso.setFrameShape(QFrame.Shape.HLine)
            sep_lasso.setStyleSheet("color: #ccc;"); layout.addWidget(sep_lasso)

            # ── Lasso ──────────────────────────────────────────────────────
            lbl_lasso = QLabel("Manuell: Lasso")
            lbl_lasso.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(lbl_lasso)
            lbl = QLabel(
                "Objekt mit gedrückter Maustaste umfahren.\n"
                "Alles innerhalb der Linie bleibt erhalten,\n"
                "alles außerhalb wird transparent."
            )
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-size: 12px; color: #555;")
            layout.addWidget(lbl)

            sep_brush = QFrame(); sep_brush.setFrameShape(QFrame.Shape.HLine)
            sep_brush.setStyleSheet("color: #ccc;"); layout.addWidget(sep_brush)

            # ── Pinsel-Nachbearbeitung ──────────────────────────────────────
            lbl_brush = QLabel("Pinsel-Nachbearbeitung:")
            lbl_brush.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(lbl_brush)
            lbl_brush_hint = QLabel(
                "Nach dem Freistellen Bereiche\n"
                "entfernen oder zurückbringen:"
            )
            lbl_brush_hint.setStyleSheet("font-size: 11px; color: #777;")
            layout.addWidget(lbl_brush_hint)

            form_brush = QFormLayout()
            self._brush_size_slider = _labeled_slider(
                form_brush, "Pinselgröße:", 5, 150, 20,
                lambda v: self._canvas.set_paint_brush_size(v)
            )
            layout.addLayout(form_brush)

            _brush_style_on  = ("QPushButton { background: %s; color: white; border-radius: 6px;"
                                "  padding: 7px 12px; font-size: 13px; }"
                                "QPushButton:hover { background: %s; }")
            _brush_style_off = ("QPushButton { background: #ecf0f1; color: #333; border-radius: 6px;"
                                "  padding: 7px 12px; font-size: 13px; }"
                                "QPushButton:hover { background: #bdc3c7; }")

            self._erase_brush_btn   = QPushButton("✕  Entfernen")
            self._restore_brush_btn = QPushButton("↩  Zurückholen")
            for b in (self._erase_brush_btn, self._restore_brush_btn):
                b.setCheckable(True)
                b.setStyleSheet(_brush_style_off)

            self._erase_brush_btn.toggled.connect(
                lambda c: self._on_brush_mode_toggled("erase", c,
                    _brush_style_on % ("#e74c3c", "#c0392b"), _brush_style_off)
            )
            self._restore_brush_btn.toggled.connect(
                lambda c: self._on_brush_mode_toggled("restore", c,
                    _brush_style_on % ("#27ae60", "#1e8449"), _brush_style_off)
            )
            layout.addWidget(self._erase_brush_btn)
            layout.addWidget(self._restore_brush_btn)

        elif key == "hintergrund":
            # ── Schritt 1: Maske festlegen ────────────────────────────────
            lbl_s1 = QLabel("Schritt 1 — Hintergrund festlegen:")
            lbl_s1.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(lbl_s1)
            lbl_s1_hint = QLabel(
                "Entweder den Hintergrund automatisch per KI\n"
                "erkennen, oder ein bestimmtes Objekt markieren."
            )
            lbl_s1_hint.setWordWrap(True)
            lbl_s1_hint.setStyleSheet("font-size: 11px; color: #555;")
            layout.addWidget(lbl_s1_hint)

            self._hg_detect_btn = self._btn("KI: Hintergrund erkennen", self._hg_detect)
            layout.addWidget(self._hg_detect_btn)

            _fstyle_off = ("QPushButton { background: #ecf0f1; color: #333; border-radius: 6px;"
                           "  padding: 7px 12px; font-size: 13px; }"
                           "QPushButton:hover { background: #bdc3c7; }")
            _fstyle_on  = ("QPushButton { background: #e67e22; color: #fff; border-radius: 6px;"
                           "  padding: 7px 12px; font-size: 13px; }"
                           "QPushButton:hover { background: #d35400; }")
            self._focus_mark_btn = QPushButton("Objekt im Bild markieren")
            self._focus_mark_btn.setCheckable(True)
            self._focus_mark_btn.setStyleSheet(_fstyle_off)
            self._focus_mark_btn.toggled.connect(
                lambda on: (
                    self._focus_mark_btn.setStyleSheet(_fstyle_on if on else _fstyle_off),
                    self._on_focus_mark_toggled(on),
                )
            )
            layout.addWidget(self._focus_mark_btn)

            self._hg_status_lbl = QLabel("")
            self._hg_status_lbl.setWordWrap(True)
            self._hg_status_lbl.setStyleSheet("font-size: 11px; color: #777;")
            layout.addWidget(self._hg_status_lbl)
            # Alias damit bestehende Methoden (_on_person_mask_ready etc.) weiter funktionieren
            self._focus_status_lbl = self._hg_status_lbl

            sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.HLine)
            sep1.setStyleSheet("color: #ccc;"); layout.addWidget(sep1)

            # ── Schritt 2: Hintergrund bearbeiten ─────────────────────────
            lbl_s2 = QLabel("Schritt 2 — Hintergrund bearbeiten:")
            lbl_s2.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(lbl_s2)

            form_hg = QFormLayout()
            self._hg_brightness_slider = _labeled_slider(
                form_hg, "Helligkeit:", -100, 100, 0, lambda _: self._hg_or_focus_preview()
            )
            self._hg_blur_slider = _labeled_slider(
                form_hg, "Unschärfe:", 0, 100, 10, lambda _: self._hg_or_focus_preview()
            )
            self._hg_dof_slider = _labeled_slider(
                form_hg, "Schärfentiefe:", 0, 100, 0, lambda _: self._hg_or_focus_preview()
            )
            layout.addLayout(form_hg)

        elif key == "entzerren":
            # ── Objektivverzerrung ────────────────────────────────────────
            lbl_lens = QLabel("Objektivverzerrung korrigieren:")
            lbl_lens.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(lbl_lens)
            form_lens = QFormLayout()
            self._distortion_slider   = _labeled_slider(form_lens, "Stärke:",        -100, 100, 0)
            self._distortion_cx_slider = _labeled_slider(form_lens, "Mittelpunkt X:", -100, 100, 0)
            self._distortion_cy_slider = _labeled_slider(form_lens, "Mittelpunkt Y:", -100, 100, 0)
            layout.addLayout(form_lens)
            self._distortion_slider.sliderPressed.connect(self._dist_press)
            self._distortion_slider.valueChanged.connect(lambda _: self._ent_preview(self._compute_distortion_img))
            self._distortion_slider.sliderReleased.connect(lambda: self._ent_commit(self._compute_distortion_img, [self._distortion_slider, self._distortion_cx_slider, self._distortion_cy_slider]))
            for _s in (self._distortion_cx_slider, self._distortion_cy_slider):
                _s.sliderPressed.connect(self._dist_press)
                _s.valueChanged.connect(lambda _: self._ent_preview(self._compute_distortion_img))
                _s.sliderReleased.connect(lambda: self._ent_commit(self._compute_distortion_img, [self._distortion_slider, self._distortion_cx_slider, self._distortion_cy_slider]))
            # "Mittelpunkt per Klick setzen"-Button
            self._dist_pick_btn = QPushButton("+ Mittelpunkt im Bild anklicken")
            self._dist_pick_btn.setCheckable(True)
            _style_pick_off = ("QPushButton { background: #ecf0f1; color: #333; border-radius: 6px;"
                               "  padding: 6px 10px; font-size: 12px; }"
                               "QPushButton:hover { background: #bdc3c7; }")
            _style_pick_on  = ("QPushButton { background: #e67e22; color: #fff; border-radius: 6px;"
                               "  padding: 6px 10px; font-size: 12px; }"
                               "QPushButton:hover { background: #d35400; }")
            self._dist_pick_btn.setStyleSheet(_style_pick_off)
            self._dist_pick_btn.toggled.connect(
                lambda on: (
                    self._dist_pick_btn.setStyleSheet(_style_pick_on if on else _style_pick_off),
                    self._canvas.set_mode(ImageCanvas.MODE_PICK_POINT if on else ImageCanvas.MODE_NONE)
                )
            )
            layout.addWidget(self._dist_pick_btn)
            self._dist_perf_chk = QCheckBox("Schnellvorschau beim Ziehen (reduzierte Auflösung)")
            self._dist_perf_chk.setChecked(True)
            self._dist_perf_chk.setStyleSheet("font-size: 11px; color: #555;")
            layout.addWidget(self._dist_perf_chk)
            # Reset-Button für Objektivverzerrung: stellt Zustand vor erster Verzerrung wieder her
            _dist_reset_btn = self._distortion_slider.parent().findChild(QPushButton)
            if _dist_reset_btn:
                _dist_reset_btn.clicked.connect(self._reset_distortion)

            sep_persp = QFrame(); sep_persp.setFrameShape(QFrame.Shape.HLine)
            sep_persp.setStyleSheet("color: #ccc;"); layout.addWidget(sep_persp)

            # ── Perspektiv-Korrektur ──────────────────────────────────────
            lbl_persp = QLabel("3D-Perspektive (stürzende Linien):")
            lbl_persp.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(lbl_persp)
            lbl_persp_hint = QLabel(
                "Dreht das Bild in 3D um die Mittelachse —\n"
                "wie ein Blick von oben/unten oder links/rechts."
            )
            lbl_persp_hint.setStyleSheet("font-size: 11px; color: #777;")
            layout.addWidget(lbl_persp_hint)
            form_persp = QFormLayout()
            self._persp_v_slider = _labeled_slider(form_persp, "Oben / Unten:", -100, 100, 0)
            self._persp_h_slider = _labeled_slider(form_persp, "Links / Rechts:", -100, 100, 0)
            layout.addLayout(form_persp)
            for _s in (self._persp_v_slider, self._persp_h_slider):
                _s.sliderPressed.connect(self._ent_press)
                _s.valueChanged.connect(lambda _: self._ent_preview(self._compute_perspective_img))
                _s.sliderReleased.connect(lambda: self._ent_commit(self._compute_perspective_img, [self._persp_v_slider, self._persp_h_slider]))

            sep_quad = QFrame(); sep_quad.setFrameShape(QFrame.Shape.HLine)
            sep_quad.setStyleSheet("color: #ccc;"); layout.addWidget(sep_quad)

            # ── 4-Punkt-Entzerrung ────────────────────────────────────────
            lbl_quad = QLabel("4-Punkt-Entzerrung (Dokument):")
            lbl_quad.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(lbl_quad)
            lbl_quad_hint = QLabel("4 Ecken anklicken, dann Anwenden")
            lbl_quad_hint.setStyleSheet("font-size: 11px; color: #777;")
            layout.addWidget(lbl_quad_hint)

            quad_btn_row = QHBoxLayout()
            quad_set_btn = QPushButton("4 Punkte setzen")
            quad_set_btn.setStyleSheet(
                "QPushButton { background: #3498db; color: white; border-radius: 6px;"
                "  padding: 7px 12px; font-size: 13px; }"
                "QPushButton:hover { background: #2980b9; }"
            )
            quad_reset_btn = QPushButton("Zurücksetzen")
            quad_reset_btn.setStyleSheet(
                "QPushButton { background: #e74c3c; color: white; border-radius: 6px;"
                "  padding: 7px 12px; font-size: 13px; }"
                "QPushButton:hover { background: #c0392b; }"
            )
            quad_btn_row.addWidget(quad_set_btn)
            quad_btn_row.addWidget(quad_reset_btn)
            quad_btn_w = QWidget(); quad_btn_w.setLayout(quad_btn_row)
            layout.addWidget(quad_btn_w)

            self._quad_count_lbl = QLabel("0 / 4 Punkte")
            self._quad_count_lbl.setStyleSheet("font-size: 12px; color: #555;")
            layout.addWidget(self._quad_count_lbl)

            self._quad_apply_btn = QPushButton("Anwenden")
            self._quad_apply_btn.setEnabled(False)
            self._quad_apply_btn.setStyleSheet(
                "QPushButton { background: #27ae60; color: white; border-radius: 6px;"
                "  padding: 7px 12px; font-size: 13px; }"
                "QPushButton:hover:enabled { background: #1e8449; }"
                "QPushButton:disabled { background: #ccc; color: #888; }"
            )
            self._quad_apply_btn.clicked.connect(self._apply_quad_warp)
            layout.addWidget(self._quad_apply_btn)

            quad_set_btn.clicked.connect(self._start_quad_mode)
            quad_reset_btn.clicked.connect(self._reset_quad_points)

            sep_horiz = QFrame(); sep_horiz.setFrameShape(QFrame.Shape.HLine)
            sep_horiz.setStyleSheet("color: #ccc;"); layout.addWidget(sep_horiz)

            # ── Horizont ausrichten ───────────────────────────────────────
            lbl_horiz = QLabel("Horizont automatisch ausrichten:")
            lbl_horiz.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(lbl_horiz)
            layout.addWidget(self._btn("Automatisch ausrichten", self._apply_auto_horizon))
            self._horizon_status_lbl = QLabel("")
            self._horizon_status_lbl.setStyleSheet("font-size: 11px; color: #777;")
            layout.addWidget(self._horizon_status_lbl)

            sep_chrom = QFrame(); sep_chrom.setFrameShape(QFrame.Shape.HLine)
            sep_chrom.setStyleSheet("color: #ccc;"); layout.addWidget(sep_chrom)

            # ── Chromatische Aberration ───────────────────────────────────
            lbl_chrom = QLabel("Chromatische Aberration korrigieren:")
            lbl_chrom.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(lbl_chrom)
            form_chrom = QFormLayout()
            self._chrom_slider = _labeled_slider(form_chrom, "Stärke:", -50, 50, 0)
            layout.addLayout(form_chrom)
            self._chrom_slider.sliderPressed.connect(self._ent_press)
            self._chrom_slider.valueChanged.connect(lambda _: self._ent_preview(self._compute_chrom_img))
            self._chrom_slider.sliderReleased.connect(lambda: self._ent_commit(self._compute_chrom_img, [self._chrom_slider]))

            sep_vign = QFrame(); sep_vign.setFrameShape(QFrame.Shape.HLine)
            sep_vign.setStyleSheet("color: #ccc;"); layout.addWidget(sep_vign)

            # ── Vignettierung entfernen ───────────────────────────────────
            lbl_vign = QLabel("Vignettierung entfernen:")
            lbl_vign.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(lbl_vign)
            form_vign = QFormLayout()
            self._vignette_slider = _labeled_slider(form_vign, "Stärke:", 0, 100, 0)
            layout.addLayout(form_vign)
            self._vignette_slider.sliderPressed.connect(self._ent_press)
            self._vignette_slider.valueChanged.connect(lambda _: self._ent_preview(self._compute_vignette_img))
            self._vignette_slider.sliderReleased.connect(lambda: self._ent_commit(self._compute_vignette_img, [self._vignette_slider]))

        elif key == "anpassen":
            self._adj_histogram = HistogramWidget()
            layout.addWidget(self._adj_histogram)
            layout.addWidget(QLabel("Im Bild ← → ziehen, um den zuletzt\nberührten Regler anzupassen:"))
            form = QFormLayout()
            self._adjust_sliders: dict[str, QSlider] = {}

            def make(lbl, min_, max_, val, name):
                s = _labeled_slider(form, lbl, min_, max_, val)
                s.sliderPressed.connect(lambda n=name: self._set_active_slider(n))
                s.valueChanged.connect(lambda _: self._adj_preview())
                self._adjust_sliders[name] = s
                return s

            self._brightness_slider  = make("Helligkeit:",        1,    200,  100, "brightness")
            self._contrast_slider    = make("Kontrast:",           1,    200,  100, "contrast")
            self._saturation_slider  = make("Sättigung:",          0,    200,  100, "saturation")
            self._sharpness_slider   = make("Schärfe:",            0,    200,  100, "sharpness")
            self._shadows_slider     = make("Schatten aufhellen:", -100, 100,    0, "shadows")
            self._hue_slider         = make("Farbton:",           -180,  180,    0, "hue")
            self._warmth_slider      = make("Wärme:",             -100,  100,    0, "warmth")
            self._exposure_slider    = make("Belichtung:",        -200,  200,    0, "exposure")
            self._blackpoint_slider  = make("Schwarzpunkt:",         0,  100,    0, "blackpoint")

            self._active_adjust_name = "brightness"
            self._active_adjust_slider = self._brightness_slider

            layout.addLayout(form)

            # KI-Optimierung row with ↺ reset button
            ki_row = QHBoxLayout()
            ki_row.setContentsMargins(0, 0, 0, 0)
            ki_row.setSpacing(4)
            ki_btn = self._btn("KI-Optimierung", self._ai_optimize)
            ki_undo_btn = QPushButton("↺")
            ki_undo_btn.setFixedSize(26, 26)
            ki_undo_btn.setToolTip("KI-Optimierung rückgängig")
            ki_undo_btn.clicked.connect(self._undo)
            ki_row.addWidget(ki_btn)
            ki_row.addWidget(ki_undo_btn)
            layout.addLayout(ki_row)

            # Reset all sliders button
            reset_all_btn = QPushButton("↺ Alle zurücksetzen")
            reset_all_btn.clicked.connect(self._adj_reset_all)
            layout.addWidget(reset_all_btn)

        elif key == "bildgroesse":
            # ── Auflösung ──────────────────────────────────────────────────
            lbl_res = QLabel("Auflösung ändern:")
            lbl_res.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(lbl_res)

            # Breite / Höhe + Seitenverhältnis-Sperre
            form = QFormLayout()
            self._width_spin  = QSpinBox(); self._width_spin.setRange(1, 20000)
            self._height_spin = QSpinBox(); self._height_spin.setRange(1, 20000)

            wh_row = QHBoxLayout()
            self._aspect_lock_chk = QPushButton("🔒 Seitenverhältnis sperren")
            self._aspect_lock_chk.setCheckable(True)
            self._aspect_lock_chk.setChecked(True)
            self._aspect_lock_chk.setStyleSheet(
                "QPushButton { background: #27ae60; color: white; border-radius: 6px;"
                "  padding: 4px 8px; font-size: 12px; }"
                "QPushButton:!checked { background: #95a5a6; }"
                "QPushButton:hover { opacity: 0.85; }"
            )
            layout.addWidget(self._aspect_lock_chk)

            form.addRow("Breite (px):", self._width_spin)
            form.addRow("Höhe (px):",  self._height_spin)

            # Prozent-Skalierung
            pct_row = QHBoxLayout()
            pct_row.setSpacing(4)
            self._pct_spin = QSpinBox()
            self._pct_spin.setRange(1, 400)
            self._pct_spin.setValue(100)
            self._pct_spin.setSuffix(" %")
            pct_apply_btn = QPushButton("Anwenden")
            pct_apply_btn.setFixedHeight(26)
            pct_apply_btn.setStyleSheet(
                "QPushButton { background: #3498db; color: white; border-radius: 4px;"
                "  font-size: 12px; padding: 2px 8px; }"
                "QPushButton:hover { background: #2980b9; }"
            )
            pct_apply_btn.clicked.connect(self._apply_pct_to_spins)
            pct_row.addWidget(self._pct_spin)
            pct_row.addWidget(pct_apply_btn)
            form.addRow("Prozent:", wh_row)
            layout.addLayout(form)
            layout.addLayout(pct_row)

            # Verbindungen für Seitenverhältnis
            self._width_spin.valueChanged.connect(self._on_width_changed)
            self._height_spin.valueChanged.connect(self._on_height_changed)
            self._resize_aspect_locked = True
            self._resize_aspect_ratio  = 1.0

            # Voreinstellungen
            lbl_pre = QLabel("Schnellauswahl:")
            lbl_pre.setStyleSheet("font-size: 11px; color: #777; margin-top:4px;")
            layout.addWidget(lbl_pre)
            _pre_style = ("QPushButton { background: #ecf0f1; color: #333; border-radius: 5px;"
                          "  padding: 4px 6px; font-size: 11px; }"
                          "QPushButton:hover { background: #bdc3c7; }")
            presets_row1 = QHBoxLayout(); presets_row1.setSpacing(4)
            presets_row2 = QHBoxLayout(); presets_row2.setSpacing(4)
            for label, pw in [("Web 1920", 1920), ("HD 1280", 1280), ("Instagram 1080", 1080)]:
                b = QPushButton(label); b.setStyleSheet(_pre_style)
                b.clicked.connect(lambda _, w=pw: self._apply_preset_width(w))
                presets_row1.addWidget(b)
            for label, pw in [("Thumbnail 800", 800), ("Halbieren", -1), ("Verdoppeln", -2)]:
                b = QPushButton(label); b.setStyleSheet(_pre_style)
                b.clicked.connect(lambda _, w=pw: self._apply_preset_width(w))
                presets_row2.addWidget(b)
            layout.addLayout(presets_row1)
            layout.addLayout(presets_row2)

            # Interpolation + Dateigröße-Vorschau
            form3 = QFormLayout()
            self._interp_combo = QComboBox()
            self._interp_combo.addItems(["LANCZOS (beste Qualität)", "BILINEAR", "NEAREST"])
            form3.addRow("Interpolation:", self._interp_combo)
            layout.addLayout(form3)
            self._resize_size_lbl = QLabel("")
            self._resize_size_lbl.setStyleSheet("font-size: 11px; color: #95a5a6;")
            layout.addWidget(self._resize_size_lbl)
            self._width_spin.valueChanged.connect(self._update_resize_size_preview)
            self._height_spin.valueChanged.connect(self._update_resize_size_preview)
            layout.addWidget(self._btn("Größe ändern", self._apply_resize))

            sep_mid = QFrame(); sep_mid.setFrameShape(QFrame.Shape.HLine)
            sep_mid.setStyleSheet("color: #ccc;"); layout.addWidget(sep_mid)

            # ── Komprimierung ──────────────────────────────────────────────
            lbl_comp = QLabel("Komprimierung:")
            lbl_comp.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(lbl_comp)

            form4 = QFormLayout()
            self._compress_fmt_combo = QComboBox()
            self._compress_fmt_combo.addItems(["JPEG", "WebP", "PNG"])
            self._compress_fmt_combo.currentIndexChanged.connect(self._on_compress_fmt_changed)
            form4.addRow("Format:", self._compress_fmt_combo)
            layout.addLayout(form4)

            form5 = QFormLayout()
            self._quality_slider = _labeled_slider(form5, "Qualität:", 1, 100, 85,
                                                   on_change=self._update_compress_preview)
            layout.addLayout(form5)

            self._quality_range_lbl = QLabel("")
            self._quality_range_lbl.setStyleSheet("font-size: 11px; color: #e67e22;")
            layout.addWidget(self._quality_range_lbl)

            self._compress_size_lbl = QLabel("")
            self._compress_size_lbl.setStyleSheet("font-size: 11px; color: #95a5a6;")
            layout.addWidget(self._compress_size_lbl)

            # Speichermodus
            self._overwrite_btn   = QPushButton("Überschreiben")
            self._new_file_btn    = QPushButton("Neue Datei")
            for b in (self._overwrite_btn, self._new_file_btn):
                b.setCheckable(True)
                b.setStyleSheet(
                    "QPushButton { background: #ecf0f1; color: #333; border-radius: 6px;"
                    "  padding: 5px 10px; font-size: 12px; }"
                    "QPushButton:checked { background: #3498db; color: white; }"
                    "QPushButton:hover { background: #bdc3c7; }"
                )
            self._new_file_btn.setChecked(True)
            self._overwrite_btn.clicked.connect(lambda: (
                self._overwrite_btn.setChecked(True), self._new_file_btn.setChecked(False)))
            self._new_file_btn.clicked.connect(lambda: (
                self._new_file_btn.setChecked(True), self._overwrite_btn.setChecked(False)))
            mode_row = QHBoxLayout(); mode_row.setSpacing(4)
            mode_row.addWidget(self._overwrite_btn)
            mode_row.addWidget(self._new_file_btn)
            layout.addLayout(mode_row)

            layout.addWidget(self._btn("Komprimiert speichern", self._save_compressed))
            self._update_compress_preview()

        elif key == "bildinfos":
            hist_lbl = QLabel("Histogramm (RGB):")
            hist_lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #ffffff;")
            layout.addWidget(hist_lbl)
            self._histogram = HistogramWidget()
            layout.addWidget(self._histogram)

            self._info_label = QLabel("Keine Informationen verfügbar")
            self._info_label.setWordWrap(True)
            self._info_label.setStyleSheet("font-size: 12px; color: #f0f0f0;")
            layout.addWidget(self._info_label)

            self._map_lbl = QLabel()
            self._map_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._map_lbl.setVisible(False)
            layout.addWidget(self._map_lbl)

            self._map_loading_lbl = QLabel("🗺 Karte wird geladen …")
            self._map_loading_lbl.setStyleSheet("font-size: 11px; color: #aaa;")
            self._map_loading_lbl.setVisible(False)
            layout.addWidget(self._map_loading_lbl)

        layout.addStretch()

        sep_orig = QFrame()
        sep_orig.setFrameShape(QFrame.Shape.HLine)
        sep_orig.setStyleSheet("color: #ccc;")
        layout.addWidget(sep_orig)
        restore_btn = QPushButton("⟲ Zurück zum Original")
        restore_btn.clicked.connect(self._restore_original)
        restore_btn.setStyleSheet(
            "QPushButton { background: #e67e22; color: white; border-radius: 6px;"
            "  padding: 7px 12px; font-size: 13px; }"
            "QPushButton:hover { background: #ca6f1e; }"
        )
        layout.addWidget(restore_btn)
        return scroll

    # ── Hilfs-Button ──────────────────────────────────────────────────────────

    @staticmethod
    def _btn(text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.clicked.connect(slot)
        b.setStyleSheet(
            "QPushButton { background: #3498db; color: white; border-radius: 6px;"
            "  padding: 7px 12px; font-size: 13px; }"
            "QPushButton:hover { background: #2980b9; }"
        )
        return b

    # ── Hilfsmethoden ────────────────────────────────────────────────────────

    @staticmethod
    def _restore_alpha(result: Image.Image, source: Image.Image) -> Image.Image:
        """Überträgt den Alpha-Kanal von source auf result, falls source RGBA ist."""
        if source.mode == "RGBA":
            result = result.convert("RGBA")
            result.putalpha(source.split()[3])
        return result

    # ── History / Speichern ───────────────────────────────────────────────────

    def _push_history(self, keep_fine_base=False):
        """Aktuellen Zustand auf den Undo-Stack legen (max. 30 Schritte).
        Ausstehende Hintergrund-/Fokus-Vorschauen werden vorher automatisch committed,
        damit Undo sauber Schritt für Schritt rückwärts geht."""
        if self._original is None:
            return
        # Ausstehende Preview zuerst in die History einbacken (eigener History-Eintrag)
        if self._has_active_preview():
            if len(self._history) >= 30:
                self._history.pop(0)
            self._history.append(self._original.copy())   # Stand VOR dem Preview
            # Preview auf _original anwenden
            if self._focus_base is not None and self._focus_mask is not None:
                from PIL import ImageFilter
                self._original = self._person_focus_compute()
            elif self._hg_base is not None and self._hg_mask is not None:
                self._original = self._hg_compute()
            self._hg_mask = None
            self._hg_base = None
            self._focus_mask = None
            self._focus_base = None
        if len(self._history) >= 30:
            self._history.pop(0)
        self._history.append(self._original.copy())
        if not keep_fine_base:
            self._fine_rotate_base = None
            self._fine_rotate_total = 0.0
        self._update_action_buttons()

    def _has_active_preview(self) -> bool:
        return (self._hg_mask is not None or self._hg_base is not None
                or self._focus_mask is not None or self._focus_base is not None)

    def _show_original(self):
        """Zeigt self._original auf dem Canvas und aktualisiert das Histogramm."""
        self._canvas.set_image(self._original)
        self._histogram.set_image(self._original)

    def _update_action_buttons(self):
        has_history = len(self._history) > 0
        self._undo_btn.setEnabled(has_history or self._has_active_preview())
        self._save_btn.setEnabled(self._original is not None)
        self._restore_btn.setEnabled(self._original_at_load is not None)

    def _undo(self):
        # Aktive Hintergrund-/Fokus-Vorschau abbrechen
        if self._has_active_preview():
            self._focus_mask = None
            self._focus_base = None
            self._hg_mask = None
            self._hg_base = None
            self._show_original()
            if hasattr(self, "_hg_status_lbl"):
                self._hg_status_lbl.setText("")
            self._update_action_buttons()
            return
        if not self._history:
            return
        self._original = self._history.pop()
        self._show_original()
        self._sync_bildgroesse_ui()
        self._update_action_buttons()

    def _save_file(self):
        if not self._original or not self._file_path:
            return
        # Ausstehende Hintergrund-/Fokus-Vorschau automatisch übernehmen
        if self._focus_base is not None and self._focus_mask is not None:
            self._person_focus_apply()
        elif self._hg_base is not None and self._hg_mask is not None:
            self._hg_apply()
        old_path = str(self._file_path)
        ext = self._file_path.suffix.lower()
        img = self._original
        if img.mode == "RGBA":
            # Kreis/Ellipse haben Transparenz → als PNG speichern
            save_path = self._file_path.with_suffix(".png")
            img.save(str(save_path), "PNG")
            # Alte Datei löschen wenn Extension gewechselt hat (z.B. .jpg → .png)
            if save_path != self._file_path and self._file_path.exists():
                try:
                    self._file_path.unlink()
                except Exception:
                    pass
            self._file_path = save_path
            self._file_label.setText(save_path.name)
        elif ext in (".jpg", ".jpeg"):
            img.convert("RGB").save(str(self._file_path), "JPEG", quality=95, optimize=True)
        elif ext == ".png":
            img.save(str(self._file_path), "PNG")
        else:
            img.convert("RGB").save(str(self._file_path), "JPEG", quality=95)
        self._history.clear()
        self._update_action_buttons()
        self._show_original()
        self.image_saved.emit(old_path, str(self._file_path))

    def _restore_original(self):
        if self._original_at_load is None or self._original_file_path is None:
            return
        import shutil
        old_path = str(self._file_path)

        # Backup zurückkopieren — Ziel: gleicher Ordner, aber Name/Extension des Originals
        if self._backup_path is not None and self._backup_path.exists():
            restored_path = self._original_file_path.parent / self._backup_path.name
            try:
                shutil.copy2(str(self._backup_path), str(restored_path))
                # Alte Datei löschen wenn Extension gewechselt hat (z.B. .png → .jpg)
                if str(restored_path) != old_path:
                    old_p = Path(old_path)
                    if old_p.exists():
                        try:
                            old_p.unlink()
                        except Exception:
                            pass
            except Exception:
                restored_path = self._original_file_path
        else:
            restored_path = self._original_file_path

        # Dateipfad auf wiederhergestellten Pfad setzen
        self._file_path = restored_path
        self._original_file_path = restored_path
        self._file_label.setText(restored_path.name)

        # In-Memory-Bild zurücksetzen
        self._history.clear()
        self._fine_rotate_base = None
        self._fine_rotate_total = 0.0
        self._original = self._original_at_load.copy()

        self._show_original()
        self._sync_bildgroesse_ui(reset_controls=True)
        self._hint_label.setText("")
        self._update_action_buttons()
        self.image_saved.emit(old_path, str(self._file_path))

    # ── Laden / Anzeige ───────────────────────────────────────────────────────

    def load_file(self, file_path: str):
        self._file_path = Path(file_path)
        self._file_label.setText(self._file_path.name)

        for action in self._group_actions.values():
            action.setChecked(False)
        self._sidebar.setFixedWidth(0)
        self._canvas.set_mode(ImageCanvas.MODE_NONE)
        self._hint_label.setText("")

        try:
            self._original = load_image(self._file_path)
        except Exception as e:
            return

        # Backup anlegen (beim allerersten Laden dieses Bildes)
        import shutil
        # Backup per Stem suchen (unabhängig von Extension, z. B. cat.jpg ↔ cat.png)
        backup_dir = self._file_path.parent / ".optimizer_originals"
        stem = self._file_path.stem
        self._backup_path = None
        if backup_dir.exists():
            for candidate in sorted(backup_dir.iterdir()):
                if candidate.stem == stem and candidate.is_file():
                    self._backup_path = candidate
                    break
        if self._backup_path is None:
            try:
                backup_dir.mkdir(exist_ok=True)
                self._backup_path = backup_dir / self._file_path.name
                shutil.copy2(str(self._file_path), str(self._backup_path))
            except Exception:
                self._backup_path = None
        try:
            self._original_at_load = load_image(self._backup_path) if self._backup_path else self._original.copy()
        except Exception:
            self._original_at_load = self._original.copy()
        self._original_file_path = self._file_path
        self._history.clear()
        self._fine_rotate_base = None
        self._fine_rotate_total = 0.0
        self._adj_base = None
        self._ent_base = None
        self._distortion_base = None
        self._quad_base = None
        self._crop_base = None
        self._hg_mask = None
        self._hg_base = None
        self._focus_mask = None
        self._focus_base = None
        self._update_action_buttons()

        self._sync_bildgroesse_ui(reset_controls=True)

        self._aspect_lock_btn.setChecked(False)
        self._canvas.clear_crop_overlay()
        self._crop_apply_btn.setEnabled(False)

        self._map_lbl.setVisible(False)
        self._map_loading_lbl.setVisible(False)
        self._stop_geo_worker()
        self._refresh_info()
        self._show_original()

    def _setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key.Key_Home), self).activated.connect(
            lambda: self._try_navigate("prev") if self._nav_prev_btn.isEnabled() else None)
        QShortcut(QKeySequence(Qt.Key.Key_End), self).activated.connect(
            lambda: self._try_navigate("next") if self._nav_next_btn.isEnabled() else None)

    def set_nav_state(self, has_prev: bool, has_next: bool):
        """Aktiviert/deaktiviert die Prev/Next-Buttons."""
        self._nav_prev_btn.setEnabled(has_prev)
        self._nav_next_btn.setEnabled(has_next)

    def _try_navigate(self, direction: str):
        """Prüft auf ungespeicherte Änderungen, fragt ggf. nach und navigiert dann."""
        if len(self._history) > 0 or self._has_active_preview():
            dlg = QMessageBox(self)
            dlg.setWindowTitle("Ungespeicherte Änderungen")
            dlg.setText("Das Bild hat ungespeicherte Änderungen.\nMöchten Sie vor dem Wechsel speichern?")
            dlg.setIcon(QMessageBox.Icon.Question)
            speichern_btn = dlg.addButton("Speichern", QMessageBox.ButtonRole.AcceptRole)
            dlg.addButton("Verwerfen", QMessageBox.ButtonRole.DestructiveRole)
            abbrechen_btn = dlg.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
            dlg.setDefaultButton(speichern_btn)
            dlg.exec()
            clicked = dlg.clickedButton()
            if clicked is abbrechen_btn:
                return
            if clicked is speichern_btn:
                self._save_file()
        if direction == "prev":
            self.nav_prev_requested.emit()
        else:
            self.nav_next_requested.emit()

    # ── Gruppen-Toggle ────────────────────────────────────────────────────────

    def _on_group_toggled(self, key: str, checked: bool):
        for k, action in self._group_actions.items():
            if k != key and action.isChecked():
                action.setChecked(False)
        if key != "freistellen":
            self._deactivate_paint_brushes()
        if key == "zuschneiden" and not checked:
            self._canvas.clear_crop_overlay()
            self._crop_apply_btn.setEnabled(False)
        if key == "entzerren":
            self._canvas.reset_quad_points()
            self._quad_img_points = []
            self._quad_count_lbl.setText("0 / 4 Punkte")
            self._quad_apply_btn.setEnabled(False)

        # Anpassen: Basis beim Öffnen setzen, beim Schließen committen
        if key == "anpassen":
            if checked:
                self._adj_base = self._original.copy() if self._original else None
                if self._original:
                    self._adj_histogram.set_image(self._original)
            else:
                self._adj_commit()

        if checked:
            self._sidebar.setCurrentWidget(self._panels[key])
            self._sidebar.setFixedWidth(420)
            # Canvas-Modus und Hint setzen
            for gk, _, mode, hint in self._groups:
                if gk == key:
                    self._canvas.set_mode(mode)
                    self._hint_label.setText(hint)
                    break
        else:
            self._sidebar.setFixedWidth(0)
            self._canvas.set_mode(ImageCanvas.MODE_NONE)
            self._hint_label.setText("")

    # ── Canvas-Signale ────────────────────────────────────────────────────────


    def _on_canvas_adjust(self, delta: int):
        """Vom Canvas: Aktiven Anpassen-Regler um delta verschieben."""
        if self._active_adjust_slider:
            self._active_adjust_slider.setValue(
                self._active_adjust_slider.value() + delta
            )

    def _on_canvas_rotate(self, delta: float):
        """Vom Canvas: Fein-Rotations-Slider verschieben."""
        self._fine_rotate_slider.setValue(
            int(self._fine_rotate_slider.value() + delta)
        )

    def _set_active_slider(self, name: str):
        self._active_adjust_name = name
        self._active_adjust_slider = self._adjust_sliders[name]

    # ── Zuschneiden ───────────────────────────────────────────────────────────

    def _on_aspect_lock_toggled(self, locked: bool):
        if locked and self._original:
            iw, ih = self._original.size
            ratio = iw / ih if ih else 1.0
            self._canvas.set_aspect_ratio(ratio)
            self._aspect_lock_btn.setText("Seitenverhältnis sperren: EIN")
        else:
            self._canvas.set_aspect_ratio(None)
            self._aspect_lock_btn.setText("Seitenverhältnis sperren: AUS")


    def _on_crop_shape_ready(self):
        """Form gezeichnet — 'Anwenden'-Button freischalten."""
        if self._person_focus_selecting:
            self._person_focus_selecting = False
            self._focus_mark_btn.blockSignals(True)
            self._focus_mark_btn.setChecked(False)
            self._focus_mark_btn.blockSignals(False)
            self._person_focus_from_rect()
            return
        self._crop_apply_btn.setEnabled(True)
        is_freehand = self._btn_freehand.isChecked()
        self._freehand_offset_w.setVisible(is_freehand)
        if is_freehand:
            self._freehand_offset_slider.blockSignals(True)
            self._freehand_offset_slider.setValue(0)
            self._freehand_offset_slider.blockSignals(False)

    def _apply_pending_crop(self):
        """Wendet die aktuell angezeigte Crop-Form auf das Bild an."""
        state = self._canvas.get_crop_state()
        if state is None:
            return
        x, y, w, h, shape = state
        self._crop_apply_btn.setEnabled(False)
        self._freehand_offset_w.setVisible(False)
        if shape == ImageCanvas.SHAPE_FREEHAND:
            poly = self._canvas.get_freehand_polygon()
            self._canvas.clear_crop_overlay()
            self._do_crop_freehand(poly)
        else:
            self._canvas.clear_crop_overlay()
            if shape == ImageCanvas.SHAPE_RECT:
                self._do_crop_rect(x, y, w, h)
            elif shape == ImageCanvas.SHAPE_CIRCLE:
                self._do_crop_circle(x, y, w, h)
            elif shape == ImageCanvas.SHAPE_ELLIPSE:
                self._do_crop_ellipse(x, y, w, h)


    def _reset_crop(self):
        if self._crop_base is not None:
            self._push_history()
            self._original = self._crop_base
            self._show_original()
            self._crop_base = None

    def _do_crop_rect(self, x: int, y: int, w: int, h: int):
        if not self._original:
            return
        self._crop_base = self._original.copy()
        self._push_history()
        iw, ih = self._original.size
        self._original = self._original.crop((x, y, min(x + w, iw), min(y + h, ih)))
        self._show_original()

    def _do_crop_circle(self, x: int, y: int, w: int, h: int):
        if not self._original:
            return
        self._crop_base = self._original.copy()
        self._push_history()
        import numpy as np
        side = min(w, h)
        iw, ih = self._original.size
        x2, y2 = min(x + side, iw), min(y + side, ih)
        img = self._original.convert("RGBA").crop((x, y, x2, y2))
        sw, sh = img.size
        arr = np.array(img)
        cx, cy = sw / 2, sh / 2
        Y, X = np.ogrid[:sh, :sw]
        arr[((X - cx) / cx) ** 2 + ((Y - cy) / cy) ** 2 > 1, 3] = 0
        self._original = Image.fromarray(arr, "RGBA")
        self._show_original()

    def _do_crop_ellipse(self, x: int, y: int, w: int, h: int):
        if not self._original:
            return
        self._crop_base = self._original.copy()
        self._push_history()
        import numpy as np
        iw, ih = self._original.size
        x2, y2 = min(x + w, iw), min(y + h, ih)
        img = self._original.convert("RGBA").crop((x, y, x2, y2))
        ew, eh = img.size
        arr = np.array(img)
        cx, cy = ew / 2, eh / 2
        Y, X = np.ogrid[:eh, :ew]
        arr[((X - cx) / max(cx, 1)) ** 2 + ((Y - cy) / max(cy, 1)) ** 2 > 1, 3] = 0
        self._original = Image.fromarray(arr, "RGBA")
        self._show_original()

    def _do_crop_freehand(self, pts: list):
        if not self._original or len(pts) < 3:
            return
        self._crop_base = self._original.copy()
        self._push_history()
        try:
            from PIL import ImageDraw
            import numpy as np
            iw, ih = self._original.size
            mask = Image.new("L", (iw, ih), 0)
            draw = ImageDraw.Draw(mask)
            clamped = [(max(0, min(int(x), iw - 1)), max(0, min(int(y), ih - 1))) for x, y in pts]
            draw.polygon(clamped, fill=255)
            arr = np.array(self._original.convert("RGBA"))
            arr[:, :, 3] = np.array(mask)
            self._original = Image.fromarray(arr, "RGBA")
            self._show_original()
        except Exception:
            self._history.pop()

    # ── Bewegen ───────────────────────────────────────────────────────────────

    def _rotate(self, degrees: int):
        if self._original:
            self._push_history(keep_fine_base=True)
            self._original = self._original.rotate(-degrees, expand=True)
            if self._fine_rotate_base is not None:
                self._fine_rotate_base = self._fine_rotate_base.rotate(-degrees, expand=True)
                self._fine_rotate_total = 0.0
            self._show_original()

    def _flip(self, direction: str):
        if self._original:
            self._push_history(keep_fine_base=True)
            t = Image.Transpose.FLIP_LEFT_RIGHT if direction == "h" else Image.Transpose.FLIP_TOP_BOTTOM
            self._original = self._original.transpose(t)
            if self._fine_rotate_base is not None:
                self._fine_rotate_base = self._fine_rotate_base.transpose(t)
                self._fine_rotate_total = 0.0
            self._show_original()

    def _on_fine_rotate_press(self):
        """Basis-Snapshot setzen wenn noch nicht vorhanden."""
        if self._fine_rotate_base is None and self._original is not None:
            self._fine_rotate_base = self._original.copy()
            self._fine_rotate_total = 0.0

    def _on_fine_rotate_drag(self, angle: int):
        """Live-Vorschau: immer von der unveränderlichen Basis aus rotieren."""
        if self._fine_rotate_base is None:
            return
        total = self._fine_rotate_total + angle
        if total != 0:
            preview = self._fine_rotate_base.rotate(-total, expand=True, resample=Image.BICUBIC)
        else:
            preview = self._fine_rotate_base.copy()
        self._canvas.set_image(preview)

    def _on_fine_rotate_release(self):
        """Beim Loslassen: History-Push + akkumulierten Gesamtwinkel anwenden."""
        if self._fine_rotate_base is None:
            return
        angle = self._fine_rotate_slider.value()
        if angle != 0:
            self._push_history(keep_fine_base=True)
            self._fine_rotate_total += angle
            self._original = self._fine_rotate_base.rotate(
                -self._fine_rotate_total, expand=True, resample=Image.BICUBIC
            )
            self._show_original()
            self._fine_rotate_slider.blockSignals(True)
            self._fine_rotate_slider.setValue(0)
            self._fine_rotate_slider.blockSignals(False)
        else:
            self._show_original()

    def _apply_fine_rotate(self):
        """Tastatur-Aktionen: Basis setzen falls nötig, dann committen."""
        self._on_fine_rotate_press()
        self._on_fine_rotate_release()

    def _reset_fine_rotate(self):
        """↺-Button: akkumulierte Feinrotation auf 0° zurücksetzen."""
        if self._fine_rotate_base is not None:
            # Bild auf den Stand vor dieser Rotations-Session zurücksetzen
            self._original = self._fine_rotate_base.copy()
            self._fine_rotate_base = None
            self._fine_rotate_total = 0.0
        self._fine_rotate_slider.blockSignals(True)
        self._fine_rotate_slider.setValue(0)
        self._fine_rotate_slider.blockSignals(False)
        self._show_original()

    # ── Bildgröße / Komprimierung ─────────────────────────────────────────────

    def _sync_bildgroesse_ui(self, reset_controls: bool = False):
        """Spinboxen und Seitenverhältnis auf aktuelle Bildgröße synchronisieren."""
        if not self._original:
            return
        iw, ih = self._original.size
        self._width_spin.blockSignals(True)
        self._height_spin.blockSignals(True)
        self._width_spin.setValue(iw)
        self._height_spin.setValue(ih)
        self._width_spin.blockSignals(False)
        self._height_spin.blockSignals(False)
        self._resize_aspect_ratio = iw / ih if ih > 0 else 1.0
        if reset_controls:
            self._pct_spin.setValue(100)
            self._compress_fmt_combo.setCurrentIndex(0)
            self._new_file_btn.setChecked(True)
            self._overwrite_btn.setChecked(False)
            self._quality_slider.blockSignals(True)
            self._quality_slider.setValue(85)
            self._quality_slider.blockSignals(False)
        self._update_resize_size_preview()
        self._update_compress_preview()

    def _on_width_changed(self, val):
        if self._aspect_lock_chk.isChecked() and self._resize_aspect_ratio > 0:
            self._height_spin.blockSignals(True)
            self._height_spin.setValue(max(1, round(val / self._resize_aspect_ratio)))
            self._height_spin.blockSignals(False)

    def _on_height_changed(self, val):
        if self._aspect_lock_chk.isChecked() and self._resize_aspect_ratio > 0:
            self._width_spin.blockSignals(True)
            self._width_spin.setValue(max(1, round(val * self._resize_aspect_ratio)))
            self._width_spin.blockSignals(False)

    def _apply_pct_to_spins(self):
        if not self._original:
            return
        pct = self._pct_spin.value() / 100.0
        iw, ih = self._original.size
        self._width_spin.blockSignals(True)
        self._height_spin.blockSignals(True)
        self._width_spin.setValue(max(1, round(iw * pct)))
        self._height_spin.setValue(max(1, round(ih * pct)))
        self._width_spin.blockSignals(False)
        self._height_spin.blockSignals(False)
        self._update_resize_size_preview()

    def _apply_preset_width(self, pw: int):
        if not self._original:
            return
        iw, ih = self._original.size
        if pw == -1:    # Halbieren
            nw, nh = iw // 2, ih // 2
        elif pw == -2:  # Verdoppeln
            nw, nh = iw * 2, ih * 2
        else:
            ratio = ih / iw if iw > 0 else 1.0
            nw = pw
            nh = max(1, round(pw * ratio))
        self._width_spin.blockSignals(True)
        self._height_spin.blockSignals(True)
        self._width_spin.setValue(nw)
        self._height_spin.setValue(nh)
        self._width_spin.blockSignals(False)
        self._height_spin.blockSignals(False)
        self._update_resize_size_preview()

    def _update_resize_size_preview(self):
        if not self._original:
            return
        import io
        nw, nh = self._width_spin.value(), self._height_spin.value()
        # Geschätzte Größe: Skalierungsfaktor × aktuelle Dateigröße (grobe Schätzung)
        buf = io.BytesIO()
        self._original.convert("RGB").save(buf, "JPEG", quality=85)
        cur_bytes = buf.tell()
        factor = (nw * nh) / max(1, self._original.width * self._original.height)
        est = cur_bytes * factor
        self._resize_size_lbl.setText(f"Geschätzte Dateigröße: ~{_fmt_bytes(est)}")

    def _update_compress_preview(self, _=None):
        if not hasattr(self, "_quality_slider"):
            return
        q = self._quality_slider.value()
        # Qualitäts-Label
        if q >= 85:
            qlbl = "Hohe Qualität"
        elif q >= 60:
            qlbl = "Mittlere Qualität"
        else:
            qlbl = "Niedrige Qualität – Artefakte möglich"
        self._quality_range_lbl.setText(qlbl)
        # Größen-Schätzung
        if not self._original:
            return
        import io
        fmt_idx = self._compress_fmt_combo.currentIndex()  # 0=JPEG, 1=WebP, 2=PNG
        buf = io.BytesIO()
        try:
            if fmt_idx == 2:  # PNG – verlustfrei
                self._original.convert("RGBA").save(buf, "PNG", optimize=True)
                self._compress_size_lbl.setText(
                    f"Geschätzte Dateigröße: ~{_fmt_bytes(buf.tell())} (verlustfrei)")
            else:
                fmt = "JPEG" if fmt_idx == 0 else "WEBP"
                self._original.convert("RGB").save(buf, fmt, quality=q, optimize=True)
                self._compress_size_lbl.setText(
                    f"Geschätzte Dateigröße: ~{_fmt_bytes(buf.tell())}")
        except Exception:
            self._compress_size_lbl.setText("")

    def _on_compress_fmt_changed(self, idx):
        # PNG hat keine Qualitätsoption
        png = (idx == 2)
        self._quality_slider.setEnabled(not png)
        self._quality_range_lbl.setVisible(not png)
        self._update_compress_preview()

    def _save_compressed(self):
        if not self._original or not self._file_path:
            return
        quality   = self._quality_slider.value()
        fmt_idx   = self._compress_fmt_combo.currentIndex()
        overwrite = self._overwrite_btn.isChecked()
        fmt_map   = {0: ("JPEG", ".jpg"), 1: ("WEBP", ".webp"), 2: ("PNG", ".png")}
        pil_fmt, ext = fmt_map[fmt_idx]
        if overwrite:
            save_path = self._file_path.with_suffix(ext)
        else:
            save_path = self._file_path.with_stem(self._file_path.stem + "_compressed").with_suffix(ext)
        img = self._original.convert("RGBA" if pil_fmt == "PNG" else "RGB")
        if pil_fmt == "PNG":
            img.save(str(save_path), "PNG", optimize=True)
        else:
            img.save(str(save_path), pil_fmt, quality=quality, optimize=True)

    # ── Hintergrund ───────────────────────────────────────────────────────────

    def _hg_detect(self):
        if not self._original:
            return
        self._hg_detect_btn.setEnabled(False)
        self._hg_detect_btn.setText("KI läuft …")
        self._hg_status_lbl.setText("")
        self._hg_worker = _HintergrundMaskWorker(self._original.copy())
        self._hg_worker.finished.connect(self._on_hg_mask_ready)
        self._hg_worker.failed.connect(self._on_hg_mask_failed)
        self._hg_worker.start()

    def _on_hg_mask_ready(self, base_rgb: Image.Image, mask):
        self._hg_base = base_rgb
        self._hg_mask = mask
        self._hg_detect_btn.setEnabled(True)
        self._hg_detect_btn.setText("KI: Hintergrund erkennen")
        self._hg_status_lbl.setText("Bereit — Regler anpassen, dann Speichern.")
        self._update_action_buttons()
        self._hg_preview()

    def _on_hg_mask_failed(self, error: str):
        self._hg_detect_btn.setEnabled(True)
        self._hg_detect_btn.setText("KI: Hintergrund erkennen")
        self._hg_status_lbl.setText(f"Fehler: {error}")

    def _hg_or_focus_preview(self):
        """Gemeinsamer Einstiegspunkt für Regler — leitet je nach aktivem Modus weiter."""
        if self._focus_base is not None and self._focus_mask is not None:
            self._person_focus_preview()
        else:
            self._hg_preview()

    def _hg_or_focus_apply(self):
        """Gemeinsamer Anwenden-Button — leitet je nach aktivem Modus weiter."""
        if self._focus_base is not None and self._focus_mask is not None:
            self._person_focus_apply()
        else:
            self._hg_apply()

    def _hg_preview(self):
        if self._hg_base is None or self._hg_mask is None:
            return
        self._canvas.set_image(self._hg_compute())

    def _hg_compute(self) -> Image.Image:
        import numpy as np
        from PIL import ImageFilter
        mask = self._hg_mask.astype(np.float32) / 255.0   # 0=Hintergrund, 1=Vordergrund
        bg = self._hg_base.convert("RGB")
        # Unschärfe auf Hintergrund
        blur_val = self._hg_blur_slider.value()
        if blur_val > 0:
            bg = bg.filter(ImageFilter.GaussianBlur(radius=blur_val * 0.175))
        # Helligkeit auf Hintergrund
        bright_val = self._hg_brightness_slider.value()
        if bright_val != 0:
            factor = 1.0 + bright_val / 100.0
            bg_arr = np.clip(np.array(bg, dtype=np.float32) * factor, 0, 255).astype(np.uint8)
            bg = Image.fromarray(bg_arr)
        # Komposit: Vordergrund (Original) über bearbeitetem Hintergrund
        fg_arr = np.array(self._hg_base.convert("RGB"), dtype=np.float32)
        bg_arr = np.array(bg, dtype=np.float32)
        m = mask[:, :, None]
        result = Image.fromarray((fg_arr * m + bg_arr * (1.0 - m)).astype(np.uint8))
        result = self._apply_dof(result, self._hg_mask)
        return self._restore_alpha(result, self._original)

    def _hg_apply(self):
        if self._hg_base is None or self._hg_mask is None:
            return
        self._push_history()
        self._original = self._hg_compute()
        self._show_original()
        self._hg_mask = None
        self._hg_base = None
        self._hg_status_lbl.setText("Angewendet. Erneut 'Erkennen' für weitere Bearbeitung.")

    # ── Person fokussieren ────────────────────────────────────────────────────

    def _on_focus_mark_toggled(self, on: bool):
        if on:
            self._person_focus_selecting = True
            self._canvas.set_crop_shape(ImageCanvas.SHAPE_RECT)
            self._canvas.set_mode(ImageCanvas.MODE_CROP)
            self._focus_status_lbl.setText("Rechteck um das Objekt ziehen …")
        else:
            self._person_focus_selecting = False
            self._canvas.clear_crop_overlay()
            self._canvas.set_mode(ImageCanvas.MODE_NONE)
            self._focus_status_lbl.setText("")

    def _person_focus_from_rect(self):
        state = self._canvas.get_crop_state()
        self._canvas.clear_crop_overlay()
        self._canvas.set_mode(ImageCanvas.MODE_NONE)
        if state is None:
            return
        x, y, w, h, _ = state
        if w < 10 or h < 10:
            self._focus_status_lbl.setText("Auswahl zu klein — bitte erneut versuchen.")
            return
        if not self._original:
            return
        iw, ih = self._original.size
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(iw, x + w), min(ih, y + h)
        self._focus_mark_btn.setEnabled(False)
        self._focus_status_lbl.setText("KI analysiert Objekt …")
        self._focus_worker = _PersonFocusWorker(self._original.copy(), x1, y1, x2, y2)
        self._focus_worker.finished.connect(self._on_person_mask_ready)
        self._focus_worker.failed.connect(self._on_person_mask_failed)
        self._focus_worker.start()

    def _on_person_mask_ready(self, base_rgb, mask):
        self._focus_base = base_rgb
        self._focus_mask = mask
        self._focus_mark_btn.setEnabled(True)
        self._focus_status_lbl.setText("Bereit — Regler anpassen, dann Speichern.")
        self._update_action_buttons()
        self._person_focus_preview()

    def _on_person_mask_failed(self, error: str):
        self._focus_mark_btn.setEnabled(True)
        self._focus_status_lbl.setText(f"Fehler: {error}")

    def _person_focus_preview(self):
        if self._focus_base is None or self._focus_mask is None:
            return
        self._canvas.set_image(self._person_focus_compute())

    def _apply_dof(self, img: Image.Image, mask_255) -> Image.Image:
        """Distanzbasierter Tiefenschärfe-Blur: je weiter vom Objekt, desto stärker unscharf."""
        dof_val = self._hg_dof_slider.value()
        if dof_val == 0:
            return img
        import cv2, numpy as np
        from PIL import ImageFilter
        img_rgb = img.convert("RGB")
        # Distanzkarte: 0 am Objekt-Rand, wächst nach außen
        binary = np.where(mask_255 > 127, 255, 0).astype(np.uint8)
        inv = (255 - binary).astype(np.uint8)
        dist = cv2.distanceTransform(inv, cv2.DIST_L2, 5)
        max_d = max(float(dist.max()), 1.0)
        weight = np.clip(dist / max_d, 0.0, 1.0) ** 0.6   # leicht nicht-linear für natürlichen Abfall
        max_radius = dof_val * 0.4   # Schieberegler 100 → Radius 40
        blurred = img_rgb.filter(ImageFilter.GaussianBlur(radius=max(0.5, max_radius)))
        arr_orig = np.array(img_rgb, dtype=np.float32)
        arr_blur = np.array(blurred, dtype=np.float32)
        w = weight[:, :, None]
        result = Image.fromarray(np.clip(arr_orig * (1.0 - w) + arr_blur * w, 0, 255).astype(np.uint8))
        return self._restore_alpha(result, img)

    def _person_focus_compute(self) -> Image.Image:
        import numpy as np
        from PIL import ImageFilter
        mask = self._focus_mask.astype(np.float32) / 255.0
        blur_radius = self._hg_blur_slider.value() * 0.175
        bg = self._focus_base.convert("RGB")
        if blur_radius > 0:
            bg = bg.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        bright_val = self._hg_brightness_slider.value()
        if bright_val != 0:
            factor = 1.0 + bright_val / 100.0
            bg = Image.fromarray(
                np.clip(np.array(bg, dtype=np.float32) * factor, 0, 255).astype(np.uint8)
            )
        fg = np.array(self._focus_base.convert("RGB"), dtype=np.float32)
        bg_arr = np.array(bg, dtype=np.float32)
        m = mask[:, :, None]
        result = Image.fromarray((fg * m + bg_arr * (1.0 - m)).astype(np.uint8))
        result = self._apply_dof(result, self._focus_mask)
        return self._restore_alpha(result, self._original)

    def _person_focus_apply(self):
        if self._focus_base is None or self._focus_mask is None:
            return
        self._push_history()
        self._original = self._person_focus_compute()
        self._show_original()
        self._focus_mask = None
        self._focus_base = None
        self._focus_status_lbl.setText("Angewendet. Erneut markieren für weitere Bearbeitung.")

    # ── Freistellen ───────────────────────────────────────────────────────────

    def _on_brush_mode_toggled(self, mode: str, checked: bool, style_on: str, style_off: str):
        if checked:
            self._paint_erase_mode = (mode == "erase")
            # Anderen Button abwählen
            other = self._restore_brush_btn if mode == "erase" else self._erase_brush_btn
            other.blockSignals(True)
            other.setChecked(False)
            other.setStyleSheet(style_off if mode == "erase" else
                                "QPushButton { background: #ecf0f1; color: #333; border-radius: 6px;"
                                "  padding: 7px 12px; font-size: 13px; }"
                                "QPushButton:hover { background: #bdc3c7; }")
            other.blockSignals(False)
            self._erase_brush_btn.setStyleSheet(
                style_on if mode == "erase" else
                "QPushButton { background: #ecf0f1; color: #333; border-radius: 6px;"
                "  padding: 7px 12px; font-size: 13px; }"
                "QPushButton:hover { background: #bdc3c7; }"
            )
            self._restore_brush_btn.setStyleSheet(
                style_on if mode == "restore" else
                "QPushButton { background: #ecf0f1; color: #333; border-radius: 6px;"
                "  padding: 7px 12px; font-size: 13px; }"
                "QPushButton:hover { background: #bdc3c7; }"
            )
            self._canvas.set_paint_brush_size(self._brush_size_slider.value())
            self._canvas.set_mode(ImageCanvas.MODE_PAINT)
            hint = "Malen zum Entfernen (rot)" if mode == "erase" else "Malen zum Zurückholen (grün)"
            self._hint_label.setText(hint)
        else:
            self._canvas.set_mode(ImageCanvas.MODE_LASSO)
            self._hint_label.setText("Objekt mit Maus umfahren → loslassen")

    def _deactivate_paint_brushes(self):
        for btn in (self._erase_brush_btn, self._restore_brush_btn):
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.setStyleSheet(
                "QPushButton { background: #ecf0f1; color: #333; border-radius: 6px;"
                "  padding: 7px 12px; font-size: 13px; }"
                "QPushButton:hover { background: #bdc3c7; }"
            )
            btn.blockSignals(False)

    def _on_paint_stroke(self, ix: int, iy: int):
        if not self._original:
            return
        import numpy as np
        size = self._brush_size_slider.value()
        arr = np.array(self._original.convert("RGBA"))
        h, w = arr.shape[:2]
        y1, y2 = max(0, iy - size), min(h, iy + size + 1)
        x1, x2 = max(0, ix - size), min(w, ix + size + 1)
        if y1 >= y2 or x1 >= x2:
            return
        Y, X = np.ogrid[y1:y2, x1:x2]
        circle = (X - ix) ** 2 + (Y - iy) ** 2 <= size ** 2
        if self._paint_erase_mode:
            arr[y1:y2, x1:x2, 3][circle] = 0
        else:
            src = np.array(self._original_at_load.convert("RGBA"))
            patch_src = src[y1:y2, x1:x2]
            patch_dst = arr[y1:y2, x1:x2]
            patch_dst[circle] = patch_src[circle]
            arr[y1:y2, x1:x2] = patch_dst
        self._original = Image.fromarray(arr, "RGBA")
        self._show_original()

    def _on_lasso_committed(self, pts: list):
        """Lasso-Freistellen: Polygon-Maske — alles innerhalb des Lassos bleibt erhalten."""
        if not self._original or len(pts) < 15:
            return
        self._push_history()
        try:
            import cv2
            import numpy as np
            img = self._original.convert("RGBA")
            arr = np.array(img)
            h, w = arr.shape[:2]
            poly = np.array(
                [[max(0, min(x, w - 1)), max(0, min(y, h - 1))] for x, y in pts],
                dtype=np.int32,
            )
            # Alles innerhalb des Lassos = sichtbar, außerhalb = transparent
            alpha = np.zeros((h, w), np.uint8)
            cv2.fillPoly(alpha, [poly], 255)
            arr[:, :, 3] = alpha
            self._original = Image.fromarray(arr, "RGBA")
            self._show_original()
        except Exception:
            self._history.pop()

    def _remove_background(self):
        if not self._original:
            return
        self._rembg_btn.setEnabled(False)
        self._rembg_btn.setText("KI läuft …")
        self._push_history()

        img_copy = self._original.copy()
        self._rembg_thread = _RembgWorker(img_copy)
        self._rembg_thread.finished.connect(self._on_rembg_done)
        self._rembg_thread.failed.connect(self._on_rembg_failed)
        self._rembg_thread.start()

    def _on_rembg_done(self, result: Image.Image):
        self._original = result
        self._show_original()
        self._rembg_btn.setEnabled(True)
        self._rembg_btn.setText("KI: Hintergrund automatisch entfernen")

    def _on_rembg_failed(self, error: str):
        if self._history:
            self._original = self._history.pop()
            self._update_action_buttons()
        self._rembg_btn.setEnabled(True)
        self._rembg_btn.setText("KI: Hintergrund automatisch entfernen")
        lbl = QLabel(f"Fehler: {error}")
        lbl.setStyleSheet("color: red; font-size: 11px;")
        self._rembg_error_lbl.setText(f"Fehler: {error}")

    # ── Entzerren ─────────────────────────────────────────────────────────────

    # ── Entzerren: generische Live-Preview-Helfer ──────────────────────────────

    def _dist_press(self):
        """Slider-Press für Objektivverzerrung: speichert einmalig den Zustand vor jeder Verzerrung."""
        if self._original and self._distortion_base is None:
            self._distortion_base = self._original.copy()
        self._ent_press()

    def _dist_center_sliders_from_img(self, ix: int, iy: int):
        """Hilfsmethode: Bildkoordinaten → Mittelpunkt-Schieberegler setzen (lautlos)."""
        if not self._original:
            return
        iw, ih = self._original.size
        sx = max(-100, min(100, int((ix - iw / 2) / (iw / 2) * 100)))
        sy = max(-100, min(100, int((iy - ih / 2) / (ih / 2) * 100)))
        for slider, val in ((self._distortion_cx_slider, sx), (self._distortion_cy_slider, sy)):
            slider.blockSignals(True)
            slider.setValue(val)
            slider.blockSignals(False)

    def _on_distortion_center_picked(self, ix: int, iy: int):
        """Live-Drag: Mittelpunkt verschieben mit optionaler Schnellvorschau."""
        if not self._original:
            return
        # Basis beim ersten Aufruf sichern + Schnellvorschau-Bild erzeugen
        if self._ent_base is None:
            self._dist_press()
            if self._ent_base is not None and self._dist_perf_chk.isChecked():
                iw, ih = self._ent_base.size
                factor = max(1, max(iw, ih) // 800)
                if factor > 1:
                    self._ent_base_small = self._ent_base.resize(
                        (iw // factor, ih // factor), Image.BILINEAR)
                else:
                    self._ent_base_small = None
        self._dist_center_sliders_from_img(ix, iy)
        if self._dist_perf_chk.isChecked() and self._ent_base_small is not None:
            # Schnellvorschau: auf kleinem Bild rechnen, dann auf Originalgröße hochskalieren
            result_small = self._compute_distortion_img(self._ent_base_small)
            if result_small is not None and self._ent_base is not None:
                result = result_small.resize(self._ent_base.size, Image.BILINEAR)
                self._canvas.set_image(result)
        else:
            self._ent_preview(self._compute_distortion_img)

    def _on_distortion_center_committed(self, ix: int, iy: int):
        """Loslassen: finale Berechnung in voller Qualität + Commit."""
        if not self._original and self._ent_base is None:
            return
        self._dist_center_sliders_from_img(ix, iy)
        self._ent_base_small = None
        self._ent_commit(self._compute_distortion_img,
                         [self._distortion_slider, self._distortion_cx_slider, self._distortion_cy_slider])
        self._dist_pick_btn.setChecked(False)

    def _reset_distortion(self):
        """Stellt das Bild auf den Zustand vor der ersten Objektivverzerrung zurück."""
        if self._distortion_base is not None:
            self._push_history()
            self._original = self._distortion_base.copy()
            self._show_original()
            self._distortion_base = None
        self._ent_base = None
        for _s in (self._distortion_slider, self._distortion_cx_slider, self._distortion_cy_slider):
            _s.blockSignals(True)
            _s.setValue(0)
            _s.blockSignals(False)

    def _ent_press(self):
        if self._original and self._ent_base is None:
            self._ent_base = self._original.copy()

    def _ent_preview(self, compute_fn):
        if self._ent_base is None:
            return
        result = compute_fn(self._ent_base)
        if result is not None:
            self._canvas.set_image(result)

    def _ent_commit(self, compute_fn, sliders: list):
        if self._ent_base is None:
            return
        result = compute_fn(self._ent_base)
        if result is not None:
            self._push_history()
            self._original = result
            self._show_original()
        self._ent_base = None

    # ── Entzerren: Compute-Methoden (arbeiten auf übergebenem Basis-Bild) ──────

    def _compute_distortion_img(self, base: Image.Image):
        try:
            import cv2, numpy as np
            k = self._distortion_slider.value() / 40.0
            arr = np.array(base.convert("RGB"))
            h, w = arr.shape[:2]
            # Mittelpunkt verschiebbar: ±100 entspricht ±50 % der halben Bildgröße
            cx = w / 2 + self._distortion_cx_slider.value() / 100.0 * (w / 2)
            cy = h / 2 + self._distortion_cy_slider.value() / 100.0 * (h / 2)
            cam  = np.array([[w, 0, cx], [0, w, cy], [0, 0, 1]], dtype=np.float64)
            dist = np.array([k, 0, 0, 0, 0], dtype=np.float64)
            return self._restore_alpha(Image.fromarray(cv2.undistort(arr, cam, dist)), base)
        except Exception:
            return None

    def _compute_perspective_img(self, base: Image.Image):
        try:
            import cv2, numpy as np
            angle_x = self._persp_v_slider.value()
            angle_y = self._persp_h_slider.value()
            arr = np.array(base.convert("RGB"))
            h, w = arr.shape[:2]
            cx, cy = w / 2.0, h / 2.0
            d = max(w, h) * 2.5
            alpha = np.radians(angle_y * 0.6)
            beta  = np.radians(angle_x * 0.6)
            ca, sa = np.cos(alpha), np.sin(alpha)
            cb, sb = np.cos(beta),  np.sin(beta)
            My = np.array([
                [d*ca - cx*sa,  0,  cx*(d*(1-ca) + cx*sa)],
                [-cy*sa,        d,  cy*cx*sa              ],
                [-sa,           0,  d + cx*sa             ],
            ], dtype=np.float64)
            Mx = np.array([
                [d,  -cx*sb,  cx*cy*sb                   ],
                [0,   d*cb - cy*sb,  cy*(d*(1-cb) + cy*sb)],
                [0,  -sb,     d + cy*sb                  ],
            ], dtype=np.float64)
            H = My @ Mx
            result = cv2.warpPerspective(arr, H, (w, h),
                flags=cv2.INTER_LANCZOS4,
                borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
            return self._restore_alpha(Image.fromarray(result), base)
        except Exception:
            return None

    def _start_quad_mode(self):
        """Aktiviert den 4-Punkt-Modus im Canvas."""
        self._quad_img_points = []
        self._canvas.reset_quad_points()
        self._quad_count_lbl.setText("0 / 4 Punkte")
        self._quad_apply_btn.setEnabled(False)
        self._canvas.set_mode(ImageCanvas.MODE_QUAD)
        self._hint_label.setText("4 Punkte im Bild anklicken")

    def _reset_quad_points(self):
        """Löscht alle gesetzten Quad-Punkte und stellt ggf. das Bild vor dem Warp wieder her."""
        if self._quad_base is not None:
            self._push_history()
            self._original = self._quad_base
            self._show_original()
            self._quad_base = None
        self._quad_img_points = []
        self._canvas.reset_quad_points()
        self._quad_count_lbl.setText("0 / 4 Punkte")
        self._quad_apply_btn.setEnabled(False)

    def _on_quad_point_added(self, pts: list):
        """Wird aufgerufen wenn ein Quad-Punkt im Canvas gesetzt wird."""
        self._quad_img_points = pts
        n = len(pts)
        if n == 4:
            self._quad_count_lbl.setText("4 / 4 Punkte ✓")
            self._quad_apply_btn.setEnabled(True)
        else:
            self._quad_count_lbl.setText(f"{n} / 4 Punkte")
            self._quad_apply_btn.setEnabled(False)

    def _apply_quad_warp(self):
        if not self._original or len(self._quad_img_points) != 4:
            return
        self._quad_base = self._original.copy()
        self._push_history()
        try:
            import cv2, numpy as np
            arr = np.array(self._original.convert("RGB"))
            h, w = arr.shape[:2]
            src = np.float32(self._quad_img_points)
            dst = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
            M = cv2.getPerspectiveTransform(src, dst)
            result = cv2.warpPerspective(arr, M, (w, h))
            self._original = self._restore_alpha(Image.fromarray(result), self._quad_base)
            self._show_original()
            # Punkte zurücksetzen
            self._quad_img_points = []
            self._canvas.reset_quad_points()
            self._quad_count_lbl.setText("0 / 4 Punkte")
            self._quad_apply_btn.setEnabled(False)
            self._canvas.set_mode(ImageCanvas.MODE_NONE)
            self._hint_label.setText("")
        except Exception:
            self._history.pop()

    def _apply_auto_horizon(self):
        if not self._original:
            return
        self._push_history()
        try:
            import cv2, numpy as np
            arr = np.array(self._original.convert("RGB"))
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            edges = cv2.Canny(gray, 50, 150)
            lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80,
                                    minLineLength=50, maxLineGap=20)
            if lines is None:
                self._horizon_status_lbl.setText("Kein Horizont erkannt")
                self._history.pop()
                return
            angles = []
            for line in lines:
                x1, y1, x2, y2 = line[0]
                if x2 != x1:
                    angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
                    if abs(angle) < 20:
                        angles.append(angle)
            if not angles:
                self._horizon_status_lbl.setText("Kein Horizont erkannt")
                self._history.pop()
                return
            median_angle = float(np.median(angles))
            self._original = self._original.rotate(-median_angle, expand=True,
                                                   resample=Image.BICUBIC)
            self._show_original()
            self._horizon_status_lbl.setText(f"Korrigiert um {median_angle:.1f}°")
        except Exception:
            self._history.pop()

    def _compute_chrom_img(self, base: Image.Image):
        try:
            import cv2, numpy as np
            arr = np.array(base.convert("RGB"))
            h, w = arr.shape[:2]
            val = self._chrom_slider.value()
            r_scale = 1.0 + val / 2000.0
            b_scale = 1.0 - val / 2000.0

            def scale_ch(ch, scale):
                nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
                scaled = cv2.resize(ch, (nw, nh), interpolation=cv2.INTER_LINEAR)
                ox, oy = (nw - w) // 2, (nh - h) // 2
                if ox >= 0 and oy >= 0:
                    return scaled[oy:oy+h, ox:ox+w]
                out = np.zeros((h, w), dtype=ch.dtype)
                out[-oy:-oy+nh, -ox:-ox+nw] = scaled
                return out

            result = np.stack([scale_ch(arr[:,:,0], r_scale),
                               arr[:,:,1],
                               scale_ch(arr[:,:,2], b_scale)], axis=2)
            return self._restore_alpha(Image.fromarray(result.astype(np.uint8)), base)
        except Exception:
            return None

    def _compute_vignette_img(self, base: Image.Image):
        try:
            import numpy as np
            arr = np.array(base.convert("RGB")).astype(np.float32)
            h, w = arr.shape[:2]
            strength = self._vignette_slider.value()
            cy, cx = h / 2.0, w / 2.0
            Y, X = np.ogrid[:h, :w]
            dist = np.sqrt(((X-cx)/cx)**2 + ((Y-cy)/cy)**2) / np.sqrt(2.0)
            correction = 1.0 + (strength / 100.0) * dist
            return self._restore_alpha(
                Image.fromarray(np.clip(arr * correction[:,:,None], 0, 255).astype(np.uint8)), base
            )
        except Exception:
            return None

    # ── Anpassen ──────────────────────────────────────────────────────────────

    _ADJ_NEUTRAL = {
        "brightness": 100, "contrast": 100, "saturation": 100, "sharpness": 100,
        "shadows": 0, "hue": 0, "warmth": 0, "exposure": 0, "blackpoint": 0,
    }

    def _adj_press(self):
        if self._original and self._adj_base is None:
            self._adj_base = self._original.copy()

    def _adj_preview(self):
        if self._adj_base is None:
            return
        result = self._compute_adjustments(self._adj_base)
        if result is not None:
            self._canvas.set_image(result)
            self._adj_histogram.set_image(result)

    def _adj_commit(self):
        if self._adj_base is None:
            return
        result = self._compute_adjustments(self._adj_base)
        if result is not None:
            self._push_history()
            self._original = result
            self._show_original()
        self._adj_base = None

    def _adj_reset_all(self):
        """Reset all Anpassen sliders to neutral and restore canvas to session base."""
        neutral = self._ADJ_NEUTRAL
        for name, slider in self._adjust_sliders.items():
            slider.blockSignals(True)
            slider.setValue(neutral.get(name, 0))
            slider.blockSignals(False)
        base = self._adj_base if self._adj_base is not None else self._original
        if base is not None:
            self._canvas.set_image(base)
            self._adj_histogram.set_image(base)

    def _compute_adjustments(self, base: Image.Image) -> Image.Image:
        import numpy as np
        img = base.convert("RGB")
        _base_for_alpha = base
        img = ImageEnhance.Brightness(img).enhance(self._brightness_slider.value() / 100.0)
        img = ImageEnhance.Contrast(img).enhance(self._contrast_slider.value() / 100.0)
        img = ImageEnhance.Color(img).enhance(self._saturation_slider.value() / 100.0)
        sharp_val = self._sharpness_slider.value()
        if sharp_val != 100:
            import cv2
            arr_sharp = np.array(img)
            if sharp_val > 100:
                # Unsharp Mask: Hochfrequenzanteile verstärken
                amount = (sharp_val - 100) / 100.0 * 3.0   # 0 … 3
                blurred = cv2.GaussianBlur(arr_sharp, (0, 0), 2.0)
                arr_sharp = cv2.addWeighted(arr_sharp, 1.0 + amount, blurred, -amount, 0)
            else:
                # Weichzeichnen (Gegenteil von Schärfen)
                sigma = (100 - sharp_val) / 100.0 * 6.0     # 0 … 6
                arr_sharp = cv2.GaussianBlur(arr_sharp, (0, 0), max(0.1, sigma))
            img = Image.fromarray(np.clip(arr_sharp, 0, 255).astype(np.uint8))
        arr = np.array(img, dtype=np.float32)
        ev = self._exposure_slider.value() / 100.0
        arr = arr * (2 ** ev)
        bp = self._blackpoint_slider.value() / 100.0 * 255
        if bp > 0:
            arr = np.clip((arr - bp) / max(255 - bp, 1) * 255, 0, 255)
        shadow = self._shadows_slider.value() / 100.0
        if shadow != 0:
            arr = arr + shadow * (1.0 - arr / 255.0) * 80
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)
        warmth = self._warmth_slider.value()
        if warmth != 0:
            r, g, b = img.split()
            img = Image.merge("RGB", (
                _shift_channel(r,  int(warmth * 0.6)),
                g,
                _shift_channel(b, -int(warmth * 0.4)),
            ))
        hue = self._hue_slider.value()
        if hue != 0:
            img = _shift_hue(img, hue)
        return self._restore_alpha(img, _base_for_alpha)

    def _apply_adjustments(self):
        """Maus-Drag-Modus: aktuellen Slider-Stand auf Original anwenden."""
        self._adj_press()
        self._adj_commit()

    def _ai_optimize(self):
        if not self._original:
            return
        self._push_history()
        try:
            import cv2, numpy as np
            source = self._original
            arr = np.array(source.convert("RGB"))
            lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
            l, a, b = cv2.split(lab)
            l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
            result = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2RGB)
            self._original = self._restore_alpha(Image.fromarray(result), source)
            self._show_original()
        except ImportError:
            pass

    def _reset_image(self):
        if self._file_path:
            self.load_file(str(self._file_path))

    # ── Auflösung ─────────────────────────────────────────────────────────────

    def _apply_resize(self):
        if not self._original:
            return
        self._push_history()
        interp = {0: Image.LANCZOS, 1: Image.BILINEAR, 2: Image.NEAREST}.get(
            self._interp_combo.currentIndex(), Image.LANCZOS
        )
        nw, nh = self._width_spin.value(), self._height_spin.value()
        self._original = self._original.resize((nw, nh), interp)
        # Seitenverhältnis nach Resize aktualisieren
        self._resize_aspect_ratio = nw / nh if nh > 0 else 1.0
        self._show_original()
        self._update_resize_size_preview()

    # ── Bildinfos ─────────────────────────────────────────────────────────────

    def _refresh_info(self):
        if not self._original or not self._file_path:
            return

        self._histogram.set_image(self._original)

        w, h = self._original.width, self._original.height
        mp = w * h / 1_000_000
        from math import gcd
        g = gcd(w, h)
        ratio = f"{w // g}:{h // g}"
        size_bytes = self._file_path.stat().st_size
        size_str = f"{size_bytes / 1024:.1f} KB" if size_bytes < 1024 * 1024 else f"{size_bytes / 1024 / 1024:.2f} MB"
        fmt = self._original.format or self._file_path.suffix.lstrip(".").upper()

        # DPI aus Bild-Metadaten
        dpi_info = self._original.info.get("dpi")
        dpi_str = f"{float(dpi_info[0]):.0f} × {float(dpi_info[1]):.0f} DPI" if dpi_info else "–"

        info = (
            "<b style='font-size:13px;color:#ffffff'>📁 Datei</b><br>"
            f"<b>Name:</b> {self._file_path.name}<br>"
            f"<b>Format:</b> {fmt}<br>"
            f"<b>Dateigröße:</b> {size_str}<br>"
            "<br>"
            "<b style='font-size:13px;color:#ffffff'>🖼 Bild</b><br>"
            f"<b>Abmessungen:</b> {w} × {h} px<br>"
            f"<b>Megapixel:</b> {mp:.1f} MP<br>"
            f"<b>Seitenverhältnis:</b> {ratio}<br>"
            f"<b>Farbmodus:</b> {self._original.mode}<br>"
            f"<b>Auflösung:</b> {dpi_str}<br>"
        )

        try:
            from PIL.ExifTags import TAGS, GPSTAGS

            # getexif() funktioniert für JPEG, PNG, WEBP, TIFF — robuster als _getexif()
            exif_obj = self._original.getexif() if hasattr(self._original, "getexif") else None
            exif_raw = dict(exif_obj) if exif_obj else {}

            # Fallback: _getexif() für ältere JPEG-Bilder
            if not exif_raw and hasattr(self._original, "_getexif"):
                try:
                    raw = self._original._getexif()
                    if raw:
                        exif_raw = raw
                except Exception:
                    pass

            if exif_raw:
                tag_map = {TAGS.get(tid, tid): val for tid, val in exif_raw.items()}

                # Kamera-Infos
                camera_fields = []
                for key, label in [
                    ("Make",      "Hersteller"),
                    ("Model",     "Kameramodell"),
                    ("LensModel", "Objektiv"),
                    ("Software",  "Software"),
                ]:
                    if key in tag_map:
                        camera_fields.append(f"<b>{label}:</b> {str(tag_map[key]).strip()}")
                if camera_fields:
                    info += "<br><b style='font-size:13px;color:#ffffff'>📷 Kamera</b><br>" + "<br>".join(camera_fields) + "<br>"

                # Aufnahme-Einstellungen
                shot_fields = []
                # Aufnahmedatum: DateTimeOriginal bevorzugen (echte Aufnahmezeit)
                date_val = tag_map.get("DateTimeOriginal") or tag_map.get("DateTime")
                if date_val:
                    shot_fields.append(f"<b>Aufnahmedatum:</b> {str(date_val).replace(':', '.', 2)}")
                if "ExposureTime" in tag_map:
                    et = tag_map["ExposureTime"]
                    et_str = f"{et.numerator}/{et.denominator} s" if hasattr(et, "numerator") else str(et)
                    shot_fields.append(f"<b>Belichtungszeit:</b> {et_str}")
                if "FNumber" in tag_map:
                    fn = tag_map["FNumber"]
                    fn_val = float(fn.numerator) / float(fn.denominator) if hasattr(fn, "numerator") else float(fn)
                    shot_fields.append(f"<b>Blende:</b> f/{fn_val:.1f}")
                if "ISOSpeedRatings" in tag_map:
                    shot_fields.append(f"<b>ISO:</b> {tag_map['ISOSpeedRatings']}")
                if "FocalLength" in tag_map:
                    fl = tag_map["FocalLength"]
                    fl_val = float(fl.numerator) / float(fl.denominator) if hasattr(fl, "numerator") else float(fl)
                    shot_fields.append(f"<b>Brennweite:</b> {fl_val:.0f} mm")
                if "ExposureBiasValue" in tag_map:
                    ev = tag_map["ExposureBiasValue"]
                    ev_val = float(ev.numerator) / float(ev.denominator) if hasattr(ev, "numerator") else float(ev)
                    shot_fields.append(f"<b>Belichtungskorrektur:</b> {ev_val:+.1f} EV")
                if "Flash" in tag_map:
                    try:
                        shot_fields.append(f"<b>Blitz:</b> {'ausgelöst' if int(tag_map['Flash']) & 1 else 'nicht ausgelöst'}")
                    except Exception:
                        pass
                if "WhiteBalance" in tag_map:
                    shot_fields.append(f"<b>Weißabgleich:</b> {'manuell' if tag_map['WhiteBalance'] else 'automatisch'}")
                if shot_fields:
                    info += "<br><b style='font-size:13px;color:#ffffff'>⚙ Aufnahme</b><br>" + "<br>".join(shot_fields) + "<br>"
                else:
                    info += "<br><b style='font-size:13px;color:#ffffff'>⚙ Aufnahme</b><br><i style='color:#aaa'>Keine Aufnahmedaten verfügbar</i><br>"

                # GPS / Aufnahmeort
                # get_ifd(0x8825) liest die GPS-Sub-IFD korrekt aus;
                # tag_map["GPSInfo"] enthält bei getexif() nur den IFD-Offset (int)
                gps_raw = None
                if hasattr(exif_obj, "get_ifd"):
                    _gps_ifd = exif_obj.get_ifd(0x8825)
                    if _gps_ifd:
                        gps_raw = dict(_gps_ifd)
                if not gps_raw:
                    _fallback = tag_map.get("GPSInfo")
                    if isinstance(_fallback, dict):
                        gps_raw = _fallback
                if gps_raw and isinstance(gps_raw, dict):
                    try:
                        gps = {GPSTAGS.get(k, k): v for k, v in gps_raw.items()}

                        def _dms_to_dd(dms, ref):
                            d = float(dms[0].numerator) / float(dms[0].denominator) if hasattr(dms[0], "numerator") else float(dms[0])
                            m = float(dms[1].numerator) / float(dms[1].denominator) if hasattr(dms[1], "numerator") else float(dms[1])
                            s = float(dms[2].numerator) / float(dms[2].denominator) if hasattr(dms[2], "numerator") else float(dms[2])
                            dd = d + m / 60 + s / 3600
                            return -dd if ref in ("S", "W") else dd

                        lat = _dms_to_dd(gps["GPSLatitude"], gps.get("GPSLatitudeRef", "N"))
                        lon = _dms_to_dd(gps["GPSLongitude"], gps.get("GPSLongitudeRef", "E"))
                        info += (
                            "<br><b style='font-size:13px;color:#ffffff'>📍 Aufnahmeort</b><br>"
                            f"<b>Breitengrad:</b> {lat:.6f}°<br>"
                            f"<b>Längengrad:</b> {lon:.6f}°<br>"
                        )
                        if "GPSAltitude" in gps:
                            alt = gps["GPSAltitude"]
                            alt_val = float(alt.numerator) / float(alt.denominator) if hasattr(alt, "numerator") else float(alt)
                            info += f"<b>Höhe:</b> {alt_val:.0f} m<br>"

                        # Geo-Worker starten
                        self._map_lbl.setVisible(False)
                        self._map_loading_lbl.setVisible(True)
                        self._geo_worker = _GeoWorker(lat, lon)
                        self._geo_worker.place_ready.connect(self._on_place_ready)
                        self._geo_worker.map_ready.connect(self._on_map_ready)
                        self._geo_worker.start()
                    except Exception:
                        pass
                else:
                    info += "<br><b style='font-size:13px;color:#ffffff'>📍 Aufnahmeort</b><br><i style='color:#aaa'>Kein GPS-Signal vorhanden</i><br>"
            else:
                info += "<br><i style='color:#aaa'>Keine EXIF-Daten in dieser Datei enthalten.</i>"
        except Exception:
            pass

        self._info_label.setText(info)

    def _stop_geo_worker(self):
        """Alten Geo-Worker sicher beenden und Referenz halten bis Thread fertig."""
        if self._geo_worker is not None and self._geo_worker.isRunning():
            # Signale trennen damit veraltete Ergebnisse die UI nicht mehr ändern
            try:
                self._geo_worker.place_ready.disconnect()
                self._geo_worker.map_ready.disconnect()
            except Exception:
                pass
            self._old_geo_workers.append(self._geo_worker)
            # finished-Signal: Worker aus der Halteliste entfernen sobald Thread endet
            self._geo_worker.finished.connect(
                lambda w=self._geo_worker: self._old_geo_workers.remove(w) if w in self._old_geo_workers else None
            )
        self._geo_worker = None

    def _on_place_ready(self, place: str):
        if place:
            current = self._info_label.text()
            current = current.replace(
                "<b style='font-size:13px;color:#ffffff'>📍 Aufnahmeort</b>",
                f"<b style='font-size:13px;color:#ffffff'>📍 Aufnahmeort</b><br><b>Ort:</b> {place}"
            )
            self._info_label.setText(current)

    def _on_map_ready(self, img):
        self._map_loading_lbl.setVisible(False)
        if img is None:
            return
        pixmap = _pil_to_pixmap(img)
        self._map_lbl.setPixmap(pixmap)
        self._map_lbl.setVisible(True)


# ── Geo-Worker: Reverse-Geocoding + OSM-Karte ────────────────────────────────

class _GeoWorker(QThread):
    place_ready = pyqtSignal(str)          # Ortsname
    map_ready   = pyqtSignal(object)       # PIL Image oder None

    def __init__(self, lat: float, lon: float):
        super().__init__()
        self._lat = lat
        self._lon = lon

    def run(self):
        import urllib.request, json, math, io
        headers = {"User-Agent": "Optimizer-App/1.0"}

        # Reverse-Geocoding via Nominatim
        try:
            url = (f"https://nominatim.openstreetmap.org/reverse"
                   f"?format=json&lat={self._lat}&lon={self._lon}&zoom=14&addressdetails=1")
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read())
            addr = data.get("address", {})
            parts = []
            for k in ("village", "suburb", "town", "city", "county", "state", "country"):
                v = addr.get(k)
                if v and v not in parts:
                    parts.append(v)
            place = ", ".join(parts[:3]) if parts else data.get("display_name", "")
            self.place_ready.emit(place)
        except Exception:
            self.place_ready.emit("")

        # OSM-Kacheln (3×3, Zoom 14) laden und zusammenfügen
        try:
            from PIL import Image as PILImage, ImageDraw
            zoom = 14
            n = 2 ** zoom
            lat_r = math.radians(self._lat)
            tx = int((self._lon + 180) / 360 * n)
            ty = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)

            full = PILImage.new("RGB", (768, 768))
            for dy in range(-1, 2):
                for dx in range(-1, 2):
                    tile_url = f"https://tile.openstreetmap.org/{zoom}/{tx+dx}/{ty+dy}.png"
                    req = urllib.request.Request(tile_url, headers=headers)
                    with urllib.request.urlopen(req, timeout=6) as r:
                        tile = PILImage.open(io.BytesIO(r.read())).convert("RGB")
                    full.paste(tile, ((dx + 1) * 256, (dy + 1) * 256))

            # Roter Marker in der Mitte
            draw = ImageDraw.Draw(full)
            cx, cy = 384, 384
            draw.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], fill=(220, 40, 40), outline=(255, 255, 255), width=2)

            full = full.resize((360, 360), PILImage.LANCZOS)
            self.map_ready.emit(full)
        except Exception:
            self.map_ready.emit(None)


# ── rembg Hintergrund-Thread ──────────────────────────────────────────────────

class _RembgWorker(QThread):
    finished = pyqtSignal(object)   # Image.Image
    failed   = pyqtSignal(str)      # Fehlermeldung

    def __init__(self, img: Image.Image):
        super().__init__()
        self._img = img

    def run(self):
        try:
            from rembg import remove
            import io
            buf = io.BytesIO()
            self._img.save(buf, format="PNG")
            result = remove(buf.getvalue())
            out = Image.open(io.BytesIO(result)).convert("RGBA")
            self.finished.emit(out)
        except Exception as e:
            self.failed.emit(str(e))


# ── Hintergrund-Maske Worker ──────────────────────────────────────────────────

class _HintergrundMaskWorker(QThread):
    finished = pyqtSignal(object, object)   # (PIL.Image RGB, numpy H×W uint8)
    failed   = pyqtSignal(str)

    def __init__(self, img: Image.Image):
        super().__init__()
        self._img = img

    def run(self):
        try:
            import numpy as np
            from rembg import remove
            import io
            buf = io.BytesIO()
            self._img.save(buf, format="PNG")
            result_bytes = remove(buf.getvalue())
            result_rgba = Image.open(io.BytesIO(result_bytes)).convert("RGBA")
            mask = np.array(result_rgba.split()[3])   # Alpha-Kanal = Vordergrund-Maske
            self.finished.emit(self._img.copy(), mask)
        except Exception as e:
            self.failed.emit(str(e))


# ── Person-Fokus Worker ───────────────────────────────────────────────────────

class _PersonFocusWorker(QThread):
    """Führt rembg auf dem Bildausschnitt aus und gibt eine vollgroße Objekt-Maske zurück."""
    finished = pyqtSignal(object, object)   # (PIL.Image RGB, numpy H×W uint8)
    failed   = pyqtSignal(str)

    def __init__(self, img: Image.Image, x1: int, y1: int, x2: int, y2: int):
        super().__init__()
        self._img = img
        self._x1, self._y1, self._x2, self._y2 = x1, y1, x2, y2

    def run(self):
        try:
            import numpy as np
            from rembg import remove
            import io
            # Volles Bild analysieren → besserer Kontext für rembg
            buf = io.BytesIO()
            self._img.save(buf, format="PNG")
            result_rgba = Image.open(io.BytesIO(remove(buf.getvalue()))).convert("RGBA")
            full_mask = np.array(result_rgba.split()[3])   # Alpha = Vordergrund gesamt
            # Maske außerhalb des markierten Ausschnitts auf 0 setzen
            iw, ih = self._img.size
            clipped = np.zeros((ih, iw), dtype=np.uint8)
            clipped[self._y1:self._y2, self._x1:self._x2] = \
                full_mask[self._y1:self._y2, self._x1:self._x2]
            self.finished.emit(self._img.copy(), clipped)
        except Exception as e:
            self.failed.emit(str(e))


# ── Modul-Hilfsfunktionen ─────────────────────────────────────────────────────

def _fmt_bytes(n: float) -> str:
    """Lesbare Dateigröße: Bytes → KB / MB."""
    if n < 1024:
        return f"{int(n)} B"
    if n < 1024 * 1024:
        return f"{n/1024:.0f} KB"
    return f"{n/1024/1024:.1f} MB"


def _shift_channel(ch: Image.Image, amount: int) -> Image.Image:
    import numpy as np
    arr = np.clip(np.array(ch, dtype=np.int16) + amount, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def _shift_hue(img: Image.Image, degrees: int) -> Image.Image:
    import numpy as np
    arr = np.array(img.convert("RGB"), dtype=np.float32) / 255.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    cmax = np.maximum.reduce([r, g, b])
    cmin = np.minimum.reduce([r, g, b])
    delta = cmax - cmin + 1e-9
    h = np.zeros_like(r)
    mr, mg, mb = cmax == r, cmax == g, cmax == b
    h[mr] = (60 * ((g[mr] - b[mr]) / delta[mr])) % 360
    h[mg] = (60 * ((b[mg] - r[mg]) / delta[mg]) + 120) % 360
    h[mb] = (60 * ((r[mb] - g[mb]) / delta[mb]) + 240) % 360
    s = np.where(cmax == 0, 0, delta / cmax)
    v = cmax
    h = (h + degrees) % 360
    h6 = h / 60.0
    i = np.floor(h6).astype(int) % 6
    f = h6 - np.floor(h6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    out = np.zeros_like(arr)
    for idx, (rv, gv, bv) in enumerate(
        [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)]
    ):
        m = i == idx
        out[m, 0], out[m, 1], out[m, 2] = rv[m], gv[m], bv[m]
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8))
