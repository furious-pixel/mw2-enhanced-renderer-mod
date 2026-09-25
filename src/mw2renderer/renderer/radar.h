#ifndef MW2ER_RADAR_H
#define MW2ER_RADAR_H

#include <stdint.h>

struct Mem;

struct Mw2erHudLine { double x0, y0, x1, y1; int color; };
// Shared radar/targeting line batch, up to six segments in output pixels.
int mw2er_hud_draw_lines(const Mw2erHudLine *lines, int count, float stroke,
                         int width, int height, const uint8_t *palette,
                         bool accumulate_alpha = false);

void mw2er_radar_mission_reset(void);
void mw2er_radar_gl_reset(void);
void mw2er_radar_capture(const Mem &mem, int player_slot, uint32_t player,
                         uint32_t mech, uint32_t body, int hud_mode,
                         int transition_phase, double transition_extent,
                         int selected_target_indicators);
int mw2er_radar_render(int width, int height, const uint8_t *palette,
                       int draw_text);

#endif
