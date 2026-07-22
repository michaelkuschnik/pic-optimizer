"""
Testcase: CompressedImageStore – Undo-History mit zlib-Komprimierung
Stellt sicher, dass Push/Pop verlustfrei funktioniert und weniger RAM braucht.
"""
import sys
import zlib
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_image(w=200, h=150, color=(255, 0, 0), mode="RGB"):
    return Image.new(mode, (w, h), color)


# ── Hilfsklasse (wird in Phase 1 nach editor_screen.py verschoben) ──────────

class CompressedImageStore:
    """Speichert PIL-Bilder als zlib-komprimierte Rohdaten."""

    def __init__(self, max_entries: int = 30):
        self._entries: list[tuple[bytes, str, tuple[int, int]]] = []
        self._max = max_entries

    def push(self, img: Image.Image) -> None:
        if len(self._entries) >= self._max:
            self._entries.pop(0)
        raw = img.tobytes()
        compressed = zlib.compress(raw, level=1)
        self._entries.append((compressed, img.mode, img.size))

    def pop(self) -> Image.Image:
        compressed, mode, size = self._entries.pop()
        raw = zlib.decompress(compressed)
        return Image.frombytes(mode, size, raw)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return len(self._entries) > 0


# ── Tests ────────────────────────────────────────────────────────────────────

def test_push_pop_preserves_rgb():
    """Push und Pop muss exakt dasselbe Bild zurückgeben (RGB)."""
    store = CompressedImageStore()
    img = _make_image(300, 200, (42, 128, 200), "RGB")
    original_bytes = img.tobytes()
    store.push(img)
    restored = store.pop()
    assert restored.mode == "RGB"
    assert restored.size == (300, 200)
    assert restored.tobytes() == original_bytes


def test_push_pop_preserves_rgba():
    """Push und Pop muss exakt dasselbe Bild zurückgeben (RGBA)."""
    store = CompressedImageStore()
    img = _make_image(250, 180, (10, 20, 30, 128), "RGBA")
    original_bytes = img.tobytes()
    store.push(img)
    restored = store.pop()
    assert restored.mode == "RGBA"
    assert restored.size == (250, 180)
    assert restored.tobytes() == original_bytes


def test_push_pop_multiple_lifo():
    """Pop gibt Bilder in umgekehrter Reihenfolge zurück (LIFO)."""
    store = CompressedImageStore()
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    originals = []
    for c in colors:
        img = _make_image(100, 100, c)
        originals.append(img.tobytes())
        store.push(img)
    assert len(store) == 3
    for expected in reversed(originals):
        restored = store.pop()
        assert restored.tobytes() == expected


def test_max_entries_enforced():
    """Älteste Einträge werden bei Überschreitung entfernt."""
    store = CompressedImageStore(max_entries=5)
    for i in range(10):
        store.push(_make_image(50, 50, (i * 25, 0, 0)))
    assert len(store) == 5


def test_clear():
    """Clear leert den Store komplett."""
    store = CompressedImageStore()
    for _ in range(5):
        store.push(_make_image())
    store.clear()
    assert len(store) == 0


def test_bool_empty():
    """Leerer Store ist falsy."""
    store = CompressedImageStore()
    assert not store


def test_bool_nonempty():
    """Nicht-leerer Store ist truthy."""
    store = CompressedImageStore()
    store.push(_make_image())
    assert store


def test_memory_reduction():
    """Komprimierte Daten müssen deutlich kleiner als Rohbilder sein."""
    store = CompressedImageStore()
    img = _make_image(2000, 2000, (128, 64, 32))
    raw_size = len(img.tobytes())  # 2000*2000*3 = 12 MB
    for _ in range(5):
        store.push(img)
    total_compressed = sum(len(entry[0]) for entry in store._entries)
    # Einfarbiges Bild komprimiert extrem gut, aber auch natürliche Bilder
    # sollten mindestens 50% Einsparung bringen
    assert total_compressed < raw_size * 5 * 0.5, (
        f"Komprimierung nicht effektiv genug: {total_compressed} vs {raw_size * 5}"
    )


def test_push_does_not_modify_original():
    """Push darf das Originalbild nicht verändern."""
    store = CompressedImageStore()
    img = _make_image(100, 100, (42, 42, 42))
    original_bytes = img.tobytes()
    store.push(img)
    assert img.tobytes() == original_bytes


if __name__ == "__main__":
    for name, obj in list(globals().items()):
        if name.startswith("test_") and callable(obj):
            obj()
            print(f"PASS: {name}")
