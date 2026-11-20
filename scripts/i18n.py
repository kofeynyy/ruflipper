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
TR_RE = re.compile(r'\bTR\(\s*((?:"(?:[^"\\]|\\.)*"\s*)+)\)')
LIT_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
C_ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}


def c_unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: C_ESC.get(m.group(1), m.group(1)), s)


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
            for m in TR_RE.finditer(text):
                key = c_unescape("".join(LIT_RE.findall(m.group(1))))
                line = text.count("\n", 0, m.start()) + 1
                found.setdefault(key, []).append(f"{path.relative_to(ROOT).as_posix()}:{line}")
    return found


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
    print(f"{len(keys)} strings in code, {len(have)} translated")
    for title, lst in (("missing", missing), ("unused", unused), ("empty", empty)):
        if lst:
            print(f"\n{title} ({len(lst)}):")
            for k in lst:
                print("  ", repr(k))
    sys.exit(1 if missing or empty else 0)


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
