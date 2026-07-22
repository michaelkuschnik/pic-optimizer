"""
Extrahiert einen Thumbnail-Frame aus einer Videodatei via FFmpeg.
"""
import subprocess
import tempfile
from pathlib import Path
from PIL import Image


def extract_video_thumbnail(video_path: str | Path, size: tuple[int, int] = (200, 200)) -> Image.Image | None:
    """
    Extrahiert den ersten brauchbaren Frame eines Videos als PIL.Image.
    Benötigt ffmpeg im PATH.
    """
    video_path = Path(video_path)
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", "00:00:01",
                "-i", str(video_path),
                "-frames:v", "1",
                "-q:v", "2",
                tmp_path,
            ],
            capture_output=True,
            timeout=15,
        )

        if result.returncode == 0 and Path(tmp_path).exists():
            img = Image.open(tmp_path).convert("RGB")
            img.thumbnail(size, Image.LANCZOS)
            img.load()
            Path(tmp_path).unlink(missing_ok=True)
            return img

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass

    return None
