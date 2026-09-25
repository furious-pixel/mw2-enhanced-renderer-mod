#ifndef MW2ER_COMPASS_ALTIMETER_H
#define MW2ER_COMPASS_ALTIMETER_H

#include <stdint.h>

struct Mem;

void mw2er_compass_altimeter_reset(void);
void mw2er_compass_altimeter_capture(const Mem &mem, uint32_t player,
                                     uint32_t mech, uint32_t body);
int mw2er_compass_altimeter_render(int width, int height,
                                   const uint8_t *palette);

#endif
