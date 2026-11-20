#pragma once

#include <furi.h>
#include <furi_hal.h>

#include <gui/gui.h>
#include <gui/view_dispatcher.h>
#include <gui/modules/variable_item_list.h>
#include <gui/modules/submenu.h>
#include <locale/locale.h>

typedef struct {
    Gui* gui;
    ViewDispatcher* view_dispatcher;
    VariableItemList* var_item_list;
    Submenu* language_menu;
    LocaleLanguage languages[8];
    uint8_t languages_count;
} SystemSettings;

typedef enum {
    SystemSettingsViewVarItemList,
    SystemSettingsViewLanguage,
} SystemSettingsView;

// Position of the items in the list that react to OK
typedef enum {
    SystemSettingsItemLanguage,
} SystemSettingsItem;
