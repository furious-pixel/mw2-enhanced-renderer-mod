#ifndef MW2ER_TEXT_UTIL_H
#define MW2ER_TEXT_UTIL_H

#include <stdint.h>

void mw2er_cp437_to_utf8(const uint8_t *source, int32_t length,
                         char *destination, int32_t capacity);

#endif
