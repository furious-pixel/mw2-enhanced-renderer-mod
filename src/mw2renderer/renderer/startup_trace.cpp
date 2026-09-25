#include "startup_trace.h"
#include "mw2er_internal.h"
#include "presentation.h"
#include "resource.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {
struct Sample {
    uint64_t frame = 0, remap = 0, classification = 0;
    double epoch_ms = 0;
    uint32_t event = 0, phase = 0, pending = 0;
    int32_t total = 0, completed = 0, entries = 0, first = 0;
    int32_t countdown = 0, target = 0, source = 0, returning = 0, brightness = 0;
    int32_t frame_duration_ticks = 0;
    bool fade_valid = false, remap_valid = false;
    Mw2erStartupWork work;
};
Sample g_samples[8192];
size_t g_count;
bool g_enabled, g_started, g_recording;
double g_begin_ms, g_scene_ms;
uint64_t g_frame;
uint32_t g_phase;
const char *g_stop;
Sample *g_current;

void stop(const char *reason)
{
    g_recording = false;
    g_stop = reason;
    g_current = nullptr;
    mw2er_startup_work = nullptr;
}

bool within_window(double now)
{
    if (!g_recording) return false;
    if (now - g_begin_ms >= 120000.0) stop("120s_limit");
    else if (g_scene_ms && now - g_scene_ms >= 8000.0) stop("8s_after_scene");
    return g_recording;
}

Sample *append(uint32_t event, uint32_t pending, double now)
{
    if (g_count == sizeof(g_samples) / sizeof(g_samples[0])) {
        stop("capacity");
        return nullptr;
    }
    Sample &s = g_samples[g_count++];
    s = {};
    s.frame = g_frame;
    s.epoch_ms = now;
    s.event = event;
    s.phase = g_phase;
    s.pending = pending;
    return &s;
}
}

Mw2erStartupWork *mw2er_startup_work;

double mw2er_startup_now_ms(void)
{
    return std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

void mw2er_startup_init(void)
{
    const char *enabled = std::getenv("MW2_STARTUP_TRACE");
    g_enabled = enabled && std::strcmp(enabled, "1") == 0;
    g_started = false;
    g_count = 0;
    stop("inactive");
}

void mw2er_startup_flush(const char *reason)
{
    if (!g_count) return;
    if (g_recording) stop(reason);
    char line[1024];
    std::snprintf(line, sizeof(line),
        "renderer_startup begin samples=%zu stop=%s flush=%s clock=steady_epoch_ms "
        "events=1:primary,4:load_begin,5:load_fade,6:load_strip,7:load_end,100:presentation "
        "phase_bits=1:loading,2:handoff,4:scene cpu_ms_only=1", g_count, g_stop, reason);
    mw2er_log(line);
    for (size_t i = 0; i < g_count; ++i) {
        const Sample &s = g_samples[i];
        const Mw2erStartupWork &w = s.work;
        std::snprintf(line, sizeof(line),
            "renderer_startup frame=%llu epoch_ms=%.3f event=%u phase=%u pending=%u "
            "dda=%d/%d first=%d entries=%d valid=%d countdown=%d target=%d source=%d return=%d "
            "brightness=%d frame_duration_ticks=%d remap=%llu classification=%llu remap_valid=%d "
            "hist=%u/%.3f atlas=%u/%.3f classify=%u/%.3f poly=%u/%.3f gpu_cel=%u/%.3f hit=%u identity=%u",
            (unsigned long long)s.frame, s.epoch_ms, s.event, s.phase, s.pending,
            s.completed, s.total, s.first, s.entries, s.fade_valid, s.countdown,
            s.target, s.source, s.returning, s.brightness, s.frame_duration_ticks,
            (unsigned long long)s.remap, (unsigned long long)s.classification, s.remap_valid,
            w.count[0], w.ms[0], w.count[1], w.ms[1], w.count[2], w.ms[2],
            w.count[3], w.ms[3], w.count[4], w.ms[4], w.count[5], w.count[6]);
        mw2er_log(line);
    }
    g_count = 0;
}

void mw2er_startup_capture(const Mw2erCaptureInput &input)
{
    if (!g_enabled) return;
    if (input.event != MW2ER_EVENT_PRIMARY &&
        (input.event < MW2ER_EVENT_LOADING_BEGIN || input.event > MW2ER_EVENT_LOADING_END)) return;
    if (input.event == MW2ER_EVENT_LOADING_BEGIN || !g_started) {
        mw2er_startup_flush("next_loading");
        g_started = g_recording = true;
        g_begin_ms = mw2er_startup_now_ms();
        g_scene_ms = 0;
        g_phase = 0;
        g_stop = "recording";
    }
    if (!g_recording) return;
    const double now = mw2er_startup_now_ms();
    if (!within_window(now)) return;
    g_frame = input.frame.frame;
    Sample *s = append(input.event, mw2er_resources_pending(input.resource_generation), now);
    if (!s || input.event != MW2ER_EVENT_PRIMARY) return;
    g_current = s;
    mw2er_startup_work = &s->work;
    const Mem mem = Mem::from(input.memory);
    s->total = mem.i32_rel(0x0015FF28);
    s->completed = mem.i32_rel(0x0015FF2C);
    s->first = mem.u8_rel(0x0015FF30);
    s->entries = mem.i32_rel(0x0015FF31);
    s->countdown = mem.i32_rel(0x000A7184);
    s->target = mem.i32_rel(0x000A7178);
    s->source = mem.i32_rel(0x000A717C);
    s->returning = mem.i32_rel(0x000A7180);
    s->brightness = mem.i32_rel(0x000A582C);
    // Most recent game frame duration, in the documented 0xB6-ticks/second timebase.
    s->frame_duration_ticks = mem.i32_rel(0x000A58C8);
    s->fade_valid = s->countdown > 0 && s->total > 0 && s->completed >= 0 &&
        s->completed < s->total && s->first == 0 && s->entries == 256;
}

void mw2er_startup_present(bool loading, bool handoff, bool scene, uint32_t pending)
{
    if (!g_recording) return;
    const double now = mw2er_startup_now_ms();
    if (!within_window(now)) return;
    const uint32_t phase = (loading ? 1u : 0u) | (handoff ? 2u : 0u) | (scene ? 4u : 0u);
    if (scene && !g_scene_ms) g_scene_ms = now;
    if (phase != g_phase) {
        g_phase = phase;
        append(100, pending, now);
    }
}

void mw2er_startup_texture_context(bool valid, uint64_t remap, uint64_t classification)
{
    if (!g_current) return;
    g_current->remap_valid = valid;
    g_current->remap = remap;
    g_current->classification = classification;
}
