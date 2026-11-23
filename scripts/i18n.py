#!/usr/bin/env python3
"""Translation tooling for the Flipper Zero UI language packs.

Source strings are marked in C code as TR("English text"). The English text is the key.
A translation source is a JSON object {"English text": "Translation", ...} with two
optional metadata keys, "@@code" (e.g. "ru") and "@@name" (shown in the language menu).

Commands:
    extract  [-o assets/lang/en.json]      collect every TR("...") from the sources
    check    assets/lang/ru.json           report missing, unused and empty translations, bad printf specifiers
    widths   assets/lang/ru.json           list translations wider than the room of their widget
    measure  "Text\nsecond line"            width of a text in pixels for each kind of widget
    sync     assets/lang/ru.json           add the new strings of the code to the json as empty entries, in source order
    sheet    assets/lang/ru.json [-o file] translator's sheet: every string, where it is shown, width budget
    build                                  check + widths + pack every assets/lang/*.json into the firmware resources
    pack     assets/lang/ru.json -o ru.lang  build one binary pack
    lookup   ru.lang "English text"        read a string back from a pack (format test)

All translations live in assets/lang/<code>.json; the .lang files are generated from them.

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

# Where a text is drawn decides its font and how wide a line may be. The budgets are in pixels
# of the 128x64 screen and are estimates of the free space of each widget: change them here
# if a screen turns out to have more or less room. Everything not listed is a free text.
#   kind -> (font, max line width in px)
KINDS = {
    "menu_item": ("primary", 100),  # main menu rows (icon on the left)
    "list_item": ("primary", 116),  # submenu rows
    "list_label": ("primary", 70),  # label of a settings row, the value sits on the right
    "button": ("primary", 52),  # left / right button of a dialog or a bottom bar
    "button_center": ("primary", 44),
    "header": ("primary", 126),  # dialog / popup headers
    "text": ("secondary", 126),  # dialog, popup, widget texts
    "keyboard": ("keyboard", 126),
}
FUNC_KIND = {
    "submenu_add_item": "list_item",
    "submenu_add_item_ex": "list_item",
    "submenu_add_lockable_item": "list_item",
    "submenu_set_header": "header",
    "submenu_change_item_label": "list_item",
    "variable_item_list_add": "list_label",
    "dialog_ex_set_header": "header",
    "dialog_ex_set_left_button_text": "button",
    "dialog_ex_set_right_button_text": "button",
    "dialog_ex_set_center_button_text": "button_center",
    "dialog_message_set_header": "header",
    "dialog_message_set_buttons": "button",
    "popup_set_header": "header",
    "elements_button_left": "button",
    "elements_button_right": "button",
    "elements_button_center": "button_center",
    "widget_add_button_element": "button",
    "menu_add_item": "menu_item",
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


def _screens():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import i18n_preview as pv

    return pv, {
        name: pv.Screen(pv.U8g2Font(pv.flipper_font_name(name))) for name in ("primary", "secondary", "keyboard")
    }


def _sample(text: str) -> str:
    """Replace printf specifiers with typical values so dynamic texts can be measured."""
    return FMT_RE.sub(lambda m: "%" if m.group(0) == "%%" else ("0000" if m.group(0)[-1] in "duxXfF" else "abcdef"), text)


def _lines_px(scr, text: str):
    return [scr.width(_sample(line).encode()) for line in text.replace("\x1b#", "").split("\n")]


def _plain(key: str) -> str:
    """Key without the context prefix of TRC()."""
    return key.split(CTX_SEP, 1)[1] if CTX_SEP in key else key


def analyze(source: str):
    """One row per string in the code: where it is used, its width budget and the fit of the translation."""
    pv, scr = _screens()
    found = extract_keys()
    tr = load_json(source)
    rows = []
    for key, where in found.items():
        funcs = sorted(FUNCS.get(key, {"?"}))
        kinds = [FUNC_KIND[f] for f in funcs if f in FUNC_KIND]
        kind = kinds[0] if kinds else "text"
        font, limit = KINDS[kind]
        en = _lines_px(scr[font], _plain(key))
        value = tr.get(key, "")
        ru = _lines_px(scr[font], value) if value.strip() else []
        rows.append(
            {
                "key": key,
                "value": value,
                "where": where[0],
                "uses": len(where),
                "funcs": funcs,
                "kind": kind,
                "font": font,
                "limit": limit,
                "en": en,
                "ru": ru,
                "over": bool(ru) and max(ru) > limit,
                "en_over": max(en) > limit,
            }
        )
    rows.sort(key=lambda r: (r["where"].split(":")[0], int(r["where"].split(":")[1])))
    return rows


def cmd_widths(a):
    """List translations with a line wider than the budget of the widget they are shown in."""
    rows = analyze(a.source)
    bad = [r for r in rows if r["over"]]
    for r in bad:
        print(
            f"{max(r['ru']):3}px > {r['limit']:3}px ({r['kind']}, English {max(r['en'])}px)  "
            f"{_plain(r['key'])!r} -> {r['value']!r}  [{r['where']}]"
        )
    print(f"{len(bad)} translations are wider than their budget")
    sys.exit(1 if bad else 0)


def cmd_measure(a):
    """Width of a text in pixels, line by line: python scripts/i18n.py measure "Текст\\nвторая строка" """
    _, scr = _screens()
    text = a.text.replace("\\n", "\n")
    for kind, (font, limit) in KINDS.items():
        if a.font and font != a.font:
            continue
        if not a.font and kind not in ("menu_item", "list_item", "list_label", "button", "text"):
            continue
        widths = _lines_px(scr[font], text)
        mark = "OK " if max(widths) <= limit else "TOO WIDE"
        print(f"{kind:13} {font:9} limit {limit:3}px  lines {widths}  {mark}")


def cmd_sheet(a):
    """Write a translator's sheet (Markdown) with every string, its place on screen and its width budget."""
    rows = analyze(a.source)
    out = []
    out.append(f"# Translator's sheet ({len(rows)} strings)\n")
    out.append("Edit `assets/lang/ru.json` (the only place where translations are stored); this sheet is generated.\n")
    out.append("Widths are in pixels of the 128x64 screen. `limit` is the room of the widget (see KINDS in scripts/i18n.py).\n")
    out.append("| # | where | kind (font, limit) | English (px) | translation (px) | |")
    out.append("|---|---|---|---|---|---|")

    def cell(text):
        return text.replace("\x1b#", "").replace("\x04", "").replace("|", "\\|").replace("\n", " ⏎ ")

    for i, r in enumerate(rows, 1):
        ctx = r["key"].split(CTX_SEP, 1)[0] + "·" if CTX_SEP in r["key"] else ""
        status = "" if not r["value"].strip() else ("OVERFLOW" if r["over"] else "ok")
        if not r["value"].strip():
            status = "TODO"
        ru = f"{cell(r['value'])} ({'/'.join(map(str, r['ru']))})" if r["ru"] else ""
        out.append(
            f"| {i} | {r['where']} | {r['kind']} ({r['font']}, {r['limit']}) | "
            f"{ctx}{cell(_plain(r['key']))} ({'/'.join(map(str, r['en']))}) | {ru} | {status} |"
        )
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    todo = sum(1 for r in rows if not r["value"].strip())
    over = sum(1 for r in rows if r["over"])
    print(f"{len(rows)} strings, {todo} not translated, {over} too wide -> {a.out}")


def cmd_sync(a):
    """Add every string of the code that has no entry yet to the json (empty, to be filled in by hand).

    Entries are ordered like the sources (file, line), so the strings of one screen are next to each
    other. Existing translations are never changed; entries that are no longer used stay at the end.
    """
    data = load_json(a.source)
    found = extract_keys()
    meta = {k: v for k, v in data.items() if k in META_KEYS}
    order = sorted(found, key=lambda k: (found[k][0].split(":")[0], int(found[k][0].split(":")[1])))
    out = dict(meta)
    added = 0
    for key in order:
        if key not in data:
            added += 1
        out[key] = data.get(key, "")
    stale = [k for k in data if k not in META_KEYS and k not in found]
    for key in stale:
        out[key] = data[key]
    Path(a.source).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{added} new empty entries, {len(stale)} entries no longer used by the code (kept at the end)")


def cmd_build(a):
    """check + width review + pack every assets/lang/*.json into the firmware resources."""
    for src in sorted((ROOT / "assets" / "lang").glob("*.json")):
        if src.stem == "en":
            continue
        data = load_json(str(src))
        code = data.get("@@code", src.stem)
        keys = set(extract_keys())
        have = {k for k in data if k not in META_KEYS and data[k].strip()}
        badfmt = [k for k in have if FMT_RE.findall(k) != FMT_RE.findall(data[k])]
        rows = analyze(str(src))
        over = [r for r in rows if r["over"]]
        print(f"{code}: {len(keys)} strings in code, {len(keys & have)} translated, {len(keys - have)} missing, "
              f"{len(over)} too wide, {len(badfmt)} with wrong printf specifiers")
        for r in over[:20]:
            print(f"   too wide ({max(r['ru'])}px > {r['limit']}px): {_plain(r['key'])!r} -> {r['value']!r}")
        for k in badfmt:
            print(f"   printf specifiers differ: {k!r}")
        entries = {k: v for k, v in data.items() if v.strip() or k in META_KEYS}
        out = ROOT / "applications" / "services" / "locale" / "resources" / "lang" / f"{code}.lang"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(build_pack(entries))
        print(f"   packed {len(entries)} entries -> {out.relative_to(ROOT).as_posix()}")


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
    p.set_defaults(fn=cmd_widths)
    p = sub.add_parser("measure")
    p.add_argument("text")
    p.add_argument("--font", choices=["primary", "secondary", "keyboard"])
    p.set_defaults(fn=cmd_measure)
    p = sub.add_parser("sheet")
    p.add_argument("source")
    p.add_argument("-o", "--out", default=str(ROOT / "build" / "i18n_sheet.md"))
    p.set_defaults(fn=cmd_sheet)
    p = sub.add_parser("sync")
    p.add_argument("source")
    p.set_defaults(fn=cmd_sync)
    p = sub.add_parser("build")
    p.set_defaults(fn=cmd_build)
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
