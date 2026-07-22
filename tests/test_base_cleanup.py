"""
Testcase: Base-Image-Bereinigung – Tool-Wechsel räumt unbenutzte Bases auf
"""
import sys
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))


class MockEditorBases:
    """Simuliert die Base-Image-Attribute von EditorScreen für isolierte Tests."""

    def __init__(self):
        self._fine_rotate_base = None
        self._fine_rotate_total = 0.0
        self._adj_base = None
        self._adj_preview_img = None
        self._adj_scale_factor = 1
        self._ent_base = None
        self._ent_base_small = None
        self._focus_base = None
        self._focus_mask = None
        self._quad_base = None
        self._crop_base = None
        self._hg_base = None
        self._hg_mask = None
        self._distortion_base = None

    def _clear_bases(self, keep=None):
        """Löscht alle Tool-Basis-Bilder außer dem angegebenen."""
        if keep != "fine_rotate":
            self._fine_rotate_base = None
            self._fine_rotate_total = 0.0
        if keep != "adj":
            self._adj_base = None
            self._adj_preview_img = None
            self._adj_scale_factor = 1
        if keep != "ent":
            self._ent_base = None
            self._ent_base_small = None
            self._distortion_base = None
        if keep != "focus":
            self._focus_base = None
            self._focus_mask = None
        if keep != "quad":
            self._quad_base = None
        if keep != "crop":
            self._crop_base = None
        if keep != "hg":
            self._hg_base = None
            self._hg_mask = None


def _dummy_image():
    return Image.new("RGB", (100, 100), (128, 128, 128))


# ── Tests ────────────────────────────────────────────────────────────────────

def test_clear_all():
    """Ohne keep wird alles gelöscht."""
    m = MockEditorBases()
    m._fine_rotate_base = _dummy_image()
    m._adj_base = _dummy_image()
    m._ent_base = _dummy_image()
    m._focus_base = _dummy_image()
    m._quad_base = _dummy_image()
    m._crop_base = _dummy_image()
    m._hg_base = _dummy_image()
    m._clear_bases(keep=None)
    assert m._fine_rotate_base is None
    assert m._adj_base is None
    assert m._ent_base is None
    assert m._focus_base is None
    assert m._quad_base is None
    assert m._crop_base is None
    assert m._hg_base is None


def test_keep_adj():
    """keep='adj' behält nur adj_base."""
    m = MockEditorBases()
    adj = _dummy_image()
    m._adj_base = adj
    m._fine_rotate_base = _dummy_image()
    m._ent_base = _dummy_image()
    m._crop_base = _dummy_image()
    m._clear_bases(keep="adj")
    assert m._adj_base is adj
    assert m._fine_rotate_base is None
    assert m._ent_base is None
    assert m._crop_base is None


def test_keep_ent():
    """keep='ent' behält Entzerren-Bases."""
    m = MockEditorBases()
    ent = _dummy_image()
    m._ent_base = ent
    m._adj_base = _dummy_image()
    m._clear_bases(keep="ent")
    assert m._ent_base is ent
    assert m._adj_base is None


def test_keep_fine_rotate():
    """keep='fine_rotate' behält Rotations-Basis und Winkel."""
    m = MockEditorBases()
    rot = _dummy_image()
    m._fine_rotate_base = rot
    m._fine_rotate_total = 15.0
    m._adj_base = _dummy_image()
    m._clear_bases(keep="fine_rotate")
    assert m._fine_rotate_base is rot
    assert m._fine_rotate_total == 15.0
    assert m._adj_base is None


def test_keep_crop():
    """keep='crop' behält nur crop_base."""
    m = MockEditorBases()
    crop = _dummy_image()
    m._crop_base = crop
    m._adj_base = _dummy_image()
    m._ent_base = _dummy_image()
    m._clear_bases(keep="crop")
    assert m._crop_base is crop
    assert m._adj_base is None
    assert m._ent_base is None


def test_adj_preview_cleared():
    """Preview-Image und Scale-Factor werden bei adj-Cleanup zurückgesetzt."""
    m = MockEditorBases()
    m._adj_base = _dummy_image()
    m._adj_preview_img = _dummy_image()
    m._adj_scale_factor = 3
    m._clear_bases(keep="ent")
    assert m._adj_base is None
    assert m._adj_preview_img is None
    assert m._adj_scale_factor == 1


if __name__ == "__main__":
    for name, obj in list(globals().items()):
        if name.startswith("test_") and callable(obj):
            obj()
            print(f"PASS: {name}")
