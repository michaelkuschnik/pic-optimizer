"""Erstellt optimizer.ico mit einem einfachen Kamera-/Bild-Icon."""
from PIL import Image, ImageDraw

def make_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size

    # Hintergrund: abgerundetes Rechteck (dunkelblau)
    bg_color = (30, 60, 114)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=s // 6, fill=bg_color)

    # Äußerer Bilderrahmen (weiß)
    m = s * 0.12
    frame_r = s * 0.08
    d.rounded_rectangle([m, m * 1.5, s - m, s - m], radius=frame_r, fill=(255, 255, 255))

    # Inneres Bild-Rechteck (hellgrau)
    p = s * 0.18
    d.rounded_rectangle([p, p * 1.8, s - p, s - p * 0.9], radius=frame_r * 0.6, fill=(220, 230, 242))

    # Kamera-Linse (Kreis, blau)
    cx, cy = s / 2, s / 2 + s * 0.06
    r_outer = s * 0.18
    r_inner = s * 0.11
    r_shine = s * 0.04
    d.ellipse([cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer], fill=(30, 60, 114))
    d.ellipse([cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner], fill=(72, 149, 239))
    # Glanzpunkt
    d.ellipse([cx - r_shine * 1.6, cy - r_shine * 1.6,
               cx - r_shine * 0.2, cy - r_shine * 0.2], fill=(255, 255, 255, 180))

    # Kamera-Buckel oben (Auslöser-Andeutung)
    bw = s * 0.22
    bh = s * 0.10
    bx = s / 2 - bw / 2
    by = m * 1.5 - bh * 0.5
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=bh / 2, fill=(30, 60, 114))

    return img


sizes = [16, 24, 32, 48, 64, 128, 256]
frames = [make_icon(s) for s in sizes]

frames[0].save(
    "optimizer.ico",
    format="ICO",
    sizes=[(s, s) for s in sizes],
    append_images=frames[1:],
)
print("optimizer.ico erstellt.")
