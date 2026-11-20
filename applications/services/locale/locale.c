#include "locale.h"

#include <storage/storage.h>
#include <stdlib.h>
#include <string.h>

#define TAG "LocaleSrv"

LocaleMeasurementUnits locale_get_measurement_unit(void) {
    return (LocaleMeasurementUnits)furi_hal_rtc_get_locale_units();
}

void locale_set_measurement_unit(LocaleMeasurementUnits format) {
    furi_hal_rtc_set_locale_units((FuriHalRtcLocaleUnits)format);
}

LocaleTimeFormat locale_get_time_format(void) {
    return (LocaleTimeFormat)furi_hal_rtc_get_locale_timeformat();
}

void locale_set_time_format(LocaleTimeFormat format) {
    furi_hal_rtc_set_locale_timeformat((FuriHalRtcLocaleTimeFormat)format);
}

LocaleDateFormat locale_get_date_format(void) {
    return (LocaleDateFormat)furi_hal_rtc_get_locale_dateformat();
}

void locale_set_date_format(LocaleDateFormat format) {
    furi_hal_rtc_set_locale_dateformat((FuriHalRtcLocaleDateFormat)format);
}

float locale_fahrenheit_to_celsius(float temp_f) {
    return (temp_f - 32.f) / 1.8f;
}

float locale_celsius_to_fahrenheit(float temp_c) {
    return temp_c * 1.8f + 32.f;
}

void locale_format_time(
    FuriString* out_str,
    const DateTime* datetime,
    const LocaleTimeFormat format,
    const bool show_seconds) {
    furi_check(out_str);
    furi_check(datetime);

    uint8_t hours = datetime->hour;
    uint8_t am_pm = 0;
    if(format == LocaleTimeFormat12h) {
        if(hours > 12) {
            hours -= 12;
            am_pm = 2;
        } else {
            am_pm = 1;
        }
        if(hours == 0) {
            hours = 12;
        }
    }

    if(show_seconds) {
        furi_string_printf(out_str, "%02u:%02u:%02u", hours, datetime->minute, datetime->second);
    } else {
        furi_string_printf(out_str, "%02u:%02u", hours, datetime->minute);
    }

    if(am_pm > 0) {
        furi_string_cat_printf(out_str, " %s", (am_pm == 1) ? ("AM") : ("PM"));
    }
}

void locale_format_date(
    FuriString* out_str,
    const DateTime* datetime,
    const LocaleDateFormat format,
    const char* separator) {
    furi_check(out_str);
    furi_check(datetime);
    furi_check(separator);

    if(format == LocaleDateFormatDMY) {
        furi_string_printf(
            out_str,
            "%02u%s%02u%s%04u",
            datetime->day,
            separator,
            datetime->month,
            separator,
            datetime->year);
    } else if(format == LocaleDateFormatMDY) {
        furi_string_printf(
            out_str,
            "%02u%s%02u%s%04u",
            datetime->month,
            separator,
            datetime->day,
            separator,
            datetime->year);
    } else {
        furi_string_printf(
            out_str,
            "%04u%s%02u%s%02u",
            datetime->year,
            separator,
            datetime->month,
            separator,
            datetime->day);
    }
}

/* ---- UI language ----
 *
 * A language pack is a sorted hash table on the SD card (documentation/Localization.md).
 * Strings are looked up lazily: the first TR() of a string does a binary search in the
 * file, the result is kept in a small cache so later calls cost one hash and one mutex.
 * The language is fixed for the whole session: it is read once, at the first TR(), and
 * changing it means saving the choice and restarting the device. That keeps the memory use
 * bounded and every returned string valid for as long as the system runs.
 * Do not call TR() from the storage service itself: lookups wait for it.
 */

#define LANG_DIR           EXT_PATH("lang")
#define LANG_SETTINGS_PATH INT_PATH(".locale_lang")
#define LANG_PACK_MAGIC    "FLNG"
#define LANG_PACK_VERSION  1
#define LANG_HEADER_SIZE   16
#define LANG_ENTRY_SIZE    8
#define LANG_META_NAME     "@@name"

#define LANG_CACHE_SLOTS     512 /* power of two, 4 KB */
#define LANG_CACHE_MAX_ITEMS 384 /* keeps the table at most 75% full */
#define LANG_CACHE_MAX_BYTES 24576 /* of string data, in 1 KB arena chunks */
#define LANG_ARENA_CHUNK     1024
#define LANG_STRING_MAX      255
#define LANG_RETRY_MS        5000
#define LANG_RETRY_COUNT     3

typedef struct {
    uint32_t hash;
    const char* text; /* NULL: free slot, lang_missing: no translation, use the key */
} LangCacheSlot;

typedef struct LangArena LangArena;
struct LangArena {
    LangArena* next;
    size_t used;
    char data[LANG_ARENA_CHUNK];
};

static const char lang_missing = '\0';

typedef struct {
    FuriMutex* mutex;
    File* file; /* pack of the current language, NULL for English */
    char code[LOCALE_LANGUAGE_CODE_SIZE];
    uint32_t count;
    uint32_t pool_offset;
    LangCacheSlot* slots;
    size_t items;
    size_t bytes;
    LangArena* arena; /* translated strings, never freed: the language is fixed per session */
    bool resolved; /* the saved language has been applied (or there is none) */
    uint8_t retries;
    uint32_t retry_tick;
    char scratch[LANG_STRING_MAX + 1];
} LocaleState;

static LocaleState* locale_state = NULL;

static uint32_t lang_hash(const char* text) {
    uint32_t hash = 0x811C9DC5UL; /* 32-bit FNV-1a, same as scripts/i18n.py */
    for(const uint8_t* p = (const uint8_t*)text; *p; p++) {
        hash ^= *p;
        hash *= 0x01000193UL;
    }
    return hash;
}

static bool lang_code_valid(const char* code) {
    size_t len = strlen(code);
    if(len == 0 || len >= LOCALE_LANGUAGE_CODE_SIZE) return false;
    for(size_t i = 0; i < len; i++) {
        char c = code[i];
        bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-';
        if(!ok) return false;
    }
    return true;
}

/** Open /ext/lang/<code>.lang and check its header. */
static bool lang_pack_open(
    Storage* storage,
    const char* code,
    File** out_file,
    uint32_t* out_count,
    uint32_t* out_pool_offset) {
    char path[sizeof(LANG_DIR) + LOCALE_LANGUAGE_CODE_SIZE + 8];
    snprintf(path, sizeof(path), LANG_DIR "/%s.lang", code);

    File* file = storage_file_alloc(storage);
    bool ok = false;
    do {
        if(!storage_file_open(file, path, FSAM_READ, FSOM_OPEN_EXISTING)) break;

        uint8_t header[LANG_HEADER_SIZE];
        if(storage_file_read(file, header, sizeof(header)) != sizeof(header)) break;
        if(memcmp(header, LANG_PACK_MAGIC, 4) != 0) break;

        uint16_t version;
        uint32_t count;
        uint32_t pool_offset;
        memcpy(&version, header + 4, sizeof(version));
        memcpy(&count, header + 8, sizeof(count));
        memcpy(&pool_offset, header + 12, sizeof(pool_offset));
        if(version != LANG_PACK_VERSION) break;
        if(count > 0x10000 || pool_offset != LANG_HEADER_SIZE + count * LANG_ENTRY_SIZE) break;
        if(storage_file_size(file) < pool_offset) break;

        *out_count = count;
        *out_pool_offset = pool_offset;
        ok = true;
    } while(false);

    if(ok) {
        *out_file = file;
    } else {
        storage_file_free(file); /* closes the file if it was opened */
    }
    return ok;
}

static bool lang_pack_read_string(File* file, uint32_t offset, char* out, size_t out_size) {
    if(!storage_file_seek(file, offset, true)) return false;
    size_t len = 0;
    while(len < out_size - 1) {
        size_t want = out_size - 1 - len;
        if(want > 32) want = 32;
        size_t got = storage_file_read(file, out + len, want);
        if(got == 0) return false;
        for(size_t i = 0; i < got; i++) {
            if(out[len + i] == '\0') return true;
        }
        len += got;
    }
    return false; /* longer than the buffer: ignore */
}

/** Binary search by hash. */
static bool lang_pack_find(
    File* file,
    uint32_t count,
    uint32_t pool_offset,
    uint32_t hash,
    char* out,
    size_t out_size) {
    uint32_t low = 0;
    uint32_t high = count;
    while(low < high) {
        uint32_t mid = low + (high - low) / 2;
        uint32_t entry[2]; /* hash, offset into the string pool */
        if(!storage_file_seek(file, LANG_HEADER_SIZE + mid * LANG_ENTRY_SIZE, true)) return false;
        if(storage_file_read(file, entry, sizeof(entry)) != sizeof(entry)) return false;

        if(entry[0] == hash) {
            return lang_pack_read_string(file, pool_offset + entry[1], out, out_size);
        } else if(entry[0] < hash) {
            low = mid + 1;
        } else {
            high = mid;
        }
    }
    return false;
}

/** Open the pack of a language, must be called with the mutex held, once. */
static bool lang_load(LocaleState* s, const char* code) {
    if(strcmp(code, LOCALE_LANGUAGE_DEFAULT) == 0) return true;

    Storage* storage = furi_record_open(RECORD_STORAGE);
    File* file = NULL;
    uint32_t count = 0;
    uint32_t pool_offset = 0;
    bool ok = lang_pack_open(storage, code, &file, &count, &pool_offset);
    furi_record_close(RECORD_STORAGE);
    if(!ok) return false;

    s->slots = calloc(LANG_CACHE_SLOTS, sizeof(LangCacheSlot));
    if(!s->slots) {
        storage_file_free(file);
        return false;
    }

    s->file = file;
    s->count = count;
    s->pool_offset = pool_offset;
    strlcpy(s->code, code, sizeof(s->code));
    return true;
}

static bool lang_settings_read(char* code, size_t size) {
    Storage* storage = furi_record_open(RECORD_STORAGE);
    File* file = storage_file_alloc(storage);
    size_t len = 0;
    if(storage_file_open(file, LANG_SETTINGS_PATH, FSAM_READ, FSOM_OPEN_EXISTING)) {
        len = storage_file_read(file, code, size - 1);
    }
    storage_file_free(file);
    furi_record_close(RECORD_STORAGE);

    while(len > 0 && (code[len - 1] == '\n' || code[len - 1] == '\r' || code[len - 1] == ' ')) {
        len--;
    }
    code[len] = '\0';
    return len > 0;
}

static bool lang_settings_write(const char* code) {
    Storage* storage = furi_record_open(RECORD_STORAGE);
    File* file = storage_file_alloc(storage);
    bool ok = false;
    if(storage_file_open(file, LANG_SETTINGS_PATH, FSAM_WRITE, FSOM_CREATE_ALWAYS)) {
        ok = storage_file_write(file, code, strlen(code)) == strlen(code);
    } else {
        FURI_LOG_E(TAG, "Cannot save language");
    }
    storage_file_free(file);
    furi_record_close(RECORD_STORAGE);
    return ok;
}

/** Apply the saved language the first time it is needed, mutex held.
 *
 * The SD card may not be mounted yet, so a missing pack is retried a few times.
 */
static void lang_ensure(LocaleState* s) {
    if(s->resolved || furi_get_tick() < s->retry_tick) return;

    char code[LOCALE_LANGUAGE_CODE_SIZE];
    if(!lang_settings_read(code, sizeof(code)) || !lang_code_valid(code) ||
       strcmp(code, LOCALE_LANGUAGE_DEFAULT) == 0) {
        s->resolved = true;
        return;
    }

    if(lang_load(s, code)) {
        s->resolved = true;
    } else if(++s->retries >= LANG_RETRY_COUNT) {
        FURI_LOG_W(TAG, "Language pack '%s' not found, using English", code);
        s->resolved = true;
    } else {
        s->retry_tick = furi_get_tick() + furi_ms_to_ticks(LANG_RETRY_MS);
    }
}

static LangCacheSlot* lang_cache_find(LocaleState* s, uint32_t hash, bool* found) {
    size_t index = hash & (LANG_CACHE_SLOTS - 1);
    for(size_t i = 0; i < LANG_CACHE_SLOTS; i++) {
        LangCacheSlot* slot = &s->slots[(index + i) & (LANG_CACHE_SLOTS - 1)];
        if(!slot->text) {
            *found = false;
            return slot;
        }
        if(slot->hash == hash) {
            *found = true;
            return slot;
        }
    }
    *found = false;
    return NULL;
}

static const char* lang_translate(LocaleState* s, const char* key) {
    uint32_t hash = lang_hash(key);
    bool found;
    LangCacheSlot* slot = lang_cache_find(s, hash, &found);
    if(found) return slot->text == &lang_missing ? key : slot->text;
    if(!slot || s->items >= LANG_CACHE_MAX_ITEMS) return key; /* cache full: stay in English */

    const char* text = NULL;
    if(lang_pack_find(s->file, s->count, s->pool_offset, hash, s->scratch, sizeof(s->scratch))) {
        size_t size = strlen(s->scratch) + 1;
        if(s->bytes + size > LANG_CACHE_MAX_BYTES) return key;
        if(!s->arena || s->arena->used + size > LANG_ARENA_CHUNK) {
            LangArena* arena = malloc(sizeof(LangArena));
            if(!arena) return key;
            arena->next = s->arena;
            arena->used = 0;
            s->arena = arena;
        }
        char* copy = s->arena->data + s->arena->used; /* lives until the device restarts */
        s->arena->used += size;
        memcpy(copy, s->scratch, size);
        s->bytes += size;
        text = copy;
    }

    slot->hash = hash;
    slot->text = text ? text : &lang_missing;
    s->items++;
    return text ? text : key;
}

/** Text of a key without its context prefix ("context\004text"). */
static const char* lang_plain(const char* key) {
    const char* separator = strchr(key, LOCALE_CONTEXT_SEPARATOR);
    return separator ? separator + 1 : key;
}

const char* locale_tr(const char* key) {
    LocaleState* s = locale_state;
    if(!key) return key;
    if(!s) return lang_plain(key);

    furi_check(furi_mutex_acquire(s->mutex, FuriWaitForever) == FuriStatusOk);
    lang_ensure(s);
    const char* result = s->file ? lang_translate(s, key) : key;
    furi_check(furi_mutex_release(s->mutex) == FuriStatusOk);
    /* no translation: show the English text, without the context */
    return result == key ? lang_plain(key) : result;
}

const char* locale_get_language(void) {
    LocaleState* s = locale_state;
    if(!s) return LOCALE_LANGUAGE_DEFAULT;

    furi_check(furi_mutex_acquire(s->mutex, FuriWaitForever) == FuriStatusOk);
    lang_ensure(s);
    const char* code = s->file ? s->code : LOCALE_LANGUAGE_DEFAULT;
    furi_check(furi_mutex_release(s->mutex) == FuriStatusOk);
    return code;
}

bool locale_set_language(const char* code) {
    furi_check(code);
    if(!lang_code_valid(code)) return false;

    if(strcmp(code, LOCALE_LANGUAGE_DEFAULT) != 0) {
        /* do not save a language whose pack is missing or damaged */
        Storage* storage = furi_record_open(RECORD_STORAGE);
        File* file = NULL;
        uint32_t count = 0;
        uint32_t pool_offset = 0;
        bool ok = lang_pack_open(storage, code, &file, &count, &pool_offset);
        if(ok) storage_file_free(file);
        furi_record_close(RECORD_STORAGE);
        if(!ok) return false;
    }

    return lang_settings_write(code);
}

size_t locale_get_languages(LocaleLanguage* languages, size_t max) {
    furi_check(languages);
    if(max == 0) return 0;

    strlcpy(languages[0].code, LOCALE_LANGUAGE_DEFAULT, sizeof(languages[0].code));
    strlcpy(languages[0].name, "English", sizeof(languages[0].name));
    size_t count = 1;

    Storage* storage = furi_record_open(RECORD_STORAGE);
    File* dir = storage_file_alloc(storage);
    if(storage_dir_open(dir, LANG_DIR)) {
        FileInfo info;
        char filename[LOCALE_LANGUAGE_CODE_SIZE + 8];
        while(count < max && storage_dir_read(dir, &info, filename, sizeof(filename))) {
            size_t len = strlen(filename);
            if(file_info_is_dir(&info) || len <= 5 || strcmp(filename + len - 5, ".lang") != 0) {
                continue;
            }
            filename[len - 5] = '\0';
            if(!lang_code_valid(filename) || strcmp(filename, LOCALE_LANGUAGE_DEFAULT) == 0) {
                continue;
            }

            File* pack = NULL;
            uint32_t pack_count = 0;
            uint32_t pool_offset = 0;
            if(!lang_pack_open(storage, filename, &pack, &pack_count, &pool_offset)) continue;

            LocaleLanguage* language = &languages[count];
            strlcpy(language->code, filename, sizeof(language->code));
            if(!lang_pack_find(
                   pack,
                   pack_count,
                   pool_offset,
                   lang_hash(LANG_META_NAME),
                   language->name,
                   sizeof(language->name))) {
                strlcpy(language->name, filename, sizeof(language->name));
            }
            storage_file_free(pack);
            count++;
        }
    }
    storage_file_free(dir);
    furi_record_close(RECORD_STORAGE);
    return count;
}

int32_t locale_on_system_start(void* p) {
    UNUSED(p);
    LocaleState* s = calloc(1, sizeof(LocaleState));
    s->mutex = furi_mutex_alloc(FuriMutexTypeNormal);
    strlcpy(s->code, LOCALE_LANGUAGE_DEFAULT, sizeof(s->code));
    locale_state = s;
    return 0;
}
