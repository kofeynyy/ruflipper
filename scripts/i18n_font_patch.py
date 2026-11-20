#!/usr/bin/env python3
"""Add Cyrillic glyphs missing from u8g2_font_haxrcorp4089_t_cyrillic (FontSecondary).

The font ships U+0410..U+044F without U+044D (э) and has no Ё / ё. This script draws
them in the same 5x7 / 4x5 pixel style, encodes them in the u8g2 glyph format and
rewrites the font array in lib/u8g2/u8g2_fonts.c. It is idempotent: glyphs that are
already present are left alone.

Usage:
    python scripts/i18n_font_patch.py          # patch in place
    python scripts/i18n_font_patch.py --check  # only report what is missing
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import i18n_preview as pv  # noqa: E402

FONT = "u8g2_font_haxrcorp4089_t_cyrillic"

# Pixel art. All glyphs sit on the baseline (y offset 0), like the neighbouring letters.
GLYPHS = {
    0x0401: (  # Ё: Е with two dots, 9 rows tall like Й
        6,
        [
            ".#.#.",
            ".....",
            "#####",
            "#....",
            "#....",
            "####.",
            "#....",
            "#....",
            "#####",
        ],
    ),
    0x044D: (  # э: mirrored е
        5,
        [
            ".##.",
            "#..#",
            "####",
            "...#",
            ".##.",
        ],
    ),
    0x0451: (  # ё: е with two dots
        5,
        [
            "#..#",
            "....",
            ".##.",
            "#..#",
            "####",
            "#...",
            ".##.",
        ],
    ),
}


class BitWriter:
    def __init__(self):
        self.bits = []

    def put(self, value: int, cnt: int):
        for i in range(cnt):
            self.bits.append((value >> i) & 1)

    def to_bytes(self) -> bytes:
        out = bytearray()
        for i in range(0, len(self.bits), 8):
            chunk = self.bits[i : i + 8]
            out.append(sum(b << j for j, b in enumerate(chunk)))
        return bytes(out)


def encode_glyph(font: pv.U8g2Font, dx: int, rows: list) -> bytes:
    h, w = len(rows), len(rows[0])
    assert all(len(r) == w for r in rows)
    max0, max1 = (1 << font.bits_0) - 1, (1 << font.bits_1) - 1

    flat = [c == "#" for r in rows for c in r]
    # alternate zero-run / one-run starting with zeros, split to the field limits
    pairs = []
    i = 0
    while i < len(flat):
        zeros = 0
        while i < len(flat) and not flat[i]:
            zeros += 1
            i += 1
        ones = 0
        while i < len(flat) and flat[i]:
            ones += 1
            i += 1
        while zeros > max0:
            pairs.append((max0, 0))
            zeros -= max0
        while ones > max1:
            pairs.append((zeros, max1))
            zeros = 0
            ones -= max1
        pairs.append((zeros, ones))

    bw = BitWriter()
    bw.put(w, font.bits_w)
    bw.put(h, font.bits_h)
    bw.put(0 + (1 << (font.bits_x - 1)), font.bits_x)  # x offset 0
    bw.put(0 + (1 << (font.bits_y - 1)), font.bits_y)  # y offset 0 (on the baseline)
    bw.put(dx + (1 << (font.bits_dx - 1)), font.bits_dx)
    for a, b in pairs:
        bw.put(a, font.bits_0)
        bw.put(b, font.bits_1)
        bw.put(0, 1)  # no repeat
    return bw.to_bytes()


def unicode_entries(font: pv.U8g2Font):
    """Return (start, [(enc, raw_entry_bytes), ...]) of the unicode glyph list."""
    d = font.d
    p = 23 + font.pos_unicode
    table = p
    while True:
        p += d[table] << 8 | d[table + 1]
        e = d[table + 2] << 8 | d[table + 3]
        table += 4
        if e >= 0xFFFF:
            break
    start = p
    entries = []
    while True:
        e = d[p] << 8 | d[p + 1]
        if e == 0:
            break
        entries.append((e, bytes(d[p : p + d[p + 2]])))
        p += d[p + 2]
    return start, entries, p  # p: offset of the terminating 0 pair


def c_literal(data: bytes) -> str:
    lines = []
    for i in range(0, len(data), 32):
        s = ""
        for b in data[i : i + 32]:
            ch = chr(b)
            if 32 <= b < 127 and ch not in '"\\?':
                s += ch
            else:
                s += "\\%03o" % b
        lines.append('    "%s"' % s)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    font = pv.U8g2Font(FONT)
    start, entries, end = unicode_entries(font)
    have = {e for e, _ in entries}
    todo = [e for e in GLYPHS if e not in have]
    print("present:", len(have), "glyphs; missing:", ", ".join(f"U+{e:04X}" for e in todo) or "none")
    if args.check or not todo:
        return

    for enc in todo:
        dx, rows = GLYPHS[enc]
        data = encode_glyph(font, dx, rows)
        entry = bytes([enc >> 8, enc & 0xFF, 3 + len(data)]) + data
        entries.append((enc, entry))
    entries.sort(key=lambda t: t[0])

    d = bytearray(font.d[:-3])  # strip the padding added by the loader
    d[0] += len(todo)  # glyph count
    new = bytes(d[:start]) + b"".join(e for _, e in entries)
    new += bytes(d[end:])  # terminator and anything after the list

    src_path = pv.FONTS_C
    text = src_path.read_text(encoding="latin-1")
    pat = re.compile(
        r"(const uint8_t %s)\[\d+\](\s*U8G2_FONT_SECTION\(\s*\"%s\"\s*\)\s*=\s*)(.*?);\n" % (FONT, FONT),
        re.S,
    )
    m = pat.search(text)
    if not m:
        sys.exit("font declaration not found")
    # declared size counts the implicit NUL that closes the string literal
    body = f"{m.group(1)}[{len(new) + 1}]{m.group(2).rstrip()}\n{c_literal(new)};\n"
    text = text[: m.start()] + body + text[m.end() :]
    with open(src_path, "w", encoding="latin-1", newline="") as f:
        f.write(text)
    print(f"patched {src_path.name}: {len(font.d) - 3} -> {len(new)} bytes")


if __name__ == "__main__":
    main()
