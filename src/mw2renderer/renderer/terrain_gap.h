#ifndef MW2ER_TERRAIN_GAP_H
#define MW2ER_TERRAIN_GAP_H

#include <stdint.h>

void mw2er_terrain_gap_load(const char *json_path);
int mw2er_terrain_delta(
    const char *mission_name,
    int nvert,
    int nfaces,
    int64_t sum_x,
    int64_t sum_z,
    float *dx,
    float *dz);

#endif
