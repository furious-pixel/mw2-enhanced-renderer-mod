#ifndef MW2ER_ABI_H
#define MW2ER_ABI_H

/*
 * Enhanced renderer mod — public C ABI (version 6).
 *
 * One exported symbol: mw2er_get_api(). Replay and DOSBox call the function
 * table. Replay-only work (dump files, window, PNG,
 * timers) stays in the host.
 *
 * No C++ types, no exceptions, no STL across this boundary.
 * Calls are serialized on one host emulation/OpenGL thread. The host must
 * keep the GL context current for GL callbacks and must not call capture,
 * resource service, render, or composition concurrently. Resource callbacks
 * run synchronously inside service_resources on that same thread.
 */

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MW2ER_ABI_VERSION 6u
#define MW2ER_ABI_VERSION_V2 2u

enum {
    MW2ER_OK = 0,
    MW2ER_ERR_GENERIC = 1,
    MW2ER_ERR_INVALID_ARGUMENT = 2,
    MW2ER_ERR_UNSUPPORTED_ABI = 3,
    MW2ER_ERR_GL = 4,
    MW2ER_ERR_MEMORY_VIEW = 5,
    MW2ER_ERR_NOT_READY = 6,
    MW2ER_ERR_UNSUPPORTED = 7
};

enum {
    MW2ER_VIEW_GAME_ONLY = 0,
    MW2ER_VIEW_MOD_ONLY = 1,
    MW2ER_VIEW_SIDE_BY_SIDE = 2,
    MW2ER_VIEW_SIDE_BY_SIDE_SUPPRESSED = 3
};

/*
 * Contiguous guest image. bytes[0] is guest linear address runtime_base.
 * runtime offset = runtime - runtime_base
 * reloc offset   = reloc + delta - runtime_base
 * bytes is borrowed only for the duration of the receiving API call.
 * The renderer treats the view as strictly read-only and never retains it.
 */
typedef struct Mw2erMemoryView {
    uint32_t struct_size;
    const uint8_t *bytes;
    uint32_t size;
    uint32_t runtime_base;
    uint32_t delta;
} Mw2erMemoryView;

typedef struct Mw2erViewport {
    uint32_t struct_size;
    int32_t mod_x;
    int32_t mod_y;
    int32_t mod_w;
    int32_t mod_h;
    int32_t backbuffer_w;
    int32_t backbuffer_h;
    uint32_t backbuffer_fbo; /* 0 = default framebuffer */
    uint64_t context_generation;
    int32_t view_mode; /* MW2ER_VIEW_* */
} Mw2erViewport;

typedef struct Mw2erInit {
    uint32_t struct_size;
    const char *reserved_path; /* ignored; retained for ABI v3 layout */
    const char *mod_dir; /* UTF-8 mw2mods root; copied at init */
    void (*log)(const char *msg); /* optional; must not throw */
    void *(*get_gl_proc_address)(const char *name); /* host context loader */
} Mw2erInit;

typedef struct Mw2erFrameInfo {
    uint32_t struct_size;
    uint64_t frame;
    double time_seconds;
    double frame_delta_seconds;
} Mw2erFrameInfo;

typedef struct Mw2erRendererDescriptor {
    uint32_t struct_size;
    const char *name;
    const char *executable_name;
    /* v2 compatibility hook; v3+ hosts consume bindings below. */
    uint32_t render_hook_reloc;
    uint32_t binding_count;
    uint32_t reserved;
    const struct Mw2erHookBinding *bindings;
    /* Basename whose successful DOS open the host should remember. */
    const char *observed_file_name;
} Mw2erRendererDescriptor;

enum { MW2ER_EVENT_LEGACY_RENDER = 1 };

typedef struct Mw2erHookBinding {
    uint32_t struct_size;
    uint32_t event; /* opaque renderer-defined event id */
    uint32_t reloc_eip;
    uint32_t kind;
} Mw2erHookBinding;

enum {
    MW2ER_HOOK_CAPTURE = 1,
    MW2ER_HOOK_RENDER = 2,
    /* Capture an event which changes compositor output or scheduling. */
    MW2ER_HOOK_COMPOSITOR = 3
};

enum {
    MW2ER_LAYER_SCENE = 1u << 0,
    MW2ER_LAYER_OVERLAY = 1u << 1
};

enum {
    MW2ER_COVERAGE_SCENE = 1u << 0,
    MW2ER_COVERAGE_HUD = 1u << 1
};

typedef struct Mw2erMissionInfo {
    uint32_t struct_size;
    uint32_t reserved;
    uint64_t session_generation;
    uint64_t mission_generation;
    uint64_t resource_generation;
} Mw2erMissionInfo;

/* Retrieve an absolute UTF-8 host path recorded at a successful DOS file
 * open. The name must match observed_file_name. The renderer owns
 * the output buffer; MW2ER_ERR_NOT_READY means no open was observed. */
typedef int32_t (*Mw2erResolveOpenedPathFn)(
    void *user, const char *file_name, char *host_path, uint32_t capacity);

typedef struct Mw2erSessionInfo {
    uint32_t struct_size;
    uint32_t reserved;
    uint64_t session_generation;
    const char *profile_id;
    const char *executable_name;
    const char *archive_identity;
    void *resolve_user;
    Mw2erResolveOpenedPathFn resolve_opened_path;
} Mw2erSessionInfo;

typedef struct Mw2erCaptureInput {
    uint32_t struct_size;
    uint32_t event;
    uint32_t flags;
    uint32_t reserved;
    uint64_t session_generation;
    uint64_t mission_generation;
    uint64_t resource_generation;
    Mw2erMemoryView memory;
    Mw2erViewport viewport;
    Mw2erFrameInfo frame;
} Mw2erCaptureInput;

typedef struct Mw2erRenderRequest {
    uint32_t struct_size;
    uint32_t required_layers;
    uint64_t session_generation;
    uint64_t mission_generation;
    uint64_t resource_generation;
    uint64_t frame;
    Mw2erViewport viewport;
} Mw2erRenderRequest;

typedef struct Mw2erPublishResult {
    uint32_t struct_size;
    uint32_t coverage;
    uint32_t completed_layers;
    uint32_t reserved;
    uint64_t publication_id;
    uint64_t source_frame;
    uint64_t session_generation;
    uint64_t mission_generation;
    uint64_t resource_generation;
    uint64_t context_generation;
} Mw2erPublishResult;

typedef struct Mw2erPresentInfo {
    uint32_t struct_size;
    uint32_t reserved;
    uint64_t present_count;
    double time_seconds;
    Mw2erViewport viewport;
} Mw2erPresentInfo;

typedef struct Mw2erPresentResult {
    uint32_t struct_size;
    uint32_t presented;
    uint32_t continuous;
    /* Legacy compositor pacing hint: the host may retain native-driven
     * presentation while continuing validated guest frame-hook pacing. */
    uint32_t suspend_frame_pacing;
    uint64_t publication_id;
} Mw2erPresentResult;

enum {
    MW2ER_RESOURCE_CEL = 1,
    MW2ER_RESOURCE_POLY = 2
};

typedef struct Mw2erResourceKey {
    uint32_t struct_size;
    uint32_t type;
    uint32_t resource_id;
    uint32_t reserved;
    uint64_t resource_generation;
} Mw2erResourceKey;

typedef int32_t (*Mw2erAcquireResourceFn)(
    void *user,
    const Mw2erResourceKey *key,
    uint32_t *payload_runtime_address);

typedef int32_t (*Mw2erReleaseResourceFn)(
    void *user,
    const Mw2erResourceKey *key);

typedef struct Mw2erResourceService {
    uint32_t struct_size;
    uint32_t max_resources;
    void *user;
    Mw2erMemoryView memory;
    Mw2erAcquireResourceFn acquire;
    Mw2erReleaseResourceFn release;
} Mw2erResourceService;

typedef struct Mw2erResourceProgress {
    uint32_t struct_size;
    uint32_t processed;
    uint32_t retained;
    uint32_t failed;
    uint32_t pending;
} Mw2erResourceProgress;

typedef struct Mw2erApi {
    uint32_t abi_version;
    uint32_t struct_size;
    const Mw2erRendererDescriptor *(*descriptor)(void);
    int32_t (*init)(const Mw2erInit *init);
    /* CPU state only; release the GL context resources before shutdown. */
    void (*shutdown)(void);
    const char *(*last_error)(void);
    /* GL context must be current. DLL loads its own 3.3 entry points. */
    int32_t (*on_gl_context)(const Mw2erViewport *vp);
    /* Call with the owning context current, before destroying that context. */
    void (*on_gl_context_lost)(void);
    int32_t (*mission_begin)(const Mw2erMemoryView *mem);
    void (*mission_end)(void);
    int32_t (*bind_frame)(
        const Mw2erMemoryView *mem,
        const Mw2erViewport *vp,
        const Mw2erFrameInfo *frame);
    int32_t (*render_scene)(void);
    int32_t (*render_hud)(void); /* completes the overlay layer; currently absent */
    int32_t (*publish)(void);
    int32_t (*composite)(const Mw2erViewport *vp);
    /* Published scene color target after publish(). fbo 0 is valid (default). */
    int32_t (*published_scene)(uint32_t *fbo, int32_t *width, int32_t *height);
    /* Last render_scene CPU split, milliseconds. Pointers may be NULL. */
    void (*last_cpu_timing)(double *extract_ms, double *draw_submit_ms);
    /* v3+ transaction. The v2 calls above are compatibility adapters. */
    int32_t (*begin_session)(const Mw2erSessionInfo *session);
    void (*end_session)(uint64_t session_generation);
    int32_t (*begin_mission)(const Mw2erMissionInfo *mission);
    void (*end_mission)(uint64_t mission_generation);
    int32_t (*capture)(const Mw2erCaptureInput *input);
    int32_t (*seal_frame)(uint64_t frame);
    int32_t (*render_frame)(const Mw2erRenderRequest *request);
    int32_t (*publish_frame)(Mw2erPublishResult *result);
    int32_t (*composite_frame)(
        const Mw2erPresentInfo *present,
        Mw2erPresentResult *result);
    /* Optional ABI v3+ tail. Called only at a host-controlled guest safe point. */
    uint32_t (*resources_pending)(uint64_t resource_generation);
    int32_t (*service_resources)(
        const Mw2erResourceService *service,
        Mw2erResourceProgress *progress);
} Mw2erApi;

typedef const Mw2erApi *(*Mw2erGetApiFn)(void);

#if defined(MW2ER_ABI_NO_LINK)
#define MW2ER_EXPORT
#elif defined(_WIN32)
#if defined(MW2ER_RENDERER_EXPORTS)
#define MW2ER_EXPORT __declspec(dllexport)
#else
#define MW2ER_EXPORT __declspec(dllimport)
#endif
#else
#define MW2ER_EXPORT __attribute__((visibility("default")))
#endif

MW2ER_EXPORT const Mw2erApi *mw2er_get_api(void);

#ifdef __cplusplus
}
#endif

#endif /* MW2ER_ABI_H */
