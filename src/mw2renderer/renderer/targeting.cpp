#include "targeting.h"

#include "mem.h"
#include "mw2er_internal.h"
#include "presentation.h"
#include "radar.h"

#include "gl_api.h"

#include <algorithm>
#include <cmath>
#include <initializer_list>
#include <utility>
#include <vector>

namespace {

enum { MAX_RESOURCE_SPRITES = 40, MARKER_COUNT = 13 };

struct ResourceSprite {
    int resource;
    Mw2erSprite sprite;
    GLuint texture;
};

struct MarkerSprites {
    int long_size, short_size, bracket_size, bracket_box, circle_size;
    int compass_long_size, compass_short_size;
    Mw2erSprite sprites[MARKER_COUNT];
    GLuint textures[MARKER_COUNT];
};

static ResourceSprite g_resources[MAX_RESOURCE_SPRITES];
static int g_resource_count;
static MarkerSprites g_markers;
// Retired names are drained before any new sprite texture is uploaded.
static GLuint g_retired_textures[MAX_RESOURCE_SPRITES];
static int g_retired_count;

static uint8_t alpha_byte(double alpha)
{
    return (uint8_t)std::nearbyint(std::clamp(alpha, 0.0, 1.0) * 255.0);
}

static Mw2erSprite make_sprite(int width, int height, int x_offset, int y_offset)
{
    Mw2erSprite result;
    result.width = width;
    result.height = height;
    result.x_offset = x_offset;
    result.y_offset = y_offset;
    result.pixels.resize((size_t)width * height * 2u);
    for (size_t i = 0; i < result.pixels.size(); i += 2)
        result.pixels[i] = 0x0E;
    return result;
}

static double segment_distance(double px, double py,
                               double x0, double y0, double x1, double y1)
{
    const double dx = x1 - x0, dy = y1 - y0;
    const double length2 = dx * dx + dy * dy;
    const double t = std::clamp(
        ((px - x0) * dx + (py - y0) * dy) / length2, 0.0, 1.0);
    return std::hypot(px - (x0 + t * dx), py - (y0 + t * dy));
}

static Mw2erSprite caret_sprite(const double points[3][2],
                                double logical_width, double logical_height,
                                double stroke, int width, int height,
                                int x_offset, int y_offset)
{
    Mw2erSprite sprite = make_sprite(
        width, height, x_offset, y_offset);
    const double scale_x = width / logical_width;
    const double scale_y = height / logical_height;
    const double aa_scale = 0.5 * (scale_x + scale_y);
    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            const double px = (x + 0.5) / scale_x;
            const double py = (y + 0.5) / scale_y;
            const double distance = std::min(
                segment_distance(px, py, points[0][0], points[0][1],
                                 points[1][0], points[1][1]),
                segment_distance(px, py, points[1][0], points[1][1],
                                 points[2][0], points[2][1]));
            sprite.pixels[((size_t)y * width + x) * 2u + 1] =
                alpha_byte(0.5 - (distance - stroke * 0.5) * aa_scale);
        }
    }
    return sprite;
}

static Mw2erSprite target_caret_sprite(int index, int width, int height)
{
    static const double points[4][3][2] = {
        {{1.0,1.0},{12.5,14.25},{24.0,1.0}},
        {{14.0,1.0},{0.75,12.5},{14.0,24.0}},
        {{1.0,1.0},{14.25,12.5},{1.0,24.0}},
        {{1.0,14.0},{12.5,0.75},{24.0,14.0}},
    };
    static const double logical_width[4] = {25.0,15.0,15.0,25.0};
    static const double logical_height[4] = {15.0,25.0,25.0,15.0};
    const int offsets[4][2] = {
        {-width / 2, -height + 1},
        {0, -height / 2},
        {-width + 1, -height / 2},
        {-width / 2, 0},
    };
    return caret_sprite(points[index], logical_width[index],
        logical_height[index], 3.0, width, height,
        offsets[index][0], offsets[index][1]);
}

static Mw2erSprite bracket_sprite(int size, int box_size,
                                  int flip_x, int flip_y)
{
    const int x_offset = flip_x ? 0 : -size + 1;
    const int y_offset = flip_y ? 0 : -size + 1;
    Mw2erSprite sprite = make_sprite(size, size, x_offset, y_offset);
    constexpr int samples = 8;
    const double scale = size / 9.0;
    const double target_center = (box_size - 1.0) * 0.5;
    const double hole_radius2 = (box_size * 0.5) * (box_size * 0.5);
    for (int y = 0; y < size; ++y) {
        for (int x = 0; x < size; ++x) {
            const int source_x = flip_x ? size - 1 - x : x;
            const int source_y = flip_y ? size - 1 - y : y;
            int covered = 0;
            for (int sy = 0; sy < samples; ++sy) {
                for (int sx = 0; sx < samples; ++sx) {
                    const double physical_x =
                        source_x + (sx + 0.5) / samples - 0.5;
                    const double physical_y =
                        source_y + (sy + 0.5) / samples - 0.5;
                    const double logical_x = physical_x / scale;
                    const double logical_y = physical_y / scale;
                    const double dx = target_center - physical_x;
                    const double dy = target_center - physical_y;
                    const bool inside_tip = logical_x < 8.0 && logical_y < 8.0;
                    const bool inside =
                        (logical_x < 0.5 && logical_y < 8.0) ||
                        (logical_y < 0.5 && logical_x < 8.0) ||
                        ((dx * dx + dy * dy) >= hole_radius2 && inside_tip);
                    covered += inside ? 1 : 0;
                }
            }
            sprite.pixels[((size_t)y * size + x) * 2u + 1] =
                alpha_byte(covered / 64.0);
        }
    }
    return sprite;
}

static Mw2erSprite nav_sprite(int diameter, double scale)
{
    Mw2erSprite sprite = make_sprite(
        diameter, diameter, -diameter / 2, (int)std::nearbyint(scale));
    const double center = (diameter - 1) * 0.5;
    const double outer_radius = std::max(1.0, diameter * 0.5 - 0.25);
    const double inner_radius = std::max(0.0, outer_radius - 3.0 * scale);
    for (int y = 0; y < diameter; ++y) {
        for (int x = 0; x < diameter; ++x) {
            const double distance = std::hypot(x - center, y - center);
            const double outer = std::clamp(
                outer_radius + 0.5 - distance, 0.0, 1.0);
            const double inner = std::clamp(
                distance - inner_radius + 0.5, 0.0, 1.0);
            sprite.pixels[((size_t)y * diameter + x) * 2u + 1] =
                alpha_byte(std::min(outer, inner));
        }
    }
    return sprite;
}

static void delete_marker_textures()
{
    for (GLuint &texture : g_markers.textures) {
        if (texture) glDeleteTextures(1, &texture);
        texture = 0;
    }
}

static void delete_retired_textures()
{
    if (g_retired_count == 0) return;
    glDeleteTextures((GLsizei)g_retired_count,
                     g_retired_textures);
    g_retired_count = 0;
}

static int textures_ready(std::initializer_list<int> indices)
{
    for (int index : indices)
        if (!g_markers.textures[index]) return 0;
    return 1;
}

static int ensure_carets(double marker_scale)
{
    const int long_size = std::max(1, (int)std::ceil(25.0 * marker_scale));
    const int short_size = std::max(1, (int)std::ceil(15.0 * marker_scale));
    const int circle_size = std::max(1, (int)std::ceil(13.0 * marker_scale));
    if (g_markers.long_size == long_size &&
        g_markers.short_size == short_size &&
        g_markers.circle_size == circle_size &&
        textures_ready({0, 1, 2, 3, 8})) return 1;
    for (int i : {0, 1, 2, 3, 8}) {
        if (g_markers.textures[i])
            glDeleteTextures(1, &g_markers.textures[i]);
        g_markers.textures[i] = 0;
    }
    g_markers.long_size = long_size;
    g_markers.short_size = short_size;
    g_markers.circle_size = circle_size;
    for (int i = 0; i < 4; ++i) {
        const int w = (i == 0 || i == 3) ? long_size : short_size;
        const int h = (i == 0 || i == 3) ? short_size : long_size;
        g_markers.sprites[i] = target_caret_sprite(i, w, h);
    }
    g_markers.sprites[8] = nav_sprite(circle_size, circle_size / 13.0);
    for (int i : {0, 1, 2, 3, 8}) {
        g_markers.textures[i] =
            mw2er_gl_upload_indexed_sprite(g_markers.sprites[i]);
        if (!g_markers.textures[i]) {
            g_markers.long_size = 0;
            return 0;
        }
    }
    return 1;
}

static int ensure_compass_carets(double panel_scale)
{
    static const double points[4][3][2] = {
        {{0.75,0.75},{5.0,5.25},{9.25,0.75}},
        {{5.25,0.75},{0.75,5.0},{5.25,9.25}},
        {{0.75,0.75},{5.25,5.0},{0.75,9.25}},
        {{0.75,5.25},{5.0,0.75},{9.25,5.25}},
    };
    static const double logical_width[4] = {10.0,6.0,6.0,10.0};
    static const double logical_height[4] = {6.0,10.0,10.0,6.0};
    const int long_size = std::max(1, (int)std::ceil(10.0 * panel_scale));
    const int short_size = std::max(1, (int)std::ceil(6.0 * panel_scale));
    if (g_markers.compass_long_size == long_size &&
        g_markers.compass_short_size == short_size &&
        textures_ready({9, 10, 11, 12})) return 1;
    for (int i = 0; i < 4; ++i) {
        const int index = 9 + i;
        if (g_markers.textures[index])
            glDeleteTextures(1, &g_markers.textures[index]);
        g_markers.textures[index] = 0;
        const int w = (i == 0 || i == 3) ? long_size : short_size;
        const int h = (i == 0 || i == 3) ? short_size : long_size;
        const int x_offset = -(int)std::nearbyint(
            points[i][1][0] * w / logical_width[i]);
        const int y_offset = -(int)std::nearbyint(
            points[i][1][1] * h / logical_height[i]);
        g_markers.sprites[index] = caret_sprite(
            points[i], logical_width[i], logical_height[i], 1.75,
            w, h, x_offset, y_offset);
        g_markers.textures[index] =
            mw2er_gl_upload_indexed_sprite(g_markers.sprites[index]);
        if (!g_markers.textures[index]) {
            g_markers.compass_long_size = 0;
            return 0;
        }
    }
    g_markers.compass_long_size = long_size;
    g_markers.compass_short_size = short_size;
    return 1;
}

static int ensure_brackets(double panel_scale)
{
    const int size = std::max(1, (int)std::ceil(9.0 * panel_scale));
    const int box = std::max(1,
        (int)std::floor(21.0 * panel_scale + 0.5));
    if (g_markers.bracket_size == size &&
        g_markers.bracket_box == box &&
        textures_ready({4, 5, 6, 7})) return 1;
    for (int i = 4; i < 8; ++i) {
        if (g_markers.textures[i])
            glDeleteTextures(1, &g_markers.textures[i]);
        g_markers.textures[i] = 0;
        g_markers.sprites[i] = bracket_sprite(size, box, i & 1, i >= 6);
        g_markers.textures[i] =
            mw2er_gl_upload_indexed_sprite(g_markers.sprites[i]);
        if (!g_markers.textures[i]) {
            g_markers.bracket_size = 0;
            return 0;
        }
    }
    g_markers.bracket_size = size;
    g_markers.bracket_box = box;
    return 1;
}

static int draw(const Mw2erSprite &sprite, GLuint texture,
                double anchor_x, double anchor_y, double scale, int color,
                const Mw2erTargetClip &clip, const uint8_t *palette,
                int width, int height, int snap)
{
    Mw2erIndexedSpriteDraw command = {};
    double x = anchor_x + sprite.x_offset * scale;
    double y = anchor_y + sprite.y_offset * scale;
    if (snap) { x = std::floor(x + 0.5); y = std::floor(y + 0.5); }
    command.x = (float)(x - sprite.x_offset * scale);
    command.y = (float)(y - sprite.y_offset * scale);
    command.scale_x = (float)scale;
    command.scale_y = (float)scale;
    command.clip_left = clip.left;
    command.clip_top = clip.top;
    command.clip_right = clip.right;
    command.clip_bottom = clip.bottom;
    command.color_override = color;
    return mw2er_gl_draw_indexed_sprites(
        sprite, texture, &command, 1, palette, width, height) == MW2ER_OK;
}

} // namespace

void mw2er_targeting_mission_reset(void)
{
    for (int i = 0; i < g_resource_count; ++i) {
        if (g_resources[i].texture)
            g_retired_textures[g_retired_count++] = g_resources[i].texture;
        g_resources[i] = {};
    }
    g_resource_count = 0;
}

void mw2er_targeting_gl_reset(void)
{
    for (int i = 0; i < g_resource_count; ++i) {
        if (g_resources[i].texture)
            glDeleteTextures(1, &g_resources[i].texture);
        g_resources[i].texture = 0;
    }
    delete_retired_textures();
    delete_marker_textures();
    g_markers.long_size = 0;
    g_markers.short_size = 0;
    g_markers.circle_size = 0;
    g_markers.bracket_size = 0;
    g_markers.bracket_box = 0;
    g_markers.compass_long_size = 0;
    g_markers.compass_short_size = 0;
}

int mw2er_targeting_capture_sprite(const Mem &mem, int resource)
{
    for (int i = 0; i < g_resource_count; ++i)
        if (g_resources[i].resource == resource) return i;
    if (resource < 0 || g_resource_count >= MAX_RESOURCE_SPRITES) return -1;
    const uint32_t shape = mw2er_resolve_cached_shape(mem, resource);
    Mw2erSprite sprite;
    if (!shape || !mw2er_decode_runtime_sprite(mem, shape, 0, &sprite)) return -1;
    ResourceSprite &entry = g_resources[g_resource_count];
    entry.resource = resource;
    entry.sprite = std::move(sprite);
    entry.texture = 0;
    return g_resource_count++;
}

int mw2er_targeting_draw_sprite(int reference, double x, double y,
                                double scale, const Mw2erTargetClip &clip,
                                const uint8_t *palette, int width, int height,
                                int snap_to_pixels)
{
    delete_retired_textures();
    if (reference < 0) return 1;
    if (reference >= g_resource_count) return 0;
    ResourceSprite &entry = g_resources[reference];
    if (!entry.texture)
        entry.texture = mw2er_gl_upload_indexed_sprite(entry.sprite);
    return entry.texture && draw(entry.sprite, entry.texture, x, y, scale, -1,
                                 clip, palette, width, height, snap_to_pixels);
}

int mw2er_targeting_draw_caret(int direction, double x, double y, int color,
                               double marker_scale,
                               const Mw2erTargetClip &clip,
                               const uint8_t *palette, int width, int height)
{
    delete_retired_textures();
    if (direction < 0 || direction > 3 ||
        !ensure_carets(marker_scale)) return 0;
    return draw(g_markers.sprites[direction], g_markers.textures[direction],
                std::floor(x + 0.5), std::floor(y + 0.5), 1.0, color,
                clip, palette, width, height, 0);
}

int mw2er_targeting_draw_nav(double x, double y, int color,
                             double marker_scale,
                             const Mw2erTargetClip &clip,
                             const uint8_t *palette, int width, int height)
{
    delete_retired_textures();
    if (!ensure_carets(marker_scale)) return 0;
    return draw(g_markers.sprites[8], g_markers.textures[8],
                std::floor(x + 0.5), std::floor(y + 0.5), 1.0, color,
                clip, palette, width, height, 0);
}

int mw2er_targeting_draw_bracket(double x, double y, double radius, int color,
                                 double panel_scale,
                                 const Mw2erTargetClip &clip,
                                 const uint8_t *palette, int width, int height)
{
    delete_retired_textures();
    if (!ensure_brackets(panel_scale)) return 0;
    const int cx = (int)std::floor(x + 0.5);
    const int cy = (int)std::floor(y + 0.5);
    const int r = std::max(0, (int)std::floor(radius));
    for (int i = 0; i < 4; ++i) {
        const int ax = cx + ((i & 1) ? r : -r);
        const int ay = cy + ((i >= 2) ? r : -r);
        if (!draw(g_markers.sprites[4 + i], g_markers.textures[4 + i],
                  ax, ay, 1.0, color, clip, palette, width, height, 0)) return 0;
    }
    return 1;
}

int mw2er_targeting_draw_acquisition(
    double x, double y, double radius, int color, double panel_scale,
    double progress, double turns, float stroke,
    const uint8_t *palette, int width, int height)
{
    const double eased = progress * progress * (3.0 - 2.0 * progress);
    const double cx = (1.0 - eased) * width * 0.5 + eased * x;
    const double cy = (1.0 - eased) * height * 0.5 + eased * y;
    const double start = 0.65 * height / std::sqrt(2.0);
    const double end = radius + std::max(1.0, std::ceil(9.0 * panel_scale)) + 2.0;
    const double extent = (1.0 - eased) * start + eased * end;
    // Quarter-normalized turns start on a diagonal and end exactly upright.
    const double angle = -(2.0 * 3.14159265358979323846 * turns +
                           3.14159265358979323846 * 0.25) * (1.0 - eased);
    const double cosine = std::cos(angle), sine = std::sin(angle);
    double corners[4][2];
    const int signs[4][2] = {{-1,-1}, {1,-1}, {1,1}, {-1,1}};
    for (int i = 0; i < 4; ++i) {
        const double lx = signs[i][0] * extent, ly = signs[i][1] * extent;
        corners[i][0] = cx + lx * cosine - ly * sine;
        corners[i][1] = cy + lx * sine + ly * cosine;
    }
    Mw2erHudLine lines[4];
    for (int i = 0; i < 4; ++i) {
        const int next = (i + 1) % 4;
        lines[i] = {corners[i][0], corners[i][1],
                    corners[next][0], corners[next][1], color};
    }
    glDisable(GL_SCISSOR_TEST);
    return mw2er_hud_draw_lines(lines, 4, stroke, width, height, palette);
}

int mw2er_targeting_draw_compass_caret(
    int direction, double x, double y, int edge_attachment,
    double panel_scale, const Mw2erTargetClip &clip,
    const uint8_t *palette, int width, int height)
{
    delete_retired_textures();
    if (direction < 0 || direction > 3 ||
        !ensure_compass_carets(panel_scale)) return 0;
    const int index = 9 + direction;
    const Mw2erSprite &sprite = g_markers.sprites[index];
    const double anchor_x = std::floor(x + 0.5);
    const double anchor_y = std::floor(y + 0.5);
    double draw_x = anchor_x;
    if (edge_attachment) {
        const int gap = std::max(1,
            (int)std::floor(2.0 * panel_scale + 0.5));
        const double left = edge_attachment < 0
            ? anchor_x - gap - sprite.width : anchor_x + gap;
        draw_x = left - sprite.x_offset;
    }
    return draw(sprite, g_markers.textures[index], draw_x, anchor_y,
                1.0, 0x0A, clip, palette, width, height, 0);
}
