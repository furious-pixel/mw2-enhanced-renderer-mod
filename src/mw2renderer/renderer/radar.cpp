#include "radar.h"

#include "config.h"
#include "font.h"
#include "gl_program.h"
#include "hud.h"
#include "mem.h"
#include "mw2er_internal.h"
#include "presentation.h"
#include "scene_draw.h"
#include "targeting.h"
#include "text_util.h"

#include "gl_api.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

namespace {

enum {
    ADDR_RADAR_CONFIG_TABLE = 0x000B4CF0,
    ADDR_RADAR_MODE_STATE = 0x000B4D04,
    ADDR_ACTIVE_CAMERA = 0x000A70E8,
    ADDR_COCKPIT_ZOOM = 0x000A6FA0,
    ADDR_CAMERA_POSITION = 0x0015FFA4,
    ADDR_RADAR_CAMERA_NODE_FALLBACK = 0x000B4CFC,
    ADDR_ENTITY_COUNT = 0x000A6270,
    ADDR_RADAR_SECONDARY_COUNT = 0x000A6274,
    ADDR_SECONDARY_MAX_INDEX = 0x000A6D50,
    ADDR_ENTITY_TABLE = 0x00108B00,
    ADDR_DIRECT_TARGETS = 0x00108BF0,
    ADDR_DIRECT_TARGET_COUNT = 0x000A6330,
    ADDR_DIRECT_TARGET_GROUP = 0x000A633C,
    ADDR_PRIMARY_CLASSIFICATION = 0x0010B631,
    ADDR_SECONDARY_TABLE = 0x00104B80,
    ADDR_SECONDARY_POSITION_TABLE = 0x00111FA0,
    ADDR_HUD_STYLE_OFFSET = 0x000B4E80,
    ADDR_SECONDARY_CLASSIFICATION = 0x0010C6C4,
    MAX_RADAR_BLIPS = 8192,
    MAX_RADAR_NAV = 4096,
    MAX_RADAR_SPRITES = 16,
    SPRITE_DRAW_CHUNK = 128,
    RADAR_RANGE_SLOT = 1395,
    RADAR_BEARING_SLOT = 1396,
};

static constexpr double k_pi = 3.14159265358979323846;
static constexpr double k_turn = 0x01680000;
static constexpr double k_fp29 = 536870912.0;
static constexpr double k_fixed = 65536.0;

struct Rect { int left, top, right, bottom; };
struct Transform { double ox, oy, sx, sy; };

struct RadarProjection {
    int left, top, width, height;
    double center_x, center_y, radius_x, radius_y;
    uint32_t ratio;
    int range_divisor;
    int32_t cam_x, cam_y, cam_z;
    int32_t mx, mz, nz;
};

struct RadarBlip { float x, y; int color; };
struct RadarNav { float x, y; int sprite; };

struct SpriteCacheEntry {
    int resource;
    uint32_t shape;
    Mw2erSprite sprite;
    GLuint texture;
};

struct RadarState {
    int visible;
    int mode;
    Rect pane;
    double transition_extent;
    double fov_heading;
    double native_fov_half;
    int fov_color;
    int fov_visible;
    int circle_color;
    int ellipse_visible;
    double center_x, center_y, radius_x, radius_y;
    RadarProjection projection;
    RadarBlip blips[MAX_RADAR_BLIPS];
    int blip_count;
    RadarNav nav[MAX_RADAR_NAV];
    int nav_count;
    int nav_refs[2];
    int player_sprite;
    int target_sprite;
    float target_x, target_y;
    int selected_valid;
    int32_t selected_world[3];
    int selected_color;
    char range_text[256];
    int range_x, range_y;
    char bearing_text[256];
    int bearing_x, bearing_y;
};

struct RadarLineVertex {
    float x, y, sx, sy, ex, ey, r, g, b, a;
};

struct RadarGl {
    GlProgram line_program;
    GlProgram ellipse_program;
    GLuint line_vao;
    GLuint line_vbo;
    GLuint ellipse_vao;
    GLuint ellipse_vbo;
    SpriteCacheEntry sprites[MAX_RADAR_SPRITES];
    int sprite_count;
    // Retired names are drained before any new sprite texture is uploaded.
    GLuint retired_textures[MAX_RADAR_SPRITES];
    int retired_count;
};

static RadarState g_radar;
static RadarGl g_gl;

static double resolved_scale(int height, float control)
{
    const double vertical = std::max(1, height) / 768.0;
    return vertical < 1.0 ? vertical
        : 1.0 + std::clamp((double)control, 0.0, 1.0) * (vertical - 1.0);
}

static Transform animated(const Rect &r, Transform t, double extent)
{
    extent = std::clamp(extent, 0.0, 1.0);
    if (extent == 1.0) return t;
    const double span_x = std::max(1, r.right - r.left);
    const double span_y = std::max(1, r.bottom - r.top);
    const double reveal_x = (1.0 + (span_x - 1.0) * extent) / span_x;
    const double reveal_y = (1.0 + (span_y - 1.0) * extent) / span_y;
    const double pivot_x = (r.left + r.right - 1) / 2;
    const double pivot_y = (r.top + r.bottom - 1) / 2;
    const double animated_left = pivot_x + (r.left - pivot_x) * extent;
    const double animated_top = pivot_y + (r.top - pivot_y) * extent;
    const double native_x = animated_left - r.left * reveal_x;
    const double native_y = animated_top - r.top * reveal_y;
    t.ox += native_x * t.sx;
    t.oy += native_y * t.sy;
    t.sx *= reveal_x;
    t.sy *= reveal_y;
    return t;
}

static Transform radar_transform(int width, int height)
{
    if (g_radar.mode == 4)
        return {0.0, 0.0, width / 1024.0, height / 768.0};
    const Mw2erRendererConfig &cfg = mw2er_config();
    const double panel_scale = resolved_scale(height, cfg.hud_panel_scaling);
    Transform t;
    if (g_radar.mode == 2) {
        const double cx = (g_radar.pane.left + g_radar.pane.right) * 0.5;
        const double cy = (g_radar.pane.top + g_radar.pane.bottom) * 0.5;
        const double extent = std::clamp(g_radar.transition_extent, 0.0, 1.0);
        const double span_x = std::max(1, g_radar.pane.right - g_radar.pane.left);
        const double span_y = std::max(1, g_radar.pane.bottom - g_radar.pane.top);
        const double sx = panel_scale *
            (1.0 + (span_x - 1.0) * extent) / span_x;
        const double sy = panel_scale *
            (1.0 + (span_y - 1.0) * extent) / span_y;
        return {width * 0.5 - cx * sx, height * 0.5 - cy * sy, sx, sy};
    } else {
        const double position_scale = resolved_scale(
            height, cfg.hud_position_scaling);
        const double canvas_x = (width - 1024.0 * position_scale) * 0.5;
        const double canvas_y = (height - 768.0 * position_scale) * 0.5;
        const double widescreen = std::max(0.0, canvas_x) *
            cfg.hud_top_widescreen_position;
        const double target_x = canvas_x + g_radar.pane.left * position_scale -
            widescreen;
        const double target_y = canvas_y + g_radar.pane.top * position_scale;
        t = {target_x - g_radar.pane.left * panel_scale,
             target_y - g_radar.pane.top * panel_scale,
             panel_scale, panel_scale};
    }
    return animated(g_radar.pane, t, g_radar.transition_extent);
}

static Rect transform_rect(const Rect &r, const Transform &t,
                           int width, int height)
{
    return {
        std::clamp((int)std::floor(t.ox + r.left * t.sx), 0, width),
        std::clamp((int)std::floor(t.oy + r.top * t.sy), 0, height),
        std::clamp((int)std::ceil(t.ox + r.right * t.sx), 0, width),
        std::clamp((int)std::ceil(t.oy + r.bottom * t.sy), 0, height),
    };
}

static void palette_color(const uint8_t *palette, int index, float out[4])
{
    index = std::clamp(index, 0, 255);
    out[0] = palette[index * 3] / 255.0f;
    out[1] = palette[index * 3 + 1] / 255.0f;
    out[2] = palette[index * 3 + 2] / 255.0f;
    out[3] = 1.0f;
}

static int32_t floor_shift3(int64_t value)
{
    if (value >= 0) return (int32_t)(value >> 3);
    return (int32_t)-(((-value) + 7) >> 3);
}

static int project_raw(const int32_t world[3], const RadarProjection &p,
                       double &x, double &y, int64_t &depth)
{
    if (!p.range_divisor) return 0;
    const int64_t dx = (int64_t)world[0] - p.cam_x;
    const int64_t dy = (int64_t)world[1] - p.cam_y;
    const int64_t dz = (int64_t)world[2] - p.cam_z;
    const int64_t rx = (dx * p.mx + dz * p.mz) >> 27;
    const int64_t ry = (dx * p.nz + dz * p.mx) >> 27;
    depth = (dy * -0x20000000ll) >> 27;
    x = p.center_x + 2.0 * rx / p.range_divisor;
    const double raw_y = p.center_y + 2.0 * ry / p.range_divisor;
    y = p.height - 1.0 - raw_y;
    return 1;
}

static int inside_ellipse(double x, double y, const RadarProjection &p)
{
    if (p.radius_x <= 0.0 || !p.ratio) return 0;
    const double dx = x - p.center_x;
    const double dy = (y - p.center_y) * 65536.0 / p.ratio;
    return dx * dx + dy * dy <= p.radius_x * p.radius_x;
}

static int project_visible(const int32_t world[3], const RadarProjection &p,
                           float &x, float &y)
{
    double px, py;
    int64_t depth;
    if (!project_raw(world, p, px, py, depth) || depth <= 0 ||
        !inside_ellipse(px, py, p)) return 0;
    x = (float)(p.left + px);
    y = (float)(p.top + py);
    return 1;
}

static int project_target(const int32_t world[3], const RadarProjection &p,
                          float &x, float &y)
{
    double px, py;
    int64_t depth;
    if (!project_raw(world, p, px, py, depth) || depth <= 0) return 0;
    if (!inside_ellipse(px, py, p)) {
        const double dx = px - p.center_x;
        const double dy = py - p.center_y;
        const double denominator = std::sqrt(
            (dx / p.radius_x) * (dx / p.radius_x) +
            (dy / p.radius_y) * (dy / p.radius_y));
        if (denominator <= 0.0) return 0;
        px = p.center_x + dx / denominator;
        py = p.center_y + dy / denominator;
    }
    x = (float)(p.left + px);
    y = (float)(p.top + py);
    return 1;
}

static int sprite_ref(const Mem &mem, int resource)
{
    if (resource < 0) return -1;
    for (int i = 0; i < g_gl.sprite_count; ++i)
        if (g_gl.sprites[i].resource == resource) return i;
    if (g_gl.sprite_count >= MAX_RADAR_SPRITES) return -1;
    const uint32_t shape = mw2er_resolve_cached_shape(mem, resource);
    if (!shape) return -1;
    Mw2erSprite sprite;
    if (!mw2er_decode_runtime_sprite(mem, shape, 0, &sprite)) return -1;
    SpriteCacheEntry &entry = g_gl.sprites[g_gl.sprite_count];
    entry.resource = resource;
    entry.shape = shape;
    entry.sprite = std::move(sprite);
    entry.texture = 0;
    return g_gl.sprite_count++;
}

static int table_sprite(const Mem &mem, uint32_t table, int slot,
                        int sub_index, int style)
{
    if (!table || sub_index < 0 || sub_index > 2) return -1;
    const int base = mem.i32(table + slot * 12u + sub_index * 4u);
    return base ? sprite_ref(mem, base + style) : -1;
}

static void append_blip(float x, float y, int color)
{
    if (g_radar.blip_count >= MAX_RADAR_BLIPS) return;
    g_radar.blips[g_radar.blip_count++] = {x, y, color};
}

static int primary_sub_index(const Mem &mem, uint32_t slot)
{
    return slot < 16 ? std::clamp((int)mem.u8_rel(
        ADDR_PRIMARY_CLASSIFICATION + slot * 0x26u), 0, 2) : 2;
}

static void read_string(const Mem &mem, uint32_t address, char *out, int capacity)
{
    out[0] = '\0';
    const uint8_t *raw = mem.view(address, 64);
    if (raw) mw2er_cp437_to_utf8(raw, 64, out, capacity);
}

static int secondary_position(const Mem &mem, int index, int count, int maximum,
                              int32_t out[3], int &sub_index)
{
    if (index < 0 || count < 0 || count > 4096 || index >= count) return 0;
    const uint32_t entry = mem.rt(ADDR_SECONDARY_TABLE + index * 0x40u);
    const int object = mem.i32(entry + 0x04);
    if (object < 0 || object > maximum) return 0;
    const uint32_t record = mem.rt(
        ADDR_SECONDARY_POSITION_TABLE + object * 0x7Cu);
    const uint32_t position = mem.u32(record + 0x1C);
    const uint32_t node = position ? 0 : mem.u32(record + 0x20);
    const uint32_t source = position ? position + 0x34 : (node ? node + 0x60 : 0);
    if (!source) return 0;
    out[0] = mem.i32(source);
    out[1] = mem.i32(source + 4);
    out[2] = mem.i32(source + 8);
    const int reference = mem.i32(entry + 0x0C);
    sub_index = reference < 0 ? 2 : std::clamp(
        (int)mem.u8_rel(ADDR_SECONDARY_CLASSIFICATION + reference), 0, 2);
    return 1;
}

static void capture_nav(const Mem &mem, uint32_t shape_table, int style)
{
    const int count = mem.i32_rel(ADDR_DIRECT_TARGET_COUNT);
    if (count < 0 || count > MAX_RADAR_NAV) return;
    const int group = mem.i32_rel(ADDR_DIRECT_TARGET_GROUP);
    const int group_bit = group >= 0 && group < 31 ? 1 << group : 0;
    g_radar.nav_refs[0] = table_sprite(mem, shape_table, 4, 0, style);
    g_radar.nav_refs[1] = table_sprite(mem, shape_table, 4, 1, style);
    for (int i = 0; i < count; ++i) {
        const uint32_t record = mem.rt(ADDR_DIRECT_TARGETS + i * 0x54u);
        if (!mem.i32(record) || mem.i32(record + 0x0C) != group ||
            (mem.u16(record + 0x24) & 1)) continue;
        const int32_t world[3] = {mem.i32(record + 0x18),
                                  mem.i32(record + 0x1C),
                                  mem.i32(record + 0x20)};
        float x, y;
        if (!project_visible(world, g_radar.projection, x, y)) continue;
        const int flags = mem.u16(record + 0x24);
        const int mask = mem.i16(record + 0x26);
        const int special = (flags & 0x20) && (mask | group_bit);
        const int reference = g_radar.nav_refs[special ? 1 : 0];
        if (reference >= 0 && g_radar.nav_count < MAX_RADAR_NAV)
            g_radar.nav[g_radar.nav_count++] = {x, y, reference};
    }
}

static void capture_text(const Mem &mem, const uint8_t mode_state[5],
                         uint32_t config, int range_scale, int body_heading,
                         int hud_mode, int transition_phase)
{
    if (transition_phase == 1 || mode_state[0] != 4 ||
        (g_radar.mode == 4 && hud_mode != 2)) return;
    const int half_range = range_scale / 2;
    const int divisor = half_range >= 100000 ? 100000 : 100;
    const uint32_t unit_address = mem.u32(config +
        (half_range >= 100000 ? 0x54 : 0x50));
    char prefix[64] = {}, unit[64] = {};
    read_string(mem, mem.u32(config + 0x3C), prefix, sizeof(prefix));
    read_string(mem, unit_address, unit, sizeof(unit));
    const int64_t fixed_value = ((int64_t)half_range << 16) / divisor;
    const double value = fixed_value / 65536.0;
    if (prefix[0] || unit[0])
        snprintf(g_radar.range_text, sizeof(g_radar.range_text),
                 "%s%3.1f%s", prefix, value, unit);
    else
        read_string(mem, mem.u32(config + 0x40), g_radar.range_text,
                    sizeof(g_radar.range_text));
    int x = mem.i32(config + 0x60);
    int y = mem.i32(config + 0x64);
    if (g_radar.mode == 1) { x -= 10; y -= 17; }
    else if (g_radar.mode == 2) { x -= 16; y -= 17; }
    g_radar.range_x = g_radar.pane.left + x;
    g_radar.range_y = g_radar.pane.top + y;
    if (g_radar.mode != 4) return;
    char bearing_prefix[64] = {};
    read_string(mem, mem.u32(config + 0x44), bearing_prefix,
                sizeof(bearing_prefix));
    int normalized = body_heading % (int)k_turn;
    if (normalized < 0) normalized += (int)k_turn;
    snprintf(g_radar.bearing_text, sizeof(g_radar.bearing_text),
             "%s%3.1f", bearing_prefix, normalized / 65536.0);
    g_radar.bearing_x = g_radar.pane.left + mem.i32(config + 0x68);
    g_radar.bearing_y = g_radar.pane.top + mem.i32(config + 0x6C);
}

static void satellite_center(const Mem &mem, uint32_t player,
                             int32_t &x, int32_t &z)
{
    x = mem.i32_rel(ADDR_CAMERA_POSITION + 4);
    z = mem.i32_rel(ADDR_CAMERA_POSITION + 8);
    if (mem.i32_rel(ADDR_RADAR_CAMERA_NODE_FALLBACK) == 0) {
        const uint32_t node = mem.u32(player + 0x44);
        if (node) { x = mem.i32(node + 0x60); z = mem.i32(node + 0x68); }
    } else {
        const uint32_t camera = mem.u32_rel(ADDR_ACTIVE_CAMERA);
        if (camera) { x = mem.i32(camera); z = mem.i32(camera + 8); }
    }
}

static int ensure_gl()
{
    if (g_gl.line_program.ok() && g_gl.ellipse_program.ok() &&
        g_gl.line_vao && g_gl.line_vbo && g_gl.ellipse_vao &&
        g_gl.ellipse_vbo) return 1;
    const std::string dir = mw2er_shader_dir();
    if (!g_gl.line_program.load((dir + "/radar_line.vert").c_str(),
                                (dir + "/radar_line.frag").c_str()) ||
        !g_gl.ellipse_program.load((dir + "/radar_ellipse.vert").c_str(),
                                   (dir + "/radar_ellipse.frag").c_str()))
        return 0;
    glGenVertexArrays(1, &g_gl.line_vao);
    glGenBuffers(1, &g_gl.line_vbo);
    glBindVertexArray(g_gl.line_vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_gl.line_vbo);
    glBufferData(GL_ARRAY_BUFFER, 36 * sizeof(RadarLineVertex), nullptr,
                 GL_STREAM_DRAW);
    const char *names[4] = {"in_pos", "in_start", "in_end", "in_color"};
    const int sizes[4] = {2, 2, 2, 4};
    const size_t offsets[4] = {0, 2, 4, 6};
    for (int i = 0; i < 4; ++i) {
        const GLint location = glGetAttribLocation(g_gl.line_program.id, names[i]);
        if (location < 0) return 0;
        glEnableVertexAttribArray((GLuint)location);
        glVertexAttribPointer((GLuint)location, sizes[i], GL_FLOAT, GL_FALSE,
            sizeof(RadarLineVertex), (const void *)(offsets[i] * sizeof(float)));
    }
    glGenVertexArrays(1, &g_gl.ellipse_vao);
    glGenBuffers(1, &g_gl.ellipse_vbo);
    glBindVertexArray(g_gl.ellipse_vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_gl.ellipse_vbo);
    glBufferData(GL_ARRAY_BUFFER, 12 * sizeof(float), nullptr, GL_STREAM_DRAW);
    const GLint position = glGetAttribLocation(g_gl.ellipse_program.id, "in_pos");
    if (position < 0) return 0;
    glEnableVertexAttribArray((GLuint)position);
    glVertexAttribPointer((GLuint)position, 2, GL_FLOAT, GL_FALSE,
                          2 * sizeof(float), nullptr);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);
    return 1;
}

} // namespace

int mw2er_hud_draw_lines(const Mw2erHudLine *lines, int count, float stroke,
                      int width, int height, const uint8_t *palette)
{
    if (!count) return 1;
    if (!ensure_gl()) return 0;
    RadarLineVertex vertices[36];
    int vertex_count = 0;
    const double fringe = std::max(0.25, (double)stroke * 0.5) + 1.0;
    for (int i = 0; i < count && vertex_count <= 30; ++i) {
        const double dx = lines[i].x1 - lines[i].x0;
        const double dy = lines[i].y1 - lines[i].y0;
        const double length = std::hypot(dx, dy);
        if (length <= 1e-6) continue;
        const double nx = -dy * fringe / length;
        const double ny = dx * fringe / length;
        float color[4];
        palette_color(palette, lines[i].color, color);
        const double corners[6][2] = {
            {lines[i].x0 + nx, lines[i].y0 + ny},
            {lines[i].x1 + nx, lines[i].y1 + ny},
            {lines[i].x0 - nx, lines[i].y0 - ny},
            {lines[i].x0 - nx, lines[i].y0 - ny},
            {lines[i].x1 + nx, lines[i].y1 + ny},
            {lines[i].x1 - nx, lines[i].y1 - ny},
        };
        for (const auto &corner : corners) {
            RadarLineVertex &v = vertices[vertex_count++];
            v = {(float)corner[0], (float)corner[1],
                 (float)lines[i].x0, (float)lines[i].y0,
                 (float)lines[i].x1, (float)lines[i].y1,
                 color[0], color[1], color[2], color[3]};
        }
    }
    if (!vertex_count) return 1;
    g_gl.line_program.use();
    g_gl.line_program.set2("u_viewport_size", (float)width, (float)height);
    g_gl.line_program.set("u_stroke_width", stroke);
    glEnable(GL_BLEND);
    glBlendFuncSeparate(GL_ONE, GL_ONE_MINUS_SRC_ALPHA, GL_ONE, GL_ZERO);
    glBindVertexArray(g_gl.line_vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_gl.line_vbo);
    glBufferSubData(GL_ARRAY_BUFFER, 0,
                    vertex_count * sizeof(RadarLineVertex), vertices);
    glDrawArrays(GL_TRIANGLES, 0, vertex_count);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);
    glDisable(GL_BLEND);
    glUseProgram(0);
    return 1;
}

namespace {

static double effective_fov_half(int width, int height)
{
    const double native_focal = 512.0 /
        std::max(1e-6, std::tan(g_radar.native_fov_half));
    const double output_focal = std::max(1.0, native_focal * height / 768.0);
    double half = std::atan(width / (2.0 * output_focal));
    const double cap = mw2er_config().max_horizontal_fov_degrees *
        k_pi / 360.0;
    return std::min(half, cap);
}

static int draw_fov(const Transform &t, float stroke, int width, int height,
                    const uint8_t *palette)
{
    if (!g_radar.fov_visible) return 1;
    Mw2erHudLine lines[2];
    const double half = effective_fov_half(width, height);
    for (int i = 0; i < 2; ++i) {
        const double angle = g_radar.fov_heading + (i ? half : -half);
        const double dx = std::cos(angle);
        const double dy = -std::sin(angle);
        double x0, y0, x1, y1;
        if (g_radar.mode == 4) {
            x0 = width * 0.5;
            y0 = height * 0.5;
            const double maximum_x = width - 1.0;
            const double maximum_y = height - 1.0;
            double edge = 1e30;
            double component = 1.0;
            if (dx > 0.0) { edge = (maximum_x - x0) / dx; component = dx; }
            else if (dx < 0.0) { edge = -x0 / dx; component = -dx; }
            double edge_y = 1e30;
            if (dy > 0.0) edge_y = (maximum_y - y0) / dy;
            else if (dy < 0.0) edge_y = -y0 / dy;
            if (edge_y < edge) { edge = edge_y; component = std::abs(dy); }
            const double overshoot = std::max(0.25, stroke * 0.5) + 2.0;
            const double scale = edge + overshoot / std::max(1e-9, component);
            x1 = x0 + dx * scale;
            y1 = y0 + dy * scale;
        } else {
            const double denominator = std::sqrt(
                (dx / g_radar.radius_x) * (dx / g_radar.radius_x) +
                (dy / g_radar.radius_y) * (dy / g_radar.radius_y));
            const double scale = denominator > 0.0 ? 1.0 / denominator : 0.0;
            const double lx0 = g_radar.pane.left + g_radar.center_x;
            const double ly0 = g_radar.pane.top + g_radar.center_y;
            x0 = t.ox + lx0 * t.sx;
            y0 = t.oy + ly0 * t.sy;
            x1 = t.ox + (lx0 + dx * scale) * t.sx;
            y1 = t.oy + (ly0 + dy * scale) * t.sy;
        }
        lines[i] = {x0, y0, x1, y1, g_radar.fov_color};
    }
    return mw2er_hud_draw_lines(lines, 2, stroke, width, height, palette);
}

static void quad(Mw2erHudVertex *out, int &count, float left, float top,
                 float right, float bottom, const float color[4])
{
    const float xy[6][2] = {{left,top},{right,top},{left,bottom},
                            {left,bottom},{right,top},{right,bottom}};
    for (const auto &p : xy)
        out[count++] = {p[0], p[1], color[0], color[1], color[2], color[3]};
}

static int draw_blips(const Transform &t, float artwork_scale,
                      int width, int height, const uint8_t *palette)
{
    Mw2erHudVertex vertices[504];
    int count = 0;
    const Rect clip = transform_rect(g_radar.pane, t, width, height);
    float colors[3][4];
    palette_color(palette, 0x0E, colors[0]);
    palette_color(palette, 0x0A, colors[1]);
    palette_color(palette, 0x0B, colors[2]);
    for (int i = 0; i < g_radar.blip_count; ++i) {
        const RadarBlip &blip = g_radar.blips[i];
        const float raw_left = (float)(t.ox + (blip.x - 1.0f) * t.sx);
        const float raw_top = (float)(t.oy + (blip.y - 1.0f) * t.sy);
        const float left = std::max((float)clip.left,
            (float)std::floor(raw_left + 0.5f));
        const float top = std::max((float)clip.top,
            (float)std::floor(raw_top + 0.5f));
        const float right = std::min((float)clip.right,
            (float)std::floor(raw_left + 2.0f * artwork_scale + 0.5f));
        const float bottom = std::min((float)clip.bottom,
            (float)std::floor(raw_top + 2.0f * artwork_scale + 0.5f));
        if (right <= left || bottom <= top) continue;
        const int color = blip.color == 0x0A ? 1 :
            (blip.color == 0x0B ? 2 : 0);
        quad(vertices, count, left, top, right, bottom, colors[color]);
        if (count == 504) {
            if (!mw2er_hud_submit_rects(vertices, count, width, height)) return 0;
            count = 0;
        }
    }
    return !count || mw2er_hud_submit_rects(vertices, count, width, height);
}

static GLuint sprite_texture(int reference)
{
    if (reference < 0 || reference >= g_gl.sprite_count) return 0;
    SpriteCacheEntry &entry = g_gl.sprites[reference];
    if (!entry.texture)
        entry.texture = mw2er_gl_upload_indexed_sprite(entry.sprite);
    return entry.texture;
}

static int draw_sprite_batch(int reference, const RadarNav *entries, int count,
                             const Transform &t, float artwork_scale,
                             int width, int height, const uint8_t *palette)
{
    if (reference < 0) return 1;
    const GLuint texture = sprite_texture(reference);
    if (!texture) return 0;
    const Rect clip = transform_rect(g_radar.pane, t, width, height);
    Mw2erIndexedSpriteDraw draws[SPRITE_DRAW_CHUNK];
    int draw_count = 0;
    for (int i = 0; i < count; ++i) {
        if (entries[i].sprite != reference) continue;
        Mw2erIndexedSpriteDraw &draw = draws[draw_count++];
        draw = {};
        const Mw2erSprite &sprite = g_gl.sprites[reference].sprite;
        const double anchor_x = t.ox + entries[i].x * t.sx;
        const double anchor_y = t.oy + entries[i].y * t.sy;
        draw.x = (float)(std::floor(
            anchor_x + sprite.x_offset * artwork_scale + 0.5) -
            sprite.x_offset * artwork_scale);
        draw.y = (float)(std::floor(
            anchor_y + sprite.y_offset * artwork_scale + 0.5) -
            sprite.y_offset * artwork_scale);
        draw.scale_x = artwork_scale;
        draw.scale_y = artwork_scale;
        draw.clip_left = clip.left; draw.clip_top = clip.top;
        draw.clip_right = clip.right; draw.clip_bottom = clip.bottom;
        draw.color_override = -1;
        if (draw_count == SPRITE_DRAW_CHUNK) {
            if (mw2er_gl_draw_indexed_sprites(g_gl.sprites[reference].sprite,
                    texture, draws, draw_count, palette, width, height) != MW2ER_OK)
                return 0;
            draw_count = 0;
        }
    }
    return !draw_count || mw2er_gl_draw_indexed_sprites(
        g_gl.sprites[reference].sprite, texture, draws, draw_count,
        palette, width, height) == MW2ER_OK;
}

static int draw_one_sprite(int reference, float x, float y,
                           const Transform &t, float artwork_scale,
                           int width, int height, const uint8_t *palette)
{
    RadarNav entry = {x, y, reference};
    return draw_sprite_batch(reference, &entry, 1, t, artwork_scale,
                             width, height, palette);
}

static int draw_ellipse(const Transform &t, float stroke, int width, int height,
                        const uint8_t *palette)
{
    if (!g_radar.ellipse_visible || g_radar.mode == 4 ||
        g_radar.radius_x <= 0.5 ||
        g_radar.radius_y <= 0.5) return 1;
    if (!ensure_gl()) return 0;
    const float cx = (float)(t.ox +
        (g_radar.pane.left + g_radar.center_x) * t.sx);
    const float cy = (float)(t.oy +
        (g_radar.pane.top + g_radar.center_y) * t.sy);
    const float rx = (float)(g_radar.radius_x * t.sx);
    const float ry = (float)(g_radar.radius_y * t.sy);
    const float fringe = std::max(0.25f, stroke * 0.5f) + 1.0f;
    const float vertices[12] = {
        cx-rx-fringe,cy-ry-fringe, cx+rx+fringe,cy-ry-fringe,
        cx-rx-fringe,cy+ry+fringe, cx-rx-fringe,cy+ry+fringe,
        cx+rx+fringe,cy-ry-fringe, cx+rx+fringe,cy+ry+fringe};
    float color[4];
    palette_color(palette, g_radar.circle_color, color);
    g_gl.ellipse_program.use();
    g_gl.ellipse_program.set2("u_viewport_size", (float)width, (float)height);
    g_gl.ellipse_program.set2("u_center", cx, cy);
    g_gl.ellipse_program.set2("u_radii", rx, ry);
    g_gl.ellipse_program.set("u_stroke_width", stroke);
    const GLint location = g_gl.ellipse_program.loc("u_color");
    if (location >= 0) glUniform4fv(location, 1, color);
    glEnable(GL_BLEND);
    glBlendFuncSeparate(GL_ONE, GL_ONE_MINUS_SRC_ALPHA, GL_ONE, GL_ZERO);
    glBindVertexArray(g_gl.ellipse_vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_gl.ellipse_vbo);
    glBufferSubData(GL_ARRAY_BUFFER, 0, sizeof(vertices), vertices);
    glDrawArrays(GL_TRIANGLES, 0, 6);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);
    glDisable(GL_BLEND);
    glUseProgram(0);
    return 1;
}

static int ray_to_view_edge(double cx, double cy, double dx, double dy,
                            int width, int height, double &x, double &y,
                            int &edge)
{
    double best = 1e30;
    edge = 3;
    if (dx > 0.0) { best = (width - cx) / dx; edge = 2; }
    else if (dx < 0.0) { best = -cx / dx; edge = 1; }
    double vertical = 1e30;
    int vertical_edge = 3;
    if (dy > 0.0) { vertical = (height - cy) / dy; vertical_edge = 0; }
    else if (dy < 0.0) { vertical = -cy / dy; vertical_edge = 3; }
    if (vertical < best) { best = vertical; edge = vertical_edge; }
    if (best == 1e30) return 0;
    x = cx + dx * best;
    y = cy + dy * best;
    return 1;
}

static int draw_satellite_target(int width, int height, const uint8_t *palette)
{
    if (!g_radar.selected_valid) return 1;
    Mw2erSceneExtract *scene = mw2er_scene_extract_current();
    double sx, sy;
    int onscreen;
    int projected_edge = -1;
    if (scene && scene->camera.satellite_view &&
        scene->camera.ortho_half_width > 0.0f) {
        const Mw2erCamera &camera = scene->camera;
        double delta[3];
        for (int i = 0; i < 3; ++i)
            delta[i] = g_radar.selected_world[i] / k_fixed - camera.position[i];
        const double view_x = delta[0] * camera.right[0] +
            delta[1] * camera.right[1] + delta[2] * camera.right[2];
        const double view_y = delta[0] * camera.up[0] +
            delta[1] * camera.up[1] + delta[2] * camera.up[2];
        const double half_height = camera.ortho_half_height > 0.0f
            ? camera.ortho_half_height : camera.ortho_half_width;
        const double half_width = half_height * width / (double)height;
        sx = width * (0.5 + view_x / (2.0 * half_width));
        sy = height * (0.5 - view_y / (2.0 * half_height));
        onscreen = sx >= 0.0 && sx < width && sy >= 0.0 && sy < height;
    } else {
        double px, py;
        int64_t depth;
        if (!project_raw(g_radar.selected_world, g_radar.projection,
                         px, py, depth) || depth <= 0) return 1;
        onscreen = px >= 0.0 && px < g_radar.projection.width &&
            py >= 0.0 && py < g_radar.projection.height;
        if (!onscreen) {
            if (!ray_to_view_edge(g_radar.center_x, g_radar.center_y,
                    px - g_radar.center_x, py - g_radar.center_y,
                    g_radar.projection.width, g_radar.projection.height,
                    px, py, projected_edge)) return 1;
            px = std::clamp(px, 3.0, g_radar.projection.width - 4.0);
            py = std::clamp(py, 3.0, g_radar.projection.height - 4.0);
        }
        sx = px * width / g_radar.projection.width;
        sy = py * height / g_radar.projection.height;
    }
    const double marker_scale = resolved_scale(
        height, mw2er_config().hud_target_marker_scaling);
    if (onscreen) {
        const int stroke = std::max(1, (int)std::floor(marker_scale + 0.5));
        int span = std::max(stroke,
            (int)std::floor(5.0 * marker_scale + 0.5));
        if ((span - stroke) & 1) ++span;
        const int cx = (int)std::floor(sx + 0.5);
        const int cy = (int)std::floor(sy + 0.5);
        const int outer_left = cx - span / 2;
        const int outer_top = cy - span / 2;
        const int center_left = cx - stroke / 2;
        const int center_top = cy - stroke / 2;
        float color[4];
        palette_color(palette, g_radar.selected_color, color);
        Mw2erHudVertex vertices[12];
        int count = 0;
        quad(vertices, count, (float)std::max(0, outer_left),
             (float)std::max(0, center_top),
             (float)std::min(width, outer_left + span),
             (float)std::min(height, center_top + stroke), color);
        quad(vertices, count, (float)std::max(0, center_left),
             (float)std::max(0, outer_top),
             (float)std::min(width, center_left + stroke),
             (float)std::min(height, outer_top + span), color);
        return mw2er_hud_submit_rects(vertices, count, width, height);
    }
    double edge_x, edge_y;
    int edge;
    if (projected_edge >= 0) {
        edge_x = sx;
        edge_y = sy;
        edge = projected_edge;
    } else if (!ray_to_view_edge(width * 0.5, height * 0.5,
                   sx - width * 0.5, sy - height * 0.5,
                   width, height, edge_x, edge_y, edge)) return 1;
    const Mw2erTargetClip clip = {0, 0, width, height};
    return mw2er_targeting_draw_caret(
        edge, edge_x, edge_y, g_radar.selected_color, marker_scale,
        clip, palette, width, height);
}

static int draw_text(int slot, const char *text, int x, int y,
                     const Transform &t, int color_index,
                     int width, int height, const uint8_t *palette)
{
    if (!text[0]) return 1;
    const int size = std::max(1, (int)std::nearbyint(
        16.0 * resolved_scale(height, mw2er_config().hud_font_scaling)));
    float color[4];
    palette_color(palette, color_index, color);
    return mw2er_font_draw(slot, text, size, 0.0f,
        (float)(t.ox + x * t.sx), (float)(t.oy + y * t.sy), 1.0f,
        color, width, height) == MW2ER_OK;
}

} // namespace

void mw2er_radar_mission_reset(void)
{
    for (int i = 0; i < g_gl.sprite_count; ++i) {
        if (g_gl.sprites[i].texture)
            g_gl.retired_textures[g_gl.retired_count++] = g_gl.sprites[i].texture;
        g_gl.sprites[i] = {};
    }
    g_gl.sprite_count = 0;
    g_radar = {};
}

void mw2er_radar_gl_reset(void)
{
    for (int i = 0; i < g_gl.sprite_count; ++i) {
        if (g_gl.sprites[i].texture)
            glDeleteTextures(1, &g_gl.sprites[i].texture);
        g_gl.sprites[i].texture = 0;
    }
    if (g_gl.retired_count != 0)
        glDeleteTextures((GLsizei)g_gl.retired_count,
                         g_gl.retired_textures);
    g_gl.retired_count = 0;
    if (g_gl.line_vbo) glDeleteBuffers(1, &g_gl.line_vbo);
    if (g_gl.line_vao) glDeleteVertexArrays(1, &g_gl.line_vao);
    if (g_gl.ellipse_vbo) glDeleteBuffers(1, &g_gl.ellipse_vbo);
    if (g_gl.ellipse_vao) glDeleteVertexArrays(1, &g_gl.ellipse_vao);
    g_gl.line_program.destroy();
    g_gl.ellipse_program.destroy();
    g_gl.line_vbo = g_gl.line_vao = 0;
    g_gl.ellipse_vbo = g_gl.ellipse_vao = 0;
}

void mw2er_radar_capture(const Mem &mem, int player_slot, uint32_t player,
                         uint32_t mech, uint32_t body, int hud_mode,
                         int transition_phase, double transition_extent,
                         int selected_target_indicators)
{
    g_radar.visible = 0;
    g_radar.blip_count = 0;
    g_radar.nav_count = 0;
    g_radar.selected_valid = 0;
    g_radar.range_text[0] = '\0';
    g_radar.bearing_text[0] = '\0';
    g_radar.player_sprite = -1;
    g_radar.target_sprite = -1;
    g_radar.nav_refs[0] = g_radar.nav_refs[1] = -1;
    uint8_t mode_state[5];
    if (!mem.read_rel(ADDR_RADAR_MODE_STATE, mode_state, sizeof(mode_state))) return;
    if (hud_mode < 0 || hud_mode > 3) return;
    const int mode = mode_state[4];
    if (mode != 1 && mode != 2 && mode != 4) return;
    const uint32_t table = mem.u32_rel(ADDR_RADAR_CONFIG_TABLE);
    const uint32_t config = table ? mem.u32(table + mode * 4u) : 0;
    const uint32_t pane_address = config ? mem.u32(config) : 0;
    if (!pane_address) return;
    const int left = mem.i32(pane_address + 0x04);
    const int top = mem.i32(pane_address + 0x08);
    const int right = mem.i32(pane_address + 0x0C);
    const int bottom = mem.i32(pane_address + 0x10);
    const int width = right - left + 1;
    const int height = bottom - top + 1;
    if (width <= 2 || height <= 2 || width > 4096 || height > 4096 ||
        (mode != 4 && transition_phase == 3 && transition_extent <= 0.0)) return;
    const uint32_t active_camera = mem.u32_rel(ADDR_ACTIVE_CAMERA);
    if (!active_camera) return;

    g_radar.visible = 1;
    g_radar.mode = mode;
    g_radar.pane = {left, top, right + 1, bottom + 1};
    g_radar.transition_extent = mode == 4 ? 1.0
        : std::clamp(transition_extent, 0.0, 1.0);
    g_radar.center_x = width >> 1;
    g_radar.center_y = height >> 1;
    g_radar.radius_x = (width >> 1) - 1;
    const uint32_t ratio = mem.u32(active_camera + 0x44);
    g_radar.radius_y = std::max(1.0,
        std::floor((g_radar.radius_x * ratio + 0x8000) / 65536.0));
    const uint32_t sub_config = mem.u32(config + 0x74);
    g_radar.fov_color = sub_config ? mem.u8(sub_config + 0x2C) : 0x0E;
    g_radar.circle_color = sub_config ? mem.u8(sub_config + 0x30) : 0x0E;
    g_radar.fov_visible = mode != 4 || hud_mode == 2;
    g_radar.ellipse_visible = mem.u32(config + 0x7C) != 0;
    const int aspect = mode == 4
        ? std::clamp(mem.i32_rel(ADDR_COCKPIT_ZOOM), 0x8000, 0x100000)
        : mem.i32(active_camera + 0x18);
    g_radar.native_fov_half = aspect
        ? std::atan2(65536.0, (double)aspect) : k_pi * 0.25;
    g_radar.native_fov_half = std::clamp(
        g_radar.native_fov_half, 0.0, k_pi * 0.5 - 1e-6);
    const int body_heading = mem.i32(body + 0x60);
    const int cockpit_yaw = mem.i32(mech + 0x0C);
    const int64_t heading = mode == 4
        ? (int64_t)body_heading + cockpit_yaw : cockpit_yaw;
    g_radar.fov_heading = k_pi * 0.5 - heading * (2.0 * k_pi / k_turn);

    const int range_scale = mem.i32(config + 0x18);
    int32_t cam_x = mem.i32_rel(ADDR_CAMERA_POSITION + 4);
    int32_t cam_z = mem.i32_rel(ADDR_CAMERA_POSITION + 8);
    if (mode == 4) satellite_center(mem, player, cam_x, cam_z);
    const int projection_heading = mode == 4 ? 0 : body_heading;
    const double yaw = projection_heading * (2.0 * k_pi / k_turn);
    const int64_t cos_fp = std::llround(std::cos(yaw) * k_fp29);
    const int64_t sin_fp = std::llround(std::sin(yaw) * k_fp29);
    const int64_t side_fp = std::llround(std::cos(yaw + k_pi * 0.5) * k_fp29);
    g_radar.projection = {left, top, width, height,
        g_radar.center_x, g_radar.center_y, g_radar.radius_x, g_radar.radius_y,
        ratio, width ? range_scale / width : 0,
        cam_x, 0x00030D40, cam_z,
        floor_shift3(cos_fp), floor_shift3(side_fp), floor_shift3(sin_fp)};

    capture_text(mem, mode_state, config, range_scale, body_heading,
                 hud_mode, transition_phase);
    if (!g_radar.projection.range_divisor || (mode == 4 && hud_mode != 2)) return;
    const int style = mem.u8_rel(ADDR_HUD_STYLE_OFFSET);
    const uint32_t shape_table = mem.u32(config + 0x70);
    capture_nav(mem, shape_table, style);

    const uint32_t handle = mem.u32(player + 0xDC);
    const int target_kind = handle & 0x0F00u;
    const int target_index = handle & 0xFFu;
    if (mode == 4) {
        if (!selected_target_indicators) return;
        if (!handle || (handle & 0x1000u) ||
            (target_kind != 0x0100 && target_kind != 0x0200 &&
             target_kind != 0x0400)) return;
        g_radar.selected_world[0] = mem.i32(player + 0xC8);
        g_radar.selected_world[1] = mem.i32(player + 0xCC);
        g_radar.selected_world[2] = mem.i32(player + 0xD0);
        int sub_index = 0;
        if (target_kind == 0x0200) {
            const uint32_t entity = mem.u32_rel(ADDR_ENTITY_TABLE + target_index * 4u);
            if (!entity) return;
            sub_index = primary_sub_index(mem, mem.u32(entity + 0x08));
            g_radar.selected_world[0] = mem.i32(entity + 0x50);
            g_radar.selected_world[1] = mem.i32(entity + 0x54);
            g_radar.selected_world[2] = mem.i32(entity + 0x58);
        } else if (target_kind == 0x0400) {
            if (!secondary_position(mem, target_index,
                mem.i32_rel(ADDR_RADAR_SECONDARY_COUNT),
                mem.i32_rel(ADDR_SECONDARY_MAX_INDEX),
                g_radar.selected_world, sub_index)) return;
        }
        const int colors[3] = {0x0E, 0x0A, 0x06};
        g_radar.selected_color = target_kind == 0x0100 ? 0x0E : colors[sub_index];
        g_radar.selected_valid = 1;
        return;
    }

    g_radar.player_sprite = sprite_ref(mem, 0xAC + style);
    int32_t selected_position[3] = {};
    int selected_sub = 0;
    int selected_found = 0;
    const int entity_count = mem.i32_rel(ADDR_ENTITY_COUNT);
    if (entity_count >= 0 && entity_count <= 4096) {
        for (int i = 0; i < entity_count; ++i) {
            const uint32_t entity = mem.u32_rel(ADDR_ENTITY_TABLE + i * 4u);
            if (!entity || i == player_slot) continue;
            const int flags = mem.u16(entity + 0x14);
            if (!(flags & 0x1400) || (flags & 0x0016)) continue;
            const int sub = primary_sub_index(mem, mem.u32(entity + 0x08));
            const int32_t world[3] = {mem.i32(entity + 0x50),
                                      mem.i32(entity + 0x54),
                                      mem.i32(entity + 0x58)};
            if (target_kind == 0x0200 && i == target_index) {
                std::memcpy(selected_position, world, sizeof(world));
                selected_sub = sub;
                selected_found = 1;
            }
            float x, y;
            if (project_visible(world, g_radar.projection, x, y)) {
                const int colors[3] = {0x0E, 0x0A, 0x0B};
                append_blip(x, y, colors[sub]);
            }
        }
    }
    const int secondary_count = mem.i32_rel(ADDR_RADAR_SECONDARY_COUNT);
    const int secondary_maximum = mem.i32_rel(ADDR_SECONDARY_MAX_INDEX);
    if (secondary_count >= 0 && secondary_count <= 4096) {
        for (int i = 0; i < secondary_count; ++i) {
            const uint32_t entry = mem.rt(ADDR_SECONDARY_TABLE + i * 0x40u);
            const int flags = mem.u16(entry);
            if (!(flags & 0x1400) || (flags & 0x001E)) continue;
            int32_t world[3];
            int sub;
            if (!secondary_position(mem, i, secondary_count,
                                    secondary_maximum, world, sub)) continue;
            if (target_kind == 0x0400 && i == target_index) {
                std::memcpy(selected_position, world, sizeof(world));
                selected_sub = sub;
                selected_found = 1;
            }
            float x, y;
            if (project_visible(world, g_radar.projection, x, y)) {
                const int colors[3] = {0x0E, 0x0A, 0x0B};
                append_blip(x, y, colors[sub]);
            }
        }
    }
    if (!handle || (handle & 0x1000u)) return;
    int slot = -1;
    if (target_kind == 0x0100) {
        selected_position[0] = mem.i32(player + 0xC8);
        selected_position[1] = mem.i32(player + 0xCC);
        selected_position[2] = mem.i32(player + 0xD0);
        selected_sub = 0;
        selected_found = 1;
    }
    if (!selected_found) return;
    float visible_x, visible_y;
    const int visible = project_visible(
        selected_position, g_radar.projection, visible_x, visible_y);
    if (visible) {
        g_radar.target_x = visible_x;
        g_radar.target_y = visible_y;
    } else if (!project_target(selected_position, g_radar.projection,
                               g_radar.target_x, g_radar.target_y)) return;
    if (target_kind == 0x0100) slot = visible ? 5 : 6;
    else slot = visible ? 2 : 3;
    g_radar.target_sprite = table_sprite(
        mem, shape_table, slot, selected_sub, style);
}

int mw2er_radar_render(int width, int height, const uint8_t *palette,
                       int draw_text_enabled)
{
    if (g_gl.retired_count != 0) {
        glDeleteTextures((GLsizei)g_gl.retired_count,
                         g_gl.retired_textures);
        g_gl.retired_count = 0;
    }
    if (!g_radar.visible) return 1;
    const Transform transform = radar_transform(width, height);
    const double panel_scale = resolved_scale(
        height, mw2er_config().hud_panel_scaling);
    const float artwork_scale = g_radar.mode == 4 ? 1.0f : (float)panel_scale;
    const float stroke_scale = g_radar.mode == 4
        ? (float)std::min(transform.sx, transform.sy) : (float)panel_scale;
    const float stroke = mw2er_config().hud_radar_stroke_width * stroke_scale;
    if (!draw_fov(transform, stroke, width, height, palette)) return 0;
    for (int i = 0; i < 2; ++i)
        if (!draw_sprite_batch(g_radar.nav_refs[i], g_radar.nav,
                g_radar.nav_count, transform, artwork_scale,
                width, height, palette)) return 0;
    if (g_radar.mode != 4 && !draw_one_sprite(
            g_radar.player_sprite,
            (float)(g_radar.pane.left + g_radar.center_x),
            (float)(g_radar.pane.top + g_radar.center_y),
            transform, artwork_scale, width, height, palette)) return 0;
    if (!draw_blips(transform, artwork_scale, width, height, palette)) return 0;
    if (g_radar.mode != 4 && !draw_one_sprite(
            g_radar.target_sprite, g_radar.target_x, g_radar.target_y,
            transform, artwork_scale, width, height, palette)) return 0;
    if (g_radar.mode == 4 &&
        !draw_satellite_target(width, height, palette)) return 0;
    if (!draw_ellipse(transform, stroke, width, height, palette)) return 0;
    glDisable(GL_SCISSOR_TEST);
    if (draw_text_enabled &&
        (!draw_text(RADAR_RANGE_SLOT, g_radar.range_text,
                    g_radar.range_x, g_radar.range_y, transform,
                    g_radar.circle_color, width, height, palette) ||
         !draw_text(RADAR_BEARING_SLOT, g_radar.bearing_text,
                    g_radar.bearing_x, g_radar.bearing_y, transform,
                    g_radar.circle_color, width, height, palette))) {
        glDisable(GL_SCISSOR_TEST);
        return 0;
    }
    glDisable(GL_SCISSOR_TEST);
    return 1;
}
