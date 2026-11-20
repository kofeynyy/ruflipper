#!/usr/bin/env python3
"""Translation tooling for the Flipper Zero UI language packs.

Source strings are marked in C code as TR("English text"). The English text is the key.
A translation source is a JSON object {"English text": "Translation", ...} with two
optional metadata keys, "@@code" (e.g. "ru") and "@@name" (shown in the language menu).

Commands:
    extract  [-o assets/lang/en.json]      collect every TR("...") from the sources
    check    assets/lang/ru.json           report missing and unused translations
    pack     assets/lang/ru.json -o ru.lang  build the binary pack read by the firmware
    lookup   ru.lang "English text"        read a string back from a pack (format test)

Pack format (little endian), see documentation/Localization.md:
    0   char[4] magic "FLNG"
    4   u16     version (1)
    6   u16     reserved (0)
    8   u32     entry count N
    12  u32     offset of the string pool from the start of the file
    16  N * { u32 hash, u32 offset }   sorted by hash, offset relative to the pool
    ... pool of NUL-terminated UTF-8 strings
hash is 32-bit FNV-1a of the UTF-8 key.
"""

import argparse
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAGIC = b"FLNG"
VERSION = 1
META_KEYS = ("@@code", "@@name")
SCAN_DIRS = ["applications", "lib/flipper_application", "furi"]


def fnv1a(data: bytes) -> int:
    h = 0x811C9DC5
    for b in data:
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


# TR("...") with C string literals, adjacent literals are concatenated
TR_RE = re.compile(r'\bTR(?:_NOOP)?\(\s*((?:"(?:[^"\\]|\\.)*"\s*)+)\)')
LIT_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
# TRC("context", "text"): the key of the translation is "context\x04text"
TRC_RE = re.compile(r'\bTRC\(\s*((?:"(?:[^"\\]|\\.)*"\s*)+),\s*((?:"(?:[^"\\]|\\.)*"\s*)+)\)')
CTX_SEP = "\x04"
C_ESC = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "e": "\x1b",  # GNU extension, used by the text box markup: \e#bold\e#
    "\\": "\\",
    '"': '"',
    "'": "'",
    "?": "?",
}
C_ESC_RE = re.compile(r"\\(?:([0-7]{1,3})|x([0-9a-fA-F]+)|(.))", re.S)


def c_unescape(s: str) -> str:
    """Decode the escapes of a C string literal, byte values as UTF-8 text."""
    out = bytearray()
    pos = 0
    for m in C_ESC_RE.finditer(s):
        out += s[pos : m.start()].encode("utf-8")
        pos = m.end()
        if m.group(1) is not None:
            out.append(int(m.group(1), 8) & 0xFF)
        elif m.group(2) is not None:
            out.append(int(m.group(2), 16) & 0xFF)
        else:
            out += C_ESC.get(m.group(3), m.group(3)).encode("utf-8")
    out += s[pos:].encode("utf-8")
    return out.decode("utf-8", errors="replace")


def extract_keys() -> dict:
    """Return {key: [file:line, ...]} for all TR("...") uses."""
    found = {}
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in (".c", ".h", ".cpp"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            # blank out comments (keeping line numbers) so examples in docs are not strings
            text = re.sub(r"/\*.*?\*/", lambda c: re.sub(r"[^\n]", " ", c.group(0)), text, flags=re.S)
            text = re.sub(r"(?m)^\s*//.*$", "", text)
            matches = [(m, c_unescape("".join(LIT_RE.findall(m.group(1))))) for m in TR_RE.finditer(text)]
            for m in TRC_RE.finditer(text):
                context = c_unescape("".join(LIT_RE.findall(m.group(1))))
                matches.append((m, context + CTX_SEP + c_unescape("".join(LIT_RE.findall(m.group(2))))))
            for m, key in matches:
                line = text.count("\n", 0, m.start()) + 1
                found.setdefault(key, []).append(f"{path.relative_to(ROOT).as_posix()}:{line}")
                # the function the text is passed to decides which font draws it
                names = re.findall(r"(\w+)\s*\(", text[max(0, m.start() - 250) : m.start()])
                names = [n for n in names if n not in ("TR", "TR_NOOP")]
                FUNCS.setdefault(key, set()).add(names[-1] if names else "?")
    return found


FUNCS = {}  # key -> names of the functions it is passed to, filled by extract_keys()

# texts drawn with FontPrimary, everything else uses FontSecondary
PRIMARY_FUNCS = {
    "submenu_add_item",
    "submenu_add_lockable_item",
    "submenu_set_header",
    "variable_item_list_add",
    "dialog_ex_set_header",
    "dialog_ex_set_left_button_text",
    "dialog_ex_set_center_button_text",
    "dialog_ex_set_right_button_text",
    "dialog_message_set_header",
    "dialog_message_set_buttons",
    "popup_set_header",
    "elements_button_left",
    "elements_button_center",
    "elements_button_right",
    "widget_add_button_element",
}

FMT_RE = re.compile(r"%[-+ #0-9.*]*(?:hh|h|ll|l|z|j|t|L)?[a-zA-Z%]")


def build_pack(entries: dict) -> bytes:
    items = []
    seen = {}
    for key, value in entries.items():
        kb = key.encode("utf-8")
        h = fnv1a(kb)
        if h in seen and seen[h] != key:
            sys.exit(f"hash collision between {seen[h]!r} and {key!r}: reword one of them")
        seen[h] = key
        items.append((h, value.encode("utf-8")))
    items.sort()

    pool = bytearray()
    table = []
    for h, v in items:
        table.append((h, len(pool)))
        pool += v + b"\0"

    pool_off = 16 + 8 * len(items)
    out = bytearray(MAGIC)
    out += struct.pack("<HHII", VERSION, 0, len(items), pool_off)
    for h, off in table:
        out += struct.pack("<II", h, off)
    return bytes(out + pool)


def lookup_pack(blob: bytes, key: str):
    """Reference implementation of the lookup done by the firmware."""
    if blob[:4] != MAGIC:
        raise ValueError("bad magic")
    version, _, count, pool_off = struct.unpack_from("<HHII", blob, 4)
    if version != VERSION:
        raise ValueError("unsupported version")
    target = fnv1a(key.encode("utf-8"))
    lo, hi = 0, count
    while lo < hi:
        mid = (lo + hi) // 2
        h, off = struct.unpack_from("<II", blob, 16 + 8 * mid)
        if h == target:
            end = blob.index(b"\0", pool_off + off)
            return blob[pool_off + off : end].decode("utf-8")
        if h < target:
            lo = mid + 1
        else:
            hi = mid
    return None


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_extract(a):
    keys = extract_keys()
    out = {k: k for k in sorted(keys)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(keys)} strings -> {a.out}")


def cmd_check(a):
    keys = set(extract_keys())
    tr = load_json(a.source)
    have = {k for k in tr if k not in META_KEYS}
    missing = sorted(keys - have)
    unused = sorted(have - keys)
    empty = sorted(k for k in have if not tr[k].strip())
    # printf specifiers must survive the translation, in the same order
    badfmt = sorted(k for k in have if FMT_RE.findall(k) != FMT_RE.findall(tr[k]))
    print(f"{len(keys)} strings in code, {len(have)} translated")
    for title, lst in (
        ("missing", missing),
        ("unused", unused),
        ("empty", empty),
        ("format specifiers differ", badfmt),
    ):
        if lst:
            print(f"\n{title} ({len(lst)}):")
            for k in lst:
                print("  ", repr(k))
    sys.exit(1 if missing or empty or badfmt else 0)


def cmd_widths(a):
    """Flag translations whose lines are wider than the screen or much wider than the English."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import i18n_preview as pv

    extract_keys()
    fonts = {
        "primary": pv.Screen(pv.U8g2Font(pv.flipper_font_name("primary"))),
        "secondary": pv.Screen(pv.U8g2Font(pv.flipper_font_name("secondary"))),
    }
    tr = load_json(a.source)
    shown = 0
    for key, value in tr.items():
        if key in META_KEYS or not value.strip():
            continue
        funcs = FUNCS.get(key, {"?"})
        font = "primary" if funcs & PRIMARY_FUNCS else "secondary"
        scr = fonts[font]
        en = max(scr.width(line.encode()) for line in key.split("\n"))
        ru = max(scr.width(line.encode()) for line in value.split("\n"))
        if ru > pv.SCREEN_W - 2 or (ru > en * a.ratio and ru > a.min_width):
            shown += 1
            print(f"{ru:3}px vs {en:3}px {font:9} {key!r} -> {value!r}")
    print(shown, "strings to review")


def cmd_pack(a):
    data = load_json(a.source)
    entries = {k: v for k, v in data.items() if v.strip() or k in META_KEYS}
    blob = build_pack(entries)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_bytes(blob)
    print(f"{len(entries)} entries, {len(blob)} bytes -> {a.out}")


def cmd_lookup(a):
    blob = Path(a.pack).read_bytes()
    print(lookup_pack(blob, a.key))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract")
    p.add_argument("-o", "--out", default=str(ROOT / "assets" / "lang" / "en.json"))
    p.set_defaults(fn=cmd_extract)
    p = sub.add_parser("check")
    p.add_argument("source")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("widths")
    p.add_argument("source")
    p.add_argument("--ratio", type=float, default=1.3, help="allowed width growth over English")
    p.add_argument("--min-width", type=int, default=45, help="ignore lines narrower than this")
    p.set_defaults(fn=cmd_widths)
    p = sub.add_parser("pack")
    p.add_argument("source")
    p.add_argument("-o", "--out", required=True)
    p.set_defaults(fn=cmd_pack)
    p = sub.add_parser("lookup")
    p.add_argument("pack")
    p.add_argument("key")
    p.set_defaults(fn=cmd_lookup)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
