#ifndef MW2ER_HUD_H
#define MW2ER_HUD_H

#include "mw2er_abi.h"
#include "scene_extract.h"

#include <stdint.h>

struct Mw2erHudVertex {
    float x, y;
    float r, g, b, a;
};

void mw2er_hud_mission_reset(void);
void mw2er_hud_gl_reset(void);
void mw2er_hud_capture_late(const Mw2erMemoryView &view);
int32_t mw2er_hud_capture_primary(const Mw2erMemoryView &view, double time_seconds,
                                  Mw2erRenderView primary_view);
int32_t mw2er_hud_capture_target(const Mw2erMemoryView &view);
int mw2er_hud_target_display_mode(void);
/* The HUD reports a semantic view; extraction and draw policy live elsewhere. */
Mw2erRenderView mw2er_hud_mfd_render_view(void);
int mw2er_hud_objectives_available(void);
int mw2er_hud_submit_rects(const Mw2erHudVertex *vertices, int32_t count,
                           int32_t width, int32_t height);
int mw2er_hud_satellite_damage_viewport(int32_t viewport[4]);
int mw2er_hud_render_satellite_damage_radar(int width, int height);
int32_t mw2er_hud_render(uint32_t overlay_fbo, int width, int height, int sample_scale);

#endif
