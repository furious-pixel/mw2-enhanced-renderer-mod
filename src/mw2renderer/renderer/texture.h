#ifndef MW2ER_TEXTURE_H
#define MW2ER_TEXTURE_H

#include "mw2er_abi.h"
#include "mem.h"

enum { MW2ER_MAX_DESC = 512 };

// Capture-time draw eligibility. Omitted materials never prevent frame publication.
enum class Mw2erMaterialResult : uint8_t {
    Unvisited, Drawable, Inactive, Unavailable, Invalid, Unsupported
};

struct Mw2erResolvedTexture {
    int width;
    int height;
    int wrap;
    int discard_ff;
    int remap_kind_id;
    float dark_ratio[3];
    float fog_terminal[3];
    float s8_ratio[3];
    const uint8_t *pixels;
    const uint8_t *source_pixels; // Immutable provider body identity within this generation.
    int resource_id;
    int animated_effect;
    int enhancement_role_id; /* 0 none, 1 camo, 2 cruise */
    float enhanced_uv_scale;
};

void mw2er_texture_free_cache(void);
// Share one palette/remap revision across all descriptors captured this frame.
void mw2er_texture_begin_frame(const Mem &mem, const float *palette_rgb);
Mw2erMaterialResult mw2er_texture_resolve(
    const Mem &mem,
    int desc_idx,
    Mw2erResolvedTexture &out);
int is_aero_lift_fan_resource(int resource_id);
int mw2er_is_imaging_effect_resource(int resource_id);
int mw2er_desc_is_imaging_effect(const Mem &mem, int desc_idx);

#endif
