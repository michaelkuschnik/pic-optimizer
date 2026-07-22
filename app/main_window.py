from pathlib import Path
from PyQt6.QtWidgets import QMainWindow, QStackedWidget
from PyQt6.QtCore import QSettings


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Optimizer")
        self.setMinimumSize(960, 640)
        self.resize(1280, 800)

        self._settings = QSettings("Optimizer", "Optimizer")

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self._setup_menu()
        self._setup_screens()

    def _setup_menu(self):
        menubar = self.menuBar()
        help_menu = menubar.addMenu("Hilfe")
        
        action_manual = help_menu.addAction("Anleitung")
        action_manual.triggered.connect(self._show_help)
        
    def _show_help(self):
        from app.screens.help_dialog import HelpDialog
        dlg = HelpDialog(self)
        dlg.exec()

    def _setup_screens(self):
        from app.screens.folder_screen import FolderScreen
        from app.screens.gallery_screen import GalleryScreen
        from app.screens.editor_screen import EditorScreen
        from app.screens.video_editor_screen import VideoEditorScreen
        from app.utils.image_loader import is_video

        self._is_video = is_video

        self.folder_screen       = FolderScreen()
        self.gallery_screen      = GalleryScreen()
        self.editor_screen       = EditorScreen()
        self.video_editor_screen = VideoEditorScreen()

        self.stack.addWidget(self.folder_screen)        # index 0
        self.stack.addWidget(self.gallery_screen)       # index 1
        self.stack.addWidget(self.editor_screen)        # index 2
        self.stack.addWidget(self.video_editor_screen)  # index 3

        self._current_editor_path: str = ""

        self.folder_screen.folder_selected.connect(self._open_gallery)
        self.gallery_screen.back_requested.connect(self._show_folder_screen)
        self.gallery_screen.image_selected.connect(self._open_file)
        self.gallery_screen.video_selected.connect(self._open_file)

        self.editor_screen.back_requested.connect(self._show_gallery)
        self.editor_screen.image_saved.connect(self.gallery_screen.refresh_item)
        self.editor_screen.nav_prev_requested.connect(self._nav_prev)
        self.editor_screen.nav_next_requested.connect(self._nav_next)

        self.video_editor_screen.back_requested.connect(self._show_gallery)
        self.video_editor_screen.nav_prev_requested.connect(self._nav_prev)
        self.video_editor_screen.nav_next_requested.connect(self._nav_next)

        # Letzten Ordner als Voreinstellung im FolderScreen setzen
        last_folder = self._settings.value("last_folder", "")
        if last_folder and Path(last_folder).is_dir():
            self.folder_screen.set_last_folder(last_folder)
        self.stack.setCurrentIndex(0)

    def _open_gallery(self, folder_path: str):
        self._settings.setValue("last_folder", folder_path)
        self.gallery_screen.load_folder(folder_path)
        self.stack.setCurrentIndex(1)

    def _show_folder_screen(self):
        self.stack.setCurrentIndex(0)

    def _show_gallery(self):
        self.stack.setCurrentIndex(1)

    def _open_file(self, file_path: str):
        """Öffnet eine Datei im passenden Editor (Bild oder Video)."""
        self._current_editor_path = file_path
        if self._is_video(file_path):
            self.video_editor_screen.load_file(file_path)
            self.stack.setCurrentIndex(3)
        else:
            self.editor_screen.load_file(file_path)
            self.stack.setCurrentIndex(2)
        self._update_nav_state()

    def _update_nav_state(self):
        files = self.gallery_screen.files
        paths = [str(f) for f in files]
        try:
            idx = paths.index(self._current_editor_path)
        except ValueError:
            self.editor_screen.set_nav_state(False, False)
            self.video_editor_screen.set_nav_state(False, False)
            return
        has_prev = idx > 0
        has_next = idx < len(files) - 1
        self.editor_screen.set_nav_state(has_prev, has_next)
        self.video_editor_screen.set_nav_state(has_prev, has_next)

    def _nav_prev(self):
        files = self.gallery_screen.files
        paths = [str(f) for f in files]
        try:
            idx = paths.index(self._current_editor_path)
        except ValueError:
            return
        if idx > 0:
            self._open_file(str(files[idx - 1]))

    def _nav_next(self):
        files = self.gallery_screen.files
        paths = [str(f) for f in files]
        try:
            idx = paths.index(self._current_editor_path)
        except ValueError:
            return
        if idx < len(files) - 1:
            self._open_file(str(files[idx + 1]))
