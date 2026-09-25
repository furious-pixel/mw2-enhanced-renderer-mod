#include "dump.h"

#include <SDL.h>
#include <stdio.h>
#include <string.h>

static const char k_landmark[] = "Framerate";

static int find_landmark(const uint8_t *data, uint32_t size, uint32_t *out_off)
{
    const uint32_t nlen = (uint32_t)(sizeof(k_landmark) - 1u);
    uint32_t i;

    if (size < nlen) {
        return 0;
    }
    for (i = 0; i + nlen <= size; ++i) {
        if (memcmp(data + i, k_landmark, nlen) == 0) {
            *out_off = i;
            return 1;
        }
    }
    return 0;
}

int mw2er_dump_open(const char *path, Mw2erDump *dump)
{
    size_t loaded = 0;
    void *data;
    uint32_t landmark_off;

    if (path == NULL || dump == NULL) {
        return 0;
    }
    memset(dump, 0, sizeof(*dump));
    dump->runtime_base = MW2ER_DUMP_RUNTIME_BASE;

    data = SDL_LoadFile(path, &loaded);
    if (data == NULL) {
        fprintf(stderr, "dump: cannot load %s: %s\n", path, SDL_GetError());
        return 0;
    }
    if (loaded == 0 || loaded > 0xffffffffu) {
        fprintf(stderr, "dump: size is not a 32-bit image\n");
        SDL_free(data);
        return 0;
    }
    dump->bytes = (const uint8_t *)data;
    dump->size = (uint32_t)loaded;

    if (!find_landmark(dump->bytes, dump->size, &landmark_off)) {
        fprintf(stderr, "dump: landmark not found\n");
        mw2er_dump_close(dump);
        return 0;
    }
    dump->delta = (dump->runtime_base + landmark_off) - MW2ER_DUMP_LANDMARK_RELOC;
    return 1;
}

void mw2er_dump_close(Mw2erDump *dump)
{
    if (dump == NULL) {
        return;
    }
    if (dump->bytes != NULL) {
        SDL_free((void *)dump->bytes);
    }
    memset(dump, 0, sizeof(*dump));
}

Mw2erMemoryView mw2er_dump_view(const Mw2erDump *dump)
{
    Mw2erMemoryView view;
    memset(&view, 0, sizeof(view));
    view.struct_size = sizeof(view);
    if (dump == NULL || dump->bytes == NULL) {
        return view;
    }
    view.bytes = dump->bytes;
    view.size = dump->size;
    view.runtime_base = dump->runtime_base;
    view.delta = dump->delta;
    return view;
}
