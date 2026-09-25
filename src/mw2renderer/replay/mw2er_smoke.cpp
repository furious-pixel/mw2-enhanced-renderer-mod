#include "host_util.h"
#include "host_window.h"

#include "gl_api.h"

#include <stdio.h>
#include <string.h>

enum { SMOKE_W = 320 };
enum { SMOKE_H = 240 };

static void host_log(const char *msg)
{
    fprintf(stderr, "%s\n", msg);
}

static Mw2erGlProc load_gl(const char *name)
{
    return (Mw2erGlProc)mw2er_host_gl_proc(name);
}

int main(int argc, char **argv)
{
    const Mw2erApi *api;
    Mw2erHostWindow window;
    Mw2erInit init;
    Mw2erViewport vp;
    Mw2erMemoryView mem;
    Mw2erFrameInfo frame;
    uint8_t dummy_mem[64];
    int32_t rc;
    const char *png_path;

#ifdef MW2ER_ARTIFACTS_DIR
    png_path = (argc > 1) ? argv[1] : MW2ER_ARTIFACTS_DIR "/mw2er_stub_frame.png";
#else
    png_path = (argc > 1) ? argv[1] : "mw2er_stub_frame.png";
#endif

    if (!mw2er_host_init()) {
        return 1;
    }
    api = mw2er_host_load_api(NULL);
    if (api == NULL) {
        mw2er_host_quit();
        return 1;
    }
    if (api->abi_version != MW2ER_ABI_VERSION || api->struct_size < sizeof(Mw2erApi)) {
        fprintf(stderr, "unsupported abi %u\n", api->abi_version);
        mw2er_host_quit();
        return 1;
    }
    if (!mw2er_host_window_open(&window, SMOKE_W, SMOKE_H, 0, "mw2renderer-smoke")) {
        mw2er_host_quit();
        return 1;
    }
    if (mw2er_load_gl(load_gl) == 0) {
        fprintf(stderr, "host mw2er_load_gl failed\n");
        mw2er_host_window_destroy(&window);
        mw2er_host_quit();
        return 1;
    }

    memset(&init, 0, sizeof(init));
    init.struct_size = sizeof(init);
    init.log = host_log;
    init.get_gl_proc_address = mw2er_host_gl_proc;
#ifdef MW2ER_DEFAULT_MOD_DIR
    init.mod_dir = MW2ER_DEFAULT_MOD_DIR;
#endif
    rc = api->init(&init);
    if (rc != MW2ER_OK) {
        fprintf(stderr, "init failed: %s\n", api->last_error());
        return 1;
    }

    memset(&vp, 0, sizeof(vp));
    vp.struct_size = sizeof(vp);
    vp.mod_w = SMOKE_W;
    vp.mod_h = SMOKE_H;
    vp.backbuffer_w = SMOKE_W;
    vp.backbuffer_h = SMOKE_H;
    vp.backbuffer_fbo = 0;
    vp.context_generation = 1;
    vp.view_mode = MW2ER_VIEW_MOD_ONLY;
    rc = api->on_gl_context(&vp);
    if (rc != MW2ER_OK) {
        fprintf(stderr, "on_gl_context failed: %s\n", api->last_error());
        return 1;
    }

    memset(dummy_mem, 0, sizeof(dummy_mem));
    dummy_mem[0] = 0x11;
    dummy_mem[1] = 0x22;
    dummy_mem[2] = 0x33;
    dummy_mem[3] = 0x44;
    memset(&mem, 0, sizeof(mem));
    mem.struct_size = sizeof(mem);
    mem.bytes = dummy_mem;
    mem.size = (uint32_t)sizeof(dummy_mem);
    mem.runtime_base = 0x170000;
    mem.delta = 0;
    rc = mw2er_host_begin_mission(api);
    if (rc != MW2ER_OK) {
        fprintf(stderr, "mission_begin failed: %s\n", api->last_error());
        return 1;
    }
    memset(&frame, 0, sizeof(frame));
    frame.struct_size = sizeof(frame);
    frame.frame = 1;
    rc = mw2er_host_frame(api, &mem, &vp, frame.frame, 1);
    if (rc != MW2ER_OK) {
        fprintf(stderr, "frame transaction failed: %s\n", api->last_error());
        return 1;
    }

    if (!mw2er_host_write_png(png_path, SMOKE_W, SMOKE_H)) {
        fprintf(stderr, "png write failed: %s\n", png_path);
        return 1;
    }
    fprintf(stdout, "wrote %s\n", png_path);

    api->end_mission(1);
    api->end_session(1);
    api->on_gl_context_lost();
    api->shutdown();
    mw2er_host_window_destroy(&window);
    mw2er_host_quit();
    return 0;
}
