"""
Benchmark-Script: Misst Performance-Metriken vor und nach Optimierungen.
Wird VOR und NACH jeder Phase ausgeführt, um Verbesserungen zu messen.

Aufruf:  .venv\\Scripts\\python.exe tests\\bench_performance.py
"""
import sys
import os
import time
import zlib
import tempfile
from pathlib import Path
from PIL import Image, ImageEnhance
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_gradient(w, h):
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :, 0] = np.linspace(0, 255, w, dtype=np.uint8)
    arr[:, :, 1] = np.linspace(0, 255, h, dtype=np.uint8)[:, None]
    arr[:, :, 2] = np.linspace(50, 200, h, dtype=np.uint8)[:, None]
    return Image.fromarray(arr)


def _measure_rss_mb():
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except ImportError:
        return 0.0


def bench_undo_history_memory():
    """Misst RAM-Verbrauch fuer 10 Undo-Eintraege eines 4K-Bildes."""
    img = _make_gradient(4000, 3000)
    raw_size_mb = len(img.tobytes()) / 1024 / 1024

    # Teste aktuelle Methode: list[Image.Image]
    rss_before = _measure_rss_mb()
    history = []
    for _ in range(10):
        history.append(img.copy())
    rss_after = _measure_rss_mb()
    ram_list = rss_after - rss_before

    # Teste komprimierte Methode: zlib
    history.clear()
    import gc; gc.collect()
    rss_before = _measure_rss_mb()
    compressed_history = []
    for _ in range(10):
        raw = img.tobytes()
        compressed = zlib.compress(raw, level=1)
        compressed_history.append((compressed, img.mode, img.size))
    rss_after = _measure_rss_mb()
    ram_zlib = rss_after - rss_before

    compressed_size_mb = sum(len(c[0]) for c in compressed_history) / 1024 / 1024
    raw_total_mb = raw_size_mb * 10

    print(f"  Bild: {img.size[0]}x{img.size[1]}, {raw_size_mb:.1f} MB/Bild")
    print(f"  10 Eintraege raw:        {raw_total_mb:.1f} MB theoretisch, ~{ram_list:.1f} MB RSS")
    print(f"  10 Eintraege komprimiert: {compressed_size_mb:.1f} MB Daten, ~{ram_zlib:.1f} MB RSS")
    print(f"  Einsparung:             {(1 - compressed_size_mb / raw_total_mb) * 100:.0f}%")
    return raw_total_mb, compressed_size_mb


def bench_adjustment_speed():
    """Misst Zeit fuer Adjustment-Berechnung bei verschiedenen Auflösungen."""
    from tests.test_adjustment_preview import compute_adjustments_standalone

    sizes = [(4000, 3000), (1500, 1125), (1000, 750)]
    results = {}

    for w, h in sizes:
        img = _make_gradient(w, h)
        # Warm-up
        compute_adjustments_standalone(img, brightness=150)
        t0 = time.perf_counter()
        for _ in range(5):
            compute_adjustments_standalone(img, brightness=150, contrast=130,
                                            saturation=80, sharpness=150,
                                            warmth=30, exposure=20)
        elapsed = time.perf_counter() - t0
        per_call = elapsed / 5
        results[f"{w}x{h}"] = per_call
        print(f"  {w}x{h}: {per_call*1000:.0f} ms/Aufruf")

    if len(results) >= 2:
        keys = list(results.keys())
        speedup = results[keys[0]] / results[keys[1]]
        print(f"  Speedup {keys[0]} -> {keys[1]}: {speedup:.1f}x")
    return results


def bench_thumbnail_generation():
    """Misst Zeit fuer Thumbnail-Generierung."""
    from app.utils.image_loader import make_thumbnail

    with tempfile.TemporaryDirectory() as d:
        # 20 Test-Bilder erstellen
        paths = []
        for i in range(20):
            p = Path(d) / f"test_{i:03d}.jpg"
            img = _make_gradient(2000, 1500)
            img.save(str(p), "JPEG", quality=85)
            paths.append(p)

        # Ohne Cache
        t0 = time.perf_counter()
        for p in paths:
            make_thumbnail(p, (180, 180))
        t_no_cache = time.perf_counter() - t0

        # Mit Cache
        from app.utils.thumb_cache import save_cached_thumbnail, get_cached_thumbnail
        for p in paths:
            thumb = make_thumbnail(p, (180, 180))
            save_cached_thumbnail(p, thumb)

        t0 = time.perf_counter()
        for p in paths:
            get_cached_thumbnail(p, (180, 180))
        t_cached = time.perf_counter() - t0

        print(f"  20 Thumbnails generieren:  {t_no_cache*1000:.0f} ms")
        print(f"  20 Thumbnails aus Cache:   {t_cached*1000:.0f} ms")
        if t_cached > 0:
            print(f"  Speedup:                   {t_no_cache / t_cached:.1f}x")
        return t_no_cache, t_cached


def bench_zlib_compress_decompress():
    """Misst Komprimierungs-/Dekomprimierungszeit fuer ein 4K-Bild."""
    img = _make_gradient(4000, 3000)
    raw = img.tobytes()

    t0 = time.perf_counter()
    for _ in range(10):
        compressed = zlib.compress(raw, level=1)
    t_compress = (time.perf_counter() - t0) / 10

    t0 = time.perf_counter()
    for _ in range(10):
        zlib.decompress(compressed)
    t_decompress = (time.perf_counter() - t0) / 10

    ratio = len(compressed) / len(raw) * 100
    print(f"  4K-Bild ({len(raw)/1024/1024:.1f} MB)")
    print(f"  Komprimieren (level=1):   {t_compress*1000:.1f} ms -> {len(compressed)/1024/1024:.1f} MB ({ratio:.0f}%)")
    print(f"  Dekomprimieren:           {t_decompress*1000:.1f} ms")
    return t_compress, t_decompress


def bench_histogram():
    """Misst Histogram-Berechnung mit und ohne Downsampling."""
    img = _make_gradient(4000, 3000)
    small = img.resize((1000, 750), Image.NEAREST)

    # Full-res
    t0 = time.perf_counter()
    for _ in range(20):
        rgb = img.convert("RGB")
        rgb.histogram()
    t_full = (time.perf_counter() - t0) / 20

    # Downsampled
    t0 = time.perf_counter()
    for _ in range(20):
        rgb = small.convert("RGB")
        rgb.histogram()
    t_small = (time.perf_counter() - t0) / 20

    print(f"  Histogram 4000x3000:  {t_full*1000:.2f} ms")
    print(f"  Histogram 1000x750:   {t_small*1000:.2f} ms")
    print(f"  Speedup:              {t_full / max(t_small, 0.0001):.1f}x")
    return t_full, t_small


if __name__ == "__main__":
    print("=" * 60)
    print("PERFORMANCE BENCHMARK -- pic_optimizer")
    print("=" * 60)

    print("\n1. Undo-History RAM-Verbrauch:")
    bench_undo_history_memory()

    print("\n2. zlib Compress/Decompress Geschwindigkeit:")
    bench_zlib_compress_decompress()

    print("\n3. Adjustment-Berechnung Geschwindigkeit:")
    bench_adjustment_speed()

    print("\n4. Thumbnail-Generierung vs. Cache:")
    bench_thumbnail_generation()

    print("\n5. Histogram-Berechnung:")
    bench_histogram()

    print("\n" + "=" * 60)
    print("BENCHMARK ABGESCHLOSSEN")
    print("=" * 60)
