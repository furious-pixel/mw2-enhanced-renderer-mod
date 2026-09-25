#include "mw2er_internal.h"
#include "config.h"
#include "terrain_gap.h"
#include "resource.h"
#include "presentation.h"
#include "scene_extract.h"
#include "scene_draw.h"
#include "hud.h"
#include "menu.h"
#include "font.h"
#include "startup_trace.h"

#include <stdio.h>
#include <exception>

struct Mw2erProcessState {
    int inited;
    int session;
    int mission;
    int frame_build;
    int frame_sealed;
    uint64_t session_generation;
    uint64_t mission_generation;
    uint64_t resource_generation;
    uint64_t sealed_frame;
    uint64_t publication_id;
    void (*log)(const char *msg);
    void *(*get_gl_proc_address)(const char *name);
    char mod_dir[MW2ER_PATH_MAX];
    char shader_dir[MW2ER_PATH_MAX];
    char profile_id[MW2ER_PATH_MAX];
    char executable_name[MW2ER_PATH_MAX];
    char archive_identity[MW2ER_PATH_MAX];
    char last_error[MW2ER_ERROR_MAX];
    Mw2erMemoryView mem;
    Mw2erViewport vp;
    Mw2erFrameInfo frame;
};

static Mw2erProcessState g_state;

static void copy_path(char *dst, size_t dst_size, const char *src)
{
    if (dst_size == 0) {
        return;
    }
    if (src == NULL || src[0] == '\0') {
        dst[0] = '\0';
        return;
    }
    size_t n = strlen(src);
    if (n >= dst_size) {
        n = dst_size - 1;
    }
    memcpy(dst, src, n);
    dst[n] = '\0';
}

static int path_fits(const char *path, size_t capacity)
{
    return path == NULL || strlen(path) < capacity;
}

void mw2er_set_error(const char *msg) noexcept
{
    copy_path(g_state.last_error, sizeof(g_state.last_error), msg);
}

void mw2er_clear_error(void) noexcept
{
    g_state.last_error[0] = '\0';
}

const char *mw2er_last_error(void) noexcept
{
    return g_state.last_error;
}

void mw2er_log(const char *msg)
{
    if (g_state.log != NULL && msg != NULL) {
        g_state.log(msg);
    }
}

const char *mw2er_shader_dir(void)
{
    return g_state.shader_dir;
}

const char *mw2er_mod_dir(void)
{
    return g_state.mod_dir;
}

const Mw2erMemoryView *mw2er_frame_mem(void)
{
    return g_state.inited ? &g_state.mem : NULL;
}

const Mw2erViewport *mw2er_frame_vp(void)
{
    return g_state.inited ? &g_state.vp : NULL;
}

static int32_t api_init(const Mw2erInit *init)
{
    mw2er_clear_error();
    memset(&g_state, 0, sizeof(g_state));
    if (init == NULL || init->struct_size < sizeof(Mw2erInit) ||
        init->mod_dir == NULL || init->mod_dir[0] == '\0' ||
        !path_fits(init->mod_dir, sizeof(g_state.mod_dir))) {
        mw2er_set_error("init requires a valid mod_dir");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    g_state.log = init->log;
    mw2er_startup_init();
    g_state.get_gl_proc_address = init->get_gl_proc_address;
    copy_path(g_state.mod_dir, sizeof(g_state.mod_dir), init->mod_dir);
    const int shader_path_len = snprintf(
        g_state.shader_dir,
        sizeof(g_state.shader_dir),
        "%s/shaders",
        g_state.mod_dir);
    if (shader_path_len < 0 ||
        (size_t)shader_path_len >= sizeof(g_state.shader_dir)) {
        mw2er_set_error("mod_dir is too long");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    g_state.inited = 1;
    mw2er_config_reset_defaults();
    mw2er_config_load_from_mod_dir(g_state.mod_dir);
    const int32_t font_result = mw2er_font_process_init(g_state.mod_dir);
    if (font_result != MW2ER_OK) {
        g_state.inited = 0;
        return font_result;
    }
    mw2er_scene_process_init();
    {
        char gap_path[MW2ER_PATH_MAX];
        snprintf(
            gap_path,
            sizeof(gap_path),
            "%s/terrain_block_deltas.json",
            g_state.mod_dir);
        mw2er_terrain_gap_load(gap_path);
    }
    mw2er_log("mw2renderer: init");
    return MW2ER_OK;
}

void *mw2er_get_gl_proc_address(const char *name)
{
    return g_state.get_gl_proc_address != NULL
        ? g_state.get_gl_proc_address(name)
        : NULL;
}

// Statusless ABI calls must not throw. Cleanup is allocation-free; an unexpected
// cleanup failure cannot safely be swallowed before the host unloads the DLL.
static void api_shutdown(void) noexcept
{
    mw2er_startup_flush("shutdown");
    mw2er_resources_shutdown();
    mw2er_scene_process_shutdown();
    mw2er_font_process_shutdown();
    memset(&g_state, 0, sizeof(g_state));
}

static void reset_frame_transaction(void) noexcept
{
    g_state.frame_build = 0;
    g_state.frame_sealed = 0;
    g_state.sealed_frame = 0;
    memset(&g_state.mem, 0, sizeof(g_state.mem));
    memset(&g_state.frame, 0, sizeof(g_state.frame));
}

static int32_t api_begin_session(const Mw2erSessionInfo *session)
{
    if (!g_state.inited) {
        mw2er_set_error("begin_session before init");
        return MW2ER_ERR_NOT_READY;
    }
    if (session == NULL || session->struct_size < sizeof(Mw2erSessionInfo) ||
        session->reserved != 0 || session->session_generation == 0 ||
        session->profile_id == NULL ||
        session->profile_id[0] == '\0' || session->executable_name == NULL ||
        session->executable_name[0] == '\0' ||
        !path_fits(session->profile_id, sizeof(g_state.profile_id)) ||
        !path_fits(session->executable_name, sizeof(g_state.executable_name)) ||
        !path_fits(session->archive_identity, sizeof(g_state.archive_identity))) {
        mw2er_set_error("begin_session: invalid session");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    if (g_state.session) {
        mw2er_set_error("begin_session while session is active");
        return MW2ER_ERR_NOT_READY;
    }
    g_state.session = 1;
    g_state.session_generation = session->session_generation;
    g_state.mission_generation = 0;
    g_state.resource_generation = 0;
    g_state.publication_id = 0;
    copy_path(g_state.profile_id, sizeof(g_state.profile_id), session->profile_id);
    copy_path(
        g_state.executable_name,
        sizeof(g_state.executable_name),
        session->executable_name);
    copy_path(
        g_state.archive_identity,
        sizeof(g_state.archive_identity),
        session->archive_identity);
    mw2er_resources_open(*session);
    reset_frame_transaction();
    mw2er_gl_invalidate_publication();
    return MW2ER_OK;
}

static void api_end_session(uint64_t session_generation) noexcept
{
    if (!g_state.session || (session_generation != 0 &&
        session_generation != g_state.session_generation)) {
        return;
    }
    mw2er_startup_flush("session_end");
    if (g_state.mission) {
        g_state.mission = 0;
        mw2er_resources_end();
        mw2er_scene_mission_reset();
    }
    g_state.session = 0;
    g_state.session_generation = 0;
    g_state.mission_generation = 0;
    g_state.resource_generation = 0;
    reset_frame_transaction();
    mw2er_gl_invalidate_publication();
}

static int32_t api_begin_mission(const Mw2erMissionInfo *mission)
{
    if (!g_state.session) {
        mw2er_set_error("begin_mission before session");
        return MW2ER_ERR_NOT_READY;
    }
    if (g_state.mission) {
        mw2er_set_error("begin_mission while mission is active");
        return MW2ER_ERR_NOT_READY;
    }
    if (mission == NULL || mission->struct_size < sizeof(Mw2erMissionInfo) ||
        mission->reserved != 0 ||
        mission->session_generation != g_state.session_generation ||
        mission->mission_generation == 0 || mission->resource_generation == 0) {
        mw2er_set_error("begin_mission: invalid generation");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    g_state.mission = 1;
    g_state.mission_generation = mission->mission_generation;
    g_state.resource_generation = mission->resource_generation;
    mw2er_startup_init();
    reset_frame_transaction();
    mw2er_presentation_reset();
    mw2er_scene_mission_reset();
    mw2er_hud_mission_reset();
    mw2er_resources_begin(g_state.resource_generation);
    mw2er_gl_invalidate_publication();
    mw2er_log("mw2renderer: mission_begin");
    return MW2ER_OK;
}

static void api_end_mission(uint64_t mission_generation) noexcept
{
    if (!g_state.mission || (mission_generation != 0 &&
        mission_generation != g_state.mission_generation)) {
        return;
    }
    mw2er_startup_flush("mission_end");
    g_state.mission = 0;
    g_state.mission_generation = 0;
    g_state.resource_generation = 0;
    reset_frame_transaction();
    mw2er_resources_end();
    mw2er_presentation_reset();
    mw2er_scene_mission_reset();
    mw2er_hud_mission_reset();
    mw2er_gl_invalidate_publication();
}

static int32_t api_on_gl_context(const Mw2erViewport *vp)
{
    if (!g_state.inited) {
        mw2er_set_error("on_gl_context before init");
        return MW2ER_ERR_NOT_READY;
    }
    if (vp == NULL || vp->struct_size < sizeof(Mw2erViewport) ||
        !mw2er_viewport_size_ok(vp->mod_w, vp->mod_h)) {
        mw2er_set_error("on_gl_context: invalid viewport");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    g_state.vp = *vp;
    return mw2er_gl_on_context(vp);
}

static void api_on_gl_context_lost(void) noexcept
{
    mw2er_gl_shutdown();
}

static int32_t api_mission_begin(const Mw2erMemoryView *mem)
{
    if (!g_state.inited) {
        mw2er_set_error("mission_begin before init");
        return MW2ER_ERR_NOT_READY;
    }
    if (!mw2er_memory_ok(mem)) {
        mw2er_set_error("mission_begin: invalid memory view");
        return MW2ER_ERR_MEMORY_VIEW;
    }
    if (!g_state.session) {
        Mw2erSessionInfo session = {};
        session.struct_size = sizeof(session);
        session.session_generation = 1;
        session.profile_id = "v2-compat";
        session.executable_name = "MW2.EXE";
        const int32_t session_result = api_begin_session(&session);
        if (session_result != MW2ER_OK) {
            return session_result;
        }
    }
    Mw2erMissionInfo mission = {};
    mission.struct_size = sizeof(mission);
    mission.session_generation = g_state.session_generation;
    mission.mission_generation = g_state.mission_generation + 1;
    mission.resource_generation = 1;
    return api_begin_mission(&mission);
}

static void api_mission_end(void) noexcept
{
    api_end_mission(g_state.mission_generation);
}

struct BorrowedMemory {
    ~BorrowedMemory() { memset(&g_state.mem, 0, sizeof(g_state.mem)); }
};

static int32_t api_capture(const Mw2erCaptureInput *input)
{
    if (!g_state.mission) {
        mw2er_set_error("capture before mission");
        return MW2ER_ERR_NOT_READY;
    }
    if (input == NULL || input->struct_size < sizeof(Mw2erCaptureInput) ||
        input->event == 0 || input->flags != 0 || input->reserved != 0 ||
        input->session_generation != g_state.session_generation ||
        input->mission_generation != g_state.mission_generation ||
        input->resource_generation != g_state.resource_generation ||
        !mw2er_memory_ok(&input->memory) ||
        (input->event == MW2ER_EVENT_PRIMARY &&
         (input->viewport.struct_size < sizeof(Mw2erViewport) ||
          !mw2er_viewport_size_ok(input->viewport.mod_w, input->viewport.mod_h))) ||
        input->frame.struct_size < sizeof(Mw2erFrameInfo)) {
        mw2er_set_error("capture: invalid input");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    const BorrowedMemory borrowed_memory;
    mw2er_startup_capture(*input);
    if (input->event == MW2ER_EVENT_LOADING_BEGIN) {
        /* The host mission is the MW2 process lifetime. Loading begin is the
         * renderer-owned boundary between missions within that process. */
        reset_frame_transaction();
        g_state.publication_id = 0;
        mw2er_scene_mission_reset();
        mw2er_resources_begin(g_state.resource_generation);
        mw2er_gl_invalidate_publication();
        mw2er_hud_mission_reset();
    }
    const int32_t presentation_result = mw2er_presentation_capture(input);
    if (presentation_result != MW2ER_OK)
        return presentation_result;
    if (input->event == MW2ER_EVENT_TARGET) {
        g_state.mem = input->memory;
        const int32_t target_result = mw2er_hud_capture_target(input->memory);
        memset(&g_state.mem, 0, sizeof(g_state.mem));
        return target_result;
    }
    if (input->event == MW2ER_EVENT_LATE) {
        mw2er_hud_capture_late(input->memory);
        mw2er_menu_capture_late(input->memory);
        return mw2er_resources_discover(input->memory);
    }
    if (input->event != MW2ER_EVENT_PRIMARY) {
        if (input->event == MW2ER_EVENT_LOADING_END)
            return mw2er_resources_discover(input->memory);
        return MW2ER_OK;
    }
    g_state.mem = input->memory;
    g_state.vp = input->viewport;
    g_state.frame = input->frame;
    const Mw2erRenderView primary_view = mw2er_primary_render_view(input->memory);
    mw2er_hud_capture_primary(input->memory, input->frame.time_seconds, primary_view);
    mw2er_menu_capture_primary(input->memory);
    int32_t result = mw2er_resources_discover(input->memory);
    if (result == MW2ER_OK)
        result = mw2er_scene_capture(primary_view);
    memset(&g_state.mem, 0, sizeof(g_state.mem));
    if (result != MW2ER_OK) {
        g_state.frame_build = 0;
        g_state.frame_sealed = 0;
        return result;
    }
    g_state.frame_build = 1;
    g_state.frame_sealed = 0;
    g_state.sealed_frame = 0;
    return MW2ER_OK;
}

static int32_t api_seal_frame(uint64_t frame)
{
    if (!g_state.mission || !g_state.frame_build ||
        frame != g_state.frame.frame) {
        mw2er_set_error("seal_frame: incomplete or mismatched frame");
        return MW2ER_ERR_NOT_READY;
    }
    g_state.frame_sealed = 1;
    g_state.sealed_frame = frame;
    return MW2ER_OK;
}

static int32_t api_render_frame(const Mw2erRenderRequest *request)
{
    if (!g_state.mission || !g_state.frame_sealed) {
        mw2er_set_error("render_frame before seal");
        return MW2ER_ERR_NOT_READY;
    }
    if (request == NULL || request->struct_size < sizeof(Mw2erRenderRequest) ||
        request->session_generation != g_state.session_generation ||
        request->mission_generation != g_state.mission_generation ||
        request->resource_generation != g_state.resource_generation ||
        request->frame != g_state.sealed_frame ||
        request->viewport.struct_size < sizeof(Mw2erViewport) ||
        !mw2er_viewport_size_ok(request->viewport.mod_w, request->viewport.mod_h) ||
        (request->required_layers & MW2ER_LAYER_SCENE) == 0 ||
        (request->required_layers & ~(MW2ER_LAYER_SCENE | MW2ER_LAYER_OVERLAY)) != 0) {
        mw2er_set_error("render_frame: invalid request");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    if (request->viewport.context_generation != mw2er_gl_context_generation()) {
        mw2er_set_error("render_frame: stale GL context");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    g_state.vp = request->viewport;
    int32_t result = mw2er_gl_ensure_size(
        request->viewport.mod_w, request->viewport.mod_h);
    if (result != MW2ER_OK) {
        return result;
    }
    result = mw2er_gl_begin_frame();
    if (result == MW2ER_OK) {
        result = mw2er_gl_render_scene();
    }
    if (result == MW2ER_OK) {
        /* The scene-only profile completes overlay as explicitly absent. */
        result = mw2er_gl_render_hud();
    }
    return result;
}

static int32_t publish_frame(Mw2erPublishResult *result)
{
    if (!g_state.frame_sealed) {
        mw2er_set_error("publish_frame before seal");
        return MW2ER_ERR_NOT_READY;
    }
    if (result != NULL && result->struct_size < sizeof(Mw2erPublishResult)) {
        mw2er_set_error("publish_frame: invalid result");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    const int32_t publish_result = mw2er_gl_publish();
    if (publish_result != MW2ER_OK) {
        return publish_result;
    }
    g_state.publication_id += 1;
    mw2er_presentation_published(
        mw2er_resources_pending(g_state.resource_generation));
    if (result != NULL) {
        result->coverage = MW2ER_COVERAGE_SCENE;
        result->completed_layers = MW2ER_LAYER_SCENE | MW2ER_LAYER_OVERLAY;
        result->reserved = 0;
        result->publication_id = g_state.publication_id;
        result->source_frame = g_state.sealed_frame;
        result->session_generation = g_state.session_generation;
        result->mission_generation = g_state.mission_generation;
        result->resource_generation = g_state.resource_generation;
        result->context_generation = mw2er_gl_context_generation();
    }
    g_state.frame_build = 0;
    g_state.frame_sealed = 0;
    return MW2ER_OK;
}

static int32_t api_publish_frame(Mw2erPublishResult *result)
{
    if (result == NULL) {
        mw2er_set_error("publish_frame: null result");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    return publish_frame(result);
}

static int32_t api_composite_frame(
    const Mw2erPresentInfo *present, Mw2erPresentResult *result)
{
    if (present == NULL || present->struct_size < sizeof(Mw2erPresentInfo) ||
        present->reserved != 0 || result == NULL ||
        result->struct_size < sizeof(Mw2erPresentResult)) {
        mw2er_set_error("composite_frame: invalid input");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    const Mw2erPresentation presentation = mw2er_presentation_get(
        present->time_seconds,
        present->viewport.view_mode,
        g_state.publication_id != 0,
        mw2er_resources_pending(g_state.resource_generation));
    const int32_t composite_result = mw2er_gl_composite(
        &present->viewport, &presentation);
    if (composite_result != MW2ER_OK) {
        return composite_result;
    }
    result->presented = 1;
    result->continuous = presentation.continuous ? 1u : 0u;
    result->suspend_frame_pacing = presentation.suspend_frame_pacing ? 1u : 0u;
    result->publication_id = g_state.publication_id;
    return MW2ER_OK;
}

static int32_t api_bind_frame(
    const Mw2erMemoryView *mem,
    const Mw2erViewport *vp,
    const Mw2erFrameInfo *frame)
{
    if (!g_state.inited || !g_state.mission) {
        mw2er_set_error("bind_frame before mission_begin");
        return MW2ER_ERR_NOT_READY;
    }
    if (!mw2er_memory_ok(mem)) {
        mw2er_set_error("bind_frame: invalid memory view");
        return MW2ER_ERR_MEMORY_VIEW;
    }
    if (vp == NULL || vp->struct_size < sizeof(Mw2erViewport) ||
        !mw2er_viewport_size_ok(vp->mod_w, vp->mod_h)) {
        mw2er_set_error("bind_frame: invalid viewport");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    if (frame == NULL || frame->struct_size < sizeof(Mw2erFrameInfo)) {
        mw2er_set_error("bind_frame: invalid frame info");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    Mw2erCaptureInput input = {};
    input.struct_size = sizeof(input);
    input.event = MW2ER_EVENT_PRIMARY;
    input.session_generation = g_state.session_generation;
    input.mission_generation = g_state.mission_generation;
    input.resource_generation = g_state.resource_generation;
    input.memory = *mem;
    input.viewport = *vp;
    input.frame = *frame;
    int32_t result = api_capture(&input);
    if (result != MW2ER_OK) {
        return result;
    }
    result = api_seal_frame(frame->frame);
    if (result != MW2ER_OK) {
        return result;
    }
    result = mw2er_gl_ensure_size(vp->mod_w, vp->mod_h);
    if (result != MW2ER_OK) {
        return result;
    }
    return mw2er_gl_begin_frame();
}

static int32_t api_render_scene(void)
{
    if (!g_state.mission) {
        mw2er_set_error("render_scene before mission_begin");
        return MW2ER_ERR_NOT_READY;
    }
    return mw2er_gl_render_scene();
}

static int32_t api_render_hud(void)
{
    if (!g_state.mission) {
        mw2er_set_error("render_hud before mission_begin");
        return MW2ER_ERR_NOT_READY;
    }
    return mw2er_gl_render_hud();
}

static int32_t api_publish(void)
{
    if (!g_state.mission) {
        mw2er_set_error("publish before mission_begin");
        return MW2ER_ERR_NOT_READY;
    }
    return publish_frame(NULL);
}

static int32_t api_composite(const Mw2erViewport *vp)
{
    if (vp == NULL || vp->struct_size < sizeof(Mw2erViewport)) {
        mw2er_set_error("composite: invalid viewport");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    g_state.vp = *vp;
    const Mw2erPresentation presentation = mw2er_presentation_get(
        g_state.frame.time_seconds,
        vp->view_mode,
        g_state.publication_id != 0,
        mw2er_resources_pending(g_state.resource_generation));
    return mw2er_gl_composite(vp, &presentation);
}

static int32_t api_published_scene(uint32_t *fbo, int32_t *width, int32_t *height)
{
    return mw2er_gl_published_scene(fbo, width, height);
}

static void api_last_cpu_timing(double *extract_ms, double *draw_submit_ms) noexcept
{
    mw2er_scene_last_cpu_timing(extract_ms, draw_submit_ms);
}

static uint32_t api_resources_pending(uint64_t resource_generation) noexcept
{
    return mw2er_resources_pending(resource_generation);
}

static int32_t api_service_resources(
    const Mw2erResourceService *service,
    Mw2erResourceProgress *progress)
{
    return mw2er_resources_service(service, progress);
}

static const Mw2erHookBinding g_bindings[] = {
    {sizeof(Mw2erHookBinding), MW2ER_EVENT_PRIMARY, 0x0002CEB7u, MW2ER_HOOK_CAPTURE},
    {sizeof(Mw2erHookBinding), MW2ER_EVENT_TARGET, 0x00047887u, MW2ER_HOOK_CAPTURE},
    {sizeof(Mw2erHookBinding), MW2ER_EVENT_LATE, 0x0002CEC1u, MW2ER_HOOK_RENDER},
    {sizeof(Mw2erHookBinding), MW2ER_EVENT_LOADING_BEGIN, 0x0002CD11u, MW2ER_HOOK_COMPOSITOR},
    {sizeof(Mw2erHookBinding), MW2ER_EVENT_LOADING_FADE, 0x0002C4E2u, MW2ER_HOOK_COMPOSITOR},
    {sizeof(Mw2erHookBinding), MW2ER_EVENT_LOADING_STRIP, 0x0002C5B9u, MW2ER_HOOK_COMPOSITOR},
    {sizeof(Mw2erHookBinding), MW2ER_EVENT_LOADING_END, 0x0002CE42u, MW2ER_HOOK_COMPOSITOR},
    {sizeof(Mw2erHookBinding), MW2ER_EVENT_OUTRO, 0x0003FC2Eu, MW2ER_HOOK_COMPOSITOR},
};

static const Mw2erRendererDescriptor g_descriptor = {
    sizeof(Mw2erRendererDescriptor),
    "mw2renderer",
    "MW2.EXE",
    0x0002CEB7u,
    (uint32_t)(sizeof(g_bindings) / sizeof(g_bindings[0])),
    0,
    g_bindings,
    kArchiveSourceExecutable,
};

static const Mw2erRendererDescriptor *api_descriptor(void) noexcept
{
    return &g_descriptor;
}

// All status-returning ABI calls contain ordinary C++ failures. The host
// disables enhancement after a fatal result; partially built frames are never reused.
template<auto Function> struct StatusBoundary;
template<typename... Args, int32_t (*Function)(Args...)>
struct StatusBoundary<Function> {
    static int32_t call(Args... args) noexcept
    {
        try {
            return Function(args...);
        } catch (const std::exception &error) {
            mw2er_set_error(error.what());
        } catch (...) {
            mw2er_set_error("unexpected C++ exception in renderer");
        }
        reset_frame_transaction();
        mw2er_gl_invalidate_publication();
        return MW2ER_ERR_GENERIC;
    }
};

static const Mw2erApi g_api = {
    MW2ER_ABI_VERSION,
    sizeof(Mw2erApi),
    api_descriptor,
    StatusBoundary<api_init>::call,
    api_shutdown,
    mw2er_last_error,
    StatusBoundary<api_on_gl_context>::call,
    api_on_gl_context_lost,
    StatusBoundary<api_mission_begin>::call,
    api_mission_end,
    StatusBoundary<api_bind_frame>::call,
    StatusBoundary<api_render_scene>::call,
    StatusBoundary<api_render_hud>::call,
    StatusBoundary<api_publish>::call,
    StatusBoundary<api_composite>::call,
    StatusBoundary<api_published_scene>::call,
    api_last_cpu_timing,
    StatusBoundary<api_begin_session>::call,
    api_end_session,
    StatusBoundary<api_begin_mission>::call,
    api_end_mission,
    StatusBoundary<api_capture>::call,
    StatusBoundary<api_seal_frame>::call,
    StatusBoundary<api_render_frame>::call,
    StatusBoundary<api_publish_frame>::call,
    StatusBoundary<api_composite_frame>::call,
    api_resources_pending,
    StatusBoundary<api_service_resources>::call,
};

extern "C" MW2ER_EXPORT const Mw2erApi *mw2er_get_api(void)
{
    return &g_api;
}
