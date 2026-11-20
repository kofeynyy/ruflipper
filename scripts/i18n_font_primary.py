#!/usr/bin/env python3
"""Generate u8g2_font_helvB08_t_cyrillic, the Cyrillic variant of FontPrimary.

helvB08_tr (FontPrimary) is ASCII only and u8g2 has no bold Cyrillic font of this size.
The new font keeps every ASCII glyph of helvB08_tr unchanged and adds U+0401, U+0410..U+044F
and U+0451. Letters that look the same as Latin ones (А В Е К М Н О Р С Т Х У, а е о р с х у)
are copied from the Latin glyphs; the rest are drawn here in the same 8px bold style.

The font is written to lib/u8g2/u8g2_fonts.c (replacing a previous copy) and declared in
lib/u8g2/u8g2.h. Re-running the script regenerates it.

Usage:
    python scripts/i18n_font_primary.py
    python scripts/i18n_font_primary.py --dry-run
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import i18n_preview as pv  # noqa: E402
from i18n_font_patch import c_literal, encode_glyph  # noqa: E402

SRC = "u8g2_font_helvB08_tr"
NAME = "u8g2_font_helvB08_t_cyrillic"
HEADER_H = Path(pv.ROOT) / "lib" / "u8g2" / "u8g2.h"

# Cyrillic letter -> Latin glyph with the same shape
HOMOGLYPHS = {
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "Х": "X", "У": "Y",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y",
}  # fmt: skip

# char -> (y offset, rows). Advance is width + 1 unless a tuple (dx, yo, rows) is given.
ART = {
    # ---- capitals: 8 rows on the baseline (taller where noted) ----
    "Б": ["######", "##....", "##....", "#####.", "##..##", "##..##", "##..##", "#####."],
    "Г": ["#####", "##...", "##...", "##...", "##...", "##...", "##...", "##..."],
    "Д": ["..#####.", "..##.##.", "..##.##.", ".##..##.", ".##..##.", ".##..##.", ".##..##.", "########", "##....##", "##....##"],
    "Ж": ["##.##.##", "##.##.##", ".######.", "..####..", "..####..", ".######.", "##.##.##", "##.##.##"],
    "З": [".####.", "##..##", "....##", "..###.", "....##", "....##", "##..##", ".####."],
    "И": ["##...##", "##..###", "##..###", "##.#.##", "##.#.##", "###..##", "###..##", "##...##"],
    "Й": [".#...#.", "..###..", "##...##", "##..###", "##..###", "##.#.##", "##.#.##", "###..##", "###..##", "##...##"],
    "Л": [".######", ".##..##", ".##..##", "##...##", "##...##", "##...##", "##...##", "##...##"],
    "П": ["######", "##..##", "##..##", "##..##", "##..##", "##..##", "##..##", "##..##"],
    "Ф": ["...##...", ".######.", "##.##.##", "##.##.##", "##.##.##", ".######.", "...##...", "...##..."],
    "Ц": ["##..##.", "##..##.", "##..##.", "##..##.", "##..##.", "##..##.", "##..##.", "#######", ".....##", ".....##"],
    "Ч": ["##..##", "##..##", "##..##", "##..##", ".#####", "....##", "....##", "....##"],
    "Ш": ["##.##.##", "##.##.##", "##.##.##", "##.##.##", "##.##.##", "##.##.##", "##.##.##", "########"],
    "Щ": ["##.##.##.", "##.##.##.", "##.##.##.", "##.##.##.", "##.##.##.", "##.##.##.", "##.##.##.", "#########", ".......##", ".......##"],
    "Ъ": ["###....", ".##....", ".##....", ".#####.", ".##..##", ".##..##", ".##..##", ".#####."],
    "Ы": ["##.....##", "##.....##", "##.....##", "#####..##", "##..##.##", "##..##.##", "##..##.##", "#####..##"],
    "Ь": ["##....", "##....", "##....", "#####.", "##..##", "##..##", "##..##", "#####."],
    "Э": [".####.", "##..##", "....##", "..####", "....##", "....##", "##..##", ".####."],
    "Ю": ["##..####.", "##.##..##", "##.##..##", "#####..##", "#####..##", "##.##..##", "##.##..##", "##..####."],
    "Я": [".#####", "##..##", "##..##", "##..##", ".#####", "##..##", "##..##", "##..##"],
    "Ё": [".#.#.", ".....", "#####", "##...", "##...", "#####", "##...", "##...", "##...", "#####"],
    # ---- lowercase: x-height 6, descenders go 2 rows below the baseline ----
    "б": ["#####", "##...", "####.", "##.##", "##.##", "##.##", "##.##", ".###."],
    "в": ["####.", "##.##", "####.", "##.##", "##.##", "####."],
    "г": ["#####", "##...", "##...", "##...", "##...", "##..."],
    "д": ["..#####", "..##.##", "..##.##", ".##..##", ".##..##", "#######", "##...##", "##...##"],
    "ж": ["##.##.##", "##.##.##", ".######.", ".######.", "##.##.##", "##.##.##"],
    "з": [".###.", "##.##", "..##.", "...##", "##.##", ".###."],
    "и": ["##..##", "##.###", "##.###", "#####.", "####.#", "###..#"],
    "й": [".#..#.", "..##..", "......", "##..##", "##.###", "##.###", "#####.", "####.#", "###..#"],
    "к": ["##..##", "##.##.", "####..", "####..", "##.##.", "##..##"],
    "л": [".#####", ".##.##", ".##.##", "##..##", "##..##", "##..##"],
    "м": ["##...##", "###.###", "#######", "##.#.##", "##...##", "##...##"],
    "н": ["##.##", "##.##", "#####", "##.##", "##.##", "##.##"],
    "п": ["#####", "##.##", "##.##", "##.##", "##.##", "##.##"],
    "т": ["#####", "..##.", "..##.", "..##.", "..##.", "..##."],
    "ф": ["...##...", "...##...", ".######.", "##.##.##", "##.##.##", "##.##.##", "##.##.##", ".######.", "...##...", "...##..."],
    "ц": ["##..##.", "##..##.", "##..##.", "##..##.", "##..##.", "#######", ".....##", ".....##"],
    "ч": ["##.##", "##.##", "##.##", ".####", "...##", "...##"],
    "ш": ["##.##.##", "##.##.##", "##.##.##", "##.##.##", "##.##.##", "########"],
    "щ": ["##.##.##.", "##.##.##.", "##.##.##.", "##.##.##.", "##.##.##.", "#########", ".......##", ".......##"],
    "ъ": ["###...", ".##...", ".####.", ".##.##", ".##.##", ".####."],
    "ы": ["##....##", "##....##", "####..##", "##.##.##", "##.##.##", "####..##"],
    "ь": ["##...", "##...", "####.", "##.##", "##.##", "####."],
    "э": [".###.", "##.##", ".####", "...##", "##.##", ".###."],
    "ю": ["##..###.", "##.##.##", "#####.##", "##.##.##", "##.##.##", "##..###."],
    "я": [".####", "##.##", "##.##", ".####", "##.##", "##.##"],
    "ё": [".#.#.", ".....", ".###.", "##.##", "#####", "##...", "##.##", ".###."],
}  # fmt: skip

# rows below the baseline, so that the bitmap's bottom lines up with the other letters
DESCENT = {
    "Д": 2, "Ц": 2, "Щ": 2, "д": 2, "ц": 2, "щ": 2, "ф": 2,
}  # fmt: skip


def build():
    src = pv.U8g2Font(SRC)
    d = src.d[:-3]  # drop the loader padding
    prefix_end = 23 + src.pos_unicode  # 8-bit glyph section ends here
    assert list(d[prefix_end:]) == [0, 4, 255, 255, 0], "unexpected helvB08_tr layout"

    entries = {}
    for cyr, lat in HOMOGLYPHS.items():
        w, px, dx, xo, yo, h = src.glyph(ord(lat))
        s = set(px)
        rows = ["".join("#" if (x, y) in s else "." for x in range(w)) for y in range(h)]
        entries[ord(cyr)] = (dx, yo, rows)
    for ch, rows in ART.items():
        w = len(rows[0])
        entries[ord(ch)] = (w + 1, -DESCENT.get(ch, 0), rows)

    blobs = []
    for enc in sorted(entries):
        dx, yo, rows = entries[enc]
        data = encode_glyph(src, dx, rows, y_off=yo)
        blobs.append(bytes([enc >> 8, enc & 0xFF, 3 + len(data)]) + data)

    head = bytearray(d[:prefix_end])
    head[0] += len(blobs)
    font = bytes(head) + bytes([0, 4, 255, 255]) + b"".join(blobs) + b"\0"
    return font, entries


def write_font(font: bytes):
    text = pv.FONTS_C.read_text(encoding="latin-1")
    decl = f'const uint8_t {NAME}[{len(font) + 1}] U8G2_FONT_SECTION("{NAME}") =\n{c_literal(font)};\n'
    block = re.compile(
        r"/\* generated by scripts/i18n_font_primary.py \*/\n.*?/\* end generated \*/\n", re.S
    )
    new_block = "/* generated by scripts/i18n_font_primary.py */\n" + decl + "/* end generated */\n"
    if block.search(text):
        text = block.sub(lambda _: new_block, text)
    else:
        text = text.rstrip("\n") + "\n\n" + new_block
    with open(pv.FONTS_C, "w", encoding="latin-1", newline="") as f:
        f.write(text)

    h = HEADER_H.read_text(encoding="latin-1")
    extern = f'extern const uint8_t {NAME}[] U8G2_FONT_SECTION("{NAME}");\n'
    if NAME not in h:
        anchor = 'extern const uint8_t u8g2_font_helvB08_te[] U8G2_FONT_SECTION("u8g2_font_helvB08_te");\n'
        assert anchor in h, "anchor for the extern declaration not found"
        h = h.replace(anchor, anchor + extern)
        with open(HEADER_H, "w", encoding="latin-1", newline="") as f:
            f.write(h)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="build and validate but do not write files")
    args = ap.parse_args()

    font, entries = build()
    print(f"{NAME}: {len(font)} bytes, {len(entries)} Cyrillic glyphs")
    if not args.dry_run:
        write_font(font)
        print("written to", pv.FONTS_C.name, "and", HEADER_H.name)


if __name__ == "__main__":
    main()
