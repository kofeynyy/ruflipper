#pragma once

#include <furi.h>
#include <furi_hal.h>

#include <gui/gui.h>
#include <gui/view_dispatcher.h>
#include <gui/modules/variable_item_list.h>
#include <locale/locale.h>

typedef struct {
    Gui* gui;
    ViewDispatcher* view_dispatcher;
    VariableItemList* var_item_list;
    LocaleLanguage languages[8];
    uint8_t languages_count;
} SystemSettings;

typedef enum {
    SystemSettingsViewVarItemList,
} SystemSettingsView;

typedef enum {
    SystemSettingsEventLanguageChanged,
} SystemSettingsEvent;
