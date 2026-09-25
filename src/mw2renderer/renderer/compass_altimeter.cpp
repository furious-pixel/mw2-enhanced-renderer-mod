#include "compass_altimeter.h"

#include "config.h"
#include "font.h"
#include "hud.h"
#include "mem.h"
#include "mw2er_internal.h"
#include "targeting.h"

#include "gl_api.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

namespace {

enum {
    ADDR_ENTITY_TABLE = 0x00108B00,
    ADDR_COMPASS_ENABLE = 0x000A6320,
    ADDR_COMPASS_CENTER = 0x000A62DC,
    ADDR_ALTIMETER_LAYOUT = 0x000A62D4,
    ADDR_COMPASS_BAR_CONFIG = 0x0010B608,
    ADDR_ALTIMETER_CONFIG = 0x0010B614,
    ADDR_HUD_POST_CONFIGS = 0x0010E298,
    ADDR_HUD_STYLE_OFFSET = 0x000B4E80,
    ADDR_RETICLE_PANE = 0x000B4768,
    ADDR_COMPASS_TARGET_CONFIG = 0x0010B5FC,
    ADDR_ALTIMETER_TARGET_X = 0x000A62CC,
    ADDR_DIRECT_TARGETS = 0x00108BF0,
    ADDR_SECONDARY_TABLE = 0x00104B80,
    ADDR_SECONDARY_POSITIONS = 0x00111FA0,
    ADDR_SECONDARY_MAX_INDEX = 0x000A6D50,
    PANEL_COMPASS = 0,
    PANEL_ALTIMETER = 1,
    MAX_SPRITES = 20,
    MAX_SHAPES = 20,
    MAX_FILLS = 8,
    COMPASS_TEXT_SLOT_BASE = 42,
    ALTIMETER_TEXT_SLOT_BASE = 54,
};

static constexpr double k_fixed = 65536.0;
static constexpr int32_t k_turn = 0x01680000;

struct Rect { int left, top, right, bottom; }; /* exclusive */
struct Transform { double ox, oy, scale; };

struct PanelState {
    int visible;
    Rect bounds;
    int pane_width;
    int pane_height;
    double scale_origin_x;
    double scale_origin_y;
};

struct SpriteDraw {
    int reference;
    int panel;
    double x, y;
    Rect clip;
};

struct ShapeDraw {
    int type;
    int panel;
    double x, y;
    Rect clip;
    int edge_attachment;
};

struct FillDraw {
    int panel;
    double left, top, right, bottom;
    int color;
};

struct ScaleState {
    int enhanced;
    PanelState panels[2];
    SpriteDraw sprites[MAX_SPRITES];
    int sprite_count;
    ShapeDraw shapes[MAX_SHAPES];
    int shape_count;
    FillDraw fills[MAX_FILLS];
    int fill_count;
};

static ScaleState g_scale;

static double resolved_scale(int height, float control)
{
    const double vertical = std::max(1, height) / 768.0;
    return vertical < 1.0 ? vertical
        : 1.0 + std::clamp((double)control, 0.0, 1.0) * (vertical - 1.0);
}

static int round_pixel(double value)
{
    return (int)std::floor(value + 0.5);
}

static int floor_div(int value, int divisor)
{
    int quotient = value / divisor;
    if (value % divisor < 0) --quotient;
    return quotient;
}

static int32_t round_fixed16(int32_t value, uint32_t scale)
{
    const int64_t product = (int64_t)value * scale;
    return (int32_t)((product >> 16) + ((product >> 15) & 1));
}

static double wrap_degrees(double value)
{
    if (value > 180.0) value -= 360.0;
    if (value < -180.0) value += 360.0;
    return value;
}

static int read_panel(const Mem &mem, uint32_t config, PanelState &panel)
{
    if (!config) return 0;
    const uint32_t pane = mem.u32(config + 0x30);
    if (!pane) return 0;
    const int left = mem.i32(pane + 0x04);
    const int top = mem.i32(pane + 0x08);
    const int right = mem.i32(pane + 0x0C);
    const int bottom = mem.i32(pane + 0x10);
    if (right < left || bottom < top) return 0;
    panel.visible = 1;
    panel.bounds = {left, top, right + 1, bottom + 1};
    panel.pane_width = mem.i16(config + 0x44);
    panel.pane_height = mem.i16(config + 0x46);
    if (panel.pane_width <= 0) panel.pane_width = right - left + 1;
    if (panel.pane_height <= 0) panel.pane_height = bottom - top + 1;
    return 1;
}

static Rect reticle_pane(const Mem &mem)
{
    return {
        mem.i32_rel(ADDR_RETICLE_PANE + 0x04),
        mem.i32_rel(ADDR_RETICLE_PANE + 0x08),
        mem.i32_rel(ADDR_RETICLE_PANE + 0x0C) + 1,
        mem.i32_rel(ADDR_RETICLE_PANE + 0x10) + 1,
    };
}

static void add_sprite(const Mem &mem, int panel, int resource,
                       double x, double y, const Rect &clip)
{
    if (g_scale.sprite_count >= MAX_SPRITES) return;
    const int reference = mw2er_targeting_capture_sprite(mem, resource);
    if (reference < 0) return;
    g_scale.sprites[g_scale.sprite_count++] = {
        reference, panel, x, y, clip};
}

static void add_shape(const Mem &mem, int panel, int style, int resource_base,
                      double x, double y, const Rect &clip,
                      int edge_attachment = 0)
{
    if (!g_scale.enhanced) {
        add_sprite(mem, panel, resource_base + style, x, y, clip);
        return;
    }
    static const int resources[10] = {
        0x01, 0x04, 0x0D, 0x10, 0x13,
        0x16, 0x1C, 0x1F, 0x22, 0x25,
    };
    int type = -1;
    for (int i = 0; i < 10; ++i)
        if (resources[i] == resource_base) { type = i; break; }
    if (type < 0 || g_scale.shape_count >= MAX_SHAPES) return;
    g_scale.shapes[g_scale.shape_count++] = {
        type, panel, x, y, clip, edge_attachment};
}

static void add_fill(int panel, double left, double top,
                     double right, double bottom, int color,
                     const Rect &clip)
{
    left = std::max(left, (double)clip.left);
    top = std::max(top, (double)clip.top);
    right = std::min(right, (double)clip.right);
    bottom = std::min(bottom, (double)clip.bottom);
    if (right <= left || bottom <= top || g_scale.fill_count >= MAX_FILLS)
        return;
    g_scale.fills[g_scale.fill_count++] = {
        panel, left, top, right, bottom, color};
}

static void add_yaw_bar(int panel, double x, double y, double width,
                        int height, const Rect &clip)
{
    static const int colors[4] = {0x0E, 0x0F, 0x0E, 0x0D};
    const int cuts[5] = {0, height / 4, height / 2,
                         height * 3 / 4, height};
    for (int i = 0; i < 4; ++i)
        add_fill(panel, x, y + cuts[i], x + width, y + cuts[i + 1],
                 colors[i], clip);
}

struct TargetInputs {
    double torso;
    double body_bearing;
    double torso_bearing;
    int32_t vertical;
};

static TargetInputs target_inputs(const Mem &mem, uint32_t mech,
                                  uint32_t body, int fractional)
{
    double heading, torso, target_bearing;
    if (fractional) {
        heading = std::fmod(mem.i32(body + 0x60) / k_fixed, 360.0);
        if (heading < 0.0) heading += 360.0;
        torso = std::fmod(mem.i32(mech + 0x0C) / k_fixed, 360.0);
        target_bearing = std::fmod(
            mem.i32(body + 0xD4) / k_fixed, 360.0);
    } else {
        int heading_i = (mem.i32(body + 0x60) >> 16) % 360;
        if (heading_i < 0) heading_i += 360;
        heading = heading_i;
        torso = (mem.i32(mech + 0x0C) >> 16) % 360;
        target_bearing = (mem.i32(body + 0xD4) >> 16) % 360;
    }
    const double body_relative = wrap_degrees(target_bearing - heading);
    TargetInputs result = {};
    result.torso = torso;
    result.body_bearing = body_relative;
    result.torso_bearing = wrap_degrees(body_relative - torso);
    result.vertical = (int32_t)(((int64_t)mem.i32(body + 0xD8) +
        mem.i32(mech + 0x1C)) % k_turn);
    return result;
}

static double altimeter_offset(int64_t value, int32_t scale, int fractional)
{
    if (fractional)
        return (double)value * scale / (100.0 * k_fixed);
    return (double)(((value * scale) / 100) >> 16);
}

static int target_altitude(const Mem &mem, uint32_t handle, int64_t &altitude)
{
    const int kind = handle & 0x0F00u;
    const int index = handle & 0xFFu;
    if (kind == 0x0100) {
        altitude = mem.i32_rel(ADDR_DIRECT_TARGETS + index * 0x54u + 0x1C);
        return 1;
    }
    if (kind == 0x0200) {
        const uint32_t entity = mem.u32_rel(ADDR_ENTITY_TABLE + index * 4u);
        const uint32_t target_mech = entity ? mem.u32(entity + 0x20) : 0;
        const uint32_t target_body = target_mech ? mem.u32(target_mech) : 0;
        if (!target_body) return 0;
        altitude = (int64_t)mem.i32(target_body + 0x54) -
            mem.i32(target_mech + 0xCC);
        return 1;
    }
    if (kind != 0x0400) return 0;
    const uint32_t entry_address = ADDR_SECONDARY_TABLE + index * 0x40u;
    const int object_index = mem.i32_rel(entry_address + 0x04);
    const int maximum = mem.i32_rel(ADDR_SECONDARY_MAX_INDEX);
    if (object_index < 0 || object_index > maximum) return 0;
    const uint32_t record = ADDR_SECONDARY_POSITIONS + object_index * 0x7Cu;
    const uint32_t position = mem.u32_rel(record + 0x1C);
    if (position) altitude = mem.i32(position + 0x38);
    else {
        const uint32_t node = mem.u32_rel(record + 0x20);
        altitude = node ? mem.i32(node + 0x64) : 0;
    }
    return 1;
}

static void capture_compass(const Mem &mem, uint32_t mech, uint32_t body,
                            uint32_t config, int style)
{
    PanelState &panel = g_scale.panels[PANEL_COMPASS];
    if (!read_panel(mem, config, panel)) return;
    const Rect clip = panel.bounds;
    const int left = clip.left, top = clip.top;
    const int center_x = mem.i32_rel(ADDR_COMPASS_CENTER);
    const int draw_y = mem.i32_rel(ADDR_COMPASS_CENTER + 4);
    const int bar_inset = mem.i32_rel(ADDR_COMPASS_BAR_CONFIG);
    const uint32_t compass_scale = mem.u32_rel(ADDR_COMPASS_BAR_CONFIG + 4);
    if (g_scale.enhanced) {
        double heading = std::fmod(mem.i32(body + 0x60) / k_fixed, 360.0);
        if (heading < 0.0) heading += 360.0;
        const double source = -(center_x - 710) +
            heading * compass_scale / k_fixed;
        const double strip_width = std::max(
            1.0, 360.0 * compass_scale / k_fixed);
        double wrapped = std::fmod(source, strip_width);
        if (wrapped < 0.0) wrapped += strip_width;
        panel.scale_origin_x = left - wrapped;
        panel.scale_origin_y = top + draw_y;
    } else {
        int heading = (mem.i32(body + 0x60) >> 16) % 360;
        if (heading < 0) heading += 360;
        const int source = center_x + round_fixed16(heading, compass_scale);
        const int strip_width = round_fixed16(360, compass_scale);
        const int second = panel.pane_width > source
            ? source + strip_width : source - strip_width;
        const int reference = mw2er_targeting_capture_sprite(mem, 0x19 + style);
        if (reference >= 0 && g_scale.sprite_count + 2 <= MAX_SPRITES) {
            g_scale.sprites[g_scale.sprite_count++] = {
                reference, PANEL_COMPASS, (double)(left + source),
                (double)(top + draw_y), clip};
            g_scale.sprites[g_scale.sprite_count++] = {
                reference, PANEL_COMPASS, (double)(left + second),
                (double)(top + draw_y), clip};
        }
    }

    const TargetInputs target = target_inputs(
        mem, mech, body, g_scale.enhanced);
    if (target.torso != 0.0 && bar_inset > 0) {
        const double bar_x = center_x + std::min(0.0, target.torso);
        add_yaw_bar(PANEL_COMPASS, left + bar_x,
                    top + draw_y - bar_inset,
                    std::abs(target.torso), bar_inset, clip);
    }
    add_shape(mem, PANEL_COMPASS, style, 0x13,
              left + center_x, top + draw_y, clip);

    const int target_status = mem.u8(body + 0xDD);
    const int special = mem.i32(mech + 0xBC) == 2;
    const int normal = (target_status & 0x0F) && !(target_status & 0x10);
    if (!normal && !special) return;
    const double bearing = (target_status & 0x01)
        ? target.body_bearing : target.torso_bearing;
    const Rect target_clip = reticle_pane(mem);
    const int top_offset = mem.i32_rel(ADDR_COMPASS_TARGET_CONFIG);
    const int side_outset = mem.i32_rel(ADDR_COMPASS_TARGET_CONFIG + 4);
    const int side_height = mem.i32_rel(ADDR_COMPASS_TARGET_CONFIG + 8);
    const int bottom_offset = mem.i32_rel(ADDR_COMPASS_TARGET_CONFIG + 0x14);
    if (normal && !special) {
        const double marker_x = left + center_x;
        if (target.vertical > -0x30000)
            add_shape(mem, PANEL_COMPASS, style, 0x25, marker_x,
                      top + draw_y - top_offset - 6, target_clip);
        if (target.vertical < 0x30000)
            add_shape(mem, PANEL_COMPASS, style, 0x1C, marker_x,
                      top + draw_y + bottom_offset + 6, target_clip);
    }
    double target_x = left + center_x;
    /* Enhanced bearings retain fractions, so use the HUD aiming window rather
     * than waiting for an exact floating-point zero. Native keeps the game's
     * integer/RLE selection rule. */
    const bool aligned = g_scale.enhanced
        ? bearing > -3.0 && bearing < 3.0
        : bearing == 0.0;
    if (aligned) {
        add_shape(mem, PANEL_COMPASS, style, 0x10,
                  target_x, top + draw_y, clip);
        add_shape(mem, PANEL_COMPASS, style, 0x16,
                  target_x, top + draw_y, clip);
    } else {
        target_x += g_scale.enhanced
            ? bearing * compass_scale / k_fixed
            : round_fixed16((int32_t)bearing, compass_scale);
        add_shape(mem, PANEL_COMPASS, style, 0x0D,
                  target_x, top + draw_y, clip);
    }
    const double caret_y = top + draw_y + (g_scale.enhanced
        ? 6 : (side_height + 1) / 2);
    if (bearing > -3.0) {
        const double right_x = g_scale.enhanced
            ? left + panel.pane_width
            : left + panel.pane_width + side_outset - 1;
        add_shape(mem, PANEL_COMPASS, style, 0x22,
                  right_x, caret_y, target_clip, 1);
        if (bearing > 90.0)
            add_shape(mem, PANEL_COMPASS, style, 0x22,
                      right_x + 1, caret_y, target_clip, 1);
    }
    if (bearing < 3.0) {
        const double left_x = g_scale.enhanced ? left : left - side_outset;
        add_shape(mem, PANEL_COMPASS, style, 0x1F,
                  left_x, caret_y, target_clip, -1);
        if (bearing < -90.0)
            add_shape(mem, PANEL_COMPASS, style, 0x1F,
                      left_x - 1, caret_y, target_clip, -1);
    }
}

static void capture_altimeter(const Mem &mem, uint32_t player,
                              uint32_t mech, uint32_t body,
                              uint32_t config, int style)
{
    PanelState &panel = g_scale.panels[PANEL_ALTIMETER];
    if (!read_panel(mem, config, panel)) return;
    const Rect clip = panel.bounds;
    const int left = clip.left, top = clip.top;
    const int scale_x = mem.i32_rel(ADDR_ALTIMETER_LAYOUT);
    const int ref_y = mem.i32_rel(ADDR_ALTIMETER_LAYOUT + 4);
    const int indicator_x = mem.i32_rel(ADDR_ALTIMETER_CONFIG);
    const int32_t altitude_scale = mem.i32_rel(ADDR_ALTIMETER_CONFIG + 4);
    const int reference_x = mem.i32_rel(ADDR_ALTIMETER_CONFIG + 8);
    const int target_marker_x = mem.i32_rel(ADDR_ALTIMETER_CONFIG + 0x0C);
    const int64_t raw_altitude = mem.i32(body + 0x54);
    const int64_t ground_reference = mem.i32(mech + 0xCC);
    const int64_t altitude = raw_altitude - ground_reference;
    double draw_y = ref_y + altimeter_offset(
        altitude - 0x4E84, altitude_scale, g_scale.enhanced);
    int scale_resource = 0x07;
    if (draw_y > ref_y) {
        scale_resource = 0x0115;
        draw_y = ref_y;
    }
    if (g_scale.enhanced) {
        panel.scale_origin_x = left + scale_x - 10;
        panel.scale_origin_y = top + draw_y;
    } else {
        add_sprite(mem, PANEL_ALTIMETER, scale_resource + style,
                   left + scale_x, top + draw_y, clip);
    }
    add_shape(mem, PANEL_ALTIMETER, style, 0x01,
              left + reference_x, top + ref_y, clip);
    const int64_t ground_under_mech = mem.i32(body + 0x74);
    const double indicator_y = ref_y + altimeter_offset(
        altitude - ground_under_mech, altitude_scale, g_scale.enhanced);
    add_shape(mem, PANEL_ALTIMETER, style, 0x04,
              left + indicator_x, top + indicator_y, clip);

    const int target_status = mem.u8(body + 0xDD);
    if (!(target_status & 0x0F) || (target_status & 0x10)) return;
    int64_t selected_altitude;
    if (!target_altitude(mem, mem.u32(player + 0xDC), selected_altitude)) return;
    const int64_t player_altitude = raw_altitude - ground_reference;
    double marker_y = ref_y + altimeter_offset(
        player_altitude - selected_altitude,
        altitude_scale, g_scale.enhanced);
    int marker = 0x1F;
    if (marker_y < 0.0) { marker_y = 0.0; marker = 0x25; }
    else if (marker_y > panel.pane_height) {
        marker_y = panel.pane_height;
        marker = 0x1C;
    }
    const int target_x = mem.i32_rel(ADDR_ALTIMETER_TARGET_X) +
        target_marker_x;
    add_shape(mem, PANEL_ALTIMETER, style, marker,
              left + target_x, top + marker_y, clip);
}

static Transform panel_transform(int panel_index, int width, int height)
{
    const PanelState &panel = g_scale.panels[panel_index];
    const Rect &r = panel.bounds;
    const Mw2erRendererConfig &cfg = mw2er_config();
    const double position = resolved_scale(height, cfg.hud_position_scaling);
    const double scale = g_scale.enhanced
        ? resolved_scale(height, cfg.hud_panel_scaling)
        : std::min(1.0, std::max(1, height) / 768.0);
    const double canvas_x = (width - 1024.0 * position) * 0.5;
    const double canvas_y = (height - 768.0 * position) * 0.5;
    const double pivot_x = panel_index == PANEL_COMPASS
        ? (r.left + r.right) * 0.5 : r.left;
    const double pivot_y = panel_index == PANEL_COMPASS
        ? r.top : (r.top + r.bottom) * 0.5;
    double target_x = canvas_x + pivot_x * position;
    const double target_y = panel_index == PANEL_COMPASS
        ? canvas_y + pivot_y * position
        : canvas_y + 768.0 * cfg.hud_middle_panel_vertical_position * position;
    if (panel_index == PANEL_ALTIMETER)
        target_x -= std::max(0.0, canvas_x) *
            cfg.hud_middle_widescreen_position;
    return {target_x - pivot_x * scale,
            target_y - pivot_y * scale, scale};
}

static Mw2erTargetClip transformed_clip(const Rect &r, const Transform &t,
                                        int width, int height)
{
    return {
        std::clamp((int)std::floor(t.ox + r.left * t.scale), 0, width),
        std::clamp((int)std::floor(t.oy + r.top * t.scale), 0, height),
        std::clamp((int)std::ceil(t.ox + r.right * t.scale), 0, width),
        std::clamp((int)std::ceil(t.oy + r.bottom * t.scale), 0, height),
    };
}

static void set_scissor(const Mw2erTargetClip &clip, int height)
{
    glEnable(GL_SCISSOR_TEST);
    glScissor(clip.left, height - clip.bottom,
              std::max(0, clip.right - clip.left),
              std::max(0, clip.bottom - clip.top));
}

static void palette_color(const uint8_t *palette, int index, float color[4])
{
    index = std::clamp(index, 0, 255);
    color[0] = palette[index * 3] / 255.0f;
    color[1] = palette[index * 3 + 1] / 255.0f;
    color[2] = palette[index * 3 + 2] / 255.0f;
    color[3] = 1.0f;
}

struct VertexWriter {
    Mw2erHudVertex vertices[512];
    int count;
    int width, height;

    VertexWriter(int w, int h) : count(0), width(w), height(h) {}

    int flush()
    {
        if (!count) return 1;
        const int result = mw2er_hud_submit_rects(
            vertices, count, width, height);
        count = 0;
        return result;
    }

    int rect(float left, float top, float right, float bottom,
             const uint8_t *palette, int color_index)
    {
        if (right <= left || bottom <= top) return 1;
        if (count > 506 && !flush()) return 0;
        float color[4];
        palette_color(palette, color_index, color);
        const float points[6][2] = {
            {left,top},{right,top},{left,bottom},
            {left,bottom},{right,top},{right,bottom},
        };
        for (const auto &point : points)
            vertices[count++] = {point[0], point[1],
                                 color[0], color[1], color[2], color[3]};
        return 1;
    }
};

struct ScissorCleanup {
    ~ScissorCleanup() { glDisable(GL_SCISSOR_TEST); }
};

static int draw_fills(int panel_index, const Transform &t,
                      const Mw2erTargetClip &clip,
                      const uint8_t *palette, int width, int height)
{
    VertexWriter writer(width, height);
    set_scissor(clip, height);
    for (int i = 0; i < g_scale.fill_count; ++i) {
        const FillDraw &fill = g_scale.fills[i];
        if (fill.panel != panel_index) continue;
        if (!writer.rect(
                (float)round_pixel(t.ox + fill.left * t.scale),
                (float)round_pixel(t.oy + fill.top * t.scale),
                (float)round_pixel(t.ox + fill.right * t.scale),
                (float)round_pixel(t.oy + fill.bottom * t.scale),
                palette, fill.color)) return 0;
    }
    return writer.flush();
}

static int draw_compass_ticks(const PanelState &panel, const Transform &t,
                              const Mw2erTargetClip &clip,
                              const uint8_t *palette, int width, int height)
{
    const int source_x = (int)(panel.bounds.left - panel.scale_origin_x);
    const int first = std::clamp(-floor_div(-source_x, 10), 0, 144);
    const int stop = std::clamp(
        floor_div(source_x + panel.bounds.right - panel.bounds.left - 1, 10) + 1,
        0, 144);
    const double ox = round_pixel(t.ox + panel.scale_origin_x * t.scale);
    const double oy = round_pixel(t.oy + panel.scale_origin_y * t.scale);
    VertexWriter writer(width, height);
    set_scissor(clip, height);
    for (int tick = first; tick < stop; ++tick) {
        const double x = tick * 10.0;
        const double top = tick % 2 == 0 ? 4.0 : 0.0;
        if (!writer.rect((float)(ox + x * t.scale),
                         (float)(oy + top * t.scale),
                         (float)(ox + (x + 2.0) * t.scale),
                         (float)(oy + 12.0 * t.scale),
                         palette, 0x0E)) return 0;
    }
    return writer.flush();
}

static int append_altimeter_tick(VertexWriter &writer, int tick,
                                  double ox, double oy, double scale,
                                  const uint8_t *palette)
{
    const double y = 3.0 + tick * 10.0;
    const double left = tick % 2 == 0 ? 40.0 : 43.0;
    auto rect = [&](double x0, double y0, double x1, double y1, int color) {
        return writer.rect((float)(ox + x0 * scale),
                           (float)(oy + y0 * scale),
                           (float)(ox + x1 * scale),
                           (float)(oy + y1 * scale), palette, color);
    };
    return rect(left, y, 52, y + 1, 0x0D) &&
        rect(left, y + 2, 52, y + 3, 0x0D) &&
        rect(left, y + 1, left + 1, y + 2, 0x0D) &&
        rect(left + 1, y + 1, 51, y + 2, 0x0E) &&
        rect(51, y + 1, 52, y + 2, 0x0D) &&
        (tick % 2 || rect(39, y, 40, y + 3, 0x0D));
}

static int draw_altimeter_ticks(const PanelState &panel, const Transform &t,
                                const Mw2erTargetClip &clip,
                                const uint8_t *palette, int width, int height)
{
    const int draw_y = (int)(panel.scale_origin_y - panel.bounds.top);
    const int first = std::max(0, -floor_div(draw_y + 3, 10));
    const int last = std::min(92,
        floor_div(panel.bounds.bottom - panel.bounds.top - draw_y - 4, 10));
    const double ox = round_pixel(t.ox + panel.scale_origin_x * t.scale);
    const double oy = round_pixel(t.oy + panel.scale_origin_y * t.scale);
    VertexWriter writer(width, height);
    set_scissor(clip, height);
    for (int tick = first; tick <= last; ++tick)
        if (!append_altimeter_tick(writer, tick, ox, oy, t.scale, palette))
            return 0;
    return writer.flush();
}

static int draw_texts(int panel_index, const PanelState &panel,
                      const Transform &t, const Mw2erTargetClip &clip,
                      const uint8_t *palette, int width, int height)
{
    const double font_scale = resolved_scale(
        height, mw2er_config().hud_font_scaling);
    const double ox = round_pixel(t.ox + panel.scale_origin_x * t.scale);
    const double oy = round_pixel(t.oy + panel.scale_origin_y * t.scale);
    float color[4];
    palette_color(palette, 0x0E, color);
    set_scissor(clip, height);
    if (panel_index == PANEL_COMPASS) {
        const int source_x = (int)(panel.bounds.left - panel.scale_origin_x);
        int label = -floor_div(50 - source_x, 60);
        const int stop = floor_div(source_x + panel.bounds.right -
            panel.bounds.left - 51, 60) + 1;
        const int size = std::max(1, round_pixel(19.0 * font_scale));
        for (; label < stop; ++label) {
            if (label < 0 || label >= 24) continue;
            char text[3];
            std::snprintf(text, sizeof(text), "%02d", (3 * (label + 1)) % 36);
            const int slot = COMPASS_TEXT_SLOT_BASE + label % 12;
            Mw2erTextMetrics metrics = {};
            if (mw2er_font_measure(slot, text, size, 0.0f, &metrics) != MW2ER_OK)
                return 0;
            const double x = ox + (50.5 + label * 60.0) * t.scale -
                metrics.width * 0.68 * 0.5;
            const double y = oy + 13.0 * t.scale;
            if (mw2er_font_draw(slot, text, size, 0.0f,
                    (float)x, (float)y, 0.68f, color,
                    width, height) != MW2ER_OK) return 0;
        }
        return 1;
    }
    const int draw_y = (int)(panel.scale_origin_y - panel.bounds.top);
    const int first_tick = std::max(0, -floor_div(draw_y + 3, 10));
    const int last_tick = std::min(92,
        floor_div(panel.bounds.bottom - panel.bounds.top - draw_y - 4, 10));
    const int first = (first_tick + 1) / 2;
    const int stop = last_tick / 2 + 1;
    const int size = std::max(1, round_pixel(16.0 * font_scale));
    for (int label = first; label < stop; ++label) {
        char text[16];
        std::snprintf(text, sizeof(text), "%d", 200 - label * 5);
        Mw2erTextMetrics metrics = {};
        const int slot = ALTIMETER_TEXT_SLOT_BASE + label;
        if (mw2er_font_measure(slot, text, size, 0.0f, &metrics) != MW2ER_OK)
            return 0;
        const double x = ox + 37.0 * t.scale - metrics.width;
        const double y = oy + (4.5 + label * 20.0) * t.scale -
            metrics.height * 0.5;
        if (mw2er_font_draw(slot, text, size, 0.0f,
                (float)x, (float)y, 1.0f, color,
                width, height) != MW2ER_OK) return 0;
    }
    return 1;
}

static const int k_shape_rect_count[6] = {1, 1, 3, 3, 2, 2};
static const int k_shape_rects[6][3][5] = {
    {{0,0,6,2,0x0F}},
    {{0,0,6,2,0x06}},
    {{-3,0,-1,14,0x0D},{-1,0,1,14,0x0E},{1,0,3,14,0x0D}},
    {{-3,0,-1,14,0x09},{-1,0,1,14,0x0A},{1,0,3,14,0x09}},
    {{-1,-5,1,0,0x0E},{-1,14,1,19,0x0E}},
    {{-1,-5,1,0,0x0A},{-1,14,1,19,0x0A}},
};

static int draw_rect_shapes(int panel_index, const Transform &t,
                            const Mw2erTargetClip &clip,
                            const uint8_t *palette, int width, int height)
{
    VertexWriter writer(width, height);
    set_scissor(clip, height);
    for (int i = 0; i < g_scale.shape_count; ++i) {
        const ShapeDraw &shape = g_scale.shapes[i];
        if (shape.panel != panel_index || shape.type >= 6) continue;
        const double ax = round_pixel(t.ox + shape.x * t.scale);
        const double ay = round_pixel(t.oy + shape.y * t.scale);
        for (int r = 0; r < k_shape_rect_count[shape.type]; ++r) {
            const int *rect = k_shape_rects[shape.type][r];
            if (!writer.rect((float)(ax + rect[0] * t.scale),
                             (float)(ay + rect[1] * t.scale),
                             (float)(ax + rect[2] * t.scale),
                             (float)(ay + rect[3] * t.scale),
                             palette, rect[4])) return 0;
        }
    }
    return writer.flush();
}

} // namespace

void mw2er_compass_altimeter_reset(void)
{
    g_scale = {};
}

void mw2er_compass_altimeter_capture(const Mem &mem, uint32_t player,
                                     uint32_t mech, uint32_t body)
{
    g_scale = {};
    const uint32_t compass_enabled = mem.u32_rel(ADDR_COMPASS_ENABLE);
    const uint32_t altimeter_enabled = mem.u32_rel(ADDR_COMPASS_ENABLE + 8);
    if (!compass_enabled && !altimeter_enabled) return;
    g_scale.enhanced = _stricmp(
        mw2er_config().hud_compass_altimeter, "enhanced") == 0;
    const uint32_t altimeter_config = mem.u32_rel(ADDR_HUD_POST_CONFIGS);
    const uint32_t compass_config = mem.u32_rel(ADDR_HUD_POST_CONFIGS + 4);
    /* Enhanced mode must not read style state or resolve any scale RLE. */
    const int style = g_scale.enhanced
        ? 0 : mem.u8_rel(ADDR_HUD_STYLE_OFFSET);
    if (compass_enabled)
        capture_compass(mem, mech, body, compass_config, style);
    if (altimeter_enabled)
        capture_altimeter(mem, player, mech, body, altimeter_config, style);
}

int mw2er_compass_altimeter_render(int width, int height,
                                   const uint8_t *palette)
{
    if (!palette || width <= 0 || height <= 0) return 0;
    ScissorCleanup scissor_cleanup;
    Transform transforms[2] = {};
    Mw2erTargetClip clips[2] = {};
    for (int panel = 0; panel < 2; ++panel) {
        if (!g_scale.panels[panel].visible) continue;
        transforms[panel] = panel_transform(panel, width, height);
        clips[panel] = transformed_clip(
            g_scale.panels[panel].bounds, transforms[panel], width, height);
        if (!draw_fills(panel, transforms[panel], clips[panel],
                        palette, width, height)) return 0;
        if (g_scale.enhanced) {
            const int ticks_ok = panel == PANEL_COMPASS
                ? draw_compass_ticks(g_scale.panels[panel], transforms[panel],
                                     clips[panel], palette, width, height)
                : draw_altimeter_ticks(g_scale.panels[panel], transforms[panel],
                                       clips[panel], palette, width, height);
            if (!ticks_ok) return 0;
        }
    }
    if (g_scale.enhanced) {
        for (int panel = 0; panel < 2; ++panel) {
            if (!g_scale.panels[panel].visible) continue;
            if (!draw_texts(panel, g_scale.panels[panel], transforms[panel],
                            clips[panel], palette, width, height)) return 0;
        }
        for (int i = 0; i < g_scale.shape_count; ++i) {
            const ShapeDraw &shape = g_scale.shapes[i];
            if (shape.type < 6) continue;
            const Transform &t = transforms[shape.panel];
            const Mw2erTargetClip clip = transformed_clip(
                shape.clip, t, width, height);
            if (!mw2er_targeting_draw_compass_caret(
                    shape.type - 6,
                    t.ox + shape.x * t.scale,
                    t.oy + shape.y * t.scale,
                    shape.edge_attachment, t.scale, clip,
                    palette, width, height)) return 0;
        }
        for (int panel = 0; panel < 2; ++panel) {
            if (g_scale.panels[panel].visible &&
                !draw_rect_shapes(panel, transforms[panel], clips[panel],
                                  palette, width, height)) return 0;
        }
    } else {
        for (int i = 0; i < g_scale.sprite_count; ++i) {
            const SpriteDraw &sprite = g_scale.sprites[i];
            const Transform &t = transforms[sprite.panel];
            const Mw2erTargetClip clip = transformed_clip(
                sprite.clip, t, width, height);
            if (!mw2er_targeting_draw_sprite(
                    sprite.reference,
                    t.ox + sprite.x * t.scale,
                    t.oy + sprite.y * t.scale,
                    t.scale, clip, palette, width, height, 0)) return 0;
        }
    }
    return 1;
}
