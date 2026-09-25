#include "hud.h"

#include "compass_altimeter.h"
#include "config.h"
#include "font.h"
#include "gl_program.h"
#include "mem.h"
#include "menu.h"
#include "mw2er_internal.h"
#include "presentation.h"
#include "radar.h"
#include "scene_draw.h"
#include "targeting.h"
#include "text_util.h"

#include "gl_api.h"

#include <algorithm>
#include <climits>
#include <cmath>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

namespace {

enum {
    ADDR_PLAYER_SLOT = 0x000A5918,
    ADDR_ENTITY_TABLE = 0x00108B00,
    ADDR_MASTER_HUD = 0x000A6314,
    ADDR_PANEL_TABLE = 0x0010E23C,
    ADDR_METER_STATE = 0x000A6900,
    ADDR_TARGET_DISPLAY = 0x000A68E8,
    ADDR_MFD_MODE = 0x000A839C,
    ADDR_ACTIVE_CAMERA = 0x000A70E8,
    ADDR_MFD_REAR_SPECIAL = 0x0010E1D0,
    ADDR_MASC_ACTIVE = 0x000A62A0,
    ADDR_GAME_TICK = 0x000A58C4,
    ADDR_CURRENT_HUD_TEXT_COLOR = 0x000B4E92,
    ADDR_MFD_HTAL_LABELS = 0x000A0B20,
    ADDR_MFD_HTAL_POSITIONS = 0x000A66EC,
    ADDR_MFD_STATIC_CONFIG = 0x0010E020,
    ADDR_PRIMARY_CLASSIFICATION = 0x0010B631,
    ADDR_SECONDARY_CLASSIFICATION = 0x0010C6C4,
    ADDR_DIRECT_TARGETS = 0x00108BF0,
    ADDR_MFD_DAMAGE_SCALE = 0x000A6280,
    ADDR_MFD_DAMAGE_UI = 0x000A66AC,
    ADDR_MFD_DAMAGE_SHAPE_BASE = 0x0010E1A8,
    ADDR_HUD_STYLE_OFFSET = 0x000B4E80,
    ADDR_FAR_DEPTH = 0x0015FF80,
    ADDR_SECONDARY_COUNT = 0x000A6D50,
    ADDR_SECONDARY_TABLE = 0x00104B80,
    ADDR_SECONDARY_POSITIONS = 0x00111FA0,
    ADDR_WEAPON_SLOT = 0x000AEF08,
    ADDR_WEAPON_DEFINITIONS = 0x000AEF1C,
    ADDR_WEAPON_STATE = 0x00163A30,
    ADDR_RETICLE_ENABLE = 0x000A6318,
    ADDR_CAMERA_RETICLE_GATE = 0x000A6FBC,
    ADDR_RADAR_MODE = 0x000B4D08,
    ADDR_TARGET_GLITCH_LATCH = 0x000A68F8,
    ADDR_MFD_GLITCH_LATCH = 0x000A68FC,
    ADDR_HUD_ANIMATION_TABLE = 0x000B4AD0,
    ADDR_SATELLITE_DAMAGE_STATE = 0x000A5648,
    ADDR_SATELLITE_DAMAGE_WINDOW = 0x000B4768,
    ADDR_SATELLITE_DAMAGE_WINDOW_VALID = 0x000B4790,
    ADDR_RETICLE_PANE = 0x000B4768,
    ADDR_AIM_Y_OFFSET = 0x000A6FD0,
    PANEL_COUNT = 25,
    CALLBACK_MFD = 0x00047AD0,
    CALLBACK_MFD_STARTUP = 0x00048080,
    CALLBACK_MFD_SHUTDOWN = 0x00048150,
    CALLBACK_TARGET = 0x00047490,
    CALLBACK_TARGET_STARTUP = 0x00047900,
    CALLBACK_TARGET_SHUTDOWN = 0x000479C0,
    CALLBACK_TARGET_TEXT = 0x00046F00,
    CALLBACK_WEAPON_STEADY = 0x00046D00,
    CALLBACK_WEAPON_STARTUP = 0x00046E50,
    CALLBACK_THROTTLE = 0x00049560,
    CALLBACK_MASC = 0x00049690,
    CALLBACK_HEAT = 0x00049710,
    CALLBACK_HEAT_RATE = 0x00049790,
    CALLBACK_JUMP_JETS = 0x00049810,
    CALLBACK_OBJECTIVES_STATUS = 0x00048FD0,
    MAX_POWER_METERS = 4,
    MAX_HUD_TEXTS = 5,
    MAX_HTAL_METERS = 11,
    MAX_DAMAGE_DRAWS = 17,
    MAX_VIDEO_NOISE_DRAWS = 2,
    METER_STATE_SIZE = 0x80,
    WEAPON_SIZE = 0x68,
    TARGET_NAV_TEXT_SLOT = 1397,
};

struct Rect { int left, top, right, bottom; }; /* exclusive right/bottom */
struct MeterRect { double left, top, right, bottom; };
struct Transform { double ox, oy, sx, sy; };
struct AuxTarget { GLuint fbo, color, depth; int w, h; };

enum MeterKind { METER_BAR, METER_THROTTLE };
enum MeterGrow { METER_LEFT_TO_RIGHT, METER_TOP_TO_BOTTOM, METER_SYMMETRIC };
enum MeterGroup { METER_GROUP_HEAT_JUMP, METER_GROUP_THROTTLE };

struct PowerMeter {
    MeterKind kind;
    MeterGrow grow;
    MeterGroup group;
    MeterRect rect;
    double rest_y;
    double amount;
    int fill_color;
    int empty_color;
    int edge_color;
    int reverse;
};

enum TextAlignment { TEXT_LEFT, TEXT_CENTER, TEXT_RIGHT };

struct HudText {
    int slot;
    MeterGroup group;
    Rect panel_bounds;
    int own_bounds;
    int clip;
    int color;
    float x;
    float y;
    TextAlignment horizontal;
    int vertical_center;
    char text[256];
};

struct PanelText {
    int slot;
    int color;
    float x;
    float y;
    TextAlignment horizontal;
    char text[256];
};

struct WeaponRow {
    int slot;
    int color;
    int active;
    Rect pane;
    float x, y;
    char text[256];
};

struct DamageDraw {
    int x, y;
    Rect clip;
    int color_override;
};

struct DamagePartLayout {
    int map_index;
    int x, y;
    Rect clip;
};

struct DamageLayout {
    int valid;
    int base_x, base_y;
    DamagePartLayout parts[16];
    int part_count;
};

enum class AcquisitionPhase { Pending, Running, Finishing, Complete };
struct TargetAcquisition {
    uint32_t handle;
    AcquisitionPhase phase;
    double started_at;
};

struct TargetingState {
    uint32_t marker_handle;
    int reticle_reference;
    int reticle_visible;
    int32_t reticle_world[3];
    int marker_visible;
    int marker_kind;
    int marker_sub_index;
    int32_t marker_world[3];
    int32_t marker_extent;
    int marker_clamp;
    int32_t marker_maximum;
    int32_t marker_projection_scale;
    Rect pane;
};

struct View {
    int visible;
    int image;
    int mirror;
    Rect pane;
    Mw2erCamera camera;
};

struct VideoNoiseDraw {
    int panel_id;
    Rect pane;
};

struct VideoNoiseState {
    uint32_t record;
    int resource;
    int state;
    int ticks_per_frame;
    int start_tick;
    int flags;
    int tick;
    int native_state;
    VideoNoiseDraw draws[MAX_VIDEO_NOISE_DRAWS];
    int draw_count;
    int ready;
    uint32_t cpu_shape;
    int cpu_frame;
    Mw2erSprite sprite;
    GLuint texture;
    uint32_t gpu_shape;
    int gpu_frame;
    int gpu_width;
    int gpu_height;
};

struct SatelliteDamageState {
    int active;
    int32_t viewport[4]; /* inclusive native bounds */
};

struct HudState {
    int visible;
    SatelliteDamageState satellite_damage;
    int objectives_panel;
    int armed;
    int previous_mode;
    int phase; /* 0 hidden, 1 startup, 2 steady, 3 shutdown */
    double transition_start;
    double extent_x;
    double extent_y;
    int target_display;
    uint32_t target_root;
    uint32_t target_entity;
    int target_nav_visible;
    int target_nav_reference;
    int target_nav_x, target_nav_y;
    View target;
    View mfd;
    PowerMeter meters[MAX_POWER_METERS];
    int meter_count;
    Rect heat_jump_bounds;
    int heat_jump_bounds_valid;
    Rect throttle_bounds;
    int throttle_bounds_valid;
    HudText texts[MAX_HUD_TEXTS];
    int text_count;
    WeaponRow weapons[PANEL_COUNT];
    int weapon_count;
    Rect weapon_bounds;
    int weapon_bounds_valid;
    int mfd_mode;
    PanelText target_texts[2];
    int target_text_count;
    PanelText htal_texts[4];
    int htal_text_count;
    PowerMeter htal_meters[MAX_HTAL_METERS];
    int htal_meter_count;
    Rect htal_clip;
    int htal_visible;
    Mw2erSprite damage_sprite;
    int damage_resource;
    uint32_t damage_cpu_key;
    GLuint damage_texture;
    uint32_t damage_gpu_key;
    DamageLayout damage_layout;
    DamageDraw damage_draws[MAX_DAMAGE_DRAWS];
    int damage_draw_count;
    int damage_aligned;
    double damage_center_x;
    double damage_bottom_y;
    VideoNoiseState video_noise;
    TargetingState targeting;
    TargetAcquisition acquisition;
    double targeting_now;
    AuxTarget target_gpu;
    AuxTarget mfd_gpu;
};

static HudState g_hud;
static SatelliteDamageState g_satellite_damage_latch;
static GlProgram g_meter_program;
static GLuint g_meter_vao;
static GLuint g_meter_vbo;
static constexpr double k_fixed = 65536.0;
static constexpr double k_turn = 0x01680000;
static constexpr double k_fp29 = 536870912.0;
static constexpr int k_alt_throttle_left = 980;
static constexpr int k_alt_throttle_top = 285;
static constexpr int k_alt_throttle_bottom = 429;
static constexpr int k_alt_throttle_neutral_y = 380;
static constexpr int k_alt_throttle_text_right = 972;
static constexpr int k_alt_throttle_masc_y = 404;

static double resolved_scale(int height, float control)
{
    const double vertical = std::max(1, height) / 768.0;
    return vertical < 1.0 ? vertical
        : 1.0 + std::clamp((double)control, 0.0, 1.0) * (vertical - 1.0);
}

static void delete_target(AuxTarget &t)
{
    if (t.fbo) glDeleteFramebuffers(1, &t.fbo);
    if (t.color) glDeleteTextures(1, &t.color);
    if (t.depth) glDeleteRenderbuffers(1, &t.depth);
    t = {};
}

static int ensure_target(AuxTarget &t, int w, int h)
{
    if (t.fbo && t.w == w && t.h == h) return 1;
    AuxTarget candidate = {};
    glGenTextures(1, &candidate.color);
    glBindTexture(GL_TEXTURE_2D, candidate.color);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA,
                 GL_UNSIGNED_BYTE, nullptr);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glGenRenderbuffers(1, &candidate.depth);
    glBindRenderbuffer(GL_RENDERBUFFER, candidate.depth);
    glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24, w, h);
    glGenFramebuffers(1, &candidate.fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, candidate.fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, candidate.color, 0);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
                              GL_RENDERBUFFER, candidate.depth);
    candidate.w = w;
    candidate.h = h;
    if (!candidate.fbo || !candidate.color || !candidate.depth ||
        glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        delete_target(candidate);
        return 0;
    }
    delete_target(t);
    t = candidate;
    return 1;
}

static void camera_from_pose(Mw2erCamera &c, int32_t x, int32_t y, int32_t z,
                             int32_t yaw, int32_t pitch, int32_t roll)
{
    std::memset(&c, 0, sizeof(c));
    const double yr = yaw * (6.283185307179586 / k_turn);
    const double pr = pitch * (6.283185307179586 / k_turn);
    const double rr = roll * (6.283185307179586 / k_turn);
    const double sy = std::sin(yr), cy = std::cos(yr);
    const double sp = std::sin(pr), cp = std::cos(pr);
    const double sr = std::sin(rr), cr = std::cos(rr);
    const double level_right[3] = {cy, 0.0, -sy};
    const double level_up[3] = {sy * sp, cp, cy * sp};
    c.position_fixed[0] = x; c.position_fixed[1] = y; c.position_fixed[2] = z;
    c.position[0] = (float)(x / k_fixed);
    c.position[1] = (float)(y / k_fixed);
    c.position[2] = (float)(z / k_fixed);
    c.forward[0] = (float)(sy * cp);
    c.forward[1] = (float)-sp;
    c.forward[2] = (float)(cy * cp);
    for (int i = 0; i < 3; ++i) {
        c.right[i] = (float)(level_right[i] * cr + level_up[i] * sr);
        c.up[i] = (float)(level_up[i] * cr - level_right[i] * sr);
        c.forward_fixed[i] = (int32_t)std::llround(c.forward[i] * k_fp29);
    }
    c.focal_length_pixels = 1024.0f;
    c.near_plane = 0.001f;
    c.clip_near_plane = c.near_plane;
    c.far_plane = 1000.0f;
}

static void pane_camera(Mw2erCamera &c, const Mem &mem, const Rect &r)
{
    const int w = std::max(1, r.right - r.left);
    const int h = std::max(1, r.bottom - r.top);
    c.pane_projection = 1;
    c.focal_length_pixels = (float)((w >> 1) * 2);
    c.projection_aspect_scale = 65535.0f / 65536.0f;
    c.projection_center_x = (float)(w >> 1);
    c.projection_center_y = (float)(h >> 1);
    const int32_t near_source = ((0x20000 * (w >> 1)) >> 17) + 1;
    c.near_plane = (float)(near_source * 4) / (65536.0f * 4.0f);
    c.clip_near_plane = c.near_plane;
    c.far_depth_fixed = mem.i32_rel(ADDR_FAR_DEPTH);
}

static int valid_rect(const Rect &r)
{
    return r.right > r.left && r.bottom > r.top &&
           r.right - r.left <= 1024 && r.bottom - r.top <= 768;
}

static void include_rect(Rect &bounds, int &valid, const Rect &r)
{
    if (!valid_rect(r)) return;
    if (!valid) {
        bounds = r;
        valid = 1;
        return;
    }
    bounds.left = std::min(bounds.left, r.left);
    bounds.top = std::min(bounds.top, r.top);
    bounds.right = std::max(bounds.right, r.right);
    bounds.bottom = std::max(bounds.bottom, r.bottom);
}

static int panel_rect(const Mem &mem, uint32_t panel, Rect &r, int destination)
{
    uint32_t pane = mem.u32(panel + 0x30);
    if (destination) {
        uint32_t controller = mem.u32(panel + 0x38);
        uint32_t descriptor = controller ? mem.u32(controller + 4) : 0;
        uint32_t dest = descriptor ? mem.u32(descriptor + 8) : 0;
        if (dest) pane = dest;
    }
    if (!pane) return 0;
    r.left = mem.i32(pane + 4);
    r.top = mem.i32(pane + 8);
    r.right = mem.i32(pane + 12) + 1;
    r.bottom = mem.i32(pane + 16) + 1;
    return valid_rect(r);
}

static int substitute_damaged_video(const Mem &mem, uint32_t panel,
                                    const Rect &pane, uint32_t latch,
                                    int panel_id)
{
    const int state = mem.i16(panel + 6);
    if (!((state == 1 && mem.i32_rel(latch) != 0) || state > 2))
        return 0;
    VideoNoiseState &noise = g_hud.video_noise;
    const uint32_t record = mem.u32_rel(ADDR_HUD_ANIMATION_TABLE);
    if (!record || noise.draw_count >= MAX_VIDEO_NOISE_DRAWS)
        return 1;
    if (!noise.draw_count) {
        noise.record = record;
        noise.native_state = mem.i32(record);
        noise.state = noise.native_state ? noise.native_state : 2;
        noise.ticks_per_frame = mem.i32(record + 8);
        noise.start_tick = mem.i32(record + 0x0C);
        noise.flags = mem.i32(record + 0x10);
        noise.resource = mem.i32(record + 0x14) +
            mem.u8_rel(ADDR_HUD_STYLE_OFFSET);
        noise.tick = mem.i32_rel(ADDR_GAME_TICK);
    }
    noise.draws[noise.draw_count++] = {panel_id, pane};
    return 1;
}

static void resolve_video_noise(const Mem &mem)
{
    VideoNoiseState &noise = g_hud.video_noise;
    noise.ready = 0;
    if (!noise.draw_count || noise.native_state == 1) return;
    const uint32_t loaded_shape = mem.u32(noise.record + 0x18);
    const uint32_t shape = loaded_shape ? loaded_shape :
        mw2er_resolve_cached_shape(mem, noise.resource);
    const uint32_t frame_count = mw2er_runtime_sprite_frame_count(mem, shape);
    if (!shape || !frame_count) return;
    uint32_t frame;
    if (noise.state == 3) {
        frame = frame_count - 1;
    } else {
        const int64_t start = noise.start_tick ? noise.start_tick : noise.tick;
        const int64_t elapsed = std::max<int64_t>(0, (int64_t)noise.tick - start);
        const uint32_t elapsed_frame = noise.ticks_per_frame > 0
            ? (uint32_t)(elapsed / noise.ticks_per_frame) : 0;
        if (noise.flags & 1) {
            if (elapsed_frame >= frame_count) {
                if (!(noise.flags & 2)) return;
                frame = frame_count - 1;
            } else {
                frame = elapsed_frame;
            }
        } else {
            frame = elapsed_frame % frame_count;
        }
    }
    if (noise.cpu_shape != shape || noise.cpu_frame != (int)frame) {
        noise.cpu_shape = 0;
        noise.cpu_frame = -1;
        if (!mw2er_decode_runtime_sprite(mem, shape, frame, &noise.sprite))
            return;
        noise.cpu_shape = shape;
        noise.cpu_frame = (int)frame;
    }
    noise.ready = 1;
}

static Transform base_transform(const Rect &r, int panel_id, int w, int h)
{
    const Mw2erRendererConfig &cfg = mw2er_config();
    const double ps = resolved_scale(h, cfg.hud_position_scaling);
    const double scale = resolved_scale(h, cfg.hud_viewport_scaling);
    const double canvas_x = ((double)w - 1024.0 * ps) * 0.5;
    const double canvas_y = ((double)h - 768.0 * ps) * 0.5;
    const double widescreen = std::max(0.0, canvas_x) *
                              cfg.hud_bottom_widescreen_position;
    const double px = panel_id == 0 ? r.left : r.right;
    const double py = r.bottom;
    const double tx = canvas_x + px * ps + (panel_id == 0 ? -widescreen : widescreen);
    const double ty = canvas_y + py * ps;
    return {tx - px * scale, ty - py * scale, scale, scale};
}

static Transform meter_transform(const Rect &r, MeterGroup group, int w, int h)
{
    const Mw2erRendererConfig &cfg = mw2er_config();
    const double position_scale = resolved_scale(h, cfg.hud_position_scaling);
    const double panel_scale = resolved_scale(h, cfg.hud_panel_scaling);
    const double canvas_x = ((double)w - 1024.0 * position_scale) * 0.5;
    const double canvas_y = ((double)h - 768.0 * position_scale) * 0.5;
    double pivot_x;
    double pivot_y;
    double target_x;
    double target_y;
    double widescreen;
    if (group == METER_GROUP_HEAT_JUMP) {
        pivot_x = (r.left + r.right) * 0.5;
        pivot_y = r.bottom;
        target_x = canvas_x + 512.0 * position_scale;
        target_y = canvas_y + pivot_y * position_scale;
        widescreen = 0.0;
    } else if (cfg.hud_alt_throttle_indicator_position) {
        pivot_x = r.right;
        pivot_y = (r.top + r.bottom) * 0.5;
        target_x = canvas_x + pivot_x * position_scale;
        target_y = canvas_y + 768.0 * cfg.hud_middle_panel_vertical_position *
            position_scale;
        widescreen = std::max(0.0, canvas_x) *
            cfg.hud_middle_widescreen_position;
    } else {
        pivot_x = r.right;
        pivot_y = r.bottom;
        target_x = canvas_x + pivot_x * position_scale;
        target_y = canvas_y + pivot_y * position_scale;
        widescreen = std::max(0.0, canvas_x) *
            cfg.hud_bottom_widescreen_position;
    }
    target_x += widescreen;
    return {target_x - pivot_x * panel_scale,
            target_y - pivot_y * panel_scale,
            panel_scale, panel_scale};
}

static Transform weapon_transform(const Rect &r, int w, int h)
{
    const Mw2erRendererConfig &cfg = mw2er_config();
    const double position_scale = resolved_scale(h, cfg.hud_position_scaling);
    const double panel_scale = resolved_scale(h, cfg.hud_panel_scaling);
    const double canvas_x = ((double)w - 1024.0 * position_scale) * 0.5;
    const double canvas_y = ((double)h - 768.0 * position_scale) * 0.5;
    const double target_x = canvas_x + r.right * position_scale +
        std::max(0.0, canvas_x) * cfg.hud_top_widescreen_position;
    const double target_y = canvas_y + r.top * position_scale;
    return {target_x - r.right * panel_scale,
            target_y - r.top * panel_scale,
            panel_scale, panel_scale};
}

static int round_output_pixel(double value)
{
    return (int)std::floor(value + 0.5);
}

static Rect snap_inclusive(const Rect &r, const Transform &t)
{
    return {
        round_output_pixel(t.ox + r.left * t.sx),
        round_output_pixel(t.oy + r.top * t.sy),
        round_output_pixel(t.ox + r.right * t.sx) - 1,
        round_output_pixel(t.oy + r.bottom * t.sy) - 1,
    };
}

static Rect snap_inclusive(const MeterRect &r, const Transform &t)
{
    return {
        round_output_pixel(t.ox + r.left * t.sx),
        round_output_pixel(t.oy + r.top * t.sy),
        round_output_pixel(t.ox + r.right * t.sx) - 1,
        round_output_pixel(t.oy + r.bottom * t.sy) - 1,
    };
}

static Transform animated(const Rect &r, Transform t)
{
    const double ex = std::clamp(g_hud.extent_x, 0.0, 1.0);
    const double ey = std::clamp(g_hud.extent_y, 0.0, 1.0);
    if (ex == 1.0 && ey == 1.0) return t;
    const double span_x = std::max(1, r.right - r.left);
    const double span_y = std::max(1, r.bottom - r.top);
    const double pivot_x = (r.left + r.right - 1) / 2;
    const double pivot_y = (r.top + r.bottom - 1) / 2;
    const double reveal_x = (1.0 + (span_x - 1.0) * ex) / span_x;
    const double reveal_y = (1.0 + (span_y - 1.0) * ey) / span_y;
    const double left = pivot_x + (r.left - pivot_x) * ex;
    const double top = pivot_y + (r.top - pivot_y) * ey;
    t.ox += (left - r.left * reveal_x) * t.sx;
    t.oy += (top - r.top * reveal_y) * t.sy;
    t.sx *= reveal_x;
    t.sy *= reveal_y;
    return t;
}

static Rect pixel_rect(const Rect &r, const Transform &t)
{
    Rect out;
    out.left = (int)std::floor(t.ox + r.left * t.sx);
    out.top = (int)std::floor(t.oy + r.top * t.sy);
    out.right = std::max(out.left + 1, (int)std::ceil(t.ox + r.right * t.sx));
    out.bottom = std::max(out.top + 1, (int)std::ceil(t.oy + r.bottom * t.sy));
    return out;
}

static void set_transition(int mode, double now)
{
    if (g_hud.armed) {
        if (mode != 1) { g_hud.phase = 0; return; }
        g_hud.armed = 0;
        g_hud.transition_start = now;
    }
    if (mode == 1) {
        if (g_hud.previous_mode != 1) g_hud.transition_start = now;
        g_hud.phase = 1;
    } else if (mode == 0 || mode == 3) {
        if (g_hud.previous_mode != 0 && g_hud.previous_mode != 3)
            g_hud.transition_start = now;
        g_hud.phase = 3;
    } else {
        g_hud.phase = 2;
    }
    const double p = std::clamp(now - g_hud.transition_start, 0.0, 1.0);
    if (g_hud.phase == 1) {
        g_hud.extent_x = std::min(1.0, p * 2.0);
        g_hud.extent_y = std::max(0.0, p * 2.0 - 1.0);
    } else if (g_hud.phase == 3) {
        g_hud.extent_x = std::max(0.0, 2.0 - p * 2.0);
        g_hud.extent_y = std::max(0.0, 1.0 - p * 2.0);
    } else {
        g_hud.extent_x = g_hud.extent_y = 1.0;
    }
}

static void fill_rect(const Rect &r, int h, const uint8_t *rgb, int index)
{
    if (!valid_rect(r)) return;
    glEnable(GL_SCISSOR_TEST);
    glScissor(r.left, h - r.bottom, r.right - r.left, r.bottom - r.top);
    glClearColor(rgb[index * 3] / 255.0f, rgb[index * 3 + 1] / 255.0f,
                 rgb[index * 3 + 2] / 255.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
}

static void border(const Rect &r, int h, const uint8_t *rgb, int index, int width)
{
    width = std::max(1, width);
    fill_rect({r.left, r.top, r.right, std::min(r.bottom, r.top + width)}, h, rgb, index);
    fill_rect({r.left, std::max(r.top, r.bottom - width), r.right, r.bottom}, h, rgb, index);
    fill_rect({r.left, r.top + width, std::min(r.right, r.left + width), r.bottom - width}, h, rgb, index);
    fill_rect({std::max(r.left, r.right - width), r.top + width, r.right, r.bottom - width}, h, rgb, index);
}

static int ensure_meter_resources()
{
    if (g_meter_program.ok() && g_meter_vao && g_meter_vbo) return 1;
    const std::string dir = mw2er_shader_dir();
    if (!g_meter_program.load((dir + "/overlay_rect.vert").c_str(),
                              (dir + "/overlay_rect.frag").c_str())) return 0;
    const GLint color_location = glGetAttribLocation(g_meter_program.id, "in_color");
    if (color_location < 0) {
        mw2er_set_error("HUD meter shader is missing in_color");
        return 0;
    }
    glGenVertexArrays(1, &g_meter_vao);
    glGenBuffers(1, &g_meter_vbo);
    glBindVertexArray(g_meter_vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_meter_vbo);
    glBufferData(GL_ARRAY_BUFFER, 512 * sizeof(Mw2erHudVertex), nullptr, GL_STREAM_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, sizeof(Mw2erHudVertex), nullptr);
    glEnableVertexAttribArray((GLuint)color_location);
    glVertexAttribPointer((GLuint)color_location, 4, GL_FLOAT, GL_FALSE,
        sizeof(Mw2erHudVertex), (const void *)(2 * sizeof(float)));
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);
    return 1;
}

static void palette_color(const uint8_t *rgb, int index, float out[4])
{
    index = std::clamp(index, 0, 255);
    out[0] = rgb[index * 3] / 255.0f;
    out[1] = rgb[index * 3 + 1] / 255.0f;
    out[2] = rgb[index * 3 + 2] / 255.0f;
    out[3] = 1.0f;
}

static void append_gradient(std::vector<Mw2erHudVertex> &vertices,
                            int left, int top, int right, int bottom,
                            const float tl[4], const float tr[4],
                            const float bl[4], const float br[4])
{
    if (right <= left || bottom <= top) return;
    auto vertex = [&vertices](int x, int y, const float c[4]) {
        vertices.push_back({(float)x, (float)y, c[0], c[1], c[2], c[3]});
    };
    vertex(left, top, tl); vertex(right, top, tr); vertex(left, bottom, bl);
    vertex(left, bottom, bl); vertex(right, top, tr); vertex(right, bottom, br);
}

static void append_solid(std::vector<Mw2erHudVertex> &vertices, const uint8_t *rgb,
                         int left, int top, int right, int bottom, int index)
{
    float color[4];
    palette_color(rgb, index, color);
    append_gradient(vertices, left, top, right, bottom,
                    color, color, color, color);
}

static void append_meter_box(std::vector<Mw2erHudVertex> &vertices,
                             const uint8_t *rgb, const Rect &box,
                             int base_index, int shade_x, int enhanced)
{
    /* box is inclusive; all emitted triangles use half-open edges. */
    const int left = box.left;
    const int top = box.top;
    const int right = box.right + 1;
    const int bottom = box.bottom + 1;
    if (right <= left || bottom <= top) return;
    if (enhanced) {
        const Mw2erRendererConfig &cfg = mw2er_config();
        float edge[4], light[4], peak_color[4];
        palette_color(rgb, base_index + cfg.hud_meter_dark_offset, edge);
        palette_color(rgb, base_index + cfg.hud_meter_light_offset, light);
        palette_color(rgb, base_index + cfg.hud_meter_peak_offset, peak_color);
        if (shade_x) {
            const int peak = round_output_pixel(
                left + (right - left) * cfg.hud_meter_peak_position);
            append_gradient(vertices, left, top, peak, bottom,
                            light, peak_color, light, peak_color);
            append_gradient(vertices, peak, top, right, bottom,
                            peak_color, edge, peak_color, edge);
        } else {
            const int peak = round_output_pixel(
                top + (bottom - top) * cfg.hud_meter_peak_position);
            append_gradient(vertices, left, top, right, peak,
                            light, light, peak_color, peak_color);
            append_gradient(vertices, left, peak, right, bottom,
                            peak_color, peak_color, edge, edge);
        }
        return;
    }
    const int thickness = shade_x ? right - left : bottom - top;
    const int cuts[5] = {0, thickness / 4, thickness / 2,
                         thickness * 3 / 4, thickness};
    const int colors[4] = {base_index - 1, base_index,
                           base_index - 1, base_index - 2};
    for (int i = 0; i < 4; ++i) {
        if (cuts[i + 1] <= cuts[i]) continue;
        if (shade_x)
            append_solid(vertices, rgb, left + cuts[i], top,
                         left + cuts[i + 1], bottom, colors[i]);
        else
            append_solid(vertices, rgb, left, top + cuts[i],
                         right, top + cuts[i + 1], colors[i]);
    }
}

static int filled_count(int length, double amount, int minimum = 0)
{
    if (length <= 0) return 0;
    const int count = std::max(minimum, round_output_pixel(
        std::clamp(amount, 0.0, 1.0) * length));
    return std::min(length, count);
}

static void append_prefix(std::vector<Mw2erHudVertex> &vertices,
                          const uint8_t *rgb, int lo, int hi,
                          int cross_lo, int cross_hi, double amount,
                          int fill_color, int empty_color,
                          int horizontal, int from_end, int enhanced)
{
    const int count = filled_count(hi - lo + 1, amount);
    auto box = [=](int span_lo, int span_hi) {
        return horizontal ? Rect{span_lo, cross_lo, span_hi, cross_hi}
                          : Rect{cross_lo, span_lo, cross_hi, span_hi};
    };
    if (count <= 0) {
        append_meter_box(vertices, rgb, box(lo, hi), empty_color,
                         !horizontal, enhanced);
    } else if (count >= hi - lo + 1) {
        append_meter_box(vertices, rgb, box(lo, hi), fill_color,
                         !horizontal, enhanced);
    } else if (from_end) {
        const int fill_lo = hi - count + 1;
        append_meter_box(vertices, rgb, box(fill_lo, hi), fill_color,
                         !horizontal, enhanced);
        append_meter_box(vertices, rgb, box(lo, fill_lo - 1), empty_color,
                         !horizontal, enhanced);
    } else {
        const int fill_hi = lo + count - 1;
        append_meter_box(vertices, rgb, box(lo, fill_hi), fill_color,
                         !horizontal, enhanced);
        append_meter_box(vertices, rgb, box(fill_hi + 1, hi), empty_color,
                         !horizontal, enhanced);
    }
}

static void append_bar_meter(std::vector<Mw2erHudVertex> &vertices,
                             const uint8_t *rgb, const PowerMeter &meter,
                             const Rect &group_bounds,
                             const Transform &transform, int enhanced)
{
    Rect bar = snap_inclusive(meter.rect, transform);
    const Rect clip = snap_inclusive(group_bounds, transform);
    bar.left = std::max(bar.left, clip.left);
    bar.top = std::max(bar.top, clip.top);
    bar.right = std::min(bar.right, clip.right);
    bar.bottom = std::min(bar.bottom, clip.bottom);
    if (bar.right < bar.left || bar.bottom < bar.top) return;
    const double amount = std::clamp(meter.amount, 0.0, 1.0);
    if (meter.grow == METER_TOP_TO_BOTTOM) {
        append_prefix(vertices, rgb, bar.top, bar.bottom, bar.left, bar.right,
                      amount, meter.fill_color, meter.empty_color,
                      0, 0, enhanced);
        return;
    }
    if (meter.grow == METER_LEFT_TO_RIGHT) {
        append_prefix(vertices, rgb, bar.left, bar.right, bar.top, bar.bottom,
                      amount, meter.fill_color, meter.empty_color,
                      1, 0, enhanced);
        return;
    }
    if (amount <= 0.0 || amount >= 1.0) {
        append_meter_box(vertices, rgb, bar,
                         amount < 1.0 ? meter.empty_color : meter.fill_color,
                         0, enhanced);
        return;
    }
    const int width = bar.right - bar.left + 1;
    const int half = width / 2;
    const int right_lo = bar.right - half + 1;
    const int count = filled_count(half,
        amount <= 0.5 ? amount * 2.0 : amount * 2.0 - 1.0);
    const int first_color = amount <= 0.5 ? meter.edge_color : meter.fill_color;
    const int second_color = amount <= 0.5 ? meter.empty_color : meter.edge_color;
    int spans[2][3];
    int span_count = 0;
    if (count <= 0) {
        spans[span_count][0] = right_lo; spans[span_count][1] = bar.right;
        spans[span_count++][2] = second_color;
    } else if (count >= half) {
        spans[span_count][0] = right_lo; spans[span_count][1] = bar.right;
        spans[span_count++][2] = first_color;
    } else if (amount <= 0.5) {
        spans[span_count][0] = bar.right - count + 1;
        spans[span_count][1] = bar.right;
        spans[span_count++][2] = first_color;
        spans[span_count][0] = right_lo;
        spans[span_count][1] = bar.right - count;
        spans[span_count++][2] = second_color;
    } else {
        spans[span_count][0] = right_lo;
        spans[span_count][1] = right_lo + count - 1;
        spans[span_count++][2] = first_color;
        spans[span_count][0] = right_lo + count;
        spans[span_count][1] = bar.right;
        spans[span_count++][2] = second_color;
    }
    const int pivot = (bar.left + half - 1) + right_lo;
    for (int i = 0; i < span_count; ++i) {
        const Rect right_box = {spans[i][0], bar.top, spans[i][1], bar.bottom};
        const Rect left_box = {pivot - spans[i][1], bar.top,
                               pivot - spans[i][0], bar.bottom};
        append_meter_box(vertices, rgb, right_box, spans[i][2], 0, enhanced);
        append_meter_box(vertices, rgb, left_box, spans[i][2], 0, enhanced);
    }
    if (width & 1) {
        for (int i = 0; i < span_count; ++i) {
            if (spans[i][0] <= right_lo && right_lo <= spans[i][1]) {
                append_meter_box(vertices, rgb,
                    {bar.left + half, bar.top, bar.left + half, bar.bottom},
                    spans[i][2], 0, enhanced);
                break;
            }
        }
    }
}

static void append_throttle_meter(std::vector<Mw2erHudVertex> &vertices,
                                  const uint8_t *rgb, const PowerMeter &meter,
                                  const Transform &transform, int enhanced)
{
    const Rect outer = snap_inclusive(meter.rect, transform);
    if (outer.right < outer.left || outer.bottom < outer.top) return;
    int stroke = std::max(1, round_output_pixel(
        std::min(std::abs(transform.sx), std::abs(transform.sy))));
    const int max_stroke = std::min(outer.right - outer.left + 1,
        outer.bottom - outer.top + 1) / 2;
    stroke = std::min(stroke, max_stroke);
    if (stroke <= 0) return;
    const Rect inner = {outer.left + stroke, outer.top + stroke,
                        outer.right - stroke, outer.bottom - stroke};
    append_solid(vertices, rgb, outer.left, outer.top,
                 outer.right + 1, inner.top, 0x0A);
    append_solid(vertices, rgb, outer.left, inner.bottom + 1,
                 outer.right + 1, outer.bottom + 1, 0x0A);
    append_solid(vertices, rgb, outer.left, inner.top,
                 inner.left, inner.bottom + 1, 0x0A);
    append_solid(vertices, rgb, inner.right + 1, inner.top,
                 outer.right + 1, inner.bottom + 1, 0x0A);
    const int rest = std::clamp(round_output_pixel(
        transform.oy + meter.rest_y * transform.sy), inner.top, inner.bottom);
    int lo;
    int hi;
    if (meter.reverse) {
        const int count = filled_count(inner.bottom - rest + 1,
                                       meter.amount, stroke);
        lo = rest;
        hi = rest + count - 1;
    } else {
        const int count = filled_count(rest - inner.top + 1,
                                       meter.amount, stroke);
        lo = rest - count + 1;
        hi = rest;
    }
    append_meter_box(vertices, rgb,
        {inner.left, lo, inner.right, hi}, meter.fill_color, 1, enhanced);
}

static int submit_meter_vertices(const std::vector<Mw2erHudVertex> &vertices,
                                 int width, int height)
{
    return mw2er_hud_submit_rects(vertices.data(), (int32_t)vertices.size(),
                                  width, height);
}

static int draw_power_meters(int width, int height, const uint8_t *rgb)
{
    if (g_hud.meter_count <= 0) return 1;
    static std::vector<Mw2erHudVertex> vertices;
    if (vertices.capacity() < 512) vertices.reserve(512);
    vertices.clear();
    const int enhanced = _stricmp(mw2er_config().hud_power_meters, "enhanced") == 0;
    for (int i = 0; i < g_hud.meter_count; ++i) {
        const PowerMeter &meter = g_hud.meters[i];
        const Rect &bounds = meter.group == METER_GROUP_HEAT_JUMP
            ? g_hud.heat_jump_bounds : g_hud.throttle_bounds;
        const int bounds_valid = meter.group == METER_GROUP_HEAT_JUMP
            ? g_hud.heat_jump_bounds_valid : g_hud.throttle_bounds_valid;
        if (!bounds_valid) continue;
        const Transform transform = meter_transform(bounds, meter.group, width, height);
        if (meter.kind == METER_THROTTLE)
            append_throttle_meter(vertices, rgb, meter, transform, enhanced);
        else
            append_bar_meter(vertices, rgb, meter, bounds, transform, enhanced);
    }
    return submit_meter_vertices(vertices, width, height);
}

static int render_view(const View &v, AuxTarget &target,
                       int target_display, GLuint overlay,
                       int out_w, int out_h, int sample, const Rect &dest)
{
    const int lw = std::max(1, v.pane.right - v.pane.left);
    const int lh = std::max(1, v.pane.bottom - v.pane.top);
    const int rw = std::max(1, dest.right - dest.left) * sample;
    const int rh = std::max(1, dest.bottom - dest.top) * sample;
    if (!ensure_target(target, rw, rh)) return 0;
    glBindFramebuffer(GL_FRAMEBUFFER, target.fbo);
    glViewport(0, 0, rw, rh);
    const int32_t result = target_display
        ? mw2er_scene_draw_target(&v.camera, lw, lh, rw, rh)
        : mw2er_scene_draw_view(&v.camera, lw, lh, rw, rh, mw2er_hud_mfd_render_view());
    if (result != 0) return 0;
    glBindFramebuffer(GL_READ_FRAMEBUFFER, target.fbo);
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, overlay);
    const int dx0 = v.mirror ? dest.right : dest.left;
    const int dx1 = v.mirror ? dest.left : dest.right;
    glBlitFramebuffer(0, 0, rw, rh, dx0, out_h - dest.bottom,
                      dx1, out_h - dest.top, GL_COLOR_BUFFER_BIT, GL_LINEAR);
    glBindFramebuffer(GL_FRAMEBUFFER, overlay);
    glViewport(0, 0, out_w, out_h);
    return 1;
}

static int32_t meter_i32(const uint8_t *state, uint32_t offset)
{
    int32_t value = 0;
    std::memcpy(&value, state + offset, sizeof(value));
    return value;
}

static int speed_kph(const Mem &mem, uint32_t mech, int &reverse)
{
    const int64_t a = std::abs((int64_t)mem.i32(mech + 0xF4));
    const int64_t b = std::abs((int64_t)mem.i32(mech + 0xF8));
    const int64_t c = std::abs((int64_t)mem.i32(mech + 0xFC));
    const int64_t largest = std::max(a, std::max(b, c));
    const int64_t approx_raw = ((largest << 2) + a + b + c - largest) >> 2;
    const int magnitude = (int)((approx_raw / 10002) * 3 / 2);
    reverse = mem.i32(mech + 0x2C) < 0;
    return reverse ? -magnitude : magnitude;
}

static int32_t round_fixed16(int32_t value, int32_t scale)
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

static int center_reticle_resource(const Mem &mem, uint32_t player,
                                   uint32_t mech, uint32_t body)
{
    const uint32_t weapon_base = mem.u32(mech + 0x54);
    const int weapon_index = mem.i32(mech + 0xAC);
    if (!weapon_base || weapon_index < 0) return 0x67;
    const uint32_t weapon = weapon_base + weapon_index * WEAPON_SIZE;
    const int weapon_id = mem.i32(weapon + 0x04);
    if (mem.i32(weapon + 0x08) != 1 || weapon_id < 0) return 0x67;
    const uint32_t definition = ADDR_WEAPON_DEFINITIONS + weapon_id * 0x58u;
    if (mem.i32_rel(definition + 0x18)) {
        const uint32_t status = mem.u32(mech + 0x10C);
        if (status & 0x0080) return 0x61;
        if (status & 0x8000) return 0x70;
        return 0x6D;
    }

    const uint32_t handle = mem.u32(player + 0xDC);
    const int target_status = (handle >> 8) & 0xFF;
    double heading = std::fmod(mem.i32(body + 0x60) / 65536.0, 360.0);
    if (heading < 0.0) heading += 360.0;
    const double torso = std::fmod(mem.i32(mech + 0x0C) / 65536.0, 360.0);
    const double target_bearing = mem.i32(body + 0xD4) / 65536.0;
    const double body_relative = wrap_degrees(
        std::fmod(target_bearing, 360.0) - heading);
    const double torso_relative = wrap_degrees(body_relative - torso);
    const int vertical = (int)(((int64_t)mem.i32(body + 0xD8) +
        mem.i32(mech + 0x1C)) % (int64_t)k_turn);
    const int64_t deltas[3] = {
        std::llabs((int64_t)mem.i32(body + 0xC8) - mem.i32(body + 0x50)),
        std::llabs((int64_t)mem.i32(body + 0xCC) - mem.i32(body + 0x54)),
        std::llabs((int64_t)mem.i32(body + 0xD0) - mem.i32(body + 0x58)),
    };
    const int64_t largest = std::max({deltas[0], deltas[1], deltas[2]});
    const int64_t target_range =
        (3 * largest + deltas[0] + deltas[1] + deltas[2]) >> 2;
    const int lower = mem.i32_rel(definition + 0x3C);
    const int upper = mem.i32_rel(definition + 0x40);
    const int valid = handle && !(target_status & 0x01) &&
        !(target_status & 0x10) && lower < target_range &&
        target_range < upper && torso_relative > -3.0 &&
        torso_relative < 3.0 && vertical > -0x30000 && vertical < 0x30000;
    return mem.i32_rel(definition) == 3
        ? (valid ? 0x6A : 0x6D) : (valid ? 0x73 : 0x76);
}

static int capture_reticle_aim(const Mem &mem, uint32_t mech, uint32_t body,
                               int32_t world[3])
{
    const uint32_t aim_node = mem.u32(body + 0x44);
    if (!aim_node) return 0;
    const int32_t distance = mem.i32(body + 0xA0);
    const int32_t matrix_xz = mem.i32(aim_node + 0x44);
    const int32_t matrix_zz = mem.i32(aim_node + 0x5C);
    world[0] = mem.i32(aim_node + 0x60);
    world[1] = mem.i32(aim_node + 0x64);
    world[2] = mem.i32(aim_node + 0x68);
    const uint32_t y_offset = mem.u32_rel(ADDR_AIM_Y_OFFSET);
    if (y_offset) world[1] += mem.i32(y_offset);
    const double yaw = std::atan2((double)matrix_xz, (double)matrix_zz);
    const double pitch = mem.i32(mech + 0x1C) * (2.0 * 3.14159265358979323846 / k_turn);
    const double cp = std::cos(pitch);
    const int32_t direction[3] = {
        (int32_t)std::llround(std::sin(yaw) * cp * k_fixed),
        (int32_t)std::llround(-std::sin(pitch) * k_fixed),
        (int32_t)std::llround(std::cos(yaw) * cp * k_fixed),
    };
    for (int i = 0; i < 3; ++i)
        world[i] += round_fixed16(direction[i], distance);
    return 1;
}

static void capture_targeting(const Mem &mem, uint32_t player,
                              uint32_t mech, uint32_t body)
{
    TargetingState &state = g_hud.targeting;
    state = {};
    state.reticle_reference = -1;
    if (mem.u8_rel(ADDR_RADAR_MODE) == 4) return;
    const int reticle_enabled = mem.u32_rel(ADDR_RETICLE_ENABLE) != 0 &&
        mem.i32_rel(ADDR_CAMERA_RETICLE_GATE) != 0;
    const int markers_enabled = mem.u32_rel(ADDR_RETICLE_ENABLE + 4) != 0;
    if (!reticle_enabled && !markers_enabled) return;
    state.pane = {
        mem.i32_rel(ADDR_RETICLE_PANE + 0x04),
        mem.i32_rel(ADDR_RETICLE_PANE + 0x08),
        mem.i32_rel(ADDR_RETICLE_PANE + 0x0C) + 1,
        mem.i32_rel(ADDR_RETICLE_PANE + 0x10) + 1,
    };
    if (!valid_rect(state.pane)) return;

    if (reticle_enabled && capture_reticle_aim(
            mem, mech, body, state.reticle_world)) {
        const int resource = center_reticle_resource(mem, player, mech, body) +
            mem.u8_rel(ADDR_HUD_STYLE_OFFSET);
        state.reticle_reference =
            mw2er_targeting_capture_sprite(mem, resource);
        state.reticle_visible = state.reticle_reference >= 0;
    }
    if (!markers_enabled) return;
    const uint32_t handle = mem.u32(player + 0xDC);
    if (!handle || (handle & 0x1000u)) return;
    const int kind = handle & 0x0F00u;
    const int index = handle & 0xFFu;
    if (kind == 0x0100) {
        state.marker_world[0] = mem.i32(player + 0xC8);
        state.marker_world[1] = mem.i32(player + 0xCC);
        state.marker_world[2] = mem.i32(player + 0xD0);
    } else if (kind == 0x0200) {
        const uint32_t entity = mem.u32_rel(ADDR_ENTITY_TABLE + index * 4u);
        const uint32_t target_mech = entity ? mem.u32(entity + 0x20) : 0;
        if (!entity || !target_mech) return;
        const uint32_t slot = mem.u32(entity + 0x08);
        state.marker_sub_index = slot < 16 ? std::clamp((int)mem.u8_rel(
            ADDR_PRIMARY_CLASSIFICATION + slot * 0x26u), 0, 2) : 2;
        state.marker_extent = mem.i32(target_mech + 0xE8);
        state.marker_world[0] = mem.i32(entity + 0x50);
        state.marker_world[1] = mem.i32(entity + 0x54);
        state.marker_world[2] = mem.i32(entity + 0x58);
    } else if (kind == 0x0400) {
        const uint32_t entry = mem.rt(ADDR_SECONDARY_TABLE + index * 0x40u);
        const int object_index = entry ? mem.i32(entry + 0x04) : -1;
        const int maximum = mem.i32_rel(ADDR_SECONDARY_COUNT);
        if (object_index < 0 || object_index >= maximum) return;
        const uint32_t object = mem.u32_rel(
            ADDR_SECONDARY_POSITIONS + object_index * 0x7Cu + 0x20);
        const uint32_t bounds = object ? mem.u32(object + 0x6C) : 0;
        if (!bounds) return;
        const int reference = mem.i32(entry + 0x0C);
        state.marker_sub_index = reference < 0 ? 2 : std::clamp(
            (int)mem.u8_rel(ADDR_SECONDARY_CLASSIFICATION + reference), 0, 2);
        state.marker_world[0] = mem.i32(bounds + 0x34);
        state.marker_world[1] = mem.i32(bounds + 0x38);
        state.marker_world[2] = mem.i32(bounds + 0x3C);
        state.marker_extent = mem.i32(bounds + 0x40) >> 1;
        state.marker_clamp = 1;
    } else {
        return;
    }
    state.marker_handle = handle;
    state.marker_kind = kind;
    const uint32_t camera = mem.u32_rel(ADDR_ACTIVE_CAMERA);
    if (camera) {
        state.marker_maximum = mem.i32(camera + 0x84) >> 1;
        state.marker_projection_scale = mem.i32(camera + 0x94);
    }
    state.marker_visible = 1;
}

static void capture_target_text(const Mem &mem, uint32_t player, const Rect &pane)
{
    const uint32_t handle = mem.u32(player + 0xDC);
    const uint32_t kind = handle & 0x0F00u;
    if (!kind || (handle & 0x1000u) || (mem.u8(player + 0xDD) & 0x10)) return;
    g_hud.target_text_count = 0;
    char name[256] = {};
    int name_color = 0x0E;
    const uint32_t index = handle & 0xFFu;
    if (kind == 0x0100u) {
        const uint32_t record = ADDR_DIRECT_TARGETS + index * 0x54u;
        const int flags = mem.u16_rel(record + 0x24);
        const int use_default = !(flags & 0x20) && (flags & 0x100);
        if (!use_default) {
            uint8_t raw[44];
            if (mem.read_rel(record + 0x28, raw, sizeof(raw)))
                mw2er_cp437_to_utf8(raw, sizeof(raw), name, sizeof(name));
        }
        name_color = (flags & 0x20) ? 0x01 : 0x02;
    } else if (kind == 0x0200u) {
        const uint32_t entity = mem.u32_rel(ADDR_ENTITY_TABLE + index * 4u);
        if (entity) {
            uint8_t raw[64];
            if (mem.read(entity + 0xE8, raw, sizeof(raw)))
                mw2er_cp437_to_utf8(raw, sizeof(raw), name, sizeof(name));
            const uint32_t slot = mem.u32(entity + 0x08);
            const int classification = std::clamp(
                (int)mem.u8_rel(ADDR_PRIMARY_CLASSIFICATION + slot * 0x26u), 0, 2);
            const int colors[3] = {0x0E, 0x0A, 0x06};
            name_color = colors[classification];
        }
    } else if (kind == 0x0400u) {
        const uint32_t record = ADDR_SECONDARY_TABLE + index * 0x40u;
        uint8_t raw[44];
        if (mem.read_rel(record + 0x14, raw, sizeof(raw)))
            mw2er_cp437_to_utf8(raw, sizeof(raw), name, sizeof(name));
        const int reference = mem.i32_rel(record + 0x0C);
        const int classification = reference < 0 ? 2 : std::clamp(
            (int)mem.u8_rel(ADDR_SECONDARY_CLASSIFICATION + reference), 0, 2);
        const int colors[3] = {0x0E, 0x0A, 0x06};
        name_color = colors[classification];
    }
    if (name[0]) {
        PanelText &entry = g_hud.target_texts[g_hud.target_text_count++];
        entry = {};
        entry.slot = 40;
        entry.color = name_color;
        entry.x = (float)pane.left;
        snprintf(entry.text, sizeof(entry.text), "%s", name);
    }
    PanelText &range = g_hud.target_texts[g_hud.target_text_count++];
    range = {};
    range.slot = 41;
    range.color = 0x0E;
    range.x = (float)pane.left;
    range.y = 16.0f;
    const int value = mem.i32(player + 0xC4) / 100;
    if (value > 1000)
        snprintf(range.text, sizeof(range.text), "%3.2fk", value * 0.001);
    else
        snprintf(range.text, sizeof(range.text), "%dm", value);
}

static int damage_color(const Mem &mem, uint32_t part_base,
                        int map_index, int damage_scale)
{
    const uint32_t part = part_base + (map_index - 1) * 0x28u;
    const int current_low = mem.i32(part);
    const int current_high = mem.i32(part + 4);
    const int shared = mem.i32(part + 8);
    const int flags = mem.u16(part + 0x26);
    int high_damage = 0;
    const int high_scale = (flags & 0xF0) >> 4;
    if (high_scale && damage_scale) {
        high_damage = 15 - (int)((int64_t)3 *
            ((int64_t)current_high / damage_scale + shared) /
            ((int64_t)high_scale << 16));
        high_damage = std::clamp(high_damage, 0, 15);
    }
    int low_damage = 0;
    const int low_scale = flags & 0x0F;
    if (low_scale && damage_scale) {
        low_damage = 15 - (int)((int64_t)3 *
            ((int64_t)current_low / damage_scale + shared) /
            ((int64_t)low_scale << 16));
        low_damage = std::clamp(low_damage, 0, 15);
    }
    const int damage = std::max(high_damage, low_damage);
    if (flags & 0x2000) return 0x00;
    if (damage > 0x0B) return 0x0B;
    if (damage > 0) return 0x03;
    return -1;
}

static int capture_damage_wireframe(const Mem &mem, uint32_t mech,
                                    const Rect &pane)
{
    g_hud.damage_draw_count = 0;
    g_hud.damage_aligned = 0;
    const uint32_t part_base = mem.u32(mech + 0x58);
    if (!part_base) return 0;
    DamageLayout &layout = g_hud.damage_layout;
    if (!layout.valid) {
        layout.part_count = 0;
        uint8_t ui[0x238];
        if (!mem.read_rel(ADDR_MFD_DAMAGE_UI, ui, sizeof(ui))) return 0;
        int base_slot = -1;
        for (int slot = 0; slot < 16; ++slot) {
            const int map_index = meter_i32(ui, slot * 4);
            if (map_index >= 1 && map_index <= 64) {
                base_slot = slot;
                break;
            }
        }
        if (base_slot < 0) return 0;
        const int base_window = 0x78 + base_slot * 20;
        layout.base_x = meter_i32(ui, base_window + 4) +
                        meter_i32(ui, 0x1B8 + base_slot * 8);
        layout.base_y = meter_i32(ui, base_window + 8) +
                        meter_i32(ui, 0x1BC + base_slot * 8);
        for (int slot = 0; slot < 16; ++slot) {
            const int map_index = meter_i32(ui, slot * 4);
            if (map_index < 1 || map_index > 64) continue;
            const int window = 0x78 + slot * 20;
            DamagePartLayout &part = layout.parts[layout.part_count++];
            part.map_index = map_index;
            part.x = meter_i32(ui, window + 4) +
                     meter_i32(ui, 0x1B8 + slot * 8);
            part.y = meter_i32(ui, window + 8) +
                     meter_i32(ui, 0x1BC + slot * 8);
            part.clip = {meter_i32(ui, window + 4),
                         meter_i32(ui, window + 8),
                         meter_i32(ui, window + 12) + 1,
                         meter_i32(ui, window + 16) + 1};
        }
        layout.valid = 1;
    }
    const int resource = mem.i32_rel(ADDR_MFD_DAMAGE_SHAPE_BASE) +
                         mem.u8_rel(ADDR_HUD_STYLE_OFFSET);
    const uint32_t shape = g_hud.damage_cpu_key &&
        g_hud.damage_resource == resource
        ? g_hud.damage_cpu_key : mw2er_resolve_cached_shape(mem, resource);
    if (!shape) return 0;
    if (g_hud.damage_cpu_key != shape) {
        Mw2erSprite decoded;
        if (!mw2er_decode_runtime_sprite(mem, shape, 0, &decoded)) return 0;
        g_hud.damage_sprite = std::move(decoded);
        g_hud.damage_cpu_key = shape;
    }
    g_hud.damage_resource = resource;
    DamageDraw &base = g_hud.damage_draws[g_hud.damage_draw_count++];
    base.x = layout.base_x;
    base.y = layout.base_y;
    base.clip = pane;
    base.color_override = -1;
    const int damage_scale = mem.i32_rel(ADDR_MFD_DAMAGE_SCALE);
    for (int i = 0; i < layout.part_count; ++i) {
        const DamagePartLayout &part = layout.parts[i];
        const int color = damage_color(mem, part_base, part.map_index, damage_scale);
        if (color < 0 || g_hud.damage_draw_count >= MAX_DAMAGE_DRAWS) continue;
        DamageDraw &draw = g_hud.damage_draws[g_hud.damage_draw_count++];
        draw.x = part.x;
        draw.y = part.y;
        draw.clip = part.clip;
        draw.color_override = color;
    }
    return 1;
}

static void capture_htal(const Mem &mem, uint32_t mech, const Rect &pane)
{
    g_hud.htal_visible = 0;
    g_hud.htal_text_count = 0;
    g_hud.htal_meter_count = 0;
    const uint32_t part_base = mem.u32(mech + 0x58);
    uint8_t labels[16], positions[36], config[0x188];
    if (!mem.read_rel(ADDR_MFD_HTAL_LABELS, labels, sizeof(labels)) ||
        !mem.read_rel(ADDR_MFD_HTAL_POSITIONS, positions, sizeof(positions)) ||
        !mem.read_rel(ADDR_MFD_STATIC_CONFIG, config, sizeof(config))) return;
    const int base_width = meter_i32(config, 0x00);
    const int bar_scale = meter_i32(config, 0x04);
    const int denominator = meter_i32(positions, 0x20);
    if (base_width <= 0 || denominator == 0) return;
    const int half_width = base_width / 2;
    const int enhanced = _stricmp(mw2er_config().hud_htal_meters, "enhanced") == 0;
    double y_offset = 0.0;
    double label_y_offset = 0.0;
    g_hud.htal_clip = pane;
    if (mw2er_config().hud_alt_htal_view) {
        double maximum_bottom = -1e30;
        int minimum_top = INT_MAX;
        for (int part = 0; part < 8; ++part) {
            const int origin_y = meter_i32(config, 0x0C + part * 8);
            minimum_top = std::min(minimum_top, origin_y);
            const int count = part >= 1 && part <= 3 ? 2 : 1;
            for (int side = 0; side < count; ++side) {
                const int raw_max = meter_i32(config, 0x148 + part * 8 + side * 4);
                const double maximum = std::max(0.0, enhanced
                    ? (double)raw_max * bar_scale / denominator
                    : (double)((int64_t)raw_max * bar_scale / denominator));
                maximum_bottom = std::max(maximum_bottom, origin_y + maximum);
            }
        }
        const double label_bottom = std::max(std::max(
            meter_i32(positions, 4), meter_i32(positions, 12)), std::max(
            meter_i32(positions, 20), meter_i32(positions, 28))) + 16.0;
        const double camera_label_bottom = pane.bottom + 9.0 + 16.0;
        y_offset = camera_label_bottom - (pane.top + maximum_bottom);
        label_y_offset = std::max(0.0, (minimum_top - label_bottom) * 0.5);
        g_hud.htal_clip.bottom = (int)std::ceil(camera_label_bottom);
    }
    const int groups[4][3] = {{0,-1,-1},{1,2,3},{4,5,-1},{6,7,-1}};
    double centers[4] = {};
    int htal_left = INT_MAX, htal_right = INT_MIN;
    for (int group = 0; group < 4; ++group) {
        int left = INT_MAX, right = INT_MIN;
        for (int j = 0; j < 3 && groups[group][j] >= 0; ++j) {
            const int part = groups[group][j];
            const int x = meter_i32(config, 0x08 + part * 8);
            const int width = part >= 1 && part <= 3
                ? half_width * 2 : base_width;
            left = std::min(left, x);
            right = std::max(right, x + width);
        }
        centers[group] = (left + right) * 0.5;
        htal_left = std::min(htal_left, left);
        htal_right = std::max(htal_right, right);
        PanelText &text = g_hud.htal_texts[g_hud.htal_text_count++];
        text = {};
        text.slot = 25 + group;
        text.color = 0x06;
        text.x = (float)(pane.left + centers[group]);
        text.y = (float)(pane.top + meter_i32(positions, group * 8 + 4) +
                         y_offset + label_y_offset);
        text.horizontal = TEXT_CENTER;
        mw2er_cp437_to_utf8(labels + group * 4, 4,
                            text.text, sizeof(text.text));
    }
    if (mw2er_config().hud_alt_htal_view && g_hud.damage_draw_count) {
        g_hud.damage_aligned = 1;
        g_hud.damage_center_x = pane.left + (htal_left + htal_right) * 0.5;
        g_hud.damage_bottom_y = 1e30;
        for (int group = 0; group < 4; ++group) {
            g_hud.damage_bottom_y = std::min(g_hud.damage_bottom_y,
                pane.top + meter_i32(positions, group * 8 + 4) +
                y_offset + label_y_offset);
        }
    }
    if (!part_base) { g_hud.htal_visible = 1; return; }
    for (int part = 0; part < 8; ++part) {
        const uint32_t part_address = part_base + part * 0x28u;
        const int flags = mem.u16(part_address + 0x26);
        const int origin_x = meter_i32(config, 0x08 + part * 8);
        const int origin_y = meter_i32(config, 0x0C + part * 8);
        const int bar_count = part >= 1 && part <= 3 ? 2 : 1;
        for (int side = 0; side < bar_count; ++side) {
            if (g_hud.htal_meter_count >= MAX_HTAL_METERS) break;
            const int raw_current = mem.i32(part_address + side * 4u);
            const int raw_max = meter_i32(config, 0x148 + part * 8 + side * 4);
            const double maximum = std::max(0.0, enhanced
                ? (double)raw_max * bar_scale / denominator
                : (double)((int64_t)raw_max * bar_scale / denominator));
            double current = flags & 0x2000 ? 0.0 : (enhanced
                ? (double)raw_current * bar_scale / denominator
                : (double)((int64_t)raw_current * bar_scale / denominator));
            current = std::clamp(current, 0.0, maximum);
            PowerMeter &meter = g_hud.htal_meters[g_hud.htal_meter_count++];
            meter = {};
            meter.kind = METER_BAR;
            meter.grow = METER_TOP_TO_BOTTOM;
            meter.fill_color = 0x0F;
            const double x = pane.left + origin_x + (side ? half_width : 0);
            const double y = pane.top + origin_y + y_offset;
            const double raw_right = x + (bar_count == 2 ? half_width : base_width);
            const double raw_bottom = y + maximum;
            meter.rect.left = std::max(x, (double)g_hud.htal_clip.left);
            meter.rect.top = std::max(y, (double)g_hud.htal_clip.top);
            meter.rect.right = std::min(raw_right, (double)g_hud.htal_clip.right);
            meter.rect.bottom = std::min(raw_bottom, (double)g_hud.htal_clip.bottom);
            const double visible_height = meter.rect.bottom - meter.rect.top;
            const double filled = std::max(0.0,
                std::min(meter.rect.bottom, y + current) - meter.rect.top);
            meter.amount = visible_height > 0.0 ? filled / visible_height : 0.0;
            meter.empty_color = flags & 0x2000 ? 0xF3
                : ((raw_max >> 2) >= raw_current ? 0x0B : 0x03);
        }
    }
    g_hud.htal_visible = 1;
}

static void capture_meter_text(const Mem &mem, uint32_t mech, uint32_t panel,
                               int panel_index, uint32_t callback,
                               const Rect &pane)
{
    const int alternate = mw2er_config().hud_alt_throttle_indicator_position &&
        (callback == mem.rt(CALLBACK_THROTTLE) || callback == mem.rt(CALLBACK_MASC));
    const uint32_t position = mem.u32(panel + 0x34);
    if (!position && !alternate) return;
    if (callback == mem.rt(CALLBACK_MASC) &&
        mem.i32_rel(ADDR_MASC_ACTIVE) == 0) return;
    if (g_hud.text_count >= MAX_HUD_TEXTS) return;
    HudText &entry = g_hud.texts[g_hud.text_count++];
    entry = {};
    entry.slot = panel_index;
    entry.group = callback == mem.rt(CALLBACK_HEAT) ||
                  callback == mem.rt(CALLBACK_HEAT_RATE) ||
                  callback == mem.rt(CALLBACK_JUMP_JETS)
        ? METER_GROUP_HEAT_JUMP : METER_GROUP_THROTTLE;
    entry.panel_bounds = pane;
    entry.own_bounds = callback == mem.rt(CALLBACK_MASC) && !alternate;
    entry.clip = !alternate;
    entry.color = 0x0E;
    int local_x = 0, local_y = 0;
    if (position) {
        local_x = mem.i32(position);
        local_y = mem.i32(position + 4);
    }
    entry.x = (float)(alternate ? k_alt_throttle_text_right : pane.left + local_x);
    entry.y = (float)(alternate
        ? (callback == mem.rt(CALLBACK_MASC)
            ? k_alt_throttle_masc_y : k_alt_throttle_neutral_y)
        : pane.top + local_y);
    entry.horizontal = alternate ? TEXT_RIGHT : TEXT_LEFT;
    entry.vertical_center = alternate;
    if (callback == mem.rt(CALLBACK_MASC)) {
        snprintf(entry.text, sizeof(entry.text), "MASC");
    } else if (callback == mem.rt(CALLBACK_THROTTLE)) {
        int reverse = 0;
        const int speed = speed_kph(mem, mech, reverse);
        entry.color = reverse ? 0x06 : 0x0E;
        snprintf(entry.text, sizeof(entry.text), "%d kph", speed);
    } else if (callback == mem.rt(CALLBACK_HEAT_RATE)) {
        snprintf(entry.text, sizeof(entry.text), "\xCE\x94H/\xCE\x94t");
    } else {
        uint8_t raw[32];
        if (mem.read(panel + 0x10, raw, sizeof(raw)))
            mw2er_cp437_to_utf8(raw, sizeof(raw),
                                entry.text, sizeof(entry.text));
    }
}

static int is_power_callback(const Mem &mem, uint32_t callback)
{
    return callback == mem.rt(CALLBACK_THROTTLE) ||
           callback == mem.rt(CALLBACK_MASC) ||
           callback == mem.rt(CALLBACK_HEAT) ||
           callback == mem.rt(CALLBACK_HEAT_RATE) ||
           callback == mem.rt(CALLBACK_JUMP_JETS);
}

static int weapon_color(const Mem &mem, uint32_t weapon)
{
    const int type = mem.i32(weapon + 0x08);
    const int readiness = mem.i32(weapon + 0x0C);
    if (type == -1) return 0x08;
    if (type == 0) return 0x0B;
    if (type == 1) {
        if (readiness == 1) return 0xFE;
        if (readiness == 2) return 0x03;
        return 0x0E;
    }
    return type == 2 ? 0x0E : 0x0B;
}

static void capture_weapon_panel(const Mem &mem, uint32_t panel,
                                 int panel_index, uint32_t weapon_base,
                                 int active_weapon, int hud_mode,
                                 int game_tick, int startup_color)
{
    const int weapon_index = mem.i32(panel + 0x0C);
    const uint32_t pane_address = mem.u32(panel + 0x30);
    const uint32_t text_position = mem.u32(panel + 0x34);
    if (weapon_index < 0 || !pane_address || !text_position) return;
    const Rect pane = {mem.i32(pane_address + 0x04),
                       mem.i32(pane_address + 0x08),
                       mem.i32(pane_address + 0x0C),
                       mem.i32(pane_address + 0x10)};
    if (!valid_rect(pane)) return;
    include_rect(g_hud.weapon_bounds, g_hud.weapon_bounds_valid, pane);
    if (!weapon_base || g_hud.weapon_count >= PANEL_COUNT) return;
    const uint64_t weapon64 = (uint64_t)weapon_base +
        (uint64_t)weapon_index * WEAPON_SIZE;
    if (weapon64 > 0xFFFFFFFFull) return;
    const uint32_t weapon = (uint32_t)weapon64;
    if (mem.i32(weapon + 0x04) < 0) return;
    if (hud_mode == 1 && game_tick <= mem.i32(panel + 0x08)) return;

    WeaponRow &row = g_hud.weapons[g_hud.weapon_count++];
    row = {};
    row.slot = panel_index;
    row.pane = pane;
    row.x = (float)(pane.left + mem.i32(text_position));
    row.y = (float)(pane.top + mem.i32(text_position + 0x04));
    row.color = hud_mode == 1 ? startup_color : weapon_color(mem, weapon);
    row.active = hud_mode == 2 && weapon_index == active_weapon;
    const uint8_t *name = mem.view(panel + 0x10, 0x20);
    if (name)
        mw2er_cp437_to_utf8(name, 0x20, row.text, sizeof(row.text));
    if (hud_mode == 2) {
        const int ammo = mem.i32(weapon + 0x20);
        const size_t used = std::strlen(row.text);
        if (ammo >= 0 && used < sizeof(row.text) - 1)
            snprintf(row.text + used, sizeof(row.text) - used, " %d", ammo);
    }
}

static void capture_power_meter(const Mem &mem, const uint8_t *meter_state,
                                uint32_t player, uint32_t mech,
                                uint32_t panel, int panel_index,
                                uint32_t callback)
{
    Rect pane;
    if (!panel_rect(mem, panel, pane, 0)) return;
    const uint32_t throttle_cb = mem.rt(CALLBACK_THROTTLE);
    const uint32_t masc_cb = mem.rt(CALLBACK_MASC);
    const uint32_t heat_cb = mem.rt(CALLBACK_HEAT);
    const uint32_t heat_rate_cb = mem.rt(CALLBACK_HEAT_RATE);
    const uint32_t jump_cb = mem.rt(CALLBACK_JUMP_JETS);
    const int alternate = mw2er_config().hud_alt_throttle_indicator_position;

    capture_meter_text(mem, mech, panel, panel_index, callback, pane);

    if (callback == heat_cb || callback == heat_rate_cb || callback == jump_cb)
        include_rect(g_hud.heat_jump_bounds, g_hud.heat_jump_bounds_valid, pane);
    if (callback == masc_cb) {
        if (alternate) {
            const Rect bounds = {911, 285, 1021, 429};
            include_rect(g_hud.throttle_bounds, g_hud.throttle_bounds_valid, bounds);
        }
        return;
    }
    if (callback != throttle_cb && callback != heat_cb &&
        callback != heat_rate_cb && callback != jump_cb) return;
    if (g_hud.meter_count >= MAX_POWER_METERS) return;

    PowerMeter meter = {};
    meter.kind = METER_BAR;
    meter.grow = METER_LEFT_TO_RIGHT;
    meter.group = callback == throttle_cb
        ? METER_GROUP_THROTTLE : METER_GROUP_HEAT_JUMP;
    if (callback == throttle_cb) {
        const Rect group_bounds = alternate ? Rect{911, 285, 1021, 429} : pane;
        include_rect(g_hud.throttle_bounds, g_hud.throttle_bounds_valid, group_bounds);
        const int max_height = meter_i32(meter_state, 0x34);
        const int outline_left = meter_i32(meter_state, 0x38);
        const int outline_top = meter_i32(meter_state, 0x3C);
        const int outline_bottom = meter_i32(meter_state, 0x44);
        const int neutral_y = meter_i32(meter_state, 0x4C);
        const int outer_left = alternate ? k_alt_throttle_left
                                         : pane.left + outline_left;
        const int outer_top = alternate ? k_alt_throttle_top
                                        : pane.top + outline_top;
        const int outer_bottom = alternate ? k_alt_throttle_bottom - 1
                                           : pane.top + outline_bottom;
        meter.kind = METER_THROTTLE;
        meter.rect = {(double)outer_left, (double)outer_top,
                      (double)(outer_left + 17), (double)(outer_bottom + 1)};
        meter.rest_y = alternate ? k_alt_throttle_neutral_y
                                 : pane.top + neutral_y;
        const int32_t current = meter_i32(meter_state, 0x0C);
        const uint32_t control = mem.u32(player + 0x20 + 0x2C);
        meter.reverse = control && mem.u8(control + 0x2F) != 0;
        const int enhanced =
            _stricmp(mw2er_config().hud_power_meters, "enhanced") == 0;
        const double raw = meter.reverse
            ? (enhanced ? -current / k_fixed : -(double)(current >> 16))
            : (enhanced ? current / k_fixed : (double)(current >> 16));
        const double source_max = std::max(1.0,
            (double)(meter.reverse ? max_height / 2 : max_height));
        meter.amount = std::clamp(raw / source_max, 0.0, 1.0);
        meter.fill_color = meter.reverse ? 0x07 : 0x0F;
    } else {
        const uint32_t state_offset = callback == heat_cb ? 0x50u
            : (callback == heat_rate_cb ? 0x60u : 0x70u);
        const int bar_x = meter_i32(meter_state, state_offset);
        const int bar_y = meter_i32(meter_state, state_offset + 4);
        const int bar_width = meter_i32(meter_state, state_offset + 8);
        const int bar_height = meter_i32(meter_state, state_offset + 12);
        if (bar_width <= 0 || bar_height <= 0) return;
        meter.rect = {(double)(pane.left + bar_x),
                      (double)(pane.top + bar_y),
                      (double)(pane.left + bar_x + bar_width),
                      (double)(pane.top + bar_y + bar_height)};
        if (callback == heat_cb) {
            const int32_t current = meter_i32(meter_state, 0x00);
            const int enhanced =
                _stricmp(mw2er_config().hud_power_meters, "enhanced") == 0;
            const double fill = enhanced ? current / k_fixed
                                         : (double)(current >> 16);
            meter.amount = std::clamp(fill / bar_width, 0.0, 1.0);
            meter.grow = METER_SYMMETRIC;
            meter.fill_color = 0x0B;
            meter.empty_color = 0x07;
            meter.edge_color = 0x03;
        } else if (callback == heat_rate_cb) {
            const int32_t current = meter_i32(meter_state, 0x18);
            if (current < 1) {
                meter.amount = 0.0;
                meter.fill_color = meter.empty_color = 0x07;
            } else if (current < 0x300) {
                meter.amount = current / 768.0;
                meter.fill_color = 0x03;
                meter.empty_color = 0x07;
            } else {
                meter.amount = std::min(1.0, current / 768.0);
                meter.fill_color = 0x0B;
                meter.empty_color = 0x03;
            }
        } else {
            if (mem.i32(mech + 0xC0) < 0) return;
            const int32_t current = meter_i32(meter_state, 0x24);
            if (current < 0) return;
            meter.amount = current <= 0 ? 0.0
                                        : std::min(1.0, current / 1820.0);
            meter.fill_color = 0x0F;
            meter.empty_color = 0x0B;
        }
    }
    g_hud.meters[g_hud.meter_count++] = meter;
}

static int hud_font_size(int height)
{
    const double scale = resolved_scale(height, mw2er_config().hud_font_scaling);
    return std::max(1, (int)std::nearbyint(16.0 * scale));
}

static Transform mfd_content_transform(int width, int height)
{
    const Rect &pane = g_hud.mfd.pane;
    const Transform frame = base_transform(pane, 1, width, height);
    const double center_x = (pane.left + pane.right) * 0.5;
    const double center_y = (pane.top + pane.bottom) * 0.5;
    const double final_x = frame.ox + center_x * frame.sx;
    const double final_y = frame.oy + center_y * frame.sy;
    const double scale = resolved_scale(height, mw2er_config().hud_panel_scaling);
    return {final_x - center_x * scale, final_y - center_y * scale,
            scale, scale};
}

static void set_text_scissor(const Rect &rect, int width, int height)
{
    const int left = std::clamp(rect.left, 0, width);
    const int top = std::clamp(rect.top, 0, height);
    const int right = std::clamp(rect.right, 0, width);
    const int bottom = std::clamp(rect.bottom, 0, height);
    glEnable(GL_SCISSOR_TEST);
    glScissor(left, height - bottom, std::max(0, right - left),
              std::max(0, bottom - top));
}

static int draw_text_at(int slot, const char *text, int color_index,
                        TextAlignment horizontal, int vertical_center,
                        float x, float y, int size_px, int width, int height,
                        const uint8_t *palette)
{
    if (!text[0]) return 1;
    if (horizontal != TEXT_LEFT || vertical_center) {
        Mw2erTextMetrics metrics = {};
        if (mw2er_font_measure(slot, text, size_px, 0.0f, &metrics) != MW2ER_OK)
            return 0;
        if (horizontal == TEXT_CENTER) x -= metrics.width * 0.5f;
        else if (horizontal == TEXT_RIGHT) x -= metrics.width;
        if (vertical_center) y -= metrics.height * 0.5f;
    }
    float color[4];
    palette_color(palette, color_index, color);
    return mw2er_font_draw(slot, text, size_px, 0.0f, x, y, 1.0f,
                           color, width, height) == MW2ER_OK;
}

static int draw_meter_texts(int width, int height, const uint8_t *palette)
{
    const int size_px = hud_font_size(height);
    for (int i = 0; i < g_hud.text_count; ++i) {
        const HudText &entry = g_hud.texts[i];
        if (!entry.text[0]) continue;
        const Rect *bounds = &entry.panel_bounds;
        if (!entry.own_bounds) {
            bounds = entry.group == METER_GROUP_HEAT_JUMP
                ? &g_hud.heat_jump_bounds : &g_hud.throttle_bounds;
        }
        const int bounds_valid = entry.own_bounds ||
            (entry.group == METER_GROUP_HEAT_JUMP
                ? g_hud.heat_jump_bounds_valid : g_hud.throttle_bounds_valid);
        if (!bounds_valid) continue;
        const Transform transform = meter_transform(
            *bounds, entry.group, width, height);
        float x = (float)(transform.ox + entry.x * transform.sx);
        float y = (float)(transform.oy + entry.y * transform.sy);
        if (entry.clip)
            set_text_scissor(pixel_rect(*bounds, transform), width, height);
        else
            glDisable(GL_SCISSOR_TEST);
        if (!draw_text_at(entry.slot, entry.text, entry.color,
                          entry.horizontal, entry.vertical_center, x, y,
                          size_px, width, height, palette)) return 0;
    }
    glDisable(GL_SCISSOR_TEST);
    return 1;
}

static int draw_hud_weapons_panel(int width, int height,
                                  const uint8_t *palette)
{
    if (!g_hud.weapon_bounds_valid || g_hud.weapon_count <= 0) return 1;
    const Transform transform = weapon_transform(
        g_hud.weapon_bounds, width, height);
    const int border_width = std::max(
        1, round_output_pixel(std::min(transform.sx, transform.sy)));

    glDisable(GL_SCISSOR_TEST);
    for (int i = 0; i < g_hud.weapon_count; ++i) {
        const WeaponRow &row = g_hud.weapons[i];
        if (!row.active) continue;
        const Rect outer = {
            round_output_pixel(transform.ox + row.pane.left * transform.sx),
            round_output_pixel(transform.oy + row.pane.top * transform.sy),
            round_output_pixel(transform.ox + row.pane.right * transform.sx),
            round_output_pixel(transform.oy + row.pane.bottom * transform.sy),
        };
        const int stroke = std::min(border_width, std::min(
            (outer.right - outer.left) / 2,
            (outer.bottom - outer.top) / 2));
        if (stroke > 0) border(outer, height, palette, row.color, stroke);
    }

    set_text_scissor(pixel_rect(g_hud.weapon_bounds, transform), width, height);
    const int size_px = hud_font_size(height);
    for (int i = 0; i < g_hud.weapon_count; ++i) {
        const WeaponRow &row = g_hud.weapons[i];
        const float x = (float)(transform.ox + row.x * transform.sx);
        const float y = (float)(transform.oy + row.y * transform.sy);
        if (!draw_text_at(row.slot, row.text, row.color, TEXT_LEFT, 0,
                          x, y, size_px, width, height, palette)) {
            glDisable(GL_SCISSOR_TEST);
            return 0;
        }
    }
    glDisable(GL_SCISSOR_TEST);
    return 1;
}

static int draw_target_texts(int width, int height, const uint8_t *palette)
{
    if (!g_hud.target.visible || !g_hud.target_text_count) return 1;
    const Transform frame = animated(g_hud.target.pane,
        base_transform(g_hud.target.pane, 0, width, height));
    const int size_px = hud_font_size(height);
    glDisable(GL_SCISSOR_TEST);
    for (int i = 0; i < g_hud.target_text_count; ++i) {
        const PanelText &entry = g_hud.target_texts[i];
        const float x = (float)(frame.ox + entry.x * frame.sx);
        const float y = (float)(frame.oy +
            (entry.y + g_hud.target.pane.bottom + 9.0f) * frame.sy);
        if (!draw_text_at(entry.slot, entry.text, entry.color,
                          entry.horizontal, 0, x, y, size_px,
                          width, height, palette)) return 0;
    }
    return 1;
}

static int draw_target_nav_sprite(int width, int height,
                                  const uint8_t *palette,
                                  const Transform &frame, const Rect &clip)
{
    if (!g_hud.target_nav_visible) return 1;
    const Mw2erTargetClip target_clip = {
        clip.left, clip.top, clip.right, clip.bottom};
    return mw2er_targeting_draw_sprite(
        g_hud.target_nav_reference,
        frame.ox + g_hud.target_nav_x * frame.sx,
        frame.oy + g_hud.target_nav_y * frame.sy,
        frame.sx, target_clip, palette, width, height);
}

struct TargetProjection {
    double x, y;
    double view_x, view_y, depth;
    int onscreen;
};

static TargetProjection project_targeting_point(const int32_t world[3],
                                                const Mw2erCamera &camera,
                                                int width, int height,
                                                double focal)
{
    double delta[3];
    for (int i = 0; i < 3; ++i)
        delta[i] = world[i] / k_fixed - camera.position[i];
    TargetProjection result = {};
    for (int i = 0; i < 3; ++i) {
        result.view_x += delta[i] * camera.right[i];
        result.view_y += delta[i] * camera.up[i];
        result.depth += delta[i] * camera.forward[i];
    }
    const double divisor = std::max(1.0e-12, std::abs(result.depth));
    result.x = width * 0.5 + focal * result.view_x / divisor;
    result.y = height * 0.5 - focal * result.view_y / divisor;
    result.onscreen = result.depth > 0.0 && result.x >= 0.0 &&
        result.x < width && result.y >= 0.0 && result.y < height;
    return result;
}

static void clip_target_direction(double dx, double dy, const Rect &pane,
                                  double &x, double &y, int &direction)
{
    const double center_x = pane.left + ((pane.right - pane.left) >> 1);
    const double center_y = pane.top + ((pane.bottom - pane.top) >> 1);
    if (std::abs(dx) < 1.0e-12 && std::abs(dy) < 1.0e-12) dy = -1.0;
    double best = 1.0e30;
    direction = 3;
    if (dx > 0.0) {
        best = (pane.right - 1 - center_x) / dx;
        direction = 2;
    } else if (dx < 0.0) {
        best = (pane.left - center_x) / dx;
        direction = 1;
    }
    double vertical = 1.0e30;
    int vertical_direction = 3;
    if (dy > 0.0) {
        vertical = (pane.bottom - 1 - center_y) / dy;
        vertical_direction = 0;
    } else if (dy < 0.0) {
        vertical = (pane.top - center_y) / dy;
    }
    if (vertical < best) {
        best = vertical;
        direction = vertical_direction;
    }
    x = center_x + dx * best;
    y = center_y + dy * best;
}

static int draw_targeting(int width, int height, const uint8_t *palette,
                          const Mw2erCamera &camera)
{
    const TargetingState &state = g_hud.targeting;
    if (!state.reticle_visible && !state.marker_visible) return 1;
    const Mw2erTargetClip screen = {0, 0, width, height};
    const double marker_scale = resolved_scale(
        height, mw2er_config().hud_target_marker_scaling);
    const double focal = mw2er_scene_output_focal(camera, width, height);
    if (state.reticle_visible) {
        const TargetProjection point = project_targeting_point(
            state.reticle_world, camera, width, height, focal);
        if (point.onscreen && !mw2er_targeting_draw_sprite(
                state.reticle_reference, point.x, point.y, marker_scale,
                screen, palette, width, height)) return 0;
    }
    if (!state.marker_visible) return 1;
    const TargetProjection point = project_targeting_point(
        state.marker_world, camera, width, height, focal);
    const int colors[3] = {0x0E, 0x0A, 0x06};
    const int color = state.marker_kind == 0x0100
        ? 0x0E : colors[std::clamp(state.marker_sub_index, 0, 2)];
    if (!point.onscreen) {
        if (g_hud.acquisition.phase == AcquisitionPhase::Running)
            g_hud.acquisition.phase = AcquisitionPhase::Pending;
        double native_x, native_y;
        int direction;
        clip_target_direction(point.view_x, -point.view_y, state.pane,
                              native_x, native_y, direction);
        const double position_scale = resolved_scale(
            height, mw2er_config().hud_position_scaling);
        const double canvas_x = (width - 1024.0 * position_scale) * 0.5;
        const double canvas_y = (height - 768.0 * position_scale) * 0.5;
        const Mw2erTargetClip clip = {
            (int)std::floor(canvas_x + state.pane.left * position_scale),
            (int)std::floor(canvas_y + state.pane.top * position_scale),
            (int)std::ceil(canvas_x + state.pane.right * position_scale),
            (int)std::ceil(canvas_y + state.pane.bottom * position_scale),
        };
        return mw2er_targeting_draw_caret(
            direction, canvas_x + native_x * position_scale,
            canvas_y + native_y * position_scale, color, marker_scale,
            clip, palette, width, height);
    }
    if (state.marker_kind == 0x0100) {
        if (!mw2er_targeting_draw_nav(
                point.x, point.y, color, marker_scale,
                screen, palette, width, height)) return 0;
        const int size_px = std::max(1,
            (int)std::floor(16.0 * marker_scale + 0.5));
        float rgba[4];
        palette_color(palette, color, rgba);
        Mw2erTextMetrics metrics = {};
        if (mw2er_font_measure(TARGET_NAV_TEXT_SLOT, "NAV", size_px,
                               0.0f, &metrics) != MW2ER_OK)
            return 0;
        return mw2er_font_draw(
            TARGET_NAV_TEXT_SLOT, "NAV", size_px, 0.0f,
            (float)std::floor(point.x - metrics.width * 0.5 + 0.5),
            (float)std::floor(point.y - 14.0 * marker_scale + 0.5),
            1.0f, rgba, width, height) == MW2ER_OK;
    }
    double radius = 12.0;
    const double depth_fixed = point.depth * k_fixed * 4.0;
    if (depth_fixed != 0.0 && state.marker_projection_scale) {
        radius = (double)state.marker_extent * state.marker_projection_scale /
            (16384.0 * depth_fixed);
        if (state.marker_clamp && state.marker_maximum > 0)
            radius = std::min(radius, (double)state.marker_maximum);
    }
    radius = std::max(0.0, radius) *
        (focal / std::max(1.0f, camera.focal_length_pixels));
    const double panel_scale = resolved_scale(
        height, mw2er_config().hud_panel_scaling);
    TargetAcquisition &acquisition = g_hud.acquisition;
    const double duration = mw2er_config().hud_targeting_animation_duration;
    if (duration > 0.0 && (acquisition.phase == AcquisitionPhase::Pending ||
                           acquisition.phase == AcquisitionPhase::Running)) {
        if (acquisition.phase == AcquisitionPhase::Pending) {
            acquisition.started_at = g_hud.targeting_now;
            acquisition.phase = AcquisitionPhase::Running;
        }
        const double progress = std::clamp(
            (g_hud.targeting_now - acquisition.started_at) / duration, 0.0, 1.0);
        const int drawn = mw2er_targeting_draw_acquisition(
            point.x, point.y, radius, color, panel_scale, progress,
            mw2er_config().hud_targeting_animation_turns,
            mw2er_config().hud_radar_stroke_width * (float)panel_scale,
            palette, width, height);
        // Publish brackets only after successfully drawing the aligned square.
        if (drawn && progress >= 1.0) acquisition.phase = AcquisitionPhase::Finishing;
        return drawn;
    }
    acquisition.phase = AcquisitionPhase::Complete;
    return mw2er_targeting_draw_bracket(
        point.x, point.y, radius, color, panel_scale,
        screen, palette, width, height);
}

static int draw_htal(int width, int height, const uint8_t *palette)
{
    if (!g_hud.htal_visible || g_hud.mfd_mode != 2 || g_hud.phase != 2)
        return 1;
    const Transform transform = mfd_content_transform(width, height);
    static std::vector<Mw2erHudVertex> vertices;
    if (vertices.capacity() < 256) vertices.reserve(256);
    vertices.clear();
    const int enhanced = _stricmp(mw2er_config().hud_htal_meters, "enhanced") == 0;
    for (int i = 0; i < g_hud.htal_meter_count; ++i) {
        append_bar_meter(vertices, palette, g_hud.htal_meters[i],
                         g_hud.htal_clip, transform, enhanced);
    }
    if (!mw2er_config().hud_alt_htal_view)
        set_text_scissor(pixel_rect(g_hud.mfd.pane,
            base_transform(g_hud.mfd.pane, 1, width, height)), width, height);
    else
        glDisable(GL_SCISSOR_TEST);
    if (!submit_meter_vertices(vertices, width, height)) return 0;
    const int size_px = hud_font_size(height);
    for (int i = 0; i < g_hud.htal_text_count; ++i) {
        const PanelText &entry = g_hud.htal_texts[i];
        const float x = (float)(transform.ox + entry.x * transform.sx);
        const float y = (float)(transform.oy + entry.y * transform.sy);
        if (!draw_text_at(entry.slot, entry.text, entry.color,
                          entry.horizontal, 0, x, y, size_px,
                          width, height, palette)) return 0;
    }
    glDisable(GL_SCISSOR_TEST);
    return 1;
}

static int draw_damage_wireframe(int width, int height, const uint8_t *palette)
{
    if (!g_hud.mfd.visible || g_hud.phase != 2 || g_hud.mfd_mode > 2 ||
        !g_hud.damage_draw_count || !g_hud.damage_cpu_key) return 1;
    if (g_hud.damage_gpu_key != g_hud.damage_cpu_key) {
        if (g_hud.damage_texture) glDeleteTextures(1, &g_hud.damage_texture);
        g_hud.damage_texture = mw2er_gl_upload_indexed_sprite(g_hud.damage_sprite);
        if (!g_hud.damage_texture) return 0;
        g_hud.damage_gpu_key = g_hud.damage_cpu_key;
    }
    const Rect &pane = g_hud.mfd.pane;
    const Transform frame = base_transform(pane, 1, width, height);
    const DamageDraw &base = g_hud.damage_draws[0];
    const double sprite_left = base.x + g_hud.damage_sprite.x_offset;
    const double sprite_top = base.y + g_hud.damage_sprite.y_offset;
    const double sprite_right = sprite_left + g_hud.damage_sprite.width;
    const double sprite_bottom = sprite_top + g_hud.damage_sprite.height;
    const double center_x = (pane.left + pane.right) * 0.5;
    const double center_y = (pane.top + pane.bottom) * 0.5;
    double scale = mw2er_config().hud_damage_wireframe_scale;
    if (scale == 0) {
        const int requested = std::max(1,
            (int)std::floor(resolved_scale(
                height, mw2er_config().hud_viewport_scaling) + 0.5));
        double max_fit = requested;
        const double left = std::max((double)pane.left, sprite_left);
        const double top = std::max((double)pane.top, sprite_top);
        const double right = std::min((double)pane.right, sprite_right);
        const double bottom = std::min((double)pane.bottom, sprite_bottom);
        if (left < center_x)
            max_fit = std::min(max_fit,
                (center_x - pane.left) * frame.sx / (center_x - left));
        if (right > center_x)
            max_fit = std::min(max_fit,
                (pane.right - center_x) * frame.sx / (right - center_x));
        if (top < center_y)
            max_fit = std::min(max_fit,
                (center_y - pane.top) * frame.sy / (center_y - top));
        if (bottom > center_y)
            max_fit = std::min(max_fit,
                (pane.bottom - center_y) * frame.sy / (bottom - center_y));
        scale = std::max(1, std::min(requested, (int)max_fit));
    }
    double origin_x;
    double origin_y;
    if (g_hud.damage_aligned) {
        const Transform content = mfd_content_transform(width, height);
        const double target_x = content.ox + g_hud.damage_center_x * content.sx;
        const double target_bottom = content.oy +
            g_hud.damage_bottom_y * content.sy - 10.0 * content.sy;
        origin_x = std::nearbyint(target_x -
            (sprite_left + g_hud.damage_sprite.width * 0.5) * scale);
        origin_y = std::nearbyint(target_bottom - sprite_bottom * scale);
    } else {
        const double final_x = frame.ox + center_x * frame.sx;
        const double final_y = frame.oy + center_y * frame.sy;
        origin_x = std::nearbyint(final_x - center_x * scale);
        origin_y = std::nearbyint(final_y - center_y * scale);
    }
    Mw2erIndexedSpriteDraw commands[MAX_DAMAGE_DRAWS];
    for (int i = 0; i < g_hud.damage_draw_count; ++i) {
        const DamageDraw &draw = g_hud.damage_draws[i];
        Mw2erIndexedSpriteDraw &command = commands[i];
        command.x = (float)(origin_x + draw.x * scale);
        command.y = (float)(origin_y + draw.y * scale);
        command.scale_x = (float)scale;
        command.scale_y = (float)scale;
        command.clip_left = (int)std::floor(origin_x + draw.clip.left * scale);
        command.clip_top = (int)std::floor(origin_y + draw.clip.top * scale);
        command.clip_right = (int)std::ceil(origin_x + draw.clip.right * scale);
        command.clip_bottom = (int)std::ceil(origin_y + draw.clip.bottom * scale);
        command.color_override = draw.color_override;
    }
    return mw2er_gl_draw_indexed_sprites(
        g_hud.damage_sprite, g_hud.damage_texture,
        commands, g_hud.damage_draw_count, palette, width, height) == MW2ER_OK;
}

static int ensure_video_noise_texture()
{
    VideoNoiseState &noise = g_hud.video_noise;
    if (noise.gpu_shape == noise.cpu_shape &&
        noise.gpu_frame == noise.cpu_frame && noise.texture)
        return 1;
    if (noise.texture && noise.gpu_width == noise.sprite.width &&
        noise.gpu_height == noise.sprite.height) {
        glActiveTexture(GL_TEXTURE2);
        glBindTexture(GL_TEXTURE_2D, noise.texture);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0,
                        noise.sprite.width, noise.sprite.height,
                        GL_RG, GL_UNSIGNED_BYTE, noise.sprite.pixels.data());
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
        glBindTexture(GL_TEXTURE_2D, 0);
    } else {
        if (noise.texture) glDeleteTextures(1, &noise.texture);
        noise.texture = mw2er_gl_upload_indexed_sprite(noise.sprite);
        if (!noise.texture) return 0;
        noise.gpu_width = noise.sprite.width;
        noise.gpu_height = noise.sprite.height;
    }
    noise.gpu_shape = noise.cpu_shape;
    noise.gpu_frame = noise.cpu_frame;
    return 1;
}

static const VideoNoiseDraw *video_noise_draw(int panel_id)
{
    const VideoNoiseState &noise = g_hud.video_noise;
    for (int i = 0; i < noise.draw_count; ++i)
        if (noise.draws[i].panel_id == panel_id) return &noise.draws[i];
    return nullptr;
}

static int draw_video_noise(int panel_id, const Transform &transform,
                            int width, int height, const uint8_t *palette)
{
    VideoNoiseState &noise = g_hud.video_noise;
    if (!noise.ready) return 1;
    const VideoNoiseDraw *entry = video_noise_draw(panel_id);
    if (!entry) return 1;
    if (!ensure_video_noise_texture()) return 0;
    const Rect clip = pixel_rect(entry->pane, transform);
    Mw2erIndexedSpriteDraw draw = {};
    draw.x = (float)(transform.ox + entry->pane.left * transform.sx);
    draw.y = (float)(transform.oy + entry->pane.top * transform.sy);
    draw.scale_x = (float)transform.sx;
    draw.scale_y = (float)transform.sy;
    draw.clip_left = clip.left;
    draw.clip_top = clip.top;
    draw.clip_right = clip.right;
    draw.clip_bottom = clip.bottom;
    draw.color_override = -1;
    return mw2er_gl_draw_indexed_sprites(
        noise.sprite, noise.texture, &draw, 1, palette, width, height) == MW2ER_OK;
}

static int draw_mfd_camera_label(const Rect &pane,
                                 const Transform &frame_transform,
                                 int width, int height,
                                 const uint8_t *palette)
{
    if (g_hud.phase != 2 || g_hud.mfd_mode < 3 || g_hud.mfd_mode > 5)
        return 1;
    static const char *labels[3] = {"rear", "down", "wpn"};
    const int slot = 101 + g_hud.mfd_mode - 3;
    const char *label = labels[g_hud.mfd_mode - 3];
    const int size_px = hud_font_size(height);
    const float x = (float)(frame_transform.ox +
        (pane.left + pane.right) * 0.5 * frame_transform.sx);
    const float y = (float)(frame_transform.oy +
        (pane.bottom + 9.0) * frame_transform.sy);
    glDisable(GL_SCISSOR_TEST);
    return draw_text_at(slot, label, 0x06, TEXT_CENTER, 0, x, y,
                        size_px, width, height, palette);
}

} // namespace

int mw2er_hud_submit_rects(const Mw2erHudVertex *vertices, int32_t count,
                           int32_t width, int32_t height)
{
    if (count <= 0) return 1;
    if (!vertices || count > 512 || width <= 0 || height <= 0) {
        mw2er_set_error("HUD rectangle vertex capacity exceeded");
        return 0;
    }
    if (!ensure_meter_resources()) return 0;
    g_meter_program.use();
    g_meter_program.set2("u_viewport_size", (float)width, (float)height);
    glBindVertexArray(g_meter_vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_meter_vbo);
    glBufferSubData(GL_ARRAY_BUFFER, 0,
                    (size_t)count * sizeof(Mw2erHudVertex), vertices);
    glDrawArrays(GL_TRIANGLES, 0, count);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);
    glUseProgram(0);
    return 1;
}

void mw2er_hud_mission_reset(void)
{
    mw2er_menu_mission_reset();
    mw2er_radar_mission_reset();
    mw2er_compass_altimeter_reset();
    mw2er_targeting_mission_reset();
    mw2er_font_clear_slots();
    const AuxTarget target_gpu = g_hud.target_gpu;
    const AuxTarget mfd_gpu = g_hud.mfd_gpu;
    const GLuint damage_texture = g_hud.damage_texture;
    const GLuint video_noise_texture = g_hud.video_noise.texture;
    g_hud = {};
    g_hud.target_gpu = target_gpu;
    g_hud.mfd_gpu = mfd_gpu;
    g_hud.damage_texture = damage_texture;
    g_hud.video_noise.texture = video_noise_texture;
    g_hud.video_noise.cpu_frame = -1;
    g_hud.video_noise.gpu_frame = -1;
    g_hud.armed = 1;
    g_hud.previous_mode = -1;
    g_satellite_damage_latch = {};
}

void mw2er_hud_gl_reset(void)
{
    mw2er_menu_gl_reset();
    mw2er_radar_gl_reset();
    mw2er_targeting_gl_reset();
    delete_target(g_hud.target_gpu);
    delete_target(g_hud.mfd_gpu);
    if (g_hud.damage_texture) glDeleteTextures(1, &g_hud.damage_texture);
    if (g_hud.video_noise.texture)
        glDeleteTextures(1, &g_hud.video_noise.texture);
    if (g_meter_vbo) glDeleteBuffers(1, &g_meter_vbo);
    if (g_meter_vao) glDeleteVertexArrays(1, &g_meter_vao);
    g_meter_program.destroy();
    g_meter_vbo = 0;
    g_meter_vao = 0;
    g_hud.damage_texture = 0;
    g_hud.damage_gpu_key = 0;
    g_hud.video_noise.texture = 0;
    g_hud.video_noise.gpu_shape = 0;
    g_hud.video_noise.gpu_frame = -1;
}

void mw2er_hud_capture_late(const Mw2erMemoryView &view)
{
    const Mem mem = Mem::from(view);
    g_satellite_damage_latch = {};
    if (!mem.ok()) return;
    resolve_video_noise(mem);
    /* The native satellite swapper publishes this window after the scene
     * hook, so latch it here for the following enhanced frame. */
    if (mem.u8_rel(ADDR_RADAR_MODE) != 4 ||
        mem.u8_rel(ADDR_SATELLITE_DAMAGE_STATE) != 2 ||
        mem.i32_rel(ADDR_SATELLITE_DAMAGE_WINDOW_VALID) == 0)
        return;
    int32_t viewport[4] = {
        mem.i32_rel(ADDR_SATELLITE_DAMAGE_WINDOW + 4),
        mem.i32_rel(ADDR_SATELLITE_DAMAGE_WINDOW + 8),
        mem.i32_rel(ADDR_SATELLITE_DAMAGE_WINDOW + 12),
        mem.i32_rel(ADDR_SATELLITE_DAMAGE_WINDOW + 16),
    };
    if (viewport[2] < viewport[0] || viewport[3] < viewport[1] ||
        viewport[2] - viewport[0] + 1 > 1024 ||
        viewport[3] - viewport[1] + 1 > 768)
        return;
    g_satellite_damage_latch.active = 1;
    std::memcpy(g_satellite_damage_latch.viewport, viewport, sizeof(viewport));
}

int32_t mw2er_hud_capture_primary(const Mw2erMemoryView &view, double now,
                                  Mw2erRenderView primary_view)
{
    const Mem mem = Mem::from(view);
    g_hud.visible = 0;
    g_hud.satellite_damage = {};
    g_hud.objectives_panel = 0;
    g_hud.target = {};
    g_hud.mfd = {};
    g_hud.target_root = 0;
    g_hud.target_entity = 0;
    const TargetAcquisition previous_acquisition = g_hud.acquisition;
    g_hud.acquisition = {};
    g_hud.targeting_now = now;
    g_hud.targeting = {};
    g_hud.targeting.reticle_reference = -1;
    g_hud.target_nav_visible = 0;
    g_hud.target_nav_reference = -1;
    g_hud.meter_count = 0;
    g_hud.text_count = 0;
    g_hud.weapon_count = 0;
    g_hud.weapon_bounds = {};
    g_hud.weapon_bounds_valid = 0;
    g_hud.mfd_mode = 0;
    g_hud.target_text_count = 0;
    g_hud.htal_text_count = 0;
    g_hud.htal_meter_count = 0;
    g_hud.htal_visible = 0;
    g_hud.damage_draw_count = 0;
    g_hud.damage_aligned = 0;
    g_hud.video_noise.draw_count = 0;
    g_hud.video_noise.ready = 0;
    g_hud.heat_jump_bounds = {};
    g_hud.heat_jump_bounds_valid = 0;
    g_hud.throttle_bounds = {};
    g_hud.throttle_bounds_valid = 0;
    if (!mem.ok()) return 0;
    const int player_slot = mem.i32_rel(ADDR_PLAYER_SLOT);
    if (player_slot < 0 || player_slot >= 4096) return 0;
    const uint32_t player = mem.u32_rel(ADDR_ENTITY_TABLE + player_slot * 4u);
    const uint32_t mech = player ? mem.u32(player + 0x20) : 0;
    const uint32_t body = mech ? mem.u32(mech) : 0;
    if (!player || !mech || !body || mem.u32(body + 4) != (uint32_t)player_slot ||
        mem.u32(player + 0x38) == 0) return 0;
    const int hud_mode = mem.i32(mech + 0xA0);
    if (g_satellite_damage_latch.active && primary_view == MW2ER_VIEW_SATELLITE) {
        g_hud.satellite_damage = g_satellite_damage_latch;
        mw2er_radar_capture(mem, player_slot, player, mech, body, hud_mode,
                            2, 1.0, 0);
        return 0;
    }
    if (mem.u32_rel(ADDR_MASTER_HUD) == 0) return 0;
    set_transition(hud_mode, now);
    g_hud.previous_mode = hud_mode;
    if (g_hud.phase == 0 || hud_mode == 4 || hud_mode == 5) return 0;
    g_hud.visible = 1;
    const double radar_progress = std::clamp(
        now - g_hud.transition_start, 0.0, 1.0);
    const double radar_extent = g_hud.phase == 1 ? radar_progress
        : (g_hud.phase == 3 ? 1.0 - radar_progress : 1.0);
    mw2er_radar_capture(mem, player_slot, player, mech, body, hud_mode,
                        g_hud.phase, radar_extent, 1);
    if (hud_mode == 2)
        mw2er_compass_altimeter_capture(mem, player, mech, body);
    else
        mw2er_compass_altimeter_reset();
    if (hud_mode == 2) capture_targeting(mem, player, mech, body);
    if (g_hud.targeting.marker_visible && g_hud.targeting.marker_kind != 0x0100) {
        if (previous_acquisition.handle == g_hud.targeting.marker_handle)
            g_hud.acquisition = previous_acquisition;
        else
            g_hud.acquisition.handle = g_hud.targeting.marker_handle;
    }
    const int callback_off = hud_mode == 1 ? 0x78 : (hud_mode == 2 ? 0x7C : 0x80);
    const uint32_t target_cb = mem.rt(hud_mode == 1 ? CALLBACK_TARGET_STARTUP
        : (hud_mode == 2 ? CALLBACK_TARGET : CALLBACK_TARGET_SHUTDOWN));
    const uint32_t mfd_cb = mem.rt(hud_mode == 1 ? CALLBACK_MFD_STARTUP
        : (hud_mode == 2 ? CALLBACK_MFD : CALLBACK_MFD_SHUTDOWN));
    const uint32_t target_text_cb = mem.rt(CALLBACK_TARGET_TEXT);
    const uint32_t weapon_startup_cb = mem.rt(CALLBACK_WEAPON_STARTUP);
    const uint32_t weapon_steady_cb = mem.rt(CALLBACK_WEAPON_STEADY);
    const uint32_t weapon_base = mem.u32(mech + 0x54);
    const int active_weapon = mem.i32(mech + 0xAC);
    const int game_tick = hud_mode == 1 ? mem.i32_rel(ADDR_GAME_TICK) : 0;
    const int startup_color = hud_mode == 1
        ? mem.u8_rel(ADDR_CURRENT_HUD_TEXT_COLOR) : 0x0E;
    uint8_t meter_state[METER_STATE_SIZE];
    int meter_state_valid = 0;
    int meter_state_attempted = 0;
    uint32_t target_panel = 0;
    uint32_t mfd_panel = 0;
    const uint32_t objectives_cb = mem.rt(CALLBACK_OBJECTIVES_STATUS);
    for (int i = 0; i < PANEL_COUNT; ++i) {
        const uint32_t panel = mem.u32_rel(ADDR_PANEL_TABLE + i * 4u);
        if (!panel || mem.u16(panel + 4) == 0) continue;
        if (mem.u32(panel + 0x78) == objectives_cb ||
            mem.u32(panel + 0x7C) == objectives_cb ||
            mem.u32(panel + 0x80) == objectives_cb)
            g_hud.objectives_panel = 1;
        if ((hud_mode == 1 || hud_mode == 2) &&
            mem.u32(panel + 0x78) == weapon_startup_cb &&
            mem.u32(panel + 0x7C) == weapon_steady_cb) {
            capture_weapon_panel(mem, panel, i, weapon_base, active_weapon,
                                 hud_mode, game_tick, startup_color);
            continue;
        }
        const uint32_t callback = mem.u32(panel + callback_off);
        if (callback == target_cb) {
            target_panel = panel;
            g_hud.target.visible = 1;
            g_hud.target.pane = {32, 559, 197, 687};
            const int pane_width = g_hud.target.pane.right -
                g_hud.target.pane.left;
            const int configured_width = mem.i32(panel + 0x44);
            const int display_width = configured_width > 0 &&
                configured_width <= pane_width ? configured_width : pane_width;
            g_hud.target_nav_x = g_hud.target.pane.left +
                (display_width >> 1);
            g_hud.target_nav_y = g_hud.target.pane.top +
                ((g_hud.target.pane.bottom - g_hud.target.pane.top - 1) >> 1);
        } else if (hud_mode == 2 && callback == target_text_cb) {
            Rect text_pane;
            if (panel_rect(mem, panel, text_pane, 0))
                capture_target_text(mem, player, text_pane);
        } else if (callback == mfd_cb) {
            mfd_panel = panel;
            g_hud.mfd.visible = panel_rect(mem, panel, g_hud.mfd.pane, 1);
        } else if (hud_mode == 2 && is_power_callback(mem, callback)) {
            if (!meter_state_attempted) {
                meter_state_attempted = 1;
                meter_state_valid = mem.read_rel(
                    ADDR_METER_STATE, meter_state, sizeof(meter_state));
            }
            if (meter_state_valid)
                capture_power_meter(
                    mem, meter_state, player, mech, panel, i, callback);
        }
    }
    g_hud.target_display = mem.i32_rel(ADDR_TARGET_DISPLAY);
    if (!g_hud.target.visible || g_hud.target_display == 0) g_hud.target.visible = 0;
    const int target_damaged = g_hud.target.visible && target_panel &&
        g_hud.phase == 2 &&
        substitute_damaged_video(mem, target_panel, g_hud.target.pane,
                                 ADDR_TARGET_GLITCH_LATCH, 0);

    if (g_hud.target.visible && hud_mode == 2 && !target_damaged) {
        const uint32_t handle = mem.u32(player + 0xDC);
        const uint32_t kind = handle & 0xF00u;
        const uint32_t index = handle & 0xFFu;
        if ((handle & 0x1000u) == 0 && (mem.u8(player + 0xDD) & 0x10) == 0 &&
            (g_hud.target_display == 1 || g_hud.target_display == 2)) {
            int32_t x = mem.i32(player + 0xC8), y = mem.i32(player + 0xCC);
            int32_t z = mem.i32(player + 0xD0), yaw = mem.i32(player + 0xD4);
            int32_t radius = 0;
            if (kind == 0x100) {
                const int flags = mem.u16_rel(
                    ADDR_DIRECT_TARGETS + index * 0x54u + 0x24);
                const int resource = ((flags & 0x20) ? 0x103 : 0x100) +
                    mem.u8_rel(ADDR_HUD_STYLE_OFFSET);
                g_hud.target_nav_reference =
                    mw2er_targeting_capture_sprite(mem, resource);
                g_hud.target_nav_visible = g_hud.target_nav_reference >= 0;
            } else if (kind == 0x200) {
                const uint32_t entity = mem.u32_rel(ADDR_ENTITY_TABLE + index * 4u);
                const uint32_t object = entity ? mem.u32(entity + 0x20) : 0;
                g_hud.target_entity = entity;
                g_hud.target_root = entity ? mem.u32(entity + 0x40) : 0;
                radius = object ? std::abs(mem.i32(object + 0xE8)) : 0;
            } else if (kind == 0x400) {
                const int resource = mem.i32_rel(ADDR_SECONDARY_TABLE + index * 0x40u + 4);
                const int count = mem.i32_rel(ADDR_SECONDARY_COUNT);
                if (resource >= 0 && resource < count) {
                    g_hud.target_root = mem.u32_rel(ADDR_SECONDARY_POSITIONS + resource * 0x7Cu + 0x20);
                    const uint32_t model = g_hud.target_root ? mem.u32(g_hud.target_root + 0x6C) : 0;
                    if (model) {
                        x = mem.i32(model + 0x34); y = mem.i32(model + 0x38);
                        z = mem.i32(model + 0x3C); radius = std::abs(mem.i32(model + 0x40));
                    }
                }
            }
            if (g_hud.target_root && radius > 0) {
                const double a = yaw * (6.283185307179586 / k_turn);
                const int64_t distance = (int64_t)radius * 3;
                const int32_t sx = (int32_t)std::llround(std::sin(a) * k_fp29);
                const int32_t cz = (int32_t)std::llround(std::cos(a) * k_fp29);
                auto mul = [](int32_t v, int64_t s) {
                    const int64_t p = (int64_t)v * s;
                    return (int32_t)(p >= 0 ? (p + (1ll << 28)) >> 29
                        : -(((-p) + (1ll << 28)) >> 29));
                };
                camera_from_pose(g_hud.target.camera, x - mul(sx, distance), y,
                                 z - mul(cz, distance), yaw, 0, 0);
                pane_camera(g_hud.target.camera, mem, g_hud.target.pane);
                g_hud.target.camera.far_plane = std::max(
                    g_hud.target.camera.near_plane * 2.0f,
                    (float)(radius / k_fixed * 8.0));
                /* Selector 1 is the damage wireframe; selector 2 uses the
                 * target-local flat material conversion during extraction. */
                g_hud.target.camera.imaging_active = g_hud.target_display == 1;
                g_hud.target.camera.imaging_wireframe = g_hud.target_display == 1;
                g_hud.target.image = 1;
            }
        }
    }

    const int mfd_mode = mem.i32_rel(ADDR_MFD_MODE);
    g_hud.mfd_mode = mfd_mode;
    if (mfd_mode < 1 || mfd_mode > 5) g_hud.mfd.visible = 0;
    const int mfd_damaged = g_hud.mfd.visible && mfd_panel &&
        mfd_mode >= 3 && mfd_mode <= 5 &&
        substitute_damaged_video(mem, mfd_panel, g_hud.mfd.pane,
                                 ADDR_MFD_GLITCH_LATCH, 1);
    if (g_hud.mfd.visible && g_hud.phase == 2 && mfd_mode <= 2) {
        if (mfd_mode == 1 || mw2er_config().hud_alt_htal_view)
            capture_damage_wireframe(mem, mech, g_hud.mfd.pane);
        if (mfd_mode == 2) capture_htal(mem, mech, g_hud.mfd.pane);
    }
    if (g_hud.mfd.visible && mfd_mode >= 3 && mfd_mode <= 5 && !mfd_damaged) {
        const uint32_t active = mem.u32_rel(ADDR_ACTIVE_CAMERA);
        if (active) {
            int32_t x = mem.i32(active), y = mem.i32(active + 4), z = mem.i32(active + 8);
            int32_t yaw = mem.i32(active + 12), roll = mem.i32(active + 20);
            if (mfd_mode == 3) {
                const int32_t body_yaw = mem.i32(body + 0x60);
                yaw = mem.i32_rel(ADDR_MFD_REAR_SPECIAL) ? body_yaw + mem.i32(body + 0x6C)
                                                        : body_yaw + 0x00B40000;
                camera_from_pose(g_hud.mfd.camera, x, y, z, yaw, 0, roll);
                g_hud.mfd.mirror = mw2er_config().rear_camera_mirror;
                g_hud.mfd.image = 1;
            } else if (mfd_mode == 4) {
                camera_from_pose(g_hud.mfd.camera, mem.i32(body + 0x50),
                    mem.i32(body + 0x54), mem.i32(body + 0x58), yaw, 0x005A0000, 0);
                g_hud.mfd.image = 1;
            } else {
                const int slot = mem.i32_rel(ADDR_WEAPON_SLOT);
                if (slot >= 0 && mem.i32_rel(ADDR_WEAPON_STATE + slot * 80u + 0x6C)) {
                    camera_from_pose(g_hud.mfd.camera,
                        mem.i32_rel(ADDR_WEAPON_STATE), mem.i32_rel(ADDR_WEAPON_STATE + 4),
                        mem.i32_rel(ADDR_WEAPON_STATE + 8), mem.i32_rel(ADDR_WEAPON_STATE + 12),
                        0, mem.i32_rel(ADDR_WEAPON_STATE + 20));
                    g_hud.mfd.image = 1;
                }
            }
            if (g_hud.mfd.image) pane_camera(g_hud.mfd.camera, mem, g_hud.mfd.pane);
        }
    }
    return 0;
}

int32_t mw2er_hud_capture_target(const Mw2erMemoryView &)
{
    if (!g_hud.target.image || !g_hud.target_root) return 0;
    return mw2er_scene_capture_target(
        g_hud.target_root, g_hud.target_entity, g_hud.target_display,
        &g_hud.target.camera);
}

int mw2er_hud_target_display_mode(void) { return g_hud.target_display; }

Mw2erRenderView mw2er_hud_mfd_render_view(void)
{
    static constexpr Mw2erRenderView views[] = {
        MW2ER_VIEW_MFD_REAR, MW2ER_VIEW_MFD_DOWN, MW2ER_VIEW_MFD_WEAPON};
    return g_hud.mfd.image ? views[g_hud.mfd_mode - 3] : MW2ER_VIEW_NONE;
}

int mw2er_hud_objectives_available(void)
{
    return g_hud.visible && g_hud.phase == 2 && g_hud.objectives_panel;
}

int mw2er_hud_satellite_damage_viewport(int32_t viewport[4])
{
    if (!viewport || !g_hud.satellite_damage.active) return 0;
    std::memcpy(viewport, g_hud.satellite_damage.viewport,
                sizeof(g_hud.satellite_damage.viewport));
    return 1;
}

int mw2er_hud_render_satellite_damage_radar(int width, int height)
{
    Mw2erSceneExtract *scene = mw2er_scene_extract_current();
    return scene && mw2er_radar_render(
        width, height, scene->palette_rgb, 0);
}

int32_t mw2er_hud_render(uint32_t overlay, int width, int height, int sample)
{
    glBindFramebuffer(GL_FRAMEBUFFER, overlay);
    glViewport(0, 0, width, height);
    glDisable(GL_DEPTH_TEST); glDisable(GL_CULL_FACE); glDisable(GL_BLEND);
    glDisable(GL_SCISSOR_TEST);
    glClearColor(0, 0, 0, 0);
    glClear(GL_COLOR_BUFFER_BIT);
    Mw2erSceneExtract *scene = mw2er_scene_extract_current();
    if (!scene) return 0;
    const uint8_t *palette = scene->palette_rgb;
    if (g_hud.satellite_damage.active) {
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        return 0;
    }
    if (!g_hud.visible)
        return mw2er_menu_render(width, height, palette);
    if (!mw2er_radar_render(width, height, palette, 1)) {
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        return MW2ER_ERR_GL;
    }
    const View *views[2] = {&g_hud.target, &g_hud.mfd};
    AuxTarget *targets[2] = {&g_hud.target_gpu, &g_hud.mfd_gpu};
    const int colors[2] = {0x08, 0x06};
    for (int i = 0; i < 2; ++i) {
        const View &v = *views[i];
        if (!v.visible) continue;
        if (i == 1 && g_hud.mfd_mode <= 2) continue;
        if (g_hud.extent_x <= 0.0 && g_hud.extent_y <= 0.0) continue;
        const Transform base = base_transform(v.pane, i, width, height);
        const Transform frame = animated(v.pane, base);
        const Rect dest = pixel_rect(v.pane, frame);
        const int damaged_video = video_noise_draw(i) != nullptr;
        if (!damaged_video) fill_rect(dest, height, palette, 0);
        const int steady_target = i != 0 || g_hud.phase == 2;
        if (v.image && steady_target) {
            render_view(v, *targets[i], i == 0, overlay,
                        width, height, sample, dest);
        }
        glBindFramebuffer(GL_FRAMEBUFFER, overlay);
        glViewport(0, 0, width, height);
        if (!draw_video_noise(i, frame, width, height, palette)) {
            glBindFramebuffer(GL_FRAMEBUFFER, 0);
            return MW2ER_ERR_GL;
        }
        if (i == 0 && !draw_target_nav_sprite(
                width, height, palette, frame, dest)) {
            glBindFramebuffer(GL_FRAMEBUFFER, 0);
            return MW2ER_ERR_GL;
        }
        if (!damaged_video) {
            const int line = std::max(1,
                (int)std::llround(std::min(frame.sx, frame.sy)));
            border(dest, height, palette, colors[i], line);
        }
        if (i == 1 && !draw_mfd_camera_label(v.pane, frame, width, height, palette)) {
            glBindFramebuffer(GL_FRAMEBUFFER, 0);
            return MW2ER_ERR_GL;
        }
    }
    glDisable(GL_SCISSOR_TEST);
    if (!mw2er_compass_altimeter_render(width, height, palette)) {
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        return MW2ER_ERR_GL;
    }
    if (!draw_power_meters(width, height, palette)) {
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        return MW2ER_ERR_GL;
    }
    if (!draw_hud_weapons_panel(width, height, palette) ||
        !draw_htal(width, height, palette) ||
        !draw_target_texts(width, height, palette)) {
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        return MW2ER_ERR_GL;
    }
    if (!draw_meter_texts(width, height, palette)) {
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        return MW2ER_ERR_GL;
    }
    if (!draw_damage_wireframe(width, height, palette)) {
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        return MW2ER_ERR_GL;
    }
    if (!draw_targeting(width, height, palette, scene->camera)) {
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        return MW2ER_ERR_GL;
    }
    if (mw2er_menu_render(width, height, palette) != MW2ER_OK) {
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        return MW2ER_ERR_GL;
    }
    glDisable(GL_SCISSOR_TEST);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    return 0;
}
