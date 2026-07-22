"""
Testcase: Adjustment-Vorschau – Korrektheit und Performance
Stellt sicher, dass Bildanpassungen korrekt berechnet werden und
Preview-Downsampling die Ergebnisqualität nicht zerstört.
"""
import sys
import time
import numpy as np
from pathlib import Path
from PIL import Image, ImageEnhance

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_gradient_image(w=400, h=300):
    """Erzeugt ein Gradientenbild für realistische Tests."""
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :, 0] = np.linspace(0, 255, w, dtype=np.uint8)  # R-Gradient
    arr[:, :, 1] = np.linspace(0, 255, h, dtype=np.uint8)[:, None]  # G-Gradient
    arr[:, :, 2] = 128
    return Image.fromarray(arr)


def _shift_channel(ch, amount):
    """Kanal-Verschiebung (wie in editor_screen.py)."""
    import numpy as np
    arr = np.array(ch, dtype=np.int16)
    arr = np.clip(arr + amount, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, mode="L")


def compute_adjustments_standalone(base, brightness=100, contrast=100,
                                   saturation=100, sharpness=100,
                                   exposure=0, blackpoint=0, shadows=0,
                                   warmth=0, hue=0):
    """Standalone-Version von _compute_adjustments für Tests.
    Exakt gleiche Logik wie in editor_screen.py."""
    import cv2
    img = base.convert("RGB")
    img = ImageEnhance.Brightness(img).enhance(brightness / 100.0)
    img = ImageEnhance.Contrast(img).enhance(contrast / 100.0)
    img = ImageEnhance.Color(img).enhance(saturation / 100.0)
    if sharpness != 100:
        arr_sharp = np.array(img)
        if sharpness > 100:
            amount = (sharpness - 100) / 100.0 * 3.0
            blurred = cv2.GaussianBlur(arr_sharp, (0, 0), 2.0)
            arr_sharp = cv2.addWeighted(arr_sharp, 1.0 + amount, blurred, -amount, 0)
        else:
            sigma = (100 - sharpness) / 100.0 * 6.0
            arr_sharp = cv2.GaussianBlur(arr_sharp, (0, 0), max(0.1, sigma))
        img = Image.fromarray(np.clip(arr_sharp, 0, 255).astype(np.uint8))
    arr = np.array(img, dtype=np.float32)
    ev = exposure / 100.0
    arr = arr * (2 ** ev)
    bp = blackpoint / 100.0 * 255
    if bp > 0:
        arr = np.clip((arr - bp) / max(255 - bp, 1) * 255, 0, 255)
    shadow = shadows / 100.0
    if shadow != 0:
        arr = arr + shadow * (1.0 - arr / 255.0) * 80
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    if warmth != 0:
        r, g, b = img.split()
        img = Image.merge("RGB", (
            _shift_channel(r, int(warmth * 0.6)),
            g,
            _shift_channel(b, -int(warmth * 0.4)),
        ))
    return img


# ── Tests ────────────────────────────────────────────────────────────────────

def test_identity_returns_unchanged():
    """Neutrale Werte dürfen das Bild nicht verändern."""
    img = _make_gradient_image(200, 150)
    result = compute_adjustments_standalone(img)
    # Bei neutralen Werten sollte das Ergebnis identisch sein
    diff = np.abs(np.array(result, dtype=np.int16) - np.array(img.convert("RGB"), dtype=np.int16))
    assert diff.max() <= 1, f"Identitätstransformation verändert Bild: max diff={diff.max()}"


def test_brightness_increases():
    """Helligkeit > 100 muss den Durchschnitt erhöhen."""
    img = _make_gradient_image(200, 150)
    result = compute_adjustments_standalone(img, brightness=200)
    mean_before = np.array(img.convert("RGB")).mean()
    mean_after = np.array(result).mean()
    assert mean_after > mean_before, "Helligkeit 200 muss heller sein"


def test_brightness_decreases():
    """Helligkeit < 100 muss den Durchschnitt senken."""
    img = _make_gradient_image(200, 150)
    result = compute_adjustments_standalone(img, brightness=50)
    mean_before = np.array(img.convert("RGB")).mean()
    mean_after = np.array(result).mean()
    assert mean_after < mean_before, "Helligkeit 50 muss dunkler sein"


def test_contrast_changes():
    """Kontrast-Änderung muss die Standardabweichung beeinflussen."""
    img = _make_gradient_image(200, 150)
    result_high = compute_adjustments_standalone(img, contrast=200)
    result_low = compute_adjustments_standalone(img, contrast=50)
    std_high = np.array(result_high).std()
    std_low = np.array(result_low).std()
    assert std_high > std_low, "Hoher Kontrast muss größere Streuung haben"


def test_warmth_shifts_colors():
    """Wärme > 0 muss Rot erhöhen und Blau senken."""
    img = Image.new("RGB", (100, 100), (128, 128, 128))
    result = compute_adjustments_standalone(img, warmth=50)
    r, g, b = np.array(result).mean(axis=(0, 1))
    assert r > 128, "Wärme muss Rot erhöhen"
    assert b < 128, "Wärme muss Blau senken"


def test_exposure_brightens():
    """Positive Exposure muss das Bild aufhellen."""
    img = Image.new("RGB", (100, 100), (64, 64, 64))
    result = compute_adjustments_standalone(img, exposure=100)
    mean_after = np.array(result).mean()
    assert mean_after > 64, "Positive Exposure muss aufhellen"


def test_preview_vs_fullres_similar():
    """Preview auf reduzierter Auflösung muss dem Full-Res Ergebnis ähneln."""
    img = _make_gradient_image(4000, 3000)
    # Full-res Berechnung
    full = compute_adjustments_standalone(img, brightness=150, contrast=130,
                                          saturation=80, warmth=30)
    # Preview: verkleinern, berechnen, hochskalieren
    factor = max(img.size) // 1500
    small = img.resize((img.width // factor, img.height // factor), Image.BILINEAR)
    preview = compute_adjustments_standalone(small, brightness=150, contrast=130,
                                              saturation=80, warmth=30)
    preview_up = preview.resize(img.size, Image.BILINEAR)
    # Vergleich: mittlere Abweichung pro Pixel
    diff = np.abs(np.array(full, dtype=np.float32) - np.array(preview_up, dtype=np.float32))
    mean_diff = diff.mean()
    assert mean_diff < 10, f"Preview weicht zu stark ab: mean_diff={mean_diff:.1f}"


def test_preview_is_faster():
    """Berechnung auf 1500px muss schneller sein als auf 4000px."""
    img_large = _make_gradient_image(4000, 3000)
    factor = max(img_large.size) // 1500
    img_small = img_large.resize(
        (img_large.width // factor, img_large.height // factor), Image.BILINEAR)

    # Warm-up
    compute_adjustments_standalone(img_small, brightness=150, contrast=130)

    t0 = time.perf_counter()
    for _ in range(3):
        compute_adjustments_standalone(img_large, brightness=150, contrast=130,
                                        saturation=80, sharpness=150)
    t_large = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(3):
        compute_adjustments_standalone(img_small, brightness=150, contrast=130,
                                        saturation=80, sharpness=150)
    t_small = time.perf_counter() - t0

    speedup = t_large / max(t_small, 0.001)
    print(f"  Full-res: {t_large:.3f}s, Preview: {t_small:.3f}s, Speedup: {speedup:.1f}x")
    assert speedup > 1.5, f"Preview nur {speedup:.1f}x schneller — zu wenig"


def test_output_mode_matches_input():
    """Ergebnis muss RGB sein, auch bei RGBA-Input."""
    img = Image.new("RGBA", (100, 100), (128, 128, 128, 200))
    result = compute_adjustments_standalone(img, brightness=150)
    assert result.mode == "RGB"


if __name__ == "__main__":
    for name, obj in list(globals().items()):
        if name.startswith("test_") and callable(obj):
            obj()
            print(f"PASS: {name}")
