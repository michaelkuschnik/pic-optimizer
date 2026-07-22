from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QToolBar, QSizePolicy, QSlider, QMessageBox, QComboBox,
    QLineEdit, QRadioButton, QButtonGroup,
)
from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QTimer, QRect, QObject, QThreadPool, QRunnable
from PyQt6.QtGui import QAction, QPainter, QColor, QFont, QPixmap, QShortcut, QKeySequence
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget


# ── RangeSlider: Schieberegler mit IN- und OUT-Griff ─────────────────────────

class RangeSlider(QWidget):
    """Zwei-Griff-Slider für IN/OUT-Punkte mit Thumbnail-Strip."""
    in_changed     = pyqtSignal(int)   # ms
    out_changed    = pyqtSignal(int)   # ms
    seek_requested = pyqtSignal(int)   # ms
    sized          = pyqtSignal(int)   # feuert einmal wenn erste echte Breite bekannt

    TRACK_H = 52
    LABEL_H = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration    = 1
        self._in_ms       = 0
        self._out_ms      = 1
        self._pos_ms      = 0
        self._remove_mode = False
        self._trim_mode   = False
        self._thumbs: list[QPixmap] = []
        self._drag: str | None = None
        self._sized       = False
        self.setFixedHeight(self.LABEL_H + self.TRACK_H)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    # ── API ───────────────────────────────────────────────────────────────────

    def set_duration(self, ms: int):
        self._duration = max(1, ms)
        self._in_ms    = 0
        self._out_ms   = self._duration
        self._pos_ms   = 0
        self.update()

    def set_position(self, ms: int):
        self._pos_ms = ms
        self.update()

    def set_remove_mode(self, enabled: bool):
        self._remove_mode = enabled
        self.update()

    def set_trim_mode(self, enabled: bool):
        self._trim_mode = enabled
        self.update()

    def set_in(self, ms: int):
        self._in_ms = max(0, min(ms, self._out_ms - 100))
        self.update()

    def set_out(self, ms: int):
        self._out_ms = min(self._duration, max(ms, self._in_ms + 100))
        self.update()

    def get_in(self)  -> int: return self._in_ms
    def get_out(self) -> int: return self._out_ms

    def set_thumbnails(self, pixmaps: list):
        self._thumbs = pixmaps
        self.update()

    # ── Koordinaten ───────────────────────────────────────────────────────────

    def _ms_to_x(self, ms: int) -> int:
        tw = self.width() - 2
        return 1 + int(ms / self._duration * tw)

    def _x_to_ms(self, x: int) -> int:
        tw = self.width() - 2
        return int(max(0, min(x - 1, tw)) / tw * self._duration)

    # ── Zeichnen ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        w   = self.width()
        ty  = self.LABEL_H          # Track-Y-Offset
        th  = self.TRACK_H

        in_x  = self._ms_to_x(self._in_ms)
        out_x = self._ms_to_x(self._out_ms)

        # Thumbnails als Hintergrund
        if self._thumbs:
            n  = len(self._thumbs)
            tw = max(1, w // n)
            for i, pix in enumerate(self._thumbs):
                rx = i * tw
                rw = tw if i < n - 1 else w - rx
                GAP = 4
                scaled = pix.scaled(rw - GAP, th,
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
                # Mittig in der Zelle platzieren
                dx = (rw - scaled.width())  // 2
                dy = (th - scaled.height()) // 2
                p.drawPixmap(rx + dx, ty + dy, scaled)
        else:
            p.fillRect(0, ty, w, th, QColor("#1a1a2e"))

        if self._trim_mode:
            if self._remove_mode:
                # Entfernen-Modus: Bereich rot markiert
                p.fillRect(0,     ty, in_x,       th, QColor(0, 0, 0, 80))
                p.fillRect(out_x, ty, w - out_x,  th, QColor(0, 0, 0, 80))
                p.fillRect(in_x, ty, out_x - in_x, th, QColor(231, 76, 60, 120))
                p.fillRect(in_x - 3,  ty, 6, th, QColor(230, 126, 34))
                p.fillRect(out_x - 3, ty, 6, th, QColor(230, 126, 34))
            else:
                # Behalten-Modus: außen abgedunkelt, innen blau
                p.fillRect(0,     ty, in_x,       th, QColor(0, 0, 0, 160))
                p.fillRect(out_x, ty, w - out_x,  th, QColor(0, 0, 0, 160))
                p.fillRect(in_x, ty, out_x - in_x, th, QColor(52, 152, 219, 70))
                p.fillRect(in_x - 3,  ty, 6, th, QColor(46, 204, 113))
                p.fillRect(out_x - 3, ty, 6, th, QColor(231, 76, 60))

            # Zeit-Labels über den Griffen
            font = QFont()
            font.setPointSize(8)
            p.setFont(font)
            p.setPen(QColor(46, 204, 113))
            p.drawText(QRect(max(0, in_x - 32), 0, 64, self.LABEL_H),
                       Qt.AlignmentFlag.AlignCenter, _ms_to_str(self._in_ms))
            p.setPen(QColor(231, 76, 60))
            p.drawText(QRect(min(w - 64, out_x - 32), 0, 64, self.LABEL_H),
                       Qt.AlignmentFlag.AlignCenter, _ms_to_str(self._out_ms))

        # Playhead (aktuelle Abspielposition)
        pos_x = self._ms_to_x(self._pos_ms)
        p.fillRect(pos_x - 1, ty, 2, th, QColor(255, 255, 255, 220))
        from PyQt6.QtGui import QPolygon
        from PyQt6.QtCore import QPoint
        tri = QPolygon([QPoint(pos_x - 5, ty), QPoint(pos_x + 5, ty), QPoint(pos_x, ty + 8)])
        p.setBrush(QColor(255, 255, 255, 220))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(tri)

        p.end()

    # ── Maus ──────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x    = event.pos().x()
        in_x = self._ms_to_x(self._in_ms)
        out_x= self._ms_to_x(self._out_ms)
        if abs(x - in_x)  <= 8:
            self._drag = "in"
        elif abs(x - out_x) <= 8:
            self._drag = "out"
        else:
            self._drag = "seek"
            self.seek_requested.emit(self._x_to_ms(x))

    def mouseMoveEvent(self, event):
        x  = event.pos().x()
        ms = self._x_to_ms(x)
        if self._drag == "in":
            self._in_ms = max(0, min(ms, self._out_ms - 100))
            self.in_changed.emit(self._in_ms)
            self.update()
        elif self._drag == "out":
            self._out_ms = min(self._duration, max(ms, self._in_ms + 100))
            self.out_changed.emit(self._out_ms)
            self.update()
        elif self._drag == "seek":
            self.seek_requested.emit(ms)
        else:
            in_x  = self._ms_to_x(self._in_ms)
            out_x = self._ms_to_x(self._out_ms)
            near  = abs(x - in_x) <= 8 or abs(x - out_x) <= 8
            self.setCursor(Qt.CursorShape.SizeHorCursor if near
                           else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self._drag = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._sized and self.width() > 0:
            self._sized = True
            self.sized.emit(self.width())


# ── Thumbnail-Strip Hintergrund-Worker ────────────────────────────────────────

class _ThumbStripSignals(QObject):
    done = pyqtSignal(list)


class _ThumbStripWorker(QRunnable):
    def __init__(self, path: Path, slider_width: int, height: int = 52):
        super().__init__()
        self.signals      = _ThumbStripSignals()
        self._path        = path
        self._slider_w    = slider_width
        self._height      = height

    def run(self):
        import subprocess, json, tempfile, os, math
        try:
            # Dauer + Video-Dimensionen ermitteln
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", "-show_streams", str(self._path)],
                capture_output=True, timeout=8
            )
            data = json.loads(probe.stdout)
            dur  = float(data.get("format", {}).get("duration", 0))
            if dur <= 0:
                self.signals.done.emit([])
                return

            # Seitenverhältnis aus erstem Video-Stream (inkl. Rotations-Tag)
            aspect = 16 / 9
            for s in data.get("streams", []):
                if s.get("codec_type") == "video":
                    vw = s.get("width",  0)
                    vh = s.get("height", 1)
                    if vw and vh:
                        # Rotation 90°/270° → Breite und Höhe tauschen
                        tags = s.get("tags", {}) or s.get("side_data_list", [{}])[0] if s.get("side_data_list") else s.get("tags", {})
                        rotate = int(tags.get("rotate", 0)) if isinstance(tags, dict) else 0
                        # Auch aus side_data_list lesen (neuere ffprobe-Versionen)
                        for sd in s.get("side_data_list", []):
                            if sd.get("side_data_type") == "Display Matrix":
                                rotate = abs(int(sd.get("rotation", 0)))
                        if rotate in (90, 270):
                            vw, vh = vh, vw
                        aspect = vw / vh
                    break

            # Wie viele Thumbnails passen lückenlos in die Slider-Breite?
            thumb_w_logical = self._height * aspect
            n = max(4, math.ceil(self._slider_w / thumb_w_logical))

            # Frames extrahieren in logischer Pixelgröße (kein DPR — vermeidet Skalierungsfehler)
            with tempfile.TemporaryDirectory() as tmp:
                rate = n / dur
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(self._path),
                     "-vf", f"fps={rate:.5f},scale=-2:{self._height}",
                     "-vframes", str(n), "-q:v", "3",
                     os.path.join(tmp, "t_%03d.jpg")],
                    capture_output=True, timeout=30
                )
                pixmaps = []
                for i in range(1, n + 1):
                    fp = os.path.join(tmp, f"t_{i:03d}.jpg")
                    if os.path.exists(fp):
                        pix = QPixmap(fp)
                        if not pix.isNull():
                            pixmaps.append(pix)
                self.signals.done.emit(pixmaps)
        except Exception:
            self.signals.done.emit([])


# ── Video-Editor-Screen ───────────────────────────────────────────────────────

class VideoEditorScreen(QWidget):
    back_requested     = pyqtSignal()
    nav_prev_requested = pyqtSignal()
    nav_next_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._file_path: Path | None = None
        self._original_path: Path | None = None   # erste geladene Datei
        self._video_history: list[Path] = []       # Undo-Stack (Dateipfade)
        self._fps: float = 30.0
        self._loop: bool = False
        self._muted: bool = False
        self._pre_mute_vol: int = 80
        self._show_first_frame: bool = False
        self._thumb_pool = QThreadPool()
        self._thumb_pool.setMaxThreadCount(1)

        self._player = QMediaPlayer()
        self._audio  = QAudioOutput()
        self._player.setAudioOutput(self._audio)
        self._audio.setVolume(0.8)

        self._build_ui()
        self._setup_shortcuts()

        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)

    # ── UI-Aufbau ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Kopfzeile
        header = QHBoxLayout()
        header.setContentsMargins(12, 2, 12, 2)

        self._file_label = QLabel("")
        self._file_label.setStyleSheet("color: #7f8c8d; font-size: 13px;")
        self._file_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        _nav_style = (
            "QPushButton { background: #34495e; color: white; border-radius: 4px;"
            "  padding: 2px 10px; font-size: 15px; }"
            "QPushButton:hover:enabled { background: #2c3e50; }"
            "QPushButton:disabled { background: #ccc; color: #888; }"
        )
        self._nav_prev_btn = QPushButton("‹")
        self._nav_prev_btn.setFixedHeight(20)
        self._nav_prev_btn.setToolTip("Vorheriges Medium")
        self._nav_prev_btn.setEnabled(False)
        self._nav_prev_btn.clicked.connect(lambda: self._try_navigate("prev"))
        self._nav_prev_btn.setStyleSheet(_nav_style)

        self._nav_next_btn = QPushButton("›")
        self._nav_next_btn.setFixedHeight(20)
        self._nav_next_btn.setToolTip("Nächstes Medium")
        self._nav_next_btn.setEnabled(False)
        self._nav_next_btn.clicked.connect(lambda: self._try_navigate("next"))
        self._nav_next_btn.setStyleSheet(_nav_style)

        close_btn = QPushButton("← Galerie")
        close_btn.setFixedHeight(20)
        close_btn.setToolTip("Zurück zur Bildübersicht")
        close_btn.clicked.connect(self._on_back)
        close_btn.setStyleSheet(
            "QPushButton { background: #34495e; color: white; border-radius: 4px;"
            "  padding: 2px 10px; font-size: 12px; }"
            "QPushButton:hover { background: #2c3e50; }"
        )

        _hdr_style = (
            "QPushButton { background: #34495e; color: white; border-radius: 4px;"
            "  padding: 2px 10px; font-size: 12px; }"
            "QPushButton:hover:enabled { background: #2c3e50; }"
            "QPushButton:disabled { background: #3a3a3a; color: #666; }"
        )

        self._restore_btn = QPushButton("⟲ Original")
        self._restore_btn.setFixedHeight(20)
        self._restore_btn.setToolTip("Zur ursprünglich geöffneten Videodatei zurückkehren")
        self._restore_btn.setEnabled(False)
        self._restore_btn.clicked.connect(self._restore_original)
        self._restore_btn.setStyleSheet(_hdr_style)

        self._undo_btn = QPushButton("↺ Rückgängig")
        self._undo_btn.setFixedHeight(20)
        self._undo_btn.setToolTip("Letzten Schnitt rückgängig machen")
        self._undo_btn.setEnabled(False)
        self._undo_btn.clicked.connect(self._undo)
        self._undo_btn.setStyleSheet(_hdr_style)

        self._save_btn = QPushButton("💾 Speichern")
        self._save_btn.setFixedHeight(20)
        self._save_btn.setToolTip("Video schneiden und speichern")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._do_trim)
        self._save_btn.setStyleSheet(
            "QPushButton { background: #27ae60; color: white; border-radius: 4px;"
            "  padding: 2px 10px; font-size: 12px; }"
            "QPushButton:hover:enabled { background: #1e8449; }"
            "QPushButton:disabled { background: #555; color: #999; }"
        )

        header.addWidget(self._file_label)
        header.addStretch()
        header.addWidget(self._restore_btn)
        header.addWidget(self._undo_btn)
        header.addWidget(self._save_btn)
        header.addSpacing(8)
        header.addWidget(self._nav_prev_btn)
        header.addWidget(self._nav_next_btn)
        header.addSpacing(8)
        header.addWidget(close_btn)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setStyleSheet(
            "QToolBar { background: #2c3e50; border: none; spacing: 2px; padding: 1px 8px; }"
            "QToolButton { color: white; padding: 2px 12px; border-radius: 4px; font-size: 12px; }"
            "QToolButton:hover { background: #34495e; }"
            "QToolButton:checked { background: #3498db; }"
        )
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        self._actions: dict[str, QAction] = {}
        for key, label in [("abspielen", "▶  Video abspielen"), ("schneiden", "✂  Video schneiden")]:
            act = QAction(label, self)
            act.setCheckable(True)
            act.toggled.connect(lambda checked, k=key: self._on_group_toggled(k, checked))
            toolbar.addAction(act)
            self._actions[key] = act

        # Steuerleiste
        self._play_controls = self._build_play_controls()

        # Video-Widget
        self._video_widget = QVideoWidget()
        self._video_widget.setStyleSheet("background: #000;")
        self._video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._player.setVideoOutput(self._video_widget)

        # RangeSlider — immer sichtbar (Seek + Schnitt)
        self._range_slider = RangeSlider()
        self._range_slider.in_changed.connect(self._on_range_in)
        self._range_slider.out_changed.connect(self._on_range_out)
        self._range_slider.sized.connect(self._reload_thumbnails)
        self._range_slider.seek_requested.connect(self._on_range_seek)

        # Schnitt-Panel (IN/OUT-Felder + Optionen, ohne Slider)
        self._trim_controls = self._build_trim_controls()
        self._trim_controls.setVisible(False)

        root.addLayout(header)
        root.addWidget(toolbar)
        root.addWidget(self._play_controls)
        root.addWidget(self._video_widget, 1)
        root.addWidget(self._range_slider)
        root.addWidget(self._trim_controls)

    def _build_play_controls(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: #1a1a2e; border-top: 1px solid #2c3e50;")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(3)

        self._seek_slider = QSlider(Qt.Orientation.Horizontal)
        self._seek_slider.setRange(0, 0)
        self._seek_slider.sliderMoved.connect(self._on_seek)
        self._seek_slider.setVisible(False)
        self._seek_slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: #34495e; border-radius: 2px; }"
            "QSlider::handle:horizontal { width: 12px; height: 12px; margin: -4px 0;"
            "  background: #3498db; border-radius: 6px; }"
            "QSlider::sub-page:horizontal { background: #3498db; border-radius: 2px; }"
        )

        controls = QHBoxLayout()
        controls.setSpacing(4)

        _ctrl_style = (
            "QPushButton { background: #2c3e50; color: #ecf0f1; border-radius: 4px;"
            "  font-size: 11px; padding: 2px 6px; }"
            "QPushButton:hover { background: #34495e; }"
            "QPushButton:checked { background: #3498db; }"
        )

        stop_btn = QPushButton("■")
        stop_btn.setFixedSize(24, 24)
        stop_btn.setToolTip("Stop – zurück zum Anfang")
        stop_btn.clicked.connect(self._stop)
        stop_btn.setStyleSheet(_ctrl_style)

        self._frame_back_btn = QPushButton("◀◀")
        self._frame_back_btn.setFixedSize(30, 24)
        self._frame_back_btn.setToolTip("Ein Frame zurück (gedrückt halten = kontinuierlich)")
        self._frame_back_btn.setStyleSheet(_ctrl_style)
        self._frame_back_btn.setAutoRepeat(True)
        self._frame_back_btn.setAutoRepeatDelay(500)
        self._frame_back_btn.setAutoRepeatInterval(80)
        self._frame_back_btn.clicked.connect(lambda: self._step_frame(-1))

        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedSize(30, 30)
        self._play_btn.setToolTip("Wiedergabe / Pause")
        self._play_btn.clicked.connect(self._toggle_play)
        self._play_btn.setStyleSheet(
            "QPushButton { background: #3498db; color: white; border-radius: 15px; font-size: 13px; }"
            "QPushButton:hover { background: #2980b9; }"
        )

        self._frame_fwd_btn = QPushButton("▶▶")
        self._frame_fwd_btn.setFixedSize(30, 24)
        self._frame_fwd_btn.setToolTip("Ein Frame vor (gedrückt halten = kontinuierlich)")
        self._frame_fwd_btn.setStyleSheet(_ctrl_style)
        self._frame_fwd_btn.setAutoRepeat(True)
        self._frame_fwd_btn.setAutoRepeatDelay(500)
        self._frame_fwd_btn.setAutoRepeatInterval(80)
        self._frame_fwd_btn.clicked.connect(lambda: self._step_frame(+1))

        self._time_label = QLabel("0:00.000 / 0:00.000")
        self._time_label.setStyleSheet("color: #ecf0f1; font-size: 11px; min-width: 120px; margin-left: 6px;")

        self._loop_btn = QPushButton("🔁")
        self._loop_btn.setFixedSize(24, 24)
        self._loop_btn.setToolTip("Endlos wiederholen: aus")
        self._loop_btn.setCheckable(True)
        self._loop_btn.clicked.connect(self._toggle_loop)
        self._loop_btn.setStyleSheet(_ctrl_style)

        spd_lbl = QLabel("Tempo:")
        spd_lbl.setStyleSheet("color: #95a5a6; font-size: 11px; margin-left: 6px;")
        self._speed_combo = QComboBox()
        self._speed_combo.addItems(["0.25×", "0.5×", "0.75×", "1×", "1.25×", "1.5×", "2×"])
        self._speed_combo.setCurrentIndex(3)
        self._speed_combo.setFixedSize(62, 24)
        self._speed_combo.setToolTip("Wiedergabegeschwindigkeit")
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        self._speed_combo.setStyleSheet(
            "QComboBox { background: #2c3e50; color: #ecf0f1; border: 1px solid #34495e;"
            "  border-radius: 4px; padding: 1px 4px; font-size: 11px; }"
            "QComboBox::drop-down { border: none; width: 14px; }"
            "QComboBox QAbstractItemView { background: #2c3e50; color: #ecf0f1;"
            "  selection-background-color: #3498db; border: 1px solid #34495e; }"
        )

        self._mute_btn = QPushButton("🔊")
        self._mute_btn.setFixedSize(24, 24)
        self._mute_btn.setToolTip("Stummschalten")
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._mute_btn.setStyleSheet(_ctrl_style)

        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(80)
        self._vol_slider.setFixedWidth(70)
        self._vol_slider.setToolTip("Lautstärke")
        self._vol_slider.valueChanged.connect(self._on_vol_changed)
        self._vol_slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 3px; background: #34495e; border-radius: 1px; }"
            "QSlider::handle:horizontal { width: 10px; height: 10px; margin: -3px 0;"
            "  background: #95a5a6; border-radius: 5px; }"
            "QSlider::sub-page:horizontal { background: #7f8c8d; border-radius: 1px; }"
        )

        fs_btn = QPushButton("⛶")
        fs_btn.setFixedSize(24, 24)
        fs_btn.setToolTip("Vollbild")
        fs_btn.clicked.connect(self._toggle_fullscreen)
        fs_btn.setStyleSheet(_ctrl_style)

        controls.addWidget(stop_btn)
        controls.addSpacing(2)
        controls.addWidget(self._frame_back_btn)
        controls.addWidget(self._play_btn)
        controls.addWidget(self._frame_fwd_btn)
        controls.addWidget(self._time_label)
        controls.addStretch()
        controls.addWidget(self._loop_btn)
        controls.addWidget(spd_lbl)
        controls.addWidget(self._speed_combo)
        controls.addSpacing(8)
        controls.addWidget(self._mute_btn)
        controls.addWidget(self._vol_slider)
        controls.addSpacing(4)
        controls.addWidget(fs_btn)

        layout.addLayout(controls)
        return w

    def _build_trim_controls(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: #1e2d3d; border-top: 2px solid #3498db;")
        root = QVBoxLayout(w)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        field_style = (
            "QLineEdit { background: #2c3e50; color: #ecf0f1; border: 1px solid #34495e;"
            "  border-radius: 4px; padding: 2px 6px; font-size: 12px; font-family: monospace; }"
            "QLineEdit:focus { border-color: #3498db; }"
        )
        btn_style = (
            "QPushButton { background: #2c3e50; color: #ecf0f1; border-radius: 4px;"
            "  padding: 3px 10px; font-size: 12px; }"
            "QPushButton:hover { background: #34495e; }"
        )
        lbl_style = "color: #95a5a6; font-size: 12px;"

        # ── Zeile 1: IN / OUT Felder ──────────────────────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        row1.addWidget(_lbl("IN:", "color: #2ecc71; font-size: 12px; font-weight: bold;"))
        self._in_field = QLineEdit("0:00.000")
        self._in_field.setFixedWidth(90)
        self._in_field.setToolTip("Startpunkt (m:ss.mmm) — Enter zum Übernehmen")
        self._in_field.setStyleSheet(field_style)
        self._in_field.returnPressed.connect(self._on_in_field_entered)
        row1.addWidget(self._in_field)

        btn_set_in = QPushButton("▶| Hier")
        btn_set_in.setToolTip("Aktuelle Position als Startpunkt setzen")
        btn_set_in.setStyleSheet(btn_style)
        btn_set_in.clicked.connect(self._set_trim_start)
        row1.addWidget(btn_set_in)

        row1.addSpacing(16)

        row1.addWidget(_lbl("OUT:", "color: #e74c3c; font-size: 12px; font-weight: bold;"))
        self._out_field = QLineEdit("0:00.000")
        self._out_field.setFixedWidth(90)
        self._out_field.setToolTip("Endpunkt (m:ss.mmm) — Enter zum Übernehmen")
        self._out_field.setStyleSheet(field_style)
        self._out_field.returnPressed.connect(self._on_out_field_entered)
        row1.addWidget(self._out_field)

        btn_set_out = QPushButton("|◀ Hier")
        btn_set_out.setToolTip("Aktuelle Position als Endpunkt setzen")
        btn_set_out.setStyleSheet(btn_style)
        btn_set_out.clicked.connect(self._set_trim_end)
        row1.addWidget(btn_set_out)

        row1.addSpacing(12)

        self._remove_btn = QPushButton("Mittelteil entfernen")
        self._remove_btn.setCheckable(True)
        self._remove_btn.setToolTip(
            "Bereich zwischen IN und OUT entfernen und die Teile davor/danach zusammenfügen"
        )
        self._remove_btn.setStyleSheet(
            "QPushButton { background: #2c3e50; color: #ecf0f1; border-radius: 4px;"
            "  padding: 3px 8px; font-size: 12px; }"
            "QPushButton:hover { background: #34495e; }"
            "QPushButton:checked { background: #e67e22; color: white; }"
        )
        self._remove_btn.toggled.connect(self._on_remove_mode_toggled)
        row1.addWidget(self._remove_btn)

        row1.addSpacing(8)

        self._mode_group = QButtonGroup(self)
        self._rb_lossless = QRadioButton("Verlustfrei")
        self._rb_exact    = QRadioButton("Framegenau")
        self._rb_lossless.setChecked(True)
        for rb in (self._rb_lossless, self._rb_exact):
            rb.setStyleSheet("color: #ecf0f1; font-size: 12px;")
            self._mode_group.addButton(rb)
            row1.addWidget(rb)

        row1.addStretch()

        self._dur_lbl = QLabel("Dauer: –")
        self._dur_lbl.setStyleSheet(lbl_style)
        row1.addWidget(self._dur_lbl)

        row1.addSpacing(8)

        self._cut_btn = QPushButton("✂ Schneiden")
        self._cut_btn.setStyleSheet(
            "QPushButton { background: #2c3e50; color: #ecf0f1; border-radius: 4px;"
            "  padding: 3px 10px; font-size: 12px; }"
            "QPushButton:hover { background: #34495e; }"
            "QPushButton:disabled { background: #555; color: #999; }"
        )
        self._cut_btn.clicked.connect(self._do_trim)
        row1.addWidget(self._cut_btn)

        root.addLayout(row1)
        return w

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def load_file(self, file_path: str):
        self._player.stop()
        self._file_path = Path(file_path)
        # Original-Pfad und History nur beim Laden einer neuen (externen) Datei zurücksetzen
        self._original_path = self._file_path
        self._video_history.clear()
        self._file_label.setText(self._file_path.name)
        self._fps = _detect_fps(self._file_path)
        self._player.setSource(QUrl.fromLocalFile(str(self._file_path)))
        self._speed_combo.setCurrentIndex(3)
        self._player.setPlaybackRate(1.0)
        self._range_slider.set_thumbnails([])
        for act in self._actions.values():
            act.blockSignals(True)
            act.setChecked(False)
            act.blockSignals(False)
        self._show_first_frame = True
        self._update_header_btns()

    def _setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key.Key_Home), self).activated.connect(
            lambda: self._try_navigate("prev") if self._nav_prev_btn.isEnabled() else None)
        QShortcut(QKeySequence(Qt.Key.Key_End), self).activated.connect(
            lambda: self._try_navigate("next") if self._nav_next_btn.isEnabled() else None)

    def set_nav_state(self, has_prev: bool, has_next: bool):
        self._nav_prev_btn.setEnabled(has_prev)
        self._nav_next_btn.setEnabled(has_next)

    def stop_playback(self):
        self._player.stop()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _on_back(self):
        self._player.stop()
        self.back_requested.emit()

    def _try_navigate(self, direction: str):
        self._player.stop()
        if direction == "prev":
            self.nav_prev_requested.emit()
        else:
            self.nav_next_requested.emit()

    # ── Toolbar-Gruppen ───────────────────────────────────────────────────────

    def _on_group_toggled(self, key: str, checked: bool):
        if checked:
            for k, act in self._actions.items():
                if k != key:
                    act.setChecked(False)
            if key == "abspielen":
                self._player.play()
        trim = key == "schneiden" and checked
        self._trim_controls.setVisible(trim)
        self._range_slider.set_trim_mode(trim)
        self._save_btn.setEnabled(trim)

    # ── Wiedergabe ────────────────────────────────────────────────────────────

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _stop(self):
        self._player.stop()
        self._player.setPosition(0)

    def _step_frame(self, direction: int):
        frame_ms = max(1, int(1000.0 / self._fps))
        new_pos  = max(0, min(self._player.position() + direction * frame_ms,
                              self._player.duration()))
        self._player.pause()
        self._player.setPosition(new_pos)

    def _toggle_loop(self, checked: bool):
        self._loop = checked
        self._loop_btn.setToolTip(
            "Endlos wiederholen: ein" if checked else "Endlos wiederholen: aus"
        )

    def _on_speed_changed(self, index: int):
        self._player.setPlaybackRate([0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0][index])

    def _toggle_mute(self):
        self._muted = not self._muted
        if self._muted:
            self._pre_mute_vol = self._vol_slider.value()
            self._audio.setVolume(0.0)
            self._mute_btn.setText("🔇")
            self._mute_btn.setToolTip("Ton einschalten")
        else:
            self._vol_slider.setValue(self._pre_mute_vol)
            self._audio.setVolume(self._pre_mute_vol / 100.0)
            self._mute_btn.setText("🔊")
            self._mute_btn.setToolTip("Stummschalten")

    def _on_vol_changed(self, value: int):
        if not self._muted:
            self._audio.setVolume(value / 100.0)

    def _toggle_fullscreen(self):
        self._video_widget.setFullScreen(not self._video_widget.isFullScreen())

    def _on_seek(self, position: int):
        self._player.setPosition(position)

    def _on_position_changed(self, position: int):
        if not self._seek_slider.isSliderDown():
            self._seek_slider.setValue(position)
        dur = self._player.duration()
        self._time_label.setText(f"{_ms_to_str(position)} / {_ms_to_str(dur)}")
        self._range_slider.set_position(position)

    def _on_duration_changed(self, duration: int):
        self._seek_slider.setRange(0, duration)
        self._range_slider.set_duration(duration)
        self._out_field.setText(_ms_to_str(duration))
        self._dur_lbl.setText(f"Dauer: {_ms_to_str(duration)}")
        self._time_label.setText(f"0:00.000 / {_ms_to_str(duration)}")
        # Thumbnails werden in showEvent geladen (Layout ist dann fertig)

    def _reload_thumbnails(self):
        if not self._file_path:
            return
        w = self._range_slider.width()
        if w < 50:
            w = self.width() - 20   # Fallback: eigene Breite
        w = max(100, w)
        self._last_thumb_w = w
        worker = _ThumbStripWorker(self._file_path, slider_width=w,
                                   height=RangeSlider.TRACK_H)
        worker.signals.done.connect(self._range_slider.set_thumbnails)
        self._thumb_pool.start(worker)

    def showEvent(self, event):
        super().showEvent(event)
        # Nach erstem sized-Signal: bei Folgebesuchen Thumbnails neu laden
        if self._file_path and self._range_slider._sized:
            self._reload_thumbnails()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Thumbnails neu laden wenn sich die Breite stark ändert
        new_w = self._range_slider.width()
        if abs(new_w - getattr(self, "_last_thumb_w", 0)) > 40:
            self._last_thumb_w = new_w
            QTimer.singleShot(300, self._reload_thumbnails)

    def _on_state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play_btn.setText("⏸" if playing else "▶")

    def _on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self._show_first_frame:
            self._show_first_frame = False
            self._player.play()
            QTimer.singleShot(80, self._player.pause)
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._loop:
                self._player.setPosition(0)
                self._player.play()
            else:
                self._player.setPosition(0)
                self._player.play()
                QTimer.singleShot(80, self._player.pause)

    # ── Schnitt: RangeSlider ──────────────────────────────────────────────────

    def _on_range_in(self, ms: int):
        self._in_field.setText(_ms_to_str(ms))
        self._update_dur_label()

    def _on_range_out(self, ms: int):
        self._out_field.setText(_ms_to_str(ms))
        self._update_dur_label()

    def _on_range_seek(self, ms: int):
        self._player.setPosition(ms)

    def _update_dur_label(self):
        d = self._range_slider.get_out() - self._range_slider.get_in()
        self._dur_lbl.setText(f"Dauer: {_ms_to_str(d)}")

    # ── Schnitt: Hier-setzen Buttons ─────────────────────────────────────────

    def _set_trim_start(self):
        ms = self._player.position()
        self._range_slider.set_in(ms)
        self._in_field.setText(_ms_to_str(ms))
        self._update_dur_label()

    def _set_trim_end(self):
        ms = self._player.position()
        self._range_slider.set_out(ms)
        self._out_field.setText(_ms_to_str(ms))
        self._update_dur_label()

    # ── Schnitt: Editierbare Zeitfelder ──────────────────────────────────────

    def _on_in_field_entered(self):
        ms = _str_to_ms(self._in_field.text())
        if ms is not None:
            self._range_slider.set_in(ms)
            self._in_field.setText(_ms_to_str(self._range_slider.get_in()))
            self._update_dur_label()

    def _on_out_field_entered(self):
        ms = _str_to_ms(self._out_field.text())
        if ms is not None:
            self._range_slider.set_out(ms)
            self._out_field.setText(_ms_to_str(self._range_slider.get_out()))
            self._update_dur_label()

    # ── Schnitt: Ausführen ────────────────────────────────────────────────────

    def _on_remove_mode_toggled(self, checked: bool):
        self._range_slider.set_remove_mode(checked)

    def _update_header_btns(self):
        self._undo_btn.setEnabled(len(self._video_history) > 0)
        self._restore_btn.setEnabled(
            self._original_path is not None and
            self._file_path != self._original_path
        )

    def _undo(self):
        if not self._video_history:
            return
        prev = self._video_history.pop()
        self._file_path = prev
        self._file_label.setText(prev.name)
        self._fps = _detect_fps(prev)
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(str(prev)))
        self._range_slider.set_thumbnails([])
        self._show_first_frame = True
        self._update_header_btns()

    def _restore_original(self):
        if self._original_path is None:
            return
        self._video_history.clear()
        self._file_path = self._original_path
        self._file_label.setText(self._original_path.name)
        self._fps = _detect_fps(self._original_path)
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(str(self._original_path)))
        self._range_slider.set_thumbnails([])
        self._show_first_frame = True
        self._update_header_btns()

    def _do_trim(self):
        if not self._file_path:
            return
        in_ms  = self._range_slider.get_in()
        out_ms = self._range_slider.get_out()

        if out_ms <= in_ms:
            QMessageBox.warning(self, "Ungültige Zeitpunkte",
                                "Endpunkt muss nach dem Startpunkt liegen.")
            return

        remove_mode = self._remove_btn.isChecked()
        lossless    = self._rb_lossless.isChecked()
        tag    = "ohne_mittelteil" if remove_mode else "schnitt"
        parent = self._file_path.parent
        stem   = self._file_path.stem
        ext    = self._file_path.suffix
        # Nächste freie Nummer suchen: video_schnitt_01.mp4, _02, …
        n = 1
        while True:
            out_path = parent / f"{stem}_{tag}_{n:02d}{ext}"
            if not out_path.exists():
                break
            n += 1

        import subprocess, tempfile
        self._player.stop()
        self._cut_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._cut_btn.setText("Läuft …")

        try:
            if remove_mode:
                # Zwei Teile herausschneiden und zusammenfügen
                with tempfile.TemporaryDirectory() as tmp:
                    import os
                    p1 = os.path.join(tmp, "part1" + self._file_path.suffix)
                    p2 = os.path.join(tmp, "part2" + self._file_path.suffix)
                    concat = os.path.join(tmp, "concat.txt")

                    if lossless:
                        # Teil 1: Anfang bis IN
                        subprocess.run(
                            ["ffmpeg", "-y", "-i", str(self._file_path),
                             "-to", _ms_to_ffmpeg(in_ms), "-c", "copy", p1],
                            capture_output=True, timeout=300)
                        # Teil 2: OUT bis Ende
                        subprocess.run(
                            ["ffmpeg", "-y", "-i", str(self._file_path),
                             "-ss", _ms_to_ffmpeg(out_ms), "-c", "copy", p2],
                            capture_output=True, timeout=300)
                        with open(concat, "w") as f:
                            f.write(f"file '{p1}'\nfile '{p2}'\n")
                        result = subprocess.run(
                            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                             "-i", concat, "-c", "copy", str(out_path)],
                            capture_output=True, timeout=300)
                    else:
                        in_s  = in_ms  / 1000.0
                        out_s = out_ms / 1000.0
                        fc = (
                            f"[0:v]trim=start=0:end={in_s},setpts=PTS-STARTPTS[v1];"
                            f"[0:a]atrim=start=0:end={in_s},asetpts=PTS-STARTPTS[a1];"
                            f"[0:v]trim=start={out_s},setpts=PTS-STARTPTS[v2];"
                            f"[0:a]atrim=start={out_s},asetpts=PTS-STARTPTS[a2];"
                            f"[v1][a1][v2][a2]concat=n=2:v=1:a=1[v][a]"
                        )
                        result = subprocess.run(
                            ["ffmpeg", "-y", "-i", str(self._file_path),
                             "-filter_complex", fc,
                             "-map", "[v]", "-map", "[a]",
                             "-c:v", "libx264", "-c:a", "aac", "-crf", "18",
                             str(out_path)],
                            capture_output=True, timeout=600)
            else:
                # Bereich behalten
                codec_args = ["-c", "copy"] if lossless else ["-c:v", "libx264", "-c:a", "aac", "-crf", "18"]
                cmd = ["ffmpeg", "-y",
                       "-ss", _ms_to_ffmpeg(in_ms),
                       "-to", _ms_to_ffmpeg(out_ms),
                       "-i", str(self._file_path)] + codec_args + [str(out_path)]
                result = subprocess.run(cmd, capture_output=True, timeout=300)

            if result.returncode == 0:
                # Alten Pfad in Undo-History legen, neue Datei laden
                self._video_history.append(self._file_path)
                self._file_path = out_path
                self._file_label.setText(out_path.name)
                self._fps = _detect_fps(out_path)
                self._player.setSource(QUrl.fromLocalFile(str(out_path)))
                self._range_slider.set_thumbnails([])
                self._show_first_frame = True
                self._update_header_btns()
                QMessageBox.information(self, "Fertig",
                    f"Gespeichert als:\n{out_path.name}")
            else:
                err = result.stderr.decode(errors="replace")[-600:]
                QMessageBox.critical(self, "Fehler beim Schneiden",
                                     f"ffmpeg meldet:\n{err}")
        except subprocess.TimeoutExpired:
            QMessageBox.critical(self, "Timeout", "Der Schnitt hat zu lange gedauert.")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", str(e))
        finally:
            self._cut_btn.setEnabled(True)
            self._save_btn.setEnabled(True)
            self._cut_btn.setText("✂ Schneiden")


# ── Hilfswidget ───────────────────────────────────────────────────────────────

def _lbl(text: str, style: str = "") -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(style)
    return l


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _detect_fps(path: Path) -> float:
    import subprocess, json
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "v:0", str(path)],
            capture_output=True, timeout=5)
        for stream in json.loads(result.stdout).get("streams", []):
            r = stream.get("r_frame_rate", "")
            if "/" in r:
                num, den = r.split("/")
                if int(den) != 0:
                    return float(int(num)) / float(int(den))
    except Exception:
        pass
    return 30.0


def _ms_to_str(ms: int) -> str:
    if ms <= 0:
        return "0:00.000"
    s_total = ms // 1000
    return f"{s_total // 60}:{s_total % 60:02d}.{ms % 1000:03d}"


def _ms_to_ffmpeg(ms: int) -> str:
    if ms <= 0:
        return "00:00:00.000"
    s_total = ms // 1000
    h = s_total // 3600
    m = (s_total % 3600) // 60
    s = s_total % 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms % 1000:03d}"


def _str_to_ms(text: str) -> int | None:
    """Parst m:ss.mmm oder m:ss oder ss.mmm → Millisekunden. None bei Fehler."""
    import re
    text = text.strip()
    m = re.fullmatch(r"(\d+):(\d{2})\.(\d{1,3})", text)
    if m:
        minutes, secs, frac = int(m.group(1)), int(m.group(2)), m.group(3)
        ms = int(frac.ljust(3, "0"))
        return minutes * 60_000 + secs * 1000 + ms
    m = re.fullmatch(r"(\d+):(\d{2})", text)
    if m:
        return int(m.group(1)) * 60_000 + int(m.group(2)) * 1000
    m = re.fullmatch(r"(\d+)\.(\d{1,3})", text)
    if m:
        return int(m.group(1)) * 1000 + int(m.group(2).ljust(3, "0"))
    return None
