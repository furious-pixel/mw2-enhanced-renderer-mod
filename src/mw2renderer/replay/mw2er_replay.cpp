#include "dump.h"
#include "host_util.h"
#include "host_window.h"

#include "gl_api.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>

#ifdef _WIN32
#include <errno.h>
#endif

enum { MW2ER_PATH_BUF = 1024 };

struct ReplayOptions {
    const char *dump_path;
    const char *png_path;
    const char *scene_png_path;
    const char *dll_path;
    const char *mod_dir;
    const char *config_path;
    int width;
    int height;
    int frames;
    int warmup;
    int batch_size;
    int visible;
    int scene_target_only;
    int bench;
    int verbose;
    int retained;
};

static void host_log(const char *msg)
{
    fprintf(stderr, "%s\n", msg);
}

static void usage(void)
{
    fprintf(
        stderr,
        "usage: mw2er_replay <dump.bin> [options]\n"
        "  -o PATH                 composited backbuffer PNG (default: dump stem + _mw2renderer.png)\n"
        "  --output-scene-image P  scene PNG (no HUD); 2x2 box downsample if SSAA\n"
        "  --scene-target-only     skip composite to the backbuffer\n"
        "  --width N               viewport width (default 1024)\n"
        "  --height N              viewport height (default 768)\n"
        "  --frames N              timed ABI frames (default 1; 240 with --bench)\n"
        "  --warmup N              untimed frames before capture/timing (default 0; 32 with --bench)\n"
        "  --batch-size N          GPU timer batch (default 1)\n"
        "  --bench                 warmup 32 + 240 timed frames, GPU/CPU report, no default PNG\n"
        "  --workload full|retained  full extracts every frame; retained redraws first extract\n"
        "  --visible               show the SDL window\n"
        "  --verbose               keep per-frame renderer logs during bench\n"
        "  --dll PATH              renderer DLL (default: next to this exe)\n"
        "  --mod-dir PATH          mw2mods root passed to mw2renderer\n"
        "  --config PATH           explicit mod.conf (overrides mod-dir)\n");
}

static int parse_i32(const char *s, int *out)
{
    char *end = NULL;
    long v;

    if (s == NULL || out == NULL) {
        return 0;
    }
    v = strtol(s, &end, 10);
    if (end == s || *end != '\0' || v <= 0 || v > 16384) {
        return 0;
    }
    *out = (int)v;
    return 1;
}

static int parse_args(int argc, char **argv, ReplayOptions *opt)
{
    int i;
    int frames_set = 0;
    int warmup_set = 0;

    memset(opt, 0, sizeof(*opt));
    opt->width = 1024;
    opt->height = 768;
    opt->frames = 1;
    opt->warmup = 0;
    opt->batch_size = 1;
    if (argc < 2) {
        usage();
        return 0;
    }
    for (i = 1; i < argc; ++i) {
        if (argv[i][0] != '-') {
            if (opt->dump_path != NULL) {
                fprintf(stderr, "unexpected argument: %s\n", argv[i]);
                return 0;
            }
            opt->dump_path = argv[i];
            continue;
        }
        if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            usage();
            return 0;
        }
        if (strcmp(argv[i], "--visible") == 0) {
            opt->visible = 1;
            continue;
        }
        if (strcmp(argv[i], "--scene-target-only") == 0) {
            opt->scene_target_only = 1;
            continue;
        }
        if (strcmp(argv[i], "--bench") == 0) {
            opt->bench = 1;
            continue;
        }
        if (strcmp(argv[i], "--verbose") == 0) {
            opt->verbose = 1;
            continue;
        }
        if (strcmp(argv[i], "--workload") == 0) {
            if (i + 1 >= argc) {
                fprintf(stderr, "missing value for --workload\n");
                return 0;
            }
            const char *w = argv[++i];
            if (strcmp(w, "retained") == 0) {
                opt->retained = 1;
            } else if (strcmp(w, "full") == 0) {
                opt->retained = 0;
            } else {
                fprintf(stderr, "bad --workload (use full or retained)\n");
                return 0;
            }
            continue;
        }
        if (i + 1 >= argc) {
            fprintf(stderr, "missing value for %s\n", argv[i]);
            return 0;
        }
        if (strcmp(argv[i], "-o") == 0 || strcmp(argv[i], "--output") == 0) {
            opt->png_path = argv[++i];
        } else if (strcmp(argv[i], "--output-scene-image") == 0) {
            opt->scene_png_path = argv[++i];
        } else if (strcmp(argv[i], "--width") == 0) {
            if (!parse_i32(argv[++i], &opt->width)) {
                fprintf(stderr, "bad --width\n");
                return 0;
            }
        } else if (strcmp(argv[i], "--height") == 0) {
            if (!parse_i32(argv[++i], &opt->height)) {
                fprintf(stderr, "bad --height\n");
                return 0;
            }
        } else if (strcmp(argv[i], "--frames") == 0) {
            if (!parse_i32(argv[++i], &opt->frames)) {
                fprintf(stderr, "bad --frames\n");
                return 0;
            }
            frames_set = 1;
        } else if (strcmp(argv[i], "--warmup") == 0) {
            char *end = NULL;
            long v = strtol(argv[++i], &end, 10);
            if (end == argv[i] || *end != '\0' || v < 0 || v > 16384) {
                fprintf(stderr, "bad --warmup\n");
                return 0;
            }
            opt->warmup = (int)v;
            warmup_set = 1;
        } else if (strcmp(argv[i], "--batch-size") == 0) {
            if (!parse_i32(argv[++i], &opt->batch_size)) {
                fprintf(stderr, "bad --batch-size\n");
                return 0;
            }
        } else if (strcmp(argv[i], "--dll") == 0) {
            opt->dll_path = argv[++i];
        } else if (strcmp(argv[i], "--mod-dir") == 0) {
            opt->mod_dir = argv[++i];
        } else if (strcmp(argv[i], "--config") == 0) {
            opt->config_path = argv[++i];
        } else {
            fprintf(stderr, "unknown option: %s\n", argv[i]);
            return 0;
        }
    }
    if (opt->dump_path == NULL) {
        usage();
        return 0;
    }
    if (opt->bench) {
        if (!frames_set) {
            opt->frames = 240;
        }
        if (!warmup_set) {
            opt->warmup = 32;
        }
    }
    if (opt->scene_target_only && opt->png_path != NULL) {
        fprintf(
            stderr,
            "--output/-o cannot be used with --scene-target-only; "
            "use --output-scene-image\n");
        return 0;
    }
    return 1;
}

static void default_png_path(const char *dump_path, char *out, size_t out_size)
{
    const char *slash;
    const char *name;
    char stem[MW2ER_PATH_BUF];
    size_t n;
    char *dot;

    slash = strrchr(dump_path, '\\');
    if (slash == NULL) {
        slash = strrchr(dump_path, '/');
    }
    name = (slash != NULL) ? slash + 1 : dump_path;
    n = strlen(name);
    if (n >= sizeof(stem)) {
        n = sizeof(stem) - 1;
    }
    memcpy(stem, name, n);
    stem[n] = '\0';
    dot = strrchr(stem, '.');
    if (dot != NULL) {
        *dot = '\0';
    }
#ifdef MW2ER_ARTIFACTS_DIR
    snprintf(out, out_size, "%s/%s_mw2renderer.png", MW2ER_ARTIFACTS_DIR, stem);
#else
    snprintf(out, out_size, "%s_mw2renderer.png", stem);
#endif
}

static int call_ok(const Mw2erApi *api, int32_t rc, const char *what)
{
    if (rc == MW2ER_OK) {
        return 1;
    }
    fprintf(stderr, "%s failed (%d): %s\n", what, (int)rc, api->last_error());
    return 0;
}

static Mw2erGlProc load_gl(const char *name)
{
    return (Mw2erGlProc)mw2er_host_gl_proc(name);
}

static int run_frame(
    const Mw2erApi *api,
    const Mw2erMemoryView *mem,
    const Mw2erViewport *vp,
    uint64_t frame,
    int composite)
{
    return call_ok(api, mw2er_host_frame(api, mem, vp, frame, composite), "frame transaction");
}

static int cmp_double(const void *a, const void *b)
{
    double da = *(const double *)a;
    double db = *(const double *)b;
    if (da < db) {
        return -1;
    }
    if (da > db) {
        return 1;
    }
    return 0;
}

static double percentile(std::vector<double> values, double p)
{
    if (values.empty()) {
        return 0.0;
    }
    qsort(values.data(), values.size(), sizeof(double), cmp_double);
    double index = (double)(values.size() - 1) * p;
    size_t lower = (size_t)index;
    size_t upper = lower + 1;
    if (upper >= values.size()) {
        return values[lower];
    }
    double frac = index - (double)lower;
    return values[lower] * (1.0 - frac) + values[upper] * frac;
}

static double mean_of(const std::vector<double> &values)
{
    double s = 0.0;
    if (values.empty()) {
        return 0.0;
    }
    for (size_t i = 0; i < values.size(); ++i) {
        s += values[i];
    }
    return s / (double)values.size();
}

static double max_of(const std::vector<double> &values)
{
    double m = 0.0;
    for (size_t i = 0; i < values.size(); ++i) {
        if (i == 0 || values[i] > m) {
            m = values[i];
        }
    }
    return m;
}

int main(int argc, char **argv)
{
    ReplayOptions opt;
    Mw2erDump dump;
    Mw2erMemoryView mem;
    Mw2erInit init;
    Mw2erViewport vp;
    const Mw2erApi *api;
    Mw2erHostWindow window;
    char png_buf[MW2ER_PATH_BUF];
    int32_t frame;
    int composite;
    uint32_t tick = 1;
    double first_extract_ms = -1.0;

    if (!parse_args(argc, argv, &opt)) {
        return 1;
    }
#ifdef MW2ER_DEFAULT_MOD_DIR
    if (opt.mod_dir == NULL) {
        opt.mod_dir = MW2ER_DEFAULT_MOD_DIR;
    }
#endif
    if (opt.config_path != NULL) {
#ifdef _WIN32
        _putenv_s("MW2ER_RENDERER_CONF", opt.config_path);
#else
        setenv("MW2ER_RENDERER_CONF", opt.config_path, 1);
#endif
    }
    if (!mw2er_host_init()) {
        return 1;
    }
    if (!mw2er_dump_open(opt.dump_path, &dump)) {
        mw2er_host_quit();
        return 1;
    }
    mem = mw2er_dump_view(&dump);
    fprintf(
        stderr,
        "dump: %u bytes  base=0x%08X  delta=0x%08X\n",
        dump.size,
        dump.runtime_base,
        dump.delta);

    /* Compatibility-only benchmark control. Set it before DLL initialization
     * so production draw calls never query the process environment. */
#ifdef _WIN32
    _putenv_s("MW2ER_WORKLOAD", opt.retained ? "retained" : "");
#else
    if (opt.retained) {
        setenv("MW2ER_WORKLOAD", "retained", 1);
    } else {
        unsetenv("MW2ER_WORKLOAD");
    }
#endif

    api = mw2er_host_load_api(opt.dll_path);
    if (api == NULL) {
        mw2er_dump_close(&dump);
        mw2er_host_quit();
        return 1;
    }
    if (api->abi_version != MW2ER_ABI_VERSION || api->struct_size < sizeof(Mw2erApi)) {
        fprintf(stderr, "unsupported abi %u\n", api->abi_version);
        mw2er_dump_close(&dump);
        mw2er_host_quit();
        return 1;
    }
    if (api->published_scene == NULL) {
        fprintf(stderr, "renderer is missing published_scene\n");
        mw2er_dump_close(&dump);
        mw2er_host_quit();
        return 1;
    }
    if (!mw2er_host_window_open(
            &window, opt.width, opt.height, opt.visible, "mw2renderer-replay")) {
        mw2er_dump_close(&dump);
        mw2er_host_quit();
        return 1;
    }
    if (mw2er_load_gl(load_gl) == 0) {
        fprintf(stderr, "host mw2er_load_gl failed\n");
        mw2er_host_window_destroy(&window);
        mw2er_dump_close(&dump);
        mw2er_host_quit();
        return 1;
    }

    memset(&init, 0, sizeof(init));
    init.struct_size = sizeof(init);
    init.log = (opt.bench && !opt.verbose) ? NULL : host_log;
    init.get_gl_proc_address = mw2er_host_gl_proc;
    init.mod_dir = opt.mod_dir;
    if (!call_ok(api, api->init(&init), "init")) {
        return 1;
    }

    memset(&vp, 0, sizeof(vp));
    vp.struct_size = sizeof(vp);
    vp.mod_w = opt.width;
    vp.mod_h = opt.height;
    vp.backbuffer_w = opt.width;
    vp.backbuffer_h = opt.height;
    vp.backbuffer_fbo = 0;
    vp.context_generation = 1;
    vp.view_mode = MW2ER_VIEW_MOD_ONLY;
    if (!call_ok(api, api->on_gl_context(&vp), "on_gl_context")) {
        return 1;
    }
    if (!call_ok(api, mw2er_host_begin_mission(api), "mission_begin")) {
        return 1;
    }
    composite = !opt.scene_target_only;
    for (frame = 0; frame < opt.warmup; ++frame) {
        if (!run_frame(api, &mem, &vp, tick++, composite)) {
            return 1;
        }
        if (first_extract_ms < 0.0 && api->last_cpu_timing) {
            api->last_cpu_timing(&first_extract_ms, NULL);
        }
        if (opt.visible) {
            mw2er_host_window_swap(&window);
            mw2er_host_window_poll(&window);
        }
    }
    glFinish();

    {
        int want_backbuffer =
            !opt.scene_target_only && (opt.png_path != NULL || !opt.bench);
        int want_scene = opt.scene_png_path != NULL;
        if (want_backbuffer || want_scene) {
            if (!run_frame(api, &mem, &vp, tick++, composite)) {
                return 1;
            }
            glFinish();
            if (want_scene) {
                uint32_t scene_fbo = 0;
                int32_t sw = 0;
                int32_t sh = 0;
                if (!call_ok(
                        api,
                        api->published_scene(&scene_fbo, &sw, &sh),
                        "published_scene")) {
                    return 1;
                }
                if (sw == opt.width * 2 && sh == opt.height * 2) {
                    if (!mw2er_host_write_png_fbo_box2x(
                            opt.scene_png_path, scene_fbo, sw, sh)) {
                        fprintf(
                            stderr,
                            "scene png write failed: %s\n",
                            opt.scene_png_path);
                        return 1;
                    }
                    fprintf(
                        stdout,
                        "output_scene_image=%s %dx%d -> %dx%d box2x\n",
                        opt.scene_png_path,
                        (int)sw,
                        (int)sh,
                        opt.width,
                        opt.height);
                } else {
                    if (!mw2er_host_write_png_fbo(
                            opt.scene_png_path, scene_fbo, sw, sh)) {
                        fprintf(
                            stderr,
                            "scene png write failed: %s\n",
                            opt.scene_png_path);
                        return 1;
                    }
                    fprintf(stdout, "output_scene_image=%s\n", opt.scene_png_path);
                }
            }
            if (want_backbuffer) {
                const char *png_path = opt.png_path;
                if (png_path == NULL) {
                    default_png_path(opt.dump_path, png_buf, sizeof(png_buf));
                    png_path = png_buf;
                }
                if (!mw2er_host_write_png(png_path, opt.width, opt.height)) {
                    fprintf(stderr, "png write failed: %s\n", png_path);
                    return 1;
                }
                fprintf(stdout, "wrote %s\n", png_path);
            }
            if (!opt.bench) {
                api->end_mission(1);
    api->end_session(1);
                api->on_gl_context_lost();
                api->shutdown();
                mw2er_host_window_destroy(&window);
                mw2er_dump_close(&dump);
                mw2er_host_quit();
                return 0;
            }
        }
    }

    std::vector<double> gpu_ms;
    std::vector<double> cpu_submit_ms;
    std::vector<double> cpu_extract_ms;
    std::vector<double> cpu_draw_ms;
    std::vector<double> cpu_finish_ms;
    int timed_frames = 0;
    GLuint query = 0;
    glGenQueries(1, &query);
    for (int first = 0; first < opt.frames; first += opt.batch_size) {
        int count = opt.batch_size;
        if (first + count > opt.frames) {
            count = opt.frames - first;
        }
        uint64_t finish_t0;
        GLuint64 gpu_ns = 0;
        glBeginQuery(GL_TIME_ELAPSED, query);
        for (int i = 0; i < count; ++i) {
            uint64_t t0 = mw2er_host_ticks();
            if (!run_frame(api, &mem, &vp, tick++, composite)) {
                return 1;
            }
            cpu_submit_ms.push_back(mw2er_host_ms_since(t0));
            if (api->last_cpu_timing) {
                double extract_ms = 0.0;
                double draw_ms = 0.0;
                api->last_cpu_timing(&extract_ms, &draw_ms);
                cpu_extract_ms.push_back(extract_ms);
                cpu_draw_ms.push_back(draw_ms);
                if (first_extract_ms < 0.0) {
                    first_extract_ms = extract_ms;
                }
            }
            if (opt.visible) {
                mw2er_host_window_swap(&window);
                mw2er_host_window_poll(&window);
            }
        }
        glEndQuery(GL_TIME_ELAPSED);
        finish_t0 = mw2er_host_ticks();
        glFinish();
        cpu_finish_ms.push_back(mw2er_host_ms_since(finish_t0) / (double)count);
        glGetQueryObjectui64v(query, GL_QUERY_RESULT, &gpu_ns);
        gpu_ms.push_back(((double)gpu_ns / 1000000.0) / (double)count);
        timed_frames += count;
    }
    glDeleteQueries(1, &query);

    {
        uint32_t scene_fbo = 0;
        int32_t sw = 0;
        int32_t sh = 0;
        api->published_scene(&scene_fbo, &sw, &sh);
        double p50 = percentile(gpu_ms, 0.50);
        fprintf(stdout, "gl_version=%s\n", (const char *)glGetString(GL_VERSION));
        fprintf(stdout, "logical_viewport=%dx%d\n", opt.width, opt.height);
        fprintf(stdout, "workload=%s\n", opt.retained ? "retained" : "full");
        if (first_extract_ms >= 0.0) {
            fprintf(stdout, "first_extract_ms=%.3f\n", first_extract_ms);
        }
        if (!cpu_extract_ms.empty()) {
            fprintf(
                stdout,
                "cpu_extract_ms p50=%.3f p95=%.3f mean=%.3f\n",
                percentile(cpu_extract_ms, 0.50),
                percentile(cpu_extract_ms, 0.95),
                mean_of(cpu_extract_ms));
            fprintf(
                stdout,
                "cpu_draw_submit_ms p50=%.3f p95=%.3f mean=%.3f\n",
                percentile(cpu_draw_ms, 0.50),
                percentile(cpu_draw_ms, 0.95),
                mean_of(cpu_draw_ms));
        }
        fprintf(stdout, "scene_render_target=%dx%d\n", (int)sw, (int)sh);
        fprintf(
            stdout,
            "timed_frames=%d batches=%d batch_size=%d warmup=%d\n",
            timed_frames,
            (int)gpu_ms.size(),
            opt.batch_size,
            opt.warmup);
        fprintf(
            stdout,
            "gpu_timeline_frame_ms p50=%.3f p95=%.3f p99=%.3f mean=%.3f max=%.3f\n",
            p50,
            percentile(gpu_ms, 0.95),
            percentile(gpu_ms, 0.99),
            mean_of(gpu_ms),
            max_of(gpu_ms));
        fprintf(
            stdout,
            "gpu_timeline_fps_from_p50=%.2f\n",
            p50 > 0.0 ? 1000.0 / p50 : 0.0);
        fprintf(
            stdout,
            "cpu_submit_ms p50=%.3f p95=%.3f mean=%.3f\n",
            percentile(cpu_submit_ms, 0.50),
            percentile(cpu_submit_ms, 0.95),
            mean_of(cpu_submit_ms));
        fprintf(
            stdout,
            "cpu_finish_ms p50=%.3f p95=%.3f mean=%.3f\n",
            percentile(cpu_finish_ms, 0.50),
            percentile(cpu_finish_ms, 0.95),
            mean_of(cpu_finish_ms));
    }

    if (opt.visible) {
        while (!mw2er_host_window_close_requested(&window)) {
            mw2er_host_window_poll(&window);
        }
    }

    api->end_mission(1);
    api->end_session(1);
    api->on_gl_context_lost();
    api->shutdown();
    mw2er_host_window_destroy(&window);
    mw2er_dump_close(&dump);
    mw2er_host_quit();
    return 0;
}
