#ifndef MW2ER_FONT_H
#define MW2ER_FONT_H

#include <stdint.h>

enum { MW2ER_FONT_SLOT_COUNT = 1398 };

struct Mw2erTextMetrics {
    float width;
    float height;
};

int32_t mw2er_font_process_init(const char *mod_dir);
void mw2er_font_process_shutdown(void);
void mw2er_font_gl_reset(void);
void mw2er_font_clear_slots(void);

int32_t mw2er_font_measure(int slot, const char *text, int size_px,
                           float letter_spacing, Mw2erTextMetrics *metrics);
int32_t mw2er_font_draw(int slot, const char *text, int size_px,
                        float letter_spacing, float x, float y,
                        float horizontal_scale, const float color[4],
                        int viewport_width, int viewport_height,
                        int opaque_background = 0);

#endif
