# Localization

The UI language is chosen in **Settings → System → Language**: OK on that item opens the list of
languages. After a choice the device restarts and runs in the new language. The language never
changes while the system is running, which keeps memory use bounded. English is built in; every
other language is a pack file on the SD card, `/ext/lang/<code>.lang`, so languages can be added
without rebuilding the firmware.

## Marking strings

```c
#include <locale/locale.h>

submenu_add_item(submenu, TR("Saved"), ...);          // translated when it runs
static const char* const names[] = {TR_NOOP("Left"), TR_NOOP("Right")};   // in a static table:
variable_item_set_current_value_text(item, TR(names[index]));              // translate where it is shown
```

The English text is the key. With English selected, or when a pack has no translation for a
string, `TR()` returns the text unchanged, so a missing translation is never an error.

Rules:

- Pass a string literal, optionally split in several adjacent literals. `TR(variable)` cannot be
  extracted by the tooling: use `TR_NOOP("...")` where the literal is, and `TR(variable)` where
  it is displayed.
- Do not call `TR()` from the storage service: lookups wait for it.
- The language is fixed for the whole session, so the pointer returned by `TR()` stays valid
  until the device restarts. Widgets that keep the pointer instead of copying the text (for
  example the labels of `variable_item_list`) are safe.
- Keep texts short: Russian is 20-30% longer than English. Check the result with
  `scripts/i18n_preview.py`.

## Tooling

`scripts/i18n.py`:

| Command | What it does |
|---|---|
| `extract [-o file]` | list all `TR("...")` / `TR_NOOP("...")` strings of the sources |
| `check assets/lang/ru.json` | report missing, unused and empty translations (exit code 1 on missing) |
| `pack assets/lang/ru.json -o ru.lang` | build the binary pack |
| `lookup ru.lang "Text"` | read a string from a pack |

Translation sources are JSON files in `assets/lang/`: `{"English text": "Translation"}` plus
`"@@code"` (language code) and `"@@name"` (name shown in the menu). The Russian pack that ships with
the firmware is `applications/services/locale/resources/lang/ru.lang`; rebuild it after changing
`assets/lang/ru.json`:

```
python scripts/i18n.py pack assets/lang/ru.json -o applications/services/locale/resources/lang/ru.lang
```

To add a language, copy `assets/lang/ru.json`, translate it, and put the packed file in
`/ext/lang/<code>.lang` (codes are 1-7 characters from `a-z 0-9 _ -`).

## Translating by hand

All translations of a language live in one file, `assets/lang/<code>.json` (for Russian
`assets/lang/ru.json`): `"English text": "translation"`. Nothing else has to be edited; the
`.lang` pack the firmware reads is generated from it.

1. `python scripts/i18n.py sync assets/lang/ru.json` adds every new string of the code to the file
   as an empty entry, in source order, so the strings of one screen are next to each other.
   Existing translations are not touched. Empty entries are shown in English.
2. Fill in the empty values. In JSON a line break is `
`; leave `%s`, `%lu`, `%d`... in place and
   in the same order, and keep `#` pairs (bold text) of the original.
3. `python scripts/i18n.py build` checks everything and packs the result into
   `applications/services/locale/resources/lang/ru.lang`. It reports translations that are wider
   than the room of the widget they are drawn in, and printf specifiers that do not match.
4. Rebuild the firmware, or copy the new `ru.lang` to `/ext/lang/` on the SD card: the strings are
   read from the pack, so a changed translation does not need a new firmware.

Tools for the width of a text (all in pixels of the 128x64 screen, using the real fonts):

- `python scripts/i18n.py measure "Text
second line"`: width of every line for each kind of widget,
  `TOO WIDE` when it exceeds the room of the widget.
- `python scripts/i18n.py sheet assets/lang/ru.json`: writes `build/i18n_sheet.md`, a table of all
  strings with the place in the code, the kind of widget, the English width and the width of the
  translation.
- `python scripts/i18n.py widths assets/lang/ru.json`: only the translations that are too wide.
- `python scripts/i18n_preview.py --font primary --text "..."`: draws the text as the device would.

The room of each kind of widget (`KINDS` in `scripts/i18n.py`) is an estimate. If a screen turns
out to have more or less space, change the number there; the check follows.

## Pack format

Little endian. Keys are not stored, only a 32-bit FNV-1a hash of each key; the tool refuses to
build a pack with colliding hashes.

```
0   char[4]  magic "FLNG"
4   u16      version = 1
6   u16      reserved = 0
8   u32      entry count N
12  u32      offset of the string pool = 16 + 8 * N
16  N * { u32 hash, u32 offset }    sorted by hash; offset is relative to the pool
... pool of NUL-terminated UTF-8 strings
```

The firmware searches the table in place on the SD card (binary search) and keeps what it
finds in a cache of at most 96 strings / 12 KB, so RAM use is bounded. Strings that do not fit
the cache are shown in English. The saved choice is the code in `/int/.locale_lang`.

## Fonts

`FontPrimary` and `FontSecondary` contain Latin and Cyrillic glyphs (see
`scripts/i18n_font_primary.py`, `scripts/i18n_font_patch.py`). Text wrapping and truncation in
`elements.c` work on UTF-8 characters, not bytes. `FontKeyboard` is still ASCII only.
