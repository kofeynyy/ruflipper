#!/usr/bin/env python3
"""Render text exactly as the Flipper Zero screen would draw it, without a device.

Reads the real u8g2 font bytes from lib/u8g2/u8g2_fonts.c and the Font -> u8g2 font
mapping from applications/services/gui/canvas.c, so the preview follows the sources.
It also mimics elements_multiline_text_aligned() (line wrapping in *bytes*, "-" and "...").

Usage:
    python scripts/i18n_preview.py --font secondary --text "Привет, мир"
    python scripts/i18n_preview.py --font primary --file text.txt --x 2 --y 0 -o out.png
    python scripts/i18n_preview.py --u8g2-font u8g2_font_6x12_t_cyrillic --text "Тест"

Pillow is required (pip install pillow).
"""

import argparse
import math
import re
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FONTS_C = ROOT / "lib" / "u8g2" / "u8g2_fonts.c"
CANVAS_C = ROOT / "applications" / "services" / "gui" / "canvas.c"

SCREEN_W, SCREEN_H = 128, 64
BG = (255, 130, 0)  # Flipper orange backlight
FG = (0, 0, 0)

ESCAPES = {"n": 10, "t": 9, "r": 13, "a": 7, "b": 8, "f": 12, "v": 11, "\\": 92, '"': 34, "'": 39}


def unescape_c(src: str) -> bytes:
    """Decode the concatenated C string literals of a u8g2 font into raw bytes."""
    out = bytearray()
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch != "\\":
            out += ch.encode("latin-1")
            i += 1
            continue
        i += 1
        ch = src[i]
        if ch in "01234567":
            j = i
            while j < n and j < i + 3 and src[j] in "01234567":
                j += 1
            out.append(int(src[i:j], 8) & 0xFF)
            i = j
        elif ch == "x":
            j = i + 1
            while j < n and src[j] in "0123456789abcdefABCDEF":
                j += 1
            out.append(int(src[i + 1 : j], 16) & 0xFF)
            i = j
        else:
            out.append(ESCAPES[ch])
            i += 1
    return bytes(out)


def load_font_bytes(name: str) -> bytes:
    text = FONTS_C.read_text(encoding="latin-1")
    m = re.search(
        r"const uint8_t %s\[(\d+)\]\s*U8G2_FONT_SECTION\(\s*\"%s\"\s*\)\s*=\s*(.*?);\n"
        % (re.escape(name), re.escape(name)),
        text,
        re.S,
    )
    if not m:
        sys.exit(f"font {name} not found in {FONTS_C}")
    size = int(m.group(1))
    literals = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2), re.S)
    data = unescape_c("".join(literals))
    if len(data) not in (size, size - 1):
        sys.exit(f"font {name}: decoded {len(data)} bytes, declared {size}")
    # the C string literal has an implicit NUL terminator that ends the glyph lists
    return data + b"\0\0\0"


def flipper_font_name(kind: str) -> str:
    kinds = {
        "primary": "FontPrimary",
        "secondary": "FontSecondary",
        "keyboard": "FontKeyboard",
        "bignumbers": "FontBigNumbers",
    }
    text = CANVAS_C.read_text(encoding="utf-8")
    m = re.search(r"font == %s\)\s*\{\s*u8g2_SetFont\(&canvas->fb,\s*(\w+)\)" % kinds[kind], text)
    if not m:
        sys.exit(f"could not find {kinds[kind]} in {CANVAS_C}")
    return m.group(1)


class BitReader:
    def __init__(self, data: bytes, pos: int):
        self.data, self.pos, self.bit = data, pos, 0

    def unsigned(self, cnt: int) -> int:
        val = 0
        got = 0
        while got < cnt:
            take = min(8 - self.bit, cnt - got)
            chunk = (self.data[self.pos] >> self.bit) & ((1 << take) - 1)
            val |= chunk << got
            got += take
            self.bit += take
            if self.bit == 8:
                self.bit = 0
                self.pos += 1
        return val

    def signed(self, cnt: int) -> int:
        return self.unsigned(cnt) - (1 << (cnt - 1))


class U8g2Font:
    def __init__(self, name: str):
        self.name = name
        self.d = load_font_bytes(name)
        d = self.d
        self.bits_0, self.bits_1 = d[2], d[3]
        self.bits_w, self.bits_h = d[4], d[5]
        self.bits_x, self.bits_y, self.bits_dx = d[6], d[7], d[8]
        self.max_h = d[10]
        self.ascent_a = d[13] - 256 if d[13] > 127 else d[13]
        self.pos_upper_a = d[17] << 8 | d[18]
        self.pos_lower_a = d[19] << 8 | d[20]
        self.pos_unicode = d[21] << 8 | d[22]

    def _glyph_ptr(self, enc: int):
        d = self.d
        p = 23
        if enc <= 255:
            if enc >= ord("a"):
                p += self.pos_lower_a
            elif enc >= ord("A"):
                p += self.pos_upper_a
            while d[p + 1] != 0:
                if d[p] == enc:
                    return p + 2
                p += d[p + 1]
            return None
        p += self.pos_unicode
        table = p
        while True:
            p += d[table] << 8 | d[table + 1]
            e = d[table + 2] << 8 | d[table + 3]
            table += 4
            if e >= enc:
                break
        while True:
            e = d[p] << 8 | d[p + 1]
            if e == 0:
                return None
            if e == enc:
                return p + 3
            p += d[p + 2]

    def glyph(self, enc: int):
        """Return (width_px, pixels[(x,y)], dx, x_off, y_off, h) or None if missing."""
        ptr = self._glyph_ptr(enc)
        if ptr is None:
            return None
        r = BitReader(self.d, ptr)
        w = r.unsigned(self.bits_w)
        h = r.unsigned(self.bits_h)
        # offsets and advance are stored even for empty glyphs such as the space
        x_off = r.signed(self.bits_x)
        y_off = r.signed(self.bits_y)
        dx = r.signed(self.bits_dx)
        pixels = []
        lx = ly = 0
        while w > 0 and ly < h:
            a = r.unsigned(self.bits_0)
            b = r.unsigned(self.bits_1)
            while True:
                for length, fg in ((a, False), (b, True)):
                    for _ in range(length):
                        if fg:
                            pixels.append((lx, ly))
                        lx += 1
                        if lx == w:
                            lx = 0
                            ly += 1
                if r.unsigned(1) == 0:
                    break
        return (w, pixels, dx, x_off, y_off, h)


class Screen:
    def __init__(self, font: U8g2Font):
        self.font = font
        self.fb = set()
        self.clipped = False
        self.missing = set()
        self.split_utf8 = False

    def _decode(self, raw: bytes):
        s = raw.decode("utf-8", errors="ignore")
        if len(s.encode("utf-8")) != len(raw):
            self.split_utf8 = True
        return [ord(c) for c in s]

    def width(self, raw: bytes) -> int:
        total = 0
        for enc in self._decode(raw):
            g = self.font.glyph(enc)
            if g is None:
                self.missing.add(chr(enc))
                continue
            total += g[2]
        return total

    def draw(self, x: int, y: int, raw: bytes):
        """y is the baseline, like u8g2_DrawUTF8."""
        for enc in self._decode(raw):
            g = self.font.glyph(enc)
            if g is None:
                self.missing.add(chr(enc))
                continue
            w, pixels, dx, x_off, y_off, h = g
            for px, py in pixels:
                sx = x + x_off + px
                sy = y - (h + y_off) + py
                if 0 <= sx < SCREEN_W and 0 <= sy < SCREEN_H:
                    self.fb.add((sx, sy))
                else:
                    self.clipped = True
            x += dx

    def font_height(self) -> int:
        h = self.font.max_h
        if self.font.name == "u8g2_font_haxrcorp4089_tr":
            h += 1  # same special case as canvas_current_font_height()
        return h

    def max_chars_to_fit(self, text: bytes, x: int) -> int:
        """Port of elements_get_max_chars_to_fit() for AlignLeft."""
        end = text.find(b"\n")
        size = len(text) if end < 0 else end
        line = text[:size]
        len_px = self.width(line)
        px_left = SCREEN_W - x
        if len_px > px_left:
            excess = math.ceil((len_px - px_left) / (len_px / size))
            if excess > 0:
                excess = max(excess, 5)
                return size - excess - 1
        return size

    def multiline(self, x: int, y: int, text: bytes):
        """Port of elements_multiline_text_aligned(AlignLeft, AlignTop)."""
        fh = self.font_height()
        pos = 0
        while pos < len(text):
            fit = self.max_chars_to_fit(text[pos:], x)
            nxt = text[pos + fit : pos + fit + 1]
            if nxt in (b"\n", b""):
                line = text[pos : pos + fit]
            elif y + fh > SCREEN_H:
                line = text[pos : pos + fit] + b"..."
            else:
                fit -= 1
                line = text[pos : pos + fit] + b"-"
            self.draw(x, y + self.font.ascent_a, line.split(b"\n")[0])
            y += fh
            pos += fit
            if text[pos : pos + 1] == b"\n":
                pos += 1
            if y > SCREEN_H:
                break

    def save(self, path: Path, scale: int):
        img = Image.new("RGB", (SCREEN_W * scale, SCREEN_H * scale), BG)
        px = img.load()
        for x, y in self.fb:
            for dy in range(scale - 1):
                for dx in range(scale - 1):
                    px[x * scale + dx, y * scale + dy] = FG
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)


DEFAULT_TEXT = (
    "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ\n"
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя\n"
    "Съешь же ещё этих мягких\n"
    "французских булок, да выпей чаю\n"
    "0123456789 Flipper Zero"
)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--font", choices=["primary", "secondary", "keyboard", "bignumbers"], default="secondary", help="Flipper Font enum value (resolved from canvas.c)")
    g.add_argument("--u8g2-font", help="raw u8g2 font name, e.g. u8g2_font_6x12_t_cyrillic")
    t = ap.add_mutually_exclusive_group()
    t.add_argument("--text", help="text to draw (\\n for line breaks)")
    t.add_argument("--file", help="UTF-8 text file to draw")
    ap.add_argument("--x", type=int, default=0)
    ap.add_argument("--y", type=int, default=0, help="top of the first line (AlignTop)")
    ap.add_argument("--scale", type=int, default=6)
    ap.add_argument("-o", "--out", default=str(ROOT / "build" / "i18n_preview.png"))
    a = ap.parse_args()

    font_name = a.u8g2_font or flipper_font_name(a.font)
    if a.file:
        text = Path(a.file).read_text(encoding="utf-8")
    elif a.text is not None:
        text = a.text.replace("\\n", "\n")
    else:
        text = DEFAULT_TEXT

    scr = Screen(U8g2Font(font_name))
    scr.multiline(a.x, a.y, text.encode("utf-8"))
    scr.save(Path(a.out), a.scale)

    print(f"font: {font_name}  line height: {scr.font_height()}px  -> {a.out}")
    if scr.missing:
        print(
            "WARNING: glyphs missing in font:",
            " ".join(f"U+{ord(c):04X}" for c in sorted(scr.missing)),
        )
    if scr.clipped:
        print("WARNING: some pixels fall outside the 128x64 screen")
    if scr.split_utf8:
        print("WARNING: a multi-byte UTF-8 character was split (wrapping cuts by bytes)")


if __name__ == "__main__":
    main()
