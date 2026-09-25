#ifndef MW2ER_TARGETING_H
#define MW2ER_TARGETING_H

#include <stdint.h>

struct Mem;
struct Mw2erTargetClip { int left, top, right, bottom; };

void mw2er_targeting_mission_reset(void);
void mw2er_targeting_gl_reset(void);
int mw2er_targeting_capture_sprite(const Mem &mem, int resource);
int mw2er_targeting_draw_sprite(int reference, double x, double y,
                                double scale, const Mw2erTargetClip &clip,
                                const uint8_t *palette, int width, int height,
                                int snap_to_pixels = 1);
int mw2er_targeting_draw_caret(int direction, double x, double y, int color,
                               double marker_scale,
                               const Mw2erTargetClip &clip,
                               const uint8_t *palette, int width, int height);
int mw2er_targeting_draw_nav(double x, double y, int color,
                             double marker_scale,
                             const Mw2erTargetClip &clip,
                             const uint8_t *palette, int width, int height);
int mw2er_targeting_draw_bracket(double x, double y, double radius, int color,
                                 double panel_scale,
                                 const Mw2erTargetClip &clip,
                                 const uint8_t *palette, int width, int height);
int mw2er_targeting_draw_acquisition(
    double x, double y, double radius, int color, double panel_scale,
    double progress, double turns, double trail_span, float stroke,
    const uint8_t *palette, int width, int height);
int mw2er_targeting_draw_compass_caret(
    int direction, double x, double y, int edge_attachment,
    double panel_scale, const Mw2erTargetClip &clip,
    const uint8_t *palette, int width, int height);

#endif
