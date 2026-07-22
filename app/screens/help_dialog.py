import os
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton
from PyQt6.QtGui import QAction

class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Benutzeranleitung")
        self.resize(800, 600)
        
        layout = QVBoxLayout(self)
        
        self.text_browser = QTextBrowser(self)
        self.text_browser.setOpenExternalLinks(True)
        self.text_browser.setStyleSheet("""
            QTextBrowser {
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
                line-height: 1.5;
                padding: 12px;
            }
        """)

        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        docs_dir = os.path.join(base_dir, "docs")
        manual_path = os.path.join(docs_dir, "USER_MANUAL.md")

        self.text_browser.setSearchPaths([docs_dir])

        if os.path.exists(manual_path):
            with open(manual_path, "r", encoding="utf-8") as f:
                markdown_content = f.read()
            self.text_browser.setMarkdown(markdown_content)
        else:
            self.text_browser.setHtml("<h2>Fehler</h2><p>Die Benutzeranleitung konnte nicht gefunden werden.</p>")
            
        layout.addWidget(self.text_browser)
        
        close_btn = QPushButton("Schließen", self)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
