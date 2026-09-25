#ifndef MW2ER_PRESENTATION_H
#define MW2ER_PRESENTATION_H

#include "mw2er_abi.h"

#include <stdint.h>
#include <vector>

enum Mw2erEvent {
    MW2ER_EVENT_PRIMARY = 1,
    MW2ER_EVENT_TARGET = 2,
    MW2ER_EVENT_LATE = 3,
    MW2ER_EVENT_LOADING_BEGIN = 4,
    MW2ER_EVENT_LOADING_FADE = 5,
    MW2ER_EVENT_LOADING_STRIP = 6,
    MW2ER_EVENT_LOADING_END = 7,
    MW2ER_EVENT_OUTRO = 8,
};

struct Mw2erSprite {
    int32_t width = 0;
    int32_t height = 0;
    int32_t x_offset = 0;
    int32_t y_offset = 0;
    std::vector<uint8_t> pixels;
};

struct Mem;
bool mw2er_decode_runtime_sprite(
    const Mem &mem, uint32_t table, uint32_t frame, Mw2erSprite *out);
uint32_t mw2er_runtime_sprite_frame_count(const Mem &mem, uint32_t table);
uint32_t mw2er_resolve_cached_shape(const Mem &mem, int32_t resource_index);

struct Mw2erLoadingVisual {
    Mw2erSprite background;
    std::vector<Mw2erSprite> strips;
    uint8_t palette[256 * 3] = {};
    int32_t clip_x = 0;
    int32_t clip_y = 0;
    int32_t strip_x = 0;
    int32_t strip_y = 0;
    uint64_t generation = 0;
    bool valid = false;
};

enum Mw2erPresentationKind {
    MW2ER_PRESENT_NOT_READY = 0,
    MW2ER_PRESENT_SCENE = 1,
    MW2ER_PRESENT_LOADING = 2,
};

struct Mw2erPresentation {
    Mw2erPresentationKind kind = MW2ER_PRESENT_NOT_READY;
    float loading_brightness = 0.0f;
    int32_t strip_index = -1;
    float fade = 0.0f;
    bool brightness_identity = true;
    bool continuous = false;
    bool suspend_frame_pacing = false;
    const float *brightness = nullptr;
    const Mw2erLoadingVisual *loading = nullptr;
};

void mw2er_presentation_reset(void);
int32_t mw2er_presentation_capture(const Mw2erCaptureInput *input);
void mw2er_presentation_published(uint32_t resources_pending);
Mw2erPresentation mw2er_presentation_get(
    double time_seconds, int32_t view_mode, bool scene_available, uint32_t resources_pending);

#endif
