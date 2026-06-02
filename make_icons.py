#!/usr/bin/env python3
"""
make_icons.py – Erzeugt die drei PNG-Icons für die Browser-Extension.
Verwendet ausschließlich die Python-Standardbibliothek (kein Pillow nötig).
Farbe: MyFeed-Blau #0F3460 mit einem weißen "M"-Buchstaben.
"""

import struct
import zlib
import os

# ── Farben ───────────────────────────────────────────────────────────────────
BG   = (15, 52, 96)    # #0F3460 – Hintergrund
FG   = (255, 255, 255) # #FFFFFF – Vordergrund (Icon-Zeichen)
ACC  = (233, 69, 96)   # #E94560 – Akzent (Punkt)

def make_png(width: int, height: int) -> bytes:
    """Erzeugt ein PNG-Bild der gegebenen Größe mit dem MyFeed-Icon."""
    pixels = [BG] * (width * height)

    # ── Hilfsfunktionen ───────────────────────────────────────────────────────
    def set_px(x: int, y: int, color: tuple) -> None:
        if 0 <= x < width and 0 <= y < height:
            pixels[y * width + x] = color

    def fill_rect(x0: int, y0: int, x1: int, y1: int, color: tuple) -> None:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                set_px(x, y, color)

    # ── Icon-Zeichnung (skaliert auf die jeweilige Größe) ─────────────────────
    # Rand (10 % des Bildes als Padding)
    pad = max(1, width // 8)
    cx, cy = width // 2, height // 2

    # Weißes "M" – vereinfacht als drei vertikale Balken mit Diagonale
    bar_w = max(1, width // 8)

    # Linker Balken
    fill_rect(pad, pad, pad + bar_w - 1, height - pad - 1, FG)
    # Rechter Balken
    fill_rect(width - pad - bar_w, pad, width - pad - 1, height - pad - 1, FG)
    # Mittlerer V-Abstieg (Diagonalen)
    steps = (height - 2 * pad) // 2
    for i in range(steps + 1):
        t = i / max(steps, 1)
        # Linke Diagonale: von oben-links nach Mitte-unten
        lx = int(pad + bar_w - 1 + t * (cx - pad - bar_w))
        ly = int(pad + t * steps)
        fill_rect(lx, ly, lx + bar_w - 1, ly + bar_w - 1, FG)
        # Rechte Diagonale: von Mitte-unten nach oben-rechts
        rx = int(cx + t * (width - pad - bar_w - cx))
        ry = int(pad + steps - i)
        fill_rect(rx, ry, rx + bar_w - 1, ry + bar_w - 1, FG)

    # Akzent-Punkt oben rechts
    dot_r = max(1, width // 6)
    dot_cx = width - pad - dot_r
    dot_cy = pad + dot_r
    for dy in range(-dot_r, dot_r + 1):
        for dx in range(-dot_r, dot_r + 1):
            if dx * dx + dy * dy <= dot_r * dot_r:
                set_px(dot_cx + dx, dot_cy + dy, ACC)

    # ── PNG-Encoding ──────────────────────────────────────────────────────────
    def chunk(ctype: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(ctype + data) & 0xFFFF_FFFF
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)

    # IHDR: width, height, bit_depth=8, color_type=2 (RGB), compression, filter, interlace
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))

    # IDAT: raw scanlines (filter byte 0 = None per row) + zlib
    raw = b"".join(
        b"\x00" + b"".join(bytes(pixels[y * width + x]) for x in range(width))
        for y in range(height)
    )
    idat = chunk(b"IDAT", zlib.compress(raw, level=9))

    iend = chunk(b"IEND", b"")

    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


def main() -> None:
    out_dir = os.path.join(os.path.dirname(__file__), "extension", "icons")
    os.makedirs(out_dir, exist_ok=True)

    for size in (16, 48, 128):
        path = os.path.join(out_dir, f"icon{size}.png")
        data = make_png(size, size)
        with open(path, "wb") as f:
            f.write(data)
        print(f"  Erstellt: {path} ({len(data)} Bytes)")


if __name__ == "__main__":
    main()
