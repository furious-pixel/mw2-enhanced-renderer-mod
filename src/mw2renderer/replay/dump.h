#ifndef MW2ER_DUMP_H
#define MW2ER_DUMP_H

#include "mw2er_abi.h"

/*
 * Raw linear guest RAM image used by dump replay.
 * File offset 0 is guest runtime address MW2ER_DUMP_RUNTIME_BASE.
 * Relocation delta is (runtime_base + landmark_file_offset) - MW2ER_DUMP_LANDMARK_RELOC
 * where the landmark is the ASCII string "Framerate".
 */

#define MW2ER_DUMP_RUNTIME_BASE 0x00170000u
#define MW2ER_DUMP_LANDMARK_RELOC 0x000A3330u

typedef struct Mw2erDump {
    const uint8_t *bytes;
    uint32_t size;
    uint32_t runtime_base;
    uint32_t delta;
} Mw2erDump;

int mw2er_dump_open(const char *path, Mw2erDump *dump);
void mw2er_dump_close(Mw2erDump *dump);
Mw2erMemoryView mw2er_dump_view(const Mw2erDump *dump);

#endif /* MW2ER_DUMP_H */
