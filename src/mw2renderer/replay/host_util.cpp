#include "host_util.h"
#include "../renderer/presentation.h"

#include <SDL.h>
#include "gl_api.h"

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { MW2ER_PATH_BUF = 1024 };

static const char *renderer_module_name(void)
{
#if defined(_WIN32)
    return "mw2renderer.dll";
#elif defined(__APPLE__)
    return "libmw2renderer.dylib";
#else
    return "libmw2renderer.so";
#endif
}

const Mw2erApi *mw2er_host_load_api(const char *dll_path_or_null)
{
    char path_buf[MW2ER_PATH_BUF];
    const char *path;
    void *lib;
    const Mw2erApi *(*get_api)(void);

    path = dll_path_or_null;
    if (path == NULL || path[0] == '\0') {
        char *base = SDL_GetBasePath();
        if (base == NULL) {
            fprintf(stderr, "SDL_GetBasePath failed: %s\n", SDL_GetError());
            return NULL;
        }
        snprintf(path_buf, sizeof(path_buf), "%s%s", base, renderer_module_name());
        SDL_free(base);
        path = path_buf;
    }
    lib = SDL_LoadObject(path);
    if (lib == NULL) {
        fprintf(stderr, "SDL_LoadObject failed: %s\n", SDL_GetError());
        return NULL;
    }
    get_api = (const Mw2erApi *(*)(void))SDL_LoadFunction(lib, "mw2er_get_api");
    if (get_api == NULL) {
        fprintf(stderr, "SDL_LoadFunction(mw2er_get_api) failed: %s\n", SDL_GetError());
        return NULL;
    }
    return get_api();
}

static unsigned char *read_fbo_top_down(uint32_t fbo, int width, int height)
{
    unsigned char *pixels;
    unsigned char *flipped;
    int row;

    pixels = (unsigned char *)malloc((size_t)width * (size_t)height * 4u);
    flipped = (unsigned char *)malloc((size_t)width * (size_t)height * 4u);
    if (pixels == NULL || flipped == NULL) {
        free(pixels);
        free(flipped);
        return NULL;
    }
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    for (row = 0; row < height; ++row) {
        memcpy(
            flipped + (size_t)row * (size_t)width * 4u,
            pixels + (size_t)(height - 1 - row) * (size_t)width * 4u,
            (size_t)width * 4u);
    }
    free(pixels);
    return flipped;
}

int mw2er_host_write_png_fbo(const char *path, uint32_t fbo, int width, int height)
{
    unsigned char *flipped;
    int ok;

    if (path == NULL || width <= 0 || height <= 0) {
        return 0;
    }
    flipped = read_fbo_top_down(fbo, width, height);
    if (flipped == NULL) {
        return 0;
    }
    ok = stbi_write_png(path, width, height, 4, flipped, width * 4);
    free(flipped);
    return ok != 0;
}

int mw2er_host_write_png_fbo_box2x(
    const char *path, uint32_t fbo, int width, int height)
{
    unsigned char *src;
    unsigned char *dst;
    int dw;
    int dh;
    int x;
    int y;
    int c;
    int ok;

    if (path == NULL || width < 2 || height < 2 || (width & 1) || (height & 1)) {
        return 0;
    }
    src = read_fbo_top_down(fbo, width, height);
    if (src == NULL) {
        return 0;
    }
    dw = width / 2;
    dh = height / 2;
    dst = (unsigned char *)malloc((size_t)dw * (size_t)dh * 4u);
    if (dst == NULL) {
        free(src);
        return 0;
    }
    for (y = 0; y < dh; ++y) {
        for (x = 0; x < dw; ++x) {
            const unsigned char *p00 =
                src + ((size_t)(2 * y) * (size_t)width + (size_t)(2 * x)) * 4u;
            const unsigned char *p10 = p00 + 4;
            const unsigned char *p01 = p00 + (size_t)width * 4u;
            const unsigned char *p11 = p01 + 4;
            unsigned char *o = dst + ((size_t)y * (size_t)dw + (size_t)x) * 4u;
            for (c = 0; c < 4; ++c) {
                unsigned int s = (unsigned int)p00[c] + p10[c] + p01[c] + p11[c];
                o[c] = (unsigned char)((s + 2u) / 4u);
            }
        }
    }
    ok = stbi_write_png(path, dw, dh, 4, dst, dw * 4);
    free(src);
    free(dst);
    return ok != 0;
}

int mw2er_host_write_png(const char *path, int width, int height)
{
    return mw2er_host_write_png_fbo(path, 0, width, height);
}

uint64_t mw2er_host_ticks(void)
{
    return (uint64_t)SDL_GetPerformanceCounter();
}

double mw2er_host_ms_since(uint64_t start_ticks)
{
    Uint64 freq = SDL_GetPerformanceFrequency();
    if (freq == 0) {
        return 0.0;
    }
    return (double)(SDL_GetPerformanceCounter() - (Uint64)start_ticks) *
        1000.0 / (double)freq;
}

// Standalone hosts use the same transaction as DOSBox-X. Each run owns one
// session and mission, with generation 1; captured guest memory is borrowed.
int32_t mw2er_host_begin_mission(const Mw2erApi *api)
{
    Mw2erSessionInfo session = {};
    session.struct_size = sizeof(session);
    session.session_generation = 1;
    session.profile_id = "standalone";
    session.executable_name = "MW2.EXE";
    int32_t result = api->begin_session(&session);
    if (result != MW2ER_OK) return result;
    Mw2erMissionInfo mission = {};
    mission.struct_size = sizeof(mission);
    mission.session_generation = mission.mission_generation = mission.resource_generation = 1;
    return api->begin_mission(&mission);
}

int32_t mw2er_host_frame(const Mw2erApi *api, const Mw2erMemoryView *mem,
                         const Mw2erViewport *vp, uint64_t frame, int composite)
{
    Mw2erCaptureInput capture = {};
    capture.struct_size = sizeof(capture);
    capture.event = MW2ER_EVENT_PRIMARY;
    capture.session_generation = capture.mission_generation = capture.resource_generation = 1;
    capture.memory = *mem;
    capture.viewport = *vp;
    capture.frame.struct_size = sizeof(capture.frame);
    capture.frame.frame = frame;
    int32_t result = api->capture(&capture);
    if (result != MW2ER_OK) return result;
    result = api->seal_frame(frame);
    if (result != MW2ER_OK) return result;
    Mw2erRenderRequest request = {};
    request.struct_size = sizeof(request);
    request.session_generation = request.mission_generation = request.resource_generation = 1;
    request.frame = frame;
    request.required_layers = MW2ER_LAYER_SCENE | MW2ER_LAYER_OVERLAY;
    request.viewport = *vp;
    result = api->render_frame(&request);
    if (result != MW2ER_OK) return result;
    Mw2erPublishResult published = {};
    published.struct_size = sizeof(published);
    result = api->publish_frame(&published);
    if (result != MW2ER_OK || !composite) return result;
    Mw2erPresentInfo present = {};
    present.struct_size = sizeof(present);
    present.viewport = *vp;
    present.present_count = frame;
    Mw2erPresentResult presented = {};
    presented.struct_size = sizeof(presented);
    return api->composite_frame(&present, &presented);
}
