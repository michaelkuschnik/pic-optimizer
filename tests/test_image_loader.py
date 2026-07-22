"""
Testcase: image_loader – Format-Routing und Backup-Logik
"""
import os
import shutil
import tempfile
from pathlib import Path
from PIL import Image

# Projekt-Root ins sys.path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.utils.image_loader import load_image, make_thumbnail, is_image, is_video, is_supported


def _make_test_jpeg(path: Path, color=(255, 0, 0)):
    img = Image.new("RGB", (100, 80), color)
    img.save(str(path), "JPEG")


def _make_test_png_rgba(path: Path):
    img = Image.new("RGBA", (100, 80), (0, 255, 0, 128))
    img.save(str(path), "PNG")


def test_load_jpeg_returns_rgb():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.jpg"
        _make_test_jpeg(p)
        img = load_image(p)
        assert img.mode == "RGB", f"Erwartet RGB, bekam {img.mode}"
        assert img.size == (100, 80)


def test_load_png_rgba_preserved():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.png"
        _make_test_png_rgba(p)
        img = load_image(p)
        assert img.mode == "RGBA", f"Erwartet RGBA, bekam {img.mode}"


def test_make_thumbnail_size():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.jpg"
        _make_test_jpeg(p)
        thumb = make_thumbnail(p, (50, 50))
        assert thumb.width <= 50 and thumb.height <= 50


def test_is_image():
    assert is_image("foto.jpg")
    assert is_image("foto.JPG")
    assert is_image("foto.png")
    assert not is_image("video.mp4")
    assert not is_image("doc.pdf")


def test_is_video():
    assert is_video("clip.mp4")
    assert is_video("film.MOV")
    assert not is_video("foto.jpg")


def test_is_supported():
    assert is_supported("foto.jpg")
    assert is_supported("clip.mp4")
    assert not is_supported("doc.pdf")


def test_no_unclosed_file_handles():
    """Stellt sicher, dass Image.open keine offenen Handles hinterlässt."""
    import gc
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.jpg"
        _make_test_jpeg(p)
        for _ in range(10):
            img = load_image(p)
            del img
        gc.collect()
        # Datei muss löschbar sein (kein Lock)
        p.unlink()
        assert not p.exists()


if __name__ == "__main__":
    test_load_jpeg_returns_rgb();        print("PASS: test_load_jpeg_returns_rgb")
    test_load_png_rgba_preserved();      print("PASS: test_load_png_rgba_preserved")
    test_make_thumbnail_size();          print("PASS: test_make_thumbnail_size")
    test_is_image();                     print("PASS: test_is_image")
    test_is_video();                     print("PASS: test_is_video")
    test_is_supported();                 print("PASS: test_is_supported")
    test_no_unclosed_file_handles();     print("PASS: test_no_unclosed_file_handles")
