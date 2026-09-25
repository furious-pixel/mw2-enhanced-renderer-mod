#include "mw2er_internal.h"
#include "config.h"
#include "gl_program.h"
#include "hud.h"
#include "font.h"
#include "presentation.h"
#include "scene_draw.h"

#include "gl_api.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <stdio.h>
#include <string>

struct Mw2erColorTarget {
    GLuint fbo;
    GLuint color;
    GLuint depth; /* 0 if none */
};

struct Mw2erGlState {
    int loaded;
    int ready;
    int32_t logical_w;
    int32_t logical_h;
    int32_t scene_w;
    int32_t scene_h;
    int sample_scale;
    uint64_t context_generation;
    int staging;
    int published;
    int published_valid;
    uint32_t staging_complete;
    uint32_t staging_overlay_present;
    uint32_t published_overlay_present;
    uint64_t published_context;
    Mw2erColorTarget scene[2];
    Mw2erColorTarget overlay[2];
    Mw2erColorTarget satellite_damage;
    GLuint quad_vao;
    GLuint quad_vbo;
    GLuint sprite_vao;
    GLuint sprite_vbo;
    GLuint brightness_texture;
    GLuint palette_texture;
    GLuint loading_textures[65];
    uint32_t loading_texture_count;
    uint64_t loading_generation;
    float uploaded_brightness[64];
    int brightness_valid;
    uint8_t uploaded_palette[256 * 3];
    int palette_valid;
};

static Mw2erGlState g_gl;
static GlProgram g_composite_program;
static GlProgram g_sprite_program;
static GlProgram g_satellite_damage_program;

enum { MW2ER_LAYER_COMPLETE = MW2ER_LAYER_SCENE | MW2ER_LAYER_OVERLAY };

static Mw2erGlProc load_host_gl(const char *name)
{
    return (Mw2erGlProc)mw2er_get_gl_proc_address(name);
}

static void delete_target(Mw2erColorTarget *t)
{
    if (t->fbo != 0) {
        glDeleteFramebuffers(1, &t->fbo);
    }
    if (t->color != 0) {
        glDeleteTextures(1, &t->color);
    }
    if (t->depth != 0) {
        glDeleteRenderbuffers(1, &t->depth);
    }
    t->fbo = 0;
    t->color = 0;
    t->depth = 0;
}

static void destroy_all_targets(void)
{
    mw2er_hud_gl_reset();
    mw2er_font_gl_reset();
    delete_target(&g_gl.scene[0]);
    delete_target(&g_gl.scene[1]);
    delete_target(&g_gl.overlay[0]);
    delete_target(&g_gl.overlay[1]);
    delete_target(&g_gl.satellite_damage);
}

static void delete_loading_textures(void)
{
    if (g_gl.loading_texture_count != 0)
        glDeleteTextures((GLsizei)g_gl.loading_texture_count, g_gl.loading_textures);
    g_gl.loading_texture_count = 0;
    g_gl.loading_generation = 0;
}

static void destroy_compositor(void)
{
    delete_loading_textures();
    if (g_gl.brightness_texture) glDeleteTextures(1, &g_gl.brightness_texture);
    if (g_gl.palette_texture) glDeleteTextures(1, &g_gl.palette_texture);
    if (g_gl.quad_vbo) glDeleteBuffers(1, &g_gl.quad_vbo);
    if (g_gl.sprite_vbo) glDeleteBuffers(1, &g_gl.sprite_vbo);
    if (g_gl.quad_vao) glDeleteVertexArrays(1, &g_gl.quad_vao);
    if (g_gl.sprite_vao) glDeleteVertexArrays(1, &g_gl.sprite_vao);
    g_composite_program.destroy();
    g_sprite_program.destroy();
    g_satellite_damage_program.destroy();
    g_gl.brightness_texture = g_gl.palette_texture = 0;
    g_gl.quad_vbo = g_gl.sprite_vbo = 0;
    g_gl.quad_vao = g_gl.sprite_vao = 0;
    g_gl.brightness_valid = 0;
    g_gl.palette_valid = 0;
}

static int32_t init_compositor(void)
{
    const std::string dir = mw2er_shader_dir();
    if (!g_composite_program.load((dir + "/blit.vert").c_str(),
                                  (dir + "/blit.frag").c_str()) ||
        !g_sprite_program.load((dir + "/overlay_sprite.vert").c_str(),
                               (dir + "/overlay_sprite.frag").c_str()) ||
        !g_satellite_damage_program.load(
            (dir + "/camera_view_blit.vert").c_str(),
            (dir + "/camera_view_blit.frag").c_str()))
        return MW2ER_ERR_GL;
    g_composite_program.use();
    g_composite_program.set("u_scene", 0);
    g_composite_program.set("u_overlay", 1);
    g_composite_program.set("u_monitor_brightness", 4);
    g_sprite_program.use();
    g_sprite_program.set("u_sprite", 2);
    g_sprite_program.set("u_palette", 3);
    g_sprite_program.set("u_override_index", -1);
    g_sprite_program.set2("u_vertex_origin", 0, 0);
    g_sprite_program.set2("u_vertex_scale", 1, 1);
    g_satellite_damage_program.use();
    g_satellite_damage_program.set("u_camera_view", 3);
    glUseProgram(0);
    const float quad[] = {-1.f, -1.f, 1.f, -1.f, -1.f, 1.f, 1.f, 1.f};
    glGenVertexArrays(1, &g_gl.quad_vao);
    glGenBuffers(1, &g_gl.quad_vbo);
    glBindVertexArray(g_gl.quad_vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_gl.quad_vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof(quad), quad, GL_STATIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 2 * sizeof(float), 0);
    glGenVertexArrays(1, &g_gl.sprite_vao);
    glGenBuffers(1, &g_gl.sprite_vbo);
    glBindVertexArray(g_gl.sprite_vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_gl.sprite_vbo);
    glBufferData(GL_ARRAY_BUFFER, 6 * 4 * sizeof(float), NULL, GL_STREAM_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), 0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float),
                          (const void *)(2 * sizeof(float)));
    glGenTextures(1, &g_gl.brightness_texture);
    glBindTexture(GL_TEXTURE_2D, g_gl.brightness_texture);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_R32F, 64, 1, 0, GL_RED, GL_FLOAT, NULL);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glGenTextures(1, &g_gl.palette_texture);
    glBindTexture(GL_TEXTURE_2D, g_gl.palette_texture);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, 256, 1, 0, GL_RGB, GL_UNSIGNED_BYTE, NULL);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glBindTexture(GL_TEXTURE_2D, 0);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);
    return MW2ER_OK;
}

static int32_t create_color_texture(GLuint *out, int32_t w, int32_t h, int linear)
{
    GLuint tex = 0;
    GLint filter = linear ? GL_LINEAR : GL_NEAREST;
    glGenTextures(1, &tex);
    glBindTexture(GL_TEXTURE_2D, tex);
    glTexImage2D(
        GL_TEXTURE_2D,
        0,
        GL_RGBA8,
        w,
        h,
        0,
        GL_RGBA,
        GL_UNSIGNED_BYTE,
        NULL);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, filter);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, filter);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glBindTexture(GL_TEXTURE_2D, 0);
    *out = tex;
    return MW2ER_OK;
}

static int32_t create_target(
    Mw2erColorTarget *t, int32_t w, int32_t h, int with_depth, int linear)
{
    GLenum status;

    delete_target(t);
    if (create_color_texture(&t->color, w, h, linear) != MW2ER_OK) {
        return MW2ER_ERR_GL;
    }
    if (with_depth) {
        glGenRenderbuffers(1, &t->depth);
        glBindRenderbuffer(GL_RENDERBUFFER, t->depth);
        glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24, w, h);
        glBindRenderbuffer(GL_RENDERBUFFER, 0);
    }
    glGenFramebuffers(1, &t->fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, t->fbo);
    glFramebufferTexture2D(
        GL_FRAMEBUFFER,
        GL_COLOR_ATTACHMENT0,
        GL_TEXTURE_2D,
        t->color,
        0);
    if (with_depth) {
        glFramebufferRenderbuffer(
            GL_FRAMEBUFFER,
            GL_DEPTH_ATTACHMENT,
            GL_RENDERBUFFER,
            t->depth);
    }
    status = glCheckFramebufferStatus(GL_FRAMEBUFFER);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    if (status != GL_FRAMEBUFFER_COMPLETE) {
        mw2er_set_error("framebuffer incomplete");
        delete_target(t);
        return MW2ER_ERR_GL;
    }
    return MW2ER_OK;
}

static void delete_frame_targets(
    Mw2erColorTarget scene[2], Mw2erColorTarget overlay[2])
{
    delete_target(&scene[0]);
    delete_target(&scene[1]);
    delete_target(&overlay[0]);
    delete_target(&overlay[1]);
}

static int32_t create_all_targets(int32_t logical_w, int32_t logical_h)
{
    if (!mw2er_viewport_size_ok(logical_w, logical_h)) {
        mw2er_set_error("viewport size out of range");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    int scale = mw2er_config_ssaa_scale();
    if (scale < 1) {
        scale = 1;
    }
    int linear = scale > 1;
    const int64_t scene_w64 = (int64_t)logical_w * scale;
    const int64_t scene_h64 = (int64_t)logical_h * scale;
    if (scene_w64 > (int64_t)MW2ER_MAX_VIEWPORT * 2 ||
        scene_h64 > (int64_t)MW2ER_MAX_VIEWPORT * 2) {
        mw2er_set_error("scene target size out of range");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    int32_t scene_w = (int32_t)scene_w64;
    int32_t scene_h = (int32_t)scene_h64;
    Mw2erColorTarget next_scene[2] = {};
    Mw2erColorTarget next_overlay[2] = {};
    if (create_target(&next_scene[0], scene_w, scene_h, 1, linear) != MW2ER_OK) {
        if (scale > 1) {
            mw2er_log("mw2renderer: SSAA target failed; falling back to none");
            scale = 1;
            linear = 0;
            scene_w = logical_w;
            scene_h = logical_h;
            if (create_target(&next_scene[0], scene_w, scene_h, 1, 0) != MW2ER_OK) {
                delete_frame_targets(next_scene, next_overlay);
                return MW2ER_ERR_GL;
            }
        } else {
            delete_frame_targets(next_scene, next_overlay);
            return MW2ER_ERR_GL;
        }
    }
    if (create_target(&next_scene[1], scene_w, scene_h, 1, linear) != MW2ER_OK ||
        create_target(&next_overlay[0], logical_w, logical_h, 0, 0) != MW2ER_OK ||
        create_target(&next_overlay[1], logical_w, logical_h, 0, 0) != MW2ER_OK) {
        delete_frame_targets(next_scene, next_overlay);
        return MW2ER_ERR_GL;
    }
    delete_frame_targets(g_gl.scene, g_gl.overlay);
    delete_target(&g_gl.satellite_damage);
    g_gl.scene[0] = next_scene[0];
    g_gl.scene[1] = next_scene[1];
    g_gl.overlay[0] = next_overlay[0];
    g_gl.overlay[1] = next_overlay[1];
    g_gl.logical_w = logical_w;
    g_gl.logical_h = logical_h;
    g_gl.scene_w = scene_w;
    g_gl.scene_h = scene_h;
    g_gl.sample_scale = scale;
    g_gl.staging = 0;
    g_gl.published = 0;
    g_gl.published_valid = 0;
    g_gl.staging_complete = 0;
    g_gl.staging_overlay_present = 0;
    return MW2ER_OK;
}

int32_t mw2er_gl_on_context(const Mw2erViewport *vp)
{
    if (g_gl.loaded) {
        mw2er_set_error("previous GL context must be released before initialization");
        return MW2ER_ERR_GL;
    }
    if (mw2er_load_gl(load_host_gl) == 0) {
        mw2er_set_error("OpenGL 3.3 context or required entry points unavailable");
        return MW2ER_ERR_GL;
    }
    g_gl.loaded = 1;
    {
        const GLubyte *version = glGetString(GL_VERSION);
        const GLubyte *vendor = glGetString(GL_VENDOR);
        char message[512];
        snprintf(
            message,
            sizeof(message),
            "mw2renderer: host GL context version=%s vendor=%s",
            version ? (const char *)version : "unknown",
            vendor ? (const char *)vendor : "unknown");
        mw2er_log(message);
    }
    if (create_all_targets(vp->mod_w, vp->mod_h) != MW2ER_OK ||
        init_compositor() != MW2ER_OK ||
        mw2er_scene_resources_init() != MW2ER_OK) {
        mw2er_gl_shutdown();
        return MW2ER_ERR_GL;
    }
    g_gl.context_generation = vp->context_generation;
    g_gl.ready = 1;
    mw2er_log("mw2renderer: gl context ready");
    return MW2ER_OK;
}

uint64_t mw2er_gl_context_generation(void)
{
    return g_gl.ready ? g_gl.context_generation : 0;
}

void mw2er_gl_shutdown(void)
{
    if (g_gl.loaded) {
        mw2er_scene_resources_shutdown();
        destroy_compositor();
        destroy_all_targets();
    }
    memset(&g_gl, 0, sizeof(g_gl));
}

void mw2er_gl_invalidate_publication(void)
{
    g_gl.published_valid = 0;
    g_gl.staging_complete = 0;
}

int32_t mw2er_gl_ensure_size(int32_t width, int32_t height)
{
    if (!g_gl.ready) {
        mw2er_set_error("GL not ready");
        return MW2ER_ERR_NOT_READY;
    }
    if (width == g_gl.logical_w && height == g_gl.logical_h) {
        return MW2ER_OK;
    }
    return create_all_targets(width, height);
}

int32_t mw2er_gl_begin_frame(void)
{
    if (!g_gl.ready) {
        mw2er_set_error("GL not ready");
        return MW2ER_ERR_NOT_READY;
    }
    g_gl.staging_complete = 0;
    g_gl.staging_overlay_present = 0;
    return MW2ER_OK;
}

int32_t mw2er_gl_render_scene(void)
{
    Mw2erColorTarget *t;

    if (!g_gl.ready) {
        mw2er_set_error("GL not ready");
        return MW2ER_ERR_NOT_READY;
    }
    t = &g_gl.scene[g_gl.staging];
    int32_t damage_viewport[4];
    if (mw2er_hud_satellite_damage_viewport(damage_viewport)) {
        /* The native damage animation changes the active window, but its
         * scratch allocation stays at the 320x240 reference maximum. Scale
         * that maximum by output height and preserve the output aspect. */
        const int32_t native_w = damage_viewport[2] - damage_viewport[0] + 1;
        const int32_t native_h = damage_viewport[3] - damage_viewport[1] + 1;
        const double vertical_scale = g_gl.logical_h / 768.0;
        const int32_t target_h = std::max(
            1, (int32_t)std::nearbyint(240.0 * vertical_scale));
        const int32_t target_w = std::max(1, (int32_t)std::nearbyint(
            240.0 * vertical_scale * g_gl.logical_w / g_gl.logical_h));
        const int32_t maximum_w = std::min({g_gl.logical_w, target_w,
            std::max(1, (int32_t)std::nearbyint(
                target_w * native_w / 320.0))});
        const int32_t maximum_h = std::min({g_gl.logical_h, target_h,
            std::max(1, (int32_t)std::nearbyint(
                target_h * native_h / 240.0))});
        const double output_aspect =
            g_gl.logical_w / (double)g_gl.logical_h;
        int32_t damage_w = maximum_w;
        int32_t damage_h = std::max(
            1, (int32_t)std::nearbyint(damage_w / output_aspect));
        if (damage_h > maximum_h) {
            damage_h = maximum_h;
            damage_w = std::max(
                1, (int32_t)std::nearbyint(damage_h * output_aspect));
        }
        const int32_t target_physical_w = target_w * g_gl.sample_scale;
        const int32_t target_physical_h = target_h * g_gl.sample_scale;
        if (!g_gl.satellite_damage.fbo) {
            if (create_target(&g_gl.satellite_damage,
                              target_physical_w, target_physical_h, 1,
                              g_gl.sample_scale > 1) != MW2ER_OK)
                return MW2ER_ERR_GL;
        }
        const int32_t render_w = damage_w * g_gl.sample_scale;
        const int32_t render_h = damage_h * g_gl.sample_scale;
        glBindFramebuffer(GL_FRAMEBUFFER, g_gl.satellite_damage.fbo);
        glViewport(0, 0, render_w, render_h);
        if (mw2er_scene_draw(damage_w, damage_h, render_w, render_h) !=
                MW2ER_OK ||
            !mw2er_hud_render_satellite_damage_radar(render_w, render_h)) {
            glBindFramebuffer(GL_FRAMEBUFFER, 0);
            return MW2ER_ERR_GL;
        }

        /* Resolve into the ordinary scene target. The shader's SSAA path
         * repeats each nearest resolved damage pixel over one 2x2 output
         * block so the final linear compositor cannot blur it a second time. */
        const float max_u = render_w / (float)target_physical_w;
        const float max_v = render_h / (float)target_physical_h;
        const float vertices[] = {
            0,0,0,max_v, 0,(float)g_gl.scene_h,0,0,
            (float)g_gl.scene_w,0,max_u,max_v,
            (float)g_gl.scene_w,(float)g_gl.scene_h,max_u,0,
        };
        glBindFramebuffer(GL_FRAMEBUFFER, t->fbo);
        glViewport(0, 0, g_gl.scene_w, g_gl.scene_h);
        glDisable(GL_BLEND);
        glDisable(GL_DEPTH_TEST);
        glDisable(GL_CULL_FACE);
        glDisable(GL_SCISSOR_TEST);
        g_satellite_damage_program.use();
        g_satellite_damage_program.set2(
            "u_viewport_size", (float)g_gl.scene_w, (float)g_gl.scene_h);
        g_satellite_damage_program.set(
            "u_resolve_satellite_damage", g_gl.sample_scale > 1);
        const GLint source_size =
            g_satellite_damage_program.loc("u_source_logical_size");
        const GLint destination_size =
            g_satellite_damage_program.loc("u_destination_logical_size");
        if (source_size >= 0) glUniform2i(source_size, damage_w, damage_h);
        if (destination_size >= 0)
            glUniform2i(destination_size, g_gl.logical_w, g_gl.logical_h);
        glActiveTexture(GL_TEXTURE3);
        glBindTexture(GL_TEXTURE_2D, g_gl.satellite_damage.color);
        glBindVertexArray(g_gl.sprite_vao);
        glBindBuffer(GL_ARRAY_BUFFER, g_gl.sprite_vbo);
        glBufferSubData(GL_ARRAY_BUFFER, 0, sizeof(vertices), vertices);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        glBindBuffer(GL_ARRAY_BUFFER, 0);
        glBindVertexArray(0);
        glBindTexture(GL_TEXTURE_2D, 0);
        glUseProgram(0);
    } else {
        glBindFramebuffer(GL_FRAMEBUFFER, t->fbo);
        glViewport(0, 0, g_gl.scene_w, g_gl.scene_h);
        if (mw2er_scene_draw(g_gl.logical_w, g_gl.logical_h,
                             g_gl.scene_w, g_gl.scene_h) != MW2ER_OK) {
            glBindFramebuffer(GL_FRAMEBUFFER, 0);
            return MW2ER_ERR_GL;
        }
    }
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    g_gl.staging_complete |= MW2ER_LAYER_SCENE;
    return MW2ER_OK;
}

int32_t mw2er_gl_render_hud(void)
{
    if (!g_gl.ready) {
        mw2er_set_error("GL not ready");
        return MW2ER_ERR_NOT_READY;
    }
    if (mw2er_hud_render(g_gl.overlay[g_gl.staging].fbo,
                         g_gl.logical_w, g_gl.logical_h,
                         g_gl.sample_scale) != MW2ER_OK) {
        return MW2ER_ERR_GL;
    }
    g_gl.staging_overlay_present = 1;
    g_gl.staging_complete |= MW2ER_LAYER_OVERLAY;
    return MW2ER_OK;
}

int32_t mw2er_gl_publish(void)
{
    if (!g_gl.ready) {
        mw2er_set_error("GL not ready");
        return MW2ER_ERR_NOT_READY;
    }
    if (g_gl.staging_complete != MW2ER_LAYER_COMPLETE) {
        mw2er_set_error("publish: incomplete frame");
        return MW2ER_ERR_NOT_READY;
    }
    g_gl.published = g_gl.staging;
    g_gl.published_valid = 1;
    g_gl.published_overlay_present = g_gl.staging_overlay_present;
    g_gl.published_context = g_gl.context_generation;
    g_gl.staging = 1 - g_gl.staging;
    g_gl.staging_complete = 0;
    return MW2ER_OK;
}

static void restore_host_baseline(const Mw2erViewport *vp)
{
    glBindFramebuffer(GL_FRAMEBUFFER, (GLuint)vp->backbuffer_fbo);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, 0);
    glBindVertexArray(0);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0);
    glUseProgram(0);
    glDisable(GL_BLEND);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_CULL_FACE);
    glDisable(GL_SCISSOR_TEST);
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    glDepthMask(GL_TRUE);
}

static GLuint upload_sprite(const Mw2erSprite& sprite)
{
    GLuint texture = 0;
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RG8, sprite.width, sprite.height, 0,
                 GL_RG, GL_UNSIGNED_BYTE, sprite.pixels.data());
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    return texture;
}

static void bind_indexed_palette(const uint8_t *palette)
{
    glActiveTexture(GL_TEXTURE3);
    glBindTexture(GL_TEXTURE_2D, g_gl.palette_texture);
    if (g_gl.palette_valid &&
        std::memcmp(g_gl.uploaded_palette, palette,
                    sizeof(g_gl.uploaded_palette)) == 0) return;
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, 256, 1, GL_RGB,
                    GL_UNSIGNED_BYTE, palette);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    std::memcpy(g_gl.uploaded_palette, palette, sizeof(g_gl.uploaded_palette));
    g_gl.palette_valid = 1;
}

uint32_t mw2er_gl_upload_indexed_sprite(const Mw2erSprite &sprite)
{
    if (!g_gl.ready || sprite.width <= 0 || sprite.height <= 0 ||
        sprite.pixels.size() != (size_t)sprite.width * sprite.height * 2)
        return 0;
    return upload_sprite(sprite);
}

int32_t mw2er_gl_draw_indexed_sprites(
    const Mw2erSprite &sprite, uint32_t texture,
    const Mw2erIndexedSpriteDraw *draws, int32_t draw_count,
    const uint8_t *palette,
    int32_t viewport_width, int32_t viewport_height)
{
    if (!g_gl.ready || !texture || !draws || draw_count < 0 || !palette ||
        viewport_width <= 0 || viewport_height <= 0)
        return MW2ER_ERR_INVALID_ARGUMENT;
    g_sprite_program.use();
    g_sprite_program.set2("u_viewport_size", (float)viewport_width,
                          (float)viewport_height);
    g_sprite_program.set2("u_vertex_origin", 0.0f, 0.0f);
    g_sprite_program.set2("u_vertex_scale", 1.0f, 1.0f);
    g_sprite_program.set("u_brightness", 1.0f);
    bind_indexed_palette(palette);
    glActiveTexture(GL_TEXTURE2);
    glBindTexture(GL_TEXTURE_2D, texture);
    glBindVertexArray(g_gl.sprite_vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_gl.sprite_vbo);
    glDisable(GL_BLEND);
    glEnable(GL_SCISSOR_TEST);
    for (int32_t i = 0; i < draw_count; ++i) {
        const Mw2erIndexedSpriteDraw &draw = draws[i];
        if (draw.scale_x <= 0.0f || draw.scale_y <= 0.0f) continue;
        const int32_t clip_left = std::clamp(draw.clip_left, 0, viewport_width);
        const int32_t clip_right = std::clamp(draw.clip_right, 0, viewport_width);
        const int32_t clip_top = std::clamp(draw.clip_top, 0, viewport_height);
        const int32_t clip_bottom = std::clamp(draw.clip_bottom, 0, viewport_height);
        if (clip_right <= clip_left || clip_bottom <= clip_top) continue;
        const float x0 = draw.x + sprite.x_offset * draw.scale_x;
        const float y0 = draw.y + sprite.y_offset * draw.scale_y;
        const float x1 = x0 + sprite.width * draw.scale_x;
        const float y1 = y0 + sprite.height * draw.scale_y;
        const float vertices[] = {
            x0,y0,0,0, x1,y0,1,0, x0,y1,0,1,
            x0,y1,0,1, x1,y0,1,0, x1,y1,1,1};
        g_sprite_program.set("u_override_index", draw.color_override);
        glBufferSubData(GL_ARRAY_BUFFER, 0, sizeof(vertices), vertices);
        glScissor(clip_left, viewport_height - clip_bottom,
                  clip_right - clip_left, clip_bottom - clip_top);
        glDrawArrays(GL_TRIANGLES, 0, 6);
    }
    glDisable(GL_SCISSOR_TEST);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);
    glBindTexture(GL_TEXTURE_2D, 0);
    glUseProgram(0);
    return MW2ER_OK;
}

static int32_t prepare_loading(const Mw2erLoadingVisual& visual)
{
    if (g_gl.loading_generation != visual.generation) {
        delete_loading_textures();
        if (visual.strips.size() + 1 > 65)
            return MW2ER_ERR_INVALID_ARGUMENT;
        g_gl.loading_textures[g_gl.loading_texture_count++] = upload_sprite(visual.background);
        for (const Mw2erSprite& sprite : visual.strips)
            g_gl.loading_textures[g_gl.loading_texture_count++] = upload_sprite(sprite);
        g_gl.loading_generation = visual.generation;
    }
    bind_indexed_palette(visual.palette);
    return MW2ER_OK;
}

static void draw_loading_sprite(const Mw2erSprite& sprite, GLuint texture, float x, float y)
{
    const float x0 = x + sprite.x_offset;
    const float y0 = y + sprite.y_offset;
    const float x1 = x0 + sprite.width;
    const float y1 = y0 + sprite.height;
    const float v[] = {
        x0,y0,0,0, x1,y0,1,0, x0,y1,0,1,
        x0,y1,0,1, x1,y0,1,0, x1,y1,1,1};
    glBindBuffer(GL_ARRAY_BUFFER, g_gl.sprite_vbo);
    glBufferSubData(GL_ARRAY_BUFFER, 0, sizeof(v), v);
    glActiveTexture(GL_TEXTURE2);
    glBindTexture(GL_TEXTURE_2D, texture);
    glDrawArrays(GL_TRIANGLES, 0, 6);
}

static int32_t composite_loading(const Mw2erViewport *vp, const Mw2erPresentation& p)
{
    glBindFramebuffer(GL_FRAMEBUFFER, (GLuint)vp->backbuffer_fbo);
    glViewport(vp->mod_x, vp->mod_y, vp->mod_w, vp->mod_h);
    glDisable(GL_BLEND);
    glDisable(GL_DEPTH_TEST);
    glEnable(GL_SCISSOR_TEST);
    glScissor(vp->mod_x, vp->mod_y, vp->mod_w, vp->mod_h);
    glClearColor(0, 0, 0, 1);
    glClear(GL_COLOR_BUFFER_BIT);
    glDisable(GL_SCISSOR_TEST);
    if (p.loading == nullptr)
        return MW2ER_OK;
    if (prepare_loading(*p.loading) != MW2ER_OK)
        return MW2ER_ERR_GL;
    g_sprite_program.use();
    g_sprite_program.set2("u_viewport_size", (float)vp->mod_w, (float)vp->mod_h);
    g_sprite_program.set("u_brightness", p.loading_brightness);
    glActiveTexture(GL_TEXTURE3);
    glBindTexture(GL_TEXTURE_2D, g_gl.palette_texture);
    glBindVertexArray(g_gl.sprite_vao);
    const float ox = ((float)vp->mod_w - 1024.0f) * 0.5f + p.loading->clip_x;
    const float oy = ((float)vp->mod_h - 768.0f) * 0.5f + p.loading->clip_y;
    draw_loading_sprite(p.loading->background, g_gl.loading_textures[0], ox, oy);
    if (!p.loading->strips.empty() && p.strip_index >= 0) {
        const size_t index = (size_t)p.strip_index % p.loading->strips.size();
        draw_loading_sprite(p.loading->strips[index], g_gl.loading_textures[index + 1],
                            ox + p.loading->strip_x, oy + p.loading->strip_y);
    }
    return MW2ER_OK;
}

int32_t mw2er_gl_composite(const Mw2erViewport *vp, const Mw2erPresentation *presentation)
{
    Mw2erColorTarget *scene;
    int32_t x;
    int32_t y;
    int32_t w;
    int32_t h;

    if (!g_gl.ready) {
        mw2er_set_error("GL not ready");
        return MW2ER_ERR_NOT_READY;
    }
    if (vp == NULL || vp->struct_size < sizeof(Mw2erViewport)) {
        mw2er_set_error("composite: invalid viewport");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    if (vp->context_generation != g_gl.context_generation) {
        mw2er_set_error("composite: stale GL context");
        return MW2ER_ERR_NOT_READY;
    }
    scene = &g_gl.scene[g_gl.published];
    x = vp->mod_x;
    y = vp->mod_y;
    w = vp->mod_w;
    h = vp->mod_h;
    if (w <= 0 || h <= 0) {
        mw2er_set_error("composite: empty viewport");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    if (presentation == NULL || presentation->kind == MW2ER_PRESENT_NOT_READY) {
        mw2er_set_error("composite: no presentable frame");
        return MW2ER_ERR_NOT_READY;
    }
    if (presentation->kind == MW2ER_PRESENT_LOADING) {
        const int32_t result = composite_loading(vp, *presentation);
        restore_host_baseline(vp);
        return result;
    }
    if (!g_gl.published_valid || g_gl.published_context != vp->context_generation ||
        g_gl.logical_w != vp->mod_w || g_gl.logical_h != vp->mod_h) {
        mw2er_set_error("composite: stale publication");
        return MW2ER_ERR_NOT_READY;
    }
    if (presentation->brightness_identity && presentation->fade <= 0.0f &&
        !g_gl.published_overlay_present) {
        glBindFramebuffer(GL_READ_FRAMEBUFFER, scene->fbo);
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, (GLuint)vp->backbuffer_fbo);
        glBlitFramebuffer(
            0,
            0,
            g_gl.scene_w,
            g_gl.scene_h,
            x,
            y,
            x + w,
            y + h,
            GL_COLOR_BUFFER_BIT,
            g_gl.sample_scale > 1 ? GL_LINEAR : GL_NEAREST);
    } else {
        glBindFramebuffer(GL_FRAMEBUFFER, (GLuint)vp->backbuffer_fbo);
        glViewport(x, y, w, h);
        glDisable(GL_BLEND);
        glDisable(GL_DEPTH_TEST);
        glDisable(GL_CULL_FACE);
        g_composite_program.use();
        g_composite_program.set("u_has_overlay", (int)g_gl.published_overlay_present);
        g_composite_program.set("u_fade_progress", presentation->fade);
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, scene->color);
        if (g_gl.published_overlay_present) {
            glActiveTexture(GL_TEXTURE1);
            glBindTexture(GL_TEXTURE_2D, g_gl.overlay[g_gl.published].color);
        }
        glActiveTexture(GL_TEXTURE4);
        glBindTexture(GL_TEXTURE_2D, g_gl.brightness_texture);
        if (presentation->brightness == NULL) {
            restore_host_baseline(vp);
            return MW2ER_ERR_INVALID_ARGUMENT;
        }
        if (!g_gl.brightness_valid ||
            memcmp(g_gl.uploaded_brightness, presentation->brightness,
                   sizeof(g_gl.uploaded_brightness)) != 0) {
            glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, 64, 1, GL_RED, GL_FLOAT,
                            presentation->brightness);
            memcpy(g_gl.uploaded_brightness, presentation->brightness,
                   sizeof(g_gl.uploaded_brightness));
            g_gl.brightness_valid = 1;
        }
        glBindVertexArray(g_gl.quad_vao);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    }
    restore_host_baseline(vp);
    return MW2ER_OK;
}

int32_t mw2er_gl_published_scene(uint32_t *fbo, int32_t *width, int32_t *height)
{
    Mw2erColorTarget *scene;

    if (!g_gl.ready) {
        mw2er_set_error("GL not ready");
        return MW2ER_ERR_NOT_READY;
    }
    if (!g_gl.published_valid) {
        mw2er_set_error("published_scene: no published frame");
        return MW2ER_ERR_NOT_READY;
    }
    if (fbo == NULL || width == NULL || height == NULL) {
        mw2er_set_error("published_scene: null out");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    scene = &g_gl.scene[g_gl.published];
    *fbo = scene->fbo;
    *width = g_gl.scene_w;
    *height = g_gl.scene_h;
    return MW2ER_OK;
}
