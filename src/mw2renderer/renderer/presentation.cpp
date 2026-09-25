#include "presentation.h"

#include "mem.h"
#include "mw2er_internal.h"
#include "resource.h"
#include "startup_trace.h"

#include <algorithm>
#include <cstdint>
#include <cstring>

namespace {

constexpr uint32_t kLoadingBackground = 0x000A5884u;
constexpr uint32_t kLoadingStrip = 0x000A5888u;
constexpr uint32_t kLoadingStripCount = 0x000A588Cu;
constexpr uint32_t kLoadingRamp = 0x000A5890u;
constexpr uint32_t kLoadingPane = 0x000B56B0u;
constexpr uint32_t kLoadingStripX = 0x000B56C4u;
constexpr uint32_t kLoadingStripY = 0x000B56C8u;
constexpr uint32_t kBrightnessStep = 0x000A582Cu;
constexpr uint32_t kBrightnessTable = 0x000B4F90u;
constexpr uint32_t kResourceHashTable = 0x000A6D08u;
constexpr uint32_t kResourceTable = 0x000AE9CCu;
constexpr double kFadeInSeconds = 0.5;
constexpr double kFadeOutSeconds = 0.25;
constexpr double kFirstFrameTimeoutSeconds = 2.0;
constexpr double kDiscoveryWaitSeconds = 3.0;
constexpr double kResourceStallSeconds = 10.0;
constexpr double kStripPeriodSeconds = 0.330;
constexpr double kOutroSeconds = 1.284;

struct PresentationState {
    Mw2erLoadingVisual visual;
    bool loading = false;
    bool preload_started = false;
    bool published_after_preload = false;
    bool ready_clock_started = false;
    bool preload_abandoned = false;
    bool resource_clock_started = false;
    bool fade_started = false;
    bool strip_started = false;
    bool handoff_started = false;
    bool outro_started = false;
    double fade_at = 0.0;
    double strip_next_at = 0.0;
    double ready_at = 0.0;
    double preload_at = 0.0;
    double resource_progress_at = 0.0;
    uint32_t last_resources_pending = 0;
    double handoff_at = 0.0;
    double outro_at = 0.0;
    int32_t strip_index = 0;
    float brightness[64] = {};
    bool brightness_identity = true;
    int32_t brightness_step = -1;
};

PresentationState g_present;
uint64_t g_visual_generation = 0;

static bool decode_runtime_sprite(
    const Mem& mem, uint32_t table, uint32_t frame, Mw2erSprite *out)
{
    if (table == 0 || out == nullptr)
        return false;
    const uint8_t *head = mem.view(table, 24);
    if (head == nullptr)
        return false;
    uint32_t shape = table;
    if (std::memcmp(head, "1.10", 4) == 0) {
        const uint32_t count = mem.u32(table + 4);
        if (count == 0 || count > 4096 || frame >= count)
            return false;
        const uint32_t offset = mem.u32(table + 8 + frame * 8);
        if (offset == 0 || offset > UINT32_MAX - table)
            return false;
        shape = table + offset;
        head = mem.view(shape, 24);
        if (head == nullptr)
            return false;
    } else if (frame != 0) {
        return false;
    }
    const int32_t x0 = mem.i32(shape + 8);
    const int32_t y0 = mem.i32(shape + 12);
    const int32_t x1 = mem.i32(shape + 16);
    const int32_t y1 = mem.i32(shape + 20);
    const int64_t width = (int64_t)x1 - x0 + 1;
    const int64_t height = (int64_t)y1 - y0 + 1;
    if (width <= 0 || height <= 0 || width > 4096 || height > 4096 ||
        width * height > 16777216)
        return false;

    out->width = (int32_t)width;
    out->height = (int32_t)height;
    out->x_offset = x0;
    out->y_offset = y0;
    out->pixels.assign((size_t)(width * height * 2), 0);
    uint32_t cursor = shape + 24;
    // This row budget keeps x below 2.1 million, even with 255-pixel skips.
    const int32_t op_limit = out->width * 2 + 8;
    for (int32_t row = 0; row < out->height; ++row) {
        int32_t x = 0;
        int32_t ops = 0;
        for (;;) {
            if (ops++ > op_limit)
                return false;
            const uint8_t *opcode_ptr = mem.view(cursor, 1);
            if (opcode_ptr == nullptr || cursor == UINT32_MAX)
                return false;
            cursor += 1;
            const uint8_t opcode = *opcode_ptr;
            if (opcode == 0)
                break;
            if (opcode == 1) {
                const uint8_t *skip = mem.view(cursor, 1);
                if (skip == nullptr || cursor == UINT32_MAX)
                    return false;
                cursor += 1;
                x += *skip;
                continue;
            }
            const int32_t count = (opcode & 1) ? (opcode - 1) / 2 : opcode / 2;
            const uint32_t color_bytes = (opcode & 1) ? (uint32_t)count : 1u;
            const uint8_t *colors = mem.view(cursor, color_bytes);
            if (colors == nullptr)
                return false;
            for (int32_t i = 0; i < count && x + i < out->width; ++i) {
                const size_t dst = ((size_t)row * out->width + (x + i)) * 2;
                out->pixels[dst] = colors[(opcode & 1) ? i : 0];
                out->pixels[dst + 1] = 255;
            }
            if (color_bytes > UINT32_MAX - cursor)
                return false;
            cursor += color_bytes;
            x += count;
        }
    }
    return true;
}

static bool capture_palette(const Mem& mem, uint32_t table, uint8_t *palette)
{
    const uint8_t *head = mem.view(table, 16);
    if (head == nullptr || std::memcmp(head, "1.10", 4) != 0)
        return false;
    const uint32_t offset = mem.u32(table + 12);
    if (offset == 0 || offset > UINT32_MAX - table)
        return false;
    const uint32_t address = table + offset;
    const uint32_t count = mem.u32(address);
    if (count == 0 || count > 256 || address > UINT32_MAX - 4)
        return false;
    const uint8_t *records = mem.view(address + 4, count * 4);
    if (records == nullptr)
        return false;
    std::memset(palette, 0, 256 * 3);
    for (uint32_t i = 0; i < count; ++i) {
        const uint32_t dst = (uint32_t)records[i * 4] * 3;
        for (uint32_t c = 0; c < 3; ++c)
            palette[dst + c] = (uint8_t)((records[i * 4 + 1 + c] & 63) * 255 / 63);
    }
    return true;
}

static bool capture_background(const Mem& mem)
{
    const uint32_t table = mem.u32_rel(kLoadingBackground);
    Mw2erSprite background;
    uint8_t palette[256 * 3];
    if (!decode_runtime_sprite(mem, table, 0, &background) || !capture_palette(mem, table, palette))
        return false;
    g_present.visual.background = std::move(background);
    g_present.visual.strips.clear();
    std::memcpy(g_present.visual.palette, palette, sizeof(palette));
    g_present.visual.clip_x = mem.i32_rel(kLoadingPane + 4);
    g_present.visual.clip_y = mem.i32_rel(kLoadingPane + 8);
    g_present.visual.strip_x = 0;
    g_present.visual.strip_y = 0;
    g_present.visual.valid = true;
    g_present.visual.generation = ++g_visual_generation;
    return true;
}

static bool capture_complete(const Mem& mem)
{
    const uint32_t background_table = mem.u32_rel(kLoadingBackground);
    const uint32_t strip_table = mem.u32_rel(kLoadingStrip);
    const uint32_t declared = mem.u32_rel(kLoadingStripCount);
    const uint8_t *strip_header = mem.view(strip_table, 8);
    if (strip_header == nullptr || std::memcmp(strip_header, "1.10", 4) != 0)
        return false;
    const uint32_t resource_count = mem.u32(strip_table + 4);
    const uint32_t count = std::min(declared, resource_count);
    if (count < 2 || count > 64)
        return false;
    Mw2erLoadingVisual visual;
    if (!decode_runtime_sprite(mem, background_table, 0, &visual.background) ||
        !capture_palette(mem, background_table, visual.palette))
        return false;
    visual.strips.reserve(count);
    for (uint32_t i = 0; i < count; ++i) {
        Mw2erSprite sprite;
        if (!decode_runtime_sprite(mem, strip_table, i, &sprite))
            return false;
        visual.strips.push_back(std::move(sprite));
    }
    const uint8_t *ramp = mem.view(mem.rt(kLoadingRamp), 48);
    if (ramp == nullptr)
        return false;
    for (uint32_t i = 0; i < 48; ++i) {
        const uint8_t value = ramp[i] > 63 ? ramp[i] >> 2 : ramp[i];
        visual.palette[i] = (uint8_t)((value & 63) * 255 / 63);
    }
    visual.clip_x = mem.i32_rel(kLoadingPane + 4);
    visual.clip_y = mem.i32_rel(kLoadingPane + 8);
    visual.strip_x = mem.i32_rel(kLoadingStripX);
    visual.strip_y = mem.i32_rel(kLoadingStripY);
    visual.valid = true;
    visual.generation = ++g_visual_generation;
    g_present.visual = std::move(visual);
    return true;
}

static void capture_brightness(const Mem& mem)
{
    const int32_t step = std::max(0, std::min(15, mem.i32_rel(kBrightnessStep)));
    if (step == g_present.brightness_step)
        return;
    const uint8_t *table = mem.view(mem.rt(kBrightnessTable) + step * 64u, 64);
    if (table == nullptr)
        return;
    g_present.brightness_step = step;
    g_present.brightness_identity = true;
    for (uint32_t i = 0; i < 64; ++i) {
        g_present.brightness[i] = table[i] / 63.0f;
        if (table[i] != i)
            g_present.brightness_identity = false;
    }
}

} // namespace

bool mw2er_decode_runtime_sprite(
    const Mem &mem, uint32_t table, uint32_t frame, Mw2erSprite *out)
{
    return decode_runtime_sprite(mem, table, frame, out);
}

uint32_t mw2er_runtime_sprite_frame_count(const Mem &mem, uint32_t table)
{
    if (!table) return 0;
    const uint8_t *head = mem.view(table, 8);
    if (!head) return 0;
    if (std::memcmp(head, "1.10", 4) != 0) return 1;
    const uint32_t count = mem.u32(table + 4);
    return count <= 4096 ? count : 0;
}

uint32_t mw2er_resolve_cached_shape(const Mem &mem, int32_t resource_index)
{
    const uint32_t resource_table = mem.u32_rel(kResourceTable);
    const uint32_t hash_table = mem.u32_rel(kResourceHashTable);
    if (!resource_table || !hash_table) return 0;
    const uint32_t key = mem.u32(resource_table);
    const uint32_t key_sum = (key & 0xFFu) + ((key >> 8) & 0xFFu) +
        ((key >> 16) & 0xFFu) + ((key >> 24) & 0xFFu);
    const uint32_t bucket = ((uint32_t)resource_index + key_sum) % 0x3F1u;
    uint32_t entry = mem.u32(hash_table + bucket * 4u);
    for (int visited = 0; entry && visited < 4096; ++visited) {
        if (mem.u16(entry) == ((uint32_t)resource_index & 0xFFFFu) &&
            mem.u32(entry + 4) == key) {
            const uint32_t payload = entry + 0x14;
            const uint8_t *header = mem.view(payload, 4);
            return header && std::memcmp(header, "1.10", 4) == 0
                ? payload : 0;
        }
        entry = mem.u32(entry + 8);
    }
    return 0;
}

void mw2er_presentation_reset(void)
{
    g_present = {};
    g_present.brightness_identity = true;
    g_present.brightness_step = -1;
    for (uint32_t i = 0; i < 64; ++i)
        g_present.brightness[i] = i / 63.0f;
}

int32_t mw2er_presentation_capture(const Mw2erCaptureInput *input)
{
    const Mem mem = Mem::from(input->memory);
    const double now = input->frame.time_seconds;
    switch (input->event) {
    case MW2ER_EVENT_PRIMARY:
    case MW2ER_EVENT_LATE:
        capture_brightness(mem);
        return MW2ER_OK;
    case MW2ER_EVENT_TARGET:
        return MW2ER_OK;
    case MW2ER_EVENT_LOADING_BEGIN:
        mw2er_presentation_reset();
        g_present.loading = true;
        return MW2ER_OK;
    case MW2ER_EVENT_LOADING_FADE:
        if (!capture_background(mem))
            mw2er_log("mw2renderer: loading background capture failed");
        g_present.fade_started = true;
        g_present.fade_at = now;
        return MW2ER_OK;
    case MW2ER_EVENT_LOADING_STRIP:
        if (!capture_complete(mem))
            mw2er_log("mw2renderer: loading screen capture failed");
        g_present.strip_started = true;
        g_present.strip_next_at = now + kStripPeriodSeconds;
        g_present.strip_index = 0;
        return MW2ER_OK;
    case MW2ER_EVENT_LOADING_END:
        g_present.preload_started = true;
        g_present.preload_at = now;
        return MW2ER_OK;
    case MW2ER_EVENT_OUTRO:
        g_present.outro_started = true;
        g_present.outro_at = now;
        return MW2ER_OK;
    default:
        return MW2ER_ERR_UNSUPPORTED;
    }
}

void mw2er_presentation_published(uint32_t resources_pending)
{
    if (g_present.preload_started && mw2er_resources_textures_discovered() &&
        resources_pending == 0)
        g_present.published_after_preload = true;
}

Mw2erPresentation mw2er_presentation_get(
    double now, int32_t view_mode, bool scene_available, uint32_t resources_pending)
{
    Mw2erPresentation out;
    out.brightness = g_present.brightness;
    out.brightness_identity = g_present.brightness_identity;
    if (g_present.preload_started && !g_present.handoff_started) {
        const bool discovered = mw2er_resources_textures_discovered() != 0;
        if (!g_present.preload_abandoned && !discovered &&
            now - g_present.preload_at >= kDiscoveryWaitSeconds) {
            g_present.preload_abandoned = true;
            mw2er_log("mw2renderer: resource preload unavailable "
                      "(texture catalog did not become ready)");
        }
        if (!g_present.preload_abandoned && discovered &&
            resources_pending != 0) {
            if (!g_present.resource_clock_started ||
                resources_pending != g_present.last_resources_pending) {
                g_present.resource_clock_started = true;
                g_present.resource_progress_at = now;
                g_present.last_resources_pending = resources_pending;
            } else if (now - g_present.resource_progress_at >=
                       kResourceStallSeconds) {
                g_present.preload_abandoned = true;
                mw2er_log("mw2renderer: resource preload stalled "
                          "(pending count did not change)");
            }
        }
        if (!g_present.preload_abandoned &&
            (!discovered || resources_pending != 0)) {
            g_present.ready_clock_started = false;
        } else {
            if (!g_present.ready_clock_started) {
                g_present.ready_clock_started = true;
                g_present.ready_at = now;
            }
            if (g_present.published_after_preload ||
                (g_present.preload_abandoned && scene_available) ||
                now - g_present.ready_at >= kFirstFrameTimeoutSeconds) {
                g_present.handoff_started = true;
                g_present.handoff_at = now;
            }
        }
    }
    bool show_loading = g_present.loading;
    if (g_present.handoff_started) {
        if (view_mode == MW2ER_VIEW_SIDE_BY_SIDE ||
            view_mode == MW2ER_VIEW_SIDE_BY_SIDE_SUPPRESSED ||
            now - g_present.handoff_at >= kFadeOutSeconds) {
            show_loading = false;
            g_present.loading = false;
        }
    }
    mw2er_startup_present(show_loading, g_present.handoff_started,
        !show_loading && scene_available, resources_pending);
    if (show_loading) {
        out.kind = MW2ER_PRESENT_LOADING;
        out.loading = g_present.visual.valid ? &g_present.visual : nullptr;
        out.continuous = true;
        /* Retain native-driven presentation for the wall-clock loading art.
         * Validated guest frame hooks may continue pacing the mission behind
         * the artwork before the compositor hands off to the scene. */
        out.suspend_frame_pacing = true;
        if (g_present.strip_started) {
            out.loading_brightness = 1.0f;
            if (now >= g_present.strip_next_at) {
                ++g_present.strip_index;
                g_present.strip_next_at = now + kStripPeriodSeconds;
            }
            out.strip_index = g_present.strip_index;
        } else if (g_present.fade_started) {
            out.loading_brightness = (float)std::max(0.0, std::min(1.0,
                (now - g_present.fade_at) / kFadeInSeconds));
        }
        if (g_present.handoff_started)
            out.loading_brightness *= (float)std::max(0.0,
                1.0 - (now - g_present.handoff_at) / kFadeOutSeconds);
        return out;
    }
    if (!scene_available)
        return out;
    out.kind = MW2ER_PRESENT_SCENE;
    if (g_present.outro_started) {
        out.fade = (float)std::max(0.0, std::min(1.0,
            (now - g_present.outro_at) / kOutroSeconds));
        out.continuous = out.fade < 1.0f;
    }
    return out;
}
