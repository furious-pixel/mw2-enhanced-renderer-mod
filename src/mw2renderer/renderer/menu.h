#ifndef MW2ER_MENU_H
#define MW2ER_MENU_H

#include "mw2er_abi.h"

#include <stdint.h>

void mw2er_menu_mission_reset(void);
void mw2er_menu_gl_reset(void);
void mw2er_menu_capture_primary(const Mw2erMemoryView &view);
void mw2er_menu_capture_late(const Mw2erMemoryView &view);
int32_t mw2er_menu_render(int32_t width, int32_t height,
                          const uint8_t *palette);

#endif
