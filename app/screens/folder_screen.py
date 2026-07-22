from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QFileDialog, QHBoxLayout
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont


class FolderScreen(QWidget):
    folder_selected = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._last_folder = ""
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(24)

        title = QLabel("Optimizer")
        font = QFont()
        font.setPointSize(42)
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #2c3e50;")

        subtitle = QLabel("Wähle einen Ordner, um Bilder und Videos anzuzeigen")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #7f8c8d; font-size: 15px;")

        # Letzter Ordner: anzeigen + direkt öffnen
        self._last_folder_label = QLabel()
        self._last_folder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._last_folder_label.setStyleSheet("color: #555; font-size: 13px;")
        self._last_folder_label.hide()

        self._btn_last = QPushButton()
        self._btn_last.setFixedSize(320, 44)
        self._btn_last.setStyleSheet(
            "QPushButton {"
            "  background-color: #27ae60;"
            "  color: white;"
            "  border-radius: 8px;"
            "  font-size: 14px;"
            "}"
            "QPushButton:hover { background-color: #219150; }"
            "QPushButton:pressed { background-color: #1a7a42; }"
        )
        self._btn_last.clicked.connect(self._open_last_folder)
        self._btn_last.hide()

        btn = QPushButton("Anderen Ordner auswählen")
        btn.setFixedSize(220, 50)
        btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #3498db;"
            "  color: white;"
            "  border-radius: 8px;"
            "  font-size: 15px;"
            "}"
            "QPushButton:hover { background-color: #2980b9; }"
            "QPushButton:pressed { background-color: #1a6fa0; }"
        )
        btn.clicked.connect(self._choose_folder)
        self._btn_choose = btn

        hint = QLabel("Tipp: In den Zielordner navigieren, dann \"Ordner auswählen\" klicken —\nauch wenn der Ordner nur Bilder (keine Unterordner) enthält.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #b0b0b0; font-size: 12px;")

        last_row = QHBoxLayout()
        last_row.addStretch()
        last_row.addWidget(self._btn_last)
        last_row.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn)
        btn_row.addStretch()

        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(16)
        layout.addWidget(self._last_folder_label)
        layout.addLayout(last_row)
        layout.addSpacing(8)
        layout.addLayout(btn_row)
        layout.addSpacing(8)
        layout.addWidget(hint)
        layout.addStretch()

    def set_last_folder(self, folder_path: str):
        self._last_folder = folder_path
        self._last_folder_label.setText(f"Zuletzt: {folder_path}")
        self._last_folder_label.show()
        # Ordnername als Button-Text
        from pathlib import Path
        name = Path(folder_path).name or folder_path
        self._btn_last.setText(f"Letzten Ordner öffnen: {name}")
        self._btn_last.show()
        # "Anderen Ordner auswählen" statt "Ordner auswählen"
        self._btn_choose.setText("Anderen Ordner auswählen")

    def _open_last_folder(self):
        if self._last_folder:
            self.folder_selected.emit(self._last_folder)

    def _choose_folder(self):
        from pathlib import Path
        if self._last_folder:
            parent = str(Path(self._last_folder).parent)
            start = parent if parent != self._last_folder else self._last_folder
        else:
            start = ""
        path = QFileDialog.getExistingDirectory(self, "Ordner auswählen", start)
        if path:
            self.folder_selected.emit(path)
