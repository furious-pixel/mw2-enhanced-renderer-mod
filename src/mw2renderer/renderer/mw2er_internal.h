#ifndef MW2ER_INTERNAL_H
#define MW2ER_INTERNAL_H

#include "mw2er_abi.h"
#include "mem.h"

#include <string.h>

enum { MW2ER_PATH_MAX = 512 };
enum { MW2ER_ERROR_MAX = 512 };
enum { MW2ER_MAX_VIEWPORT = 8192 };

static inline int mw2er_memory_ok(const Mw2erMemoryView *mem)
{
    return mem != NULL && mem->struct_size >= sizeof(Mw2erMemoryView) &&
           Mem::from(*mem).ok();
}

static inline int mw2er_viewport_size_ok(int32_t width, int32_t height)
{
    return width > 0 && height > 0 && width <= MW2ER_MAX_VIEWPORT &&
           height <= MW2ER_MAX_VIEWPORT;
}

void mw2er_set_error(const char *msg);
void mw2er_clear_error(void);
const char *mw2er_last_error(void);
void mw2er_log(const char *msg);
const char *mw2er_mod_dir(void);
const char *mw2er_shader_dir(void);
void *mw2er_get_gl_proc_address(const char *name);
const Mw2erMemoryView *mw2er_frame_mem(void);
const Mw2erViewport *mw2er_frame_vp(void);

int32_t mw2er_gl_on_context(const Mw2erViewport *vp);
void mw2er_gl_shutdown(void);
uint64_t mw2er_gl_context_generation(void);
int32_t mw2er_gl_ensure_size(int32_t width, int32_t height);
int32_t mw2er_gl_begin_frame(void);
void mw2er_gl_invalidate_publication(void);
int32_t mw2er_gl_render_scene(void);
int32_t mw2er_gl_render_hud(void);
int32_t mw2er_gl_publish(void);
int32_t mw2er_gl_composite(const Mw2erViewport *vp, const struct Mw2erPresentation *presentation);
int32_t mw2er_gl_published_scene(uint32_t *fbo, int32_t *width, int32_t *height);
struct Mw2erSprite;
struct Mw2erIndexedSpriteDraw {
    float x, y, scale_x, scale_y;
    int32_t clip_left, clip_top, clip_right, clip_bottom;
    int32_t color_override;
};
uint32_t mw2er_gl_upload_indexed_sprite(const Mw2erSprite &sprite);
int32_t mw2er_gl_draw_indexed_sprites(
    const Mw2erSprite &sprite, uint32_t texture,
    const Mw2erIndexedSpriteDraw *draws, int32_t draw_count,
    const uint8_t *palette,
    int32_t viewport_width, int32_t viewport_height);

#endif
