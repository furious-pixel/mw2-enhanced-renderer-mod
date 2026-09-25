#ifndef MW2ER_HOST_UTIL_H
#define MW2ER_HOST_UTIL_H

#include "mw2er_abi.h"

#include <stdint.h>

const Mw2erApi *mw2er_host_load_api(const char *dll_path_or_null);
int32_t mw2er_host_begin_mission(const Mw2erApi *api);
int32_t mw2er_host_frame(const Mw2erApi *api, const Mw2erMemoryView *mem,
                         const Mw2erViewport *vp, uint64_t frame, int composite);
int mw2er_host_write_png(const char *path, int width, int height);
int mw2er_host_write_png_fbo(const char *path, uint32_t fbo, int width, int height);
/* 2x2 box average of an even-sized FBO down to width/2 x height/2. */
int mw2er_host_write_png_fbo_box2x(
    const char *path, uint32_t fbo, int width, int height);
uint64_t mw2er_host_ticks(void);
double mw2er_host_ms_since(uint64_t start_ticks);

#endif /* MW2ER_HOST_UTIL_H */
