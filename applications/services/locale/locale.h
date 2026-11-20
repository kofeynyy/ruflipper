#pragma once

#include <stdbool.h>
#include <furi.h>
#include <furi_hal.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    LocaleMeasurementUnitsMetric = 0, /**< Metric measurement units */
    LocaleMeasurementUnitsImperial = 1, /**< Imperial measurement units */
} LocaleMeasurementUnits;

typedef enum {
    LocaleTimeFormat24h = 0, /**< 24-hour format */
    LocaleTimeFormat12h = 1, /**< 12-hour format */
} LocaleTimeFormat;

typedef enum {
    LocaleDateFormatDMY = 0, /**< Day/Month/Year */
    LocaleDateFormatMDY = 1, /**< Month/Day/Year */
    LocaleDateFormatYMD = 2, /**< Year/Month/Day */
} LocaleDateFormat;

/** Get Locale measurement units
 *
 * @return     The locale measurement units.
 */
LocaleMeasurementUnits locale_get_measurement_unit(void);

/** Set locale measurement units
 *
 * @param[in]  format  The locale measurements units
 */
void locale_set_measurement_unit(LocaleMeasurementUnits format);

/** Convert Fahrenheit to Celsius
 *
 * @param[in]  temp_f  The Temperature in Fahrenheit
 *
 * @return     The Temperature in Celsius 
 */
float locale_fahrenheit_to_celsius(float temp_f);

/** Convert Celsius to Fahrenheit
 *
 * @param[in]  temp_c  The Temperature in Celsius 
 *
 * @return     The Temperature in Fahrenheit
 */
float locale_celsius_to_fahrenheit(float temp_c);

/** Get Locale time format
 *
 * @return     The locale time format.
 */
LocaleTimeFormat locale_get_time_format(void);

/** Set Locale Time Format
 *
 * @param[in]  format  The Locale Time Format
 */
void locale_set_time_format(LocaleTimeFormat format);

/** Format time to furi string
 *
 * @param[out] out_str       The FuriString to store formatted time
 * @param[in]  datetime      Pointer to the datetime
 * @param[in]  format        The Locale Time Format
 * @param[in]  show_seconds  The show seconds flag
 */
void locale_format_time(
    FuriString* out_str,
    const DateTime* datetime,
    const LocaleTimeFormat format,
    const bool show_seconds);

/** Get Locale DateFormat
 *
 * @return     The Locale DateFormat.
 */
LocaleDateFormat locale_get_date_format(void);

/** Set Locale DateFormat
 *
 * @param[in]  format  The Locale DateFormat
 */
void locale_set_date_format(LocaleDateFormat format);

/** Format date to furi string
 *
 * @param[out] out_str    The FuriString to store formatted date
 * @param[in]  datetime   Pointer to the datetime
 * @param[in]  format     The format
 * @param[in]  separator  The separator
 */
void locale_format_date(
    FuriString* out_str,
    const DateTime* datetime,
    const LocaleDateFormat format,
    const char* separator);

/* ---- UI language ----
 *
 * User-visible strings are marked with TR("English text"). The English text is the key:
 * with the default language (or when a string has no translation) TR() returns it
 * unchanged. Other languages are packs in /ext/lang/<code>.lang, see
 * documentation/Localization.md.
 */

#define LOCALE_LANGUAGE_CODE_SIZE 8
#define LOCALE_LANGUAGE_NAME_SIZE 24
#define LOCALE_LANGUAGE_DEFAULT   "en"

typedef struct {
    char code[LOCALE_LANGUAGE_CODE_SIZE]; /**< e.g. "ru" */
    char name[LOCALE_LANGUAGE_NAME_SIZE]; /**< display name, e.g. "Русский" */
} LocaleLanguage;

/** Translate a UI string into the current language
 *
 * The returned pointer stays valid until the language has been changed twice: copy
 * the text if it must live longer than that.
 *
 * @param[in]  key  English text, a string literal in practice
 *
 * @return     translated text, or key itself when there is no translation
 */
const char* locale_tr(const char* key);

#define TR(text) locale_tr(text)

/** Get the current UI language code
 *
 * @return     language code, "en" by default
 */
const char* locale_get_language(void);

/** Switch the UI language and remember the choice
 *
 * @param[in]  code  language code, "en" or the name of a pack in /ext/lang
 *
 * @return     true if the language was switched
 */
bool locale_set_language(const char* code);

/** List the languages that can be selected
 *
 * English is always the first item; the others are the packs found in /ext/lang.
 *
 * @param[out] languages  array to fill
 * @param[in]  max        size of the array
 *
 * @return     number of languages written
 */
size_t locale_get_languages(LocaleLanguage* languages, size_t max);

#ifdef __cplusplus
}
#endif
