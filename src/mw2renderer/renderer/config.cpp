#include "config.h"
#include "mw2er_internal.h"

#include <algorithm>
#include <cmath>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static Mw2erRendererConfig g_cfg;

static void set_defaults(Mw2erRendererConfig &c)
{
    memset(&c, 0, sizeof(c));
    snprintf(c.antialiasing, sizeof(c.antialiasing), "ssaa_4x");
    c.ssaa_line_width = 1.0f;
    c.max_horizontal_fov_degrees = 105.0f;
    snprintf(c.entity_lod_selection, sizeof(c.entity_lod_selection), "projected_size");
    c.entity_lod_detail0_pixels = 16.0f;
    c.entity_lod_detail1_pixels = 10.0f;
    c.entity_lod_detail2_pixels = 5.0f;
    c.entity_lod_hysteresis = 0.12f;
    c.enable_diagnostic_logging = 0;
    c.load_resources_from_prj = 1;
    c.enhanced_enhanced_imaging = 1;
    c.enhanced_imaging_distance_ratio = 1.5f;
    c.enhanced_imaging_reveal_time = 0.3f;
    c.reduce_terrain_gaps = 1;
    c.enhanced_heli_rotors = 1;
    c.enhanced_aero_lift_fans = 1;
    c.enhanced_mech_textures = 1;
    c.enhanced_dropship_textures = 1;
    c.enhanced_mech_texture_uv_scale = 2.0f;
    c.enhanced_dropship_texture_uv_scale = 2.0f;
    c.hud_position_scaling = 1.0f;
    c.hud_panel_scaling = 0.6f;
    c.hud_viewport_scaling = 1.0f;
    c.hud_font_scaling = 0.6f;
    c.hud_target_marker_scaling = 0.0f;
    c.hud_damage_wireframe_scale = 0;
    c.hud_middle_panel_vertical_position = 0.550781f;
    c.hud_top_widescreen_position = 0.5f;
    c.hud_middle_widescreen_position = 0.25f;
    c.hud_bottom_widescreen_position = 0.0f;
    snprintf(c.hud_compass_altimeter, sizeof(c.hud_compass_altimeter), "enhanced");
    snprintf(c.hud_power_meters, sizeof(c.hud_power_meters), "enhanced");
    c.hud_meter_dark_offset = -2;
    c.hud_meter_light_offset = -1;
    c.hud_meter_peak_offset = 0;
    c.hud_meter_peak_position = 0.375f;
    c.hud_alt_throttle_indicator_position = 1;
    snprintf(c.hud_htal_meters, sizeof(c.hud_htal_meters), "enhanced");
    c.hud_alt_htal_view = 1;
    c.rear_camera_mirror = 1;
    c.hud_radar_stroke_width = 1.5f;
    c.hud_targeting_animation_trail_ms = 15.0f;
    c.hud_targeting_animation_duration = 0.33f;
    c.hud_targeting_animation_turns = 0.25f;
}

void mw2er_config_reset_defaults(void)
{
    set_defaults(g_cfg);
}

static void trim(char *s)
{
    char *e;
    while (*s == ' ' || *s == '\t' || *s == '\r') {
        memmove(s, s + 1, strlen(s));
    }
    e = s + strlen(s);
    while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r' || e[-1] == '\n')) {
        *--e = '\0';
    }
    if (s[0] == '"' && e > s + 1 && e[-1] == '"') {
        e[-1] = '\0';
        memmove(s, s + 1, strlen(s));
    }
}

static int parse_bool(const char *v, int fallback)
{
    if (_stricmp(v, "true") == 0 || _stricmp(v, "1") == 0 ||
        _stricmp(v, "yes") == 0 || _stricmp(v, "on") == 0) {
        return 1;
    }
    if (_stricmp(v, "false") == 0 || _stricmp(v, "0") == 0 ||
        _stricmp(v, "no") == 0 || _stricmp(v, "off") == 0) {
        return 0;
    }
    return fallback;
}

static float clampf(float v, float lo, float hi)
{
    if (v < lo) {
        return lo;
    }
    if (v > hi) {
        return hi;
    }
    return v;
}

static void clamp_loaded(void)
{
    if (_stricmp(g_cfg.antialiasing, "ssaa_4x") != 0 &&
        _stricmp(g_cfg.antialiasing, "none") != 0) {
        snprintf(g_cfg.antialiasing, sizeof(g_cfg.antialiasing), "ssaa_4x");
    }
    g_cfg.ssaa_line_width = clampf(g_cfg.ssaa_line_width, 0.5f, 8.0f);
    g_cfg.max_horizontal_fov_degrees =
        clampf(g_cfg.max_horizontal_fov_degrees, 30.0f, 170.0f);
    g_cfg.entity_lod_detail0_pixels =
        clampf(g_cfg.entity_lod_detail0_pixels, 0.25f, 4096.0f);
    g_cfg.entity_lod_detail1_pixels =
        clampf(g_cfg.entity_lod_detail1_pixels, 0.25f, 4096.0f);
    g_cfg.entity_lod_detail2_pixels =
        clampf(g_cfg.entity_lod_detail2_pixels, 0.25f, 4096.0f);
    g_cfg.entity_lod_hysteresis =
        clampf(g_cfg.entity_lod_hysteresis, 0.0f, 0.45f);
    g_cfg.enhanced_imaging_distance_ratio =
        clampf(g_cfg.enhanced_imaging_distance_ratio, 0.1f, 10.0f);
    g_cfg.enhanced_imaging_reveal_time =
        clampf(g_cfg.enhanced_imaging_reveal_time, 0.0f, 30.0f);
    if (g_cfg.entity_lod_selection[0] == '\0') {
        snprintf(
            g_cfg.entity_lod_selection,
            sizeof(g_cfg.entity_lod_selection),
            "projected_size");
    }
    g_cfg.enhanced_mech_texture_uv_scale =
        clampf(g_cfg.enhanced_mech_texture_uv_scale, 0.125f, 16.0f);
    g_cfg.enhanced_dropship_texture_uv_scale =
        clampf(g_cfg.enhanced_dropship_texture_uv_scale, 0.125f, 16.0f);
    g_cfg.hud_position_scaling = clampf(g_cfg.hud_position_scaling, 0.0f, 1.0f);
    g_cfg.hud_panel_scaling = clampf(g_cfg.hud_panel_scaling, 0.0f, 1.0f);
    g_cfg.hud_viewport_scaling = clampf(g_cfg.hud_viewport_scaling, 0.0f, 1.0f);
    g_cfg.hud_font_scaling = clampf(g_cfg.hud_font_scaling, 0.0f, 1.0f);
    g_cfg.hud_target_marker_scaling =
        clampf(g_cfg.hud_target_marker_scaling, 0.0f, 1.0f);
    g_cfg.hud_middle_panel_vertical_position =
        clampf(g_cfg.hud_middle_panel_vertical_position, 0.0f, 1.0f);
    g_cfg.hud_top_widescreen_position =
        clampf(g_cfg.hud_top_widescreen_position, 0.0f, 1.0f);
    g_cfg.hud_middle_widescreen_position =
        clampf(g_cfg.hud_middle_widescreen_position, 0.0f, 1.0f);
    g_cfg.hud_bottom_widescreen_position =
        clampf(g_cfg.hud_bottom_widescreen_position, 0.0f, 1.0f);
    if (_stricmp(g_cfg.hud_compass_altimeter, "native") != 0 &&
        _stricmp(g_cfg.hud_compass_altimeter, "enhanced") != 0) {
        snprintf(g_cfg.hud_compass_altimeter,
                 sizeof(g_cfg.hud_compass_altimeter), "enhanced");
    }
    if (_stricmp(g_cfg.hud_power_meters, "native") != 0 &&
        _stricmp(g_cfg.hud_power_meters, "enhanced") != 0) {
        snprintf(g_cfg.hud_power_meters, sizeof(g_cfg.hud_power_meters), "enhanced");
    }
    g_cfg.hud_meter_dark_offset =
        std::max(-16, std::min(16, g_cfg.hud_meter_dark_offset));
    g_cfg.hud_meter_light_offset =
        std::max(-16, std::min(16, g_cfg.hud_meter_light_offset));
    g_cfg.hud_meter_peak_offset =
        std::max(-16, std::min(16, g_cfg.hud_meter_peak_offset));
    g_cfg.hud_meter_peak_position =
        clampf(g_cfg.hud_meter_peak_position, 0.1f, 0.9f);
    g_cfg.hud_targeting_animation_trail_ms = std::isfinite(g_cfg.hud_targeting_animation_trail_ms)
        ? clampf(g_cfg.hud_targeting_animation_trail_ms, 0.0f, 250.0f) : 15.0f;
    g_cfg.hud_targeting_animation_duration = std::isfinite(g_cfg.hud_targeting_animation_duration)
        ? clampf(g_cfg.hud_targeting_animation_duration, 0.0f, 5.0f) : 0.33f;
    g_cfg.hud_targeting_animation_turns = std::isfinite(g_cfg.hud_targeting_animation_turns)
        ? std::floor(clampf(g_cfg.hud_targeting_animation_turns, 0.0f, 10.0f) * 4.0f + 0.5f) / 4.0f
        : 0.25f;
    g_cfg.hud_radar_stroke_width =
        clampf(g_cfg.hud_radar_stroke_width, 0.5f, 8.0f);
    if (_stricmp(g_cfg.hud_htal_meters, "native") != 0 &&
        _stricmp(g_cfg.hud_htal_meters, "enhanced") != 0) {
        snprintf(g_cfg.hud_htal_meters, sizeof(g_cfg.hud_htal_meters), "enhanced");
    }
}

static void apply_key(const char *key, const char *value)
{
    if (strcmp(key, "antialiasing") == 0) {
        snprintf(g_cfg.antialiasing, sizeof(g_cfg.antialiasing), "%s", value);
    } else if (strcmp(key, "ssaa_line_width") == 0) {
        g_cfg.ssaa_line_width = (float)atof(value);
    } else if (strcmp(key, "max_horizontal_fov_degrees") == 0) {
        g_cfg.max_horizontal_fov_degrees = (float)atof(value);
    } else if (strcmp(key, "entity_lod_selection") == 0) {
        snprintf(
            g_cfg.entity_lod_selection,
            sizeof(g_cfg.entity_lod_selection),
            "%s",
            value);
    } else if (strcmp(key, "entity_lod_detail0_pixels") == 0) {
        g_cfg.entity_lod_detail0_pixels = (float)atof(value);
    } else if (strcmp(key, "entity_lod_detail1_pixels") == 0) {
        g_cfg.entity_lod_detail1_pixels = (float)atof(value);
    } else if (strcmp(key, "entity_lod_detail2_pixels") == 0) {
        g_cfg.entity_lod_detail2_pixels = (float)atof(value);
    } else if (strcmp(key, "entity_lod_hysteresis") == 0) {
        g_cfg.entity_lod_hysteresis = (float)atof(value);
    } else if (strcmp(key, "enable_diagnostic_logging") == 0) {
        g_cfg.enable_diagnostic_logging =
            parse_bool(value, g_cfg.enable_diagnostic_logging);
    } else if (strcmp(key, "load_resources_from_prj") == 0) {
        g_cfg.load_resources_from_prj =
            parse_bool(value, g_cfg.load_resources_from_prj);
    } else if (strcmp(key, "enhanced_enhanced_imaging") == 0) {
        g_cfg.enhanced_enhanced_imaging =
            parse_bool(value, g_cfg.enhanced_enhanced_imaging);
    } else if (strcmp(key, "enhanced_imaging_distance_ratio") == 0) {
        g_cfg.enhanced_imaging_distance_ratio = (float)atof(value);
    } else if (strcmp(key, "enhanced_imaging_reveal_time") == 0) {
        g_cfg.enhanced_imaging_reveal_time = (float)atof(value);
    } else if (strcmp(key, "reduce_terrain_gaps") == 0) {
        g_cfg.reduce_terrain_gaps = parse_bool(value, g_cfg.reduce_terrain_gaps);
    } else if (strcmp(key, "enhanced_heli_rotors") == 0) {
        g_cfg.enhanced_heli_rotors = parse_bool(value, g_cfg.enhanced_heli_rotors);
    } else if (strcmp(key, "enhanced_aero_lift_fans") == 0) {
        g_cfg.enhanced_aero_lift_fans =
            parse_bool(value, g_cfg.enhanced_aero_lift_fans);
    } else if (strcmp(key, "enhanced_mech_textures") == 0) {
        g_cfg.enhanced_mech_textures =
            parse_bool(value, g_cfg.enhanced_mech_textures);
    } else if (strcmp(key, "enhanced_dropship_textures") == 0) {
        g_cfg.enhanced_dropship_textures =
            parse_bool(value, g_cfg.enhanced_dropship_textures);
    } else if (strcmp(key, "enhanced_mech_texture_uv_scale") == 0) {
        g_cfg.enhanced_mech_texture_uv_scale = (float)atof(value);
    } else if (strcmp(key, "enhanced_dropship_texture_uv_scale") == 0) {
        g_cfg.enhanced_dropship_texture_uv_scale = (float)atof(value);
    }
}

static void apply_hud_key(const char *key, const char *value)
{
    if (strcmp(key, "position_scaling") == 0) {
        g_cfg.hud_position_scaling = (float)atof(value);
    } else if (strcmp(key, "panel_scaling") == 0) {
        g_cfg.hud_panel_scaling = (float)atof(value);
    } else if (strcmp(key, "viewport_scaling") == 0) {
        g_cfg.hud_viewport_scaling = (float)atof(value);
    } else if (strcmp(key, "font_scaling") == 0) {
        g_cfg.hud_font_scaling = (float)atof(value);
    } else if (strcmp(key, "target_marker_scaling") == 0) {
        g_cfg.hud_target_marker_scaling = (float)atof(value);
    } else if (strcmp(key, "damage_wireframe_scaling") == 0) {
        g_cfg.hud_damage_wireframe_scale =
            _stricmp(value, "1x") == 0 ? 1 :
            _stricmp(value, "2x") == 0 ? 2 :
            _stricmp(value, "3x") == 0 ? 3 : 0;
    } else if (strcmp(key, "middle_panel_vertical_position") == 0) {
        g_cfg.hud_middle_panel_vertical_position = (float)atof(value);
    } else if (strcmp(key, "top_panel_widescreen_position") == 0) {
        g_cfg.hud_top_widescreen_position = (float)atof(value);
    } else if (strcmp(key, "middle_panel_widescreen_position") == 0) {
        g_cfg.hud_middle_widescreen_position = (float)atof(value);
    } else if (strcmp(key, "bottom_panel_widescreen_position") == 0 ||
               strcmp(key, "bottom_widescreen_position") == 0) {
        g_cfg.hud_bottom_widescreen_position = (float)atof(value);
    } else if (strcmp(key, "compass_altimeter") == 0) {
        snprintf(g_cfg.hud_compass_altimeter,
                 sizeof(g_cfg.hud_compass_altimeter), "%s", value);
    } else if (strcmp(key, "power_meters") == 0) {
        snprintf(g_cfg.hud_power_meters, sizeof(g_cfg.hud_power_meters), "%s", value);
    } else if (strcmp(key, "meter_dark_offset") == 0) {
        g_cfg.hud_meter_dark_offset = atoi(value);
    } else if (strcmp(key, "meter_light_offset") == 0) {
        g_cfg.hud_meter_light_offset = atoi(value);
    } else if (strcmp(key, "meter_peak_offset") == 0) {
        g_cfg.hud_meter_peak_offset = atoi(value);
    } else if (strcmp(key, "meter_peak_position") == 0) {
        g_cfg.hud_meter_peak_position = (float)atof(value);
    } else if (strcmp(key, "alt_throttle_indicator_position") == 0) {
        g_cfg.hud_alt_throttle_indicator_position =
            parse_bool(value, g_cfg.hud_alt_throttle_indicator_position);
    } else if (strcmp(key, "htal_meters") == 0) {
        snprintf(g_cfg.hud_htal_meters, sizeof(g_cfg.hud_htal_meters), "%s", value);
    } else if (strcmp(key, "alt_htal_view") == 0) {
        g_cfg.hud_alt_htal_view = parse_bool(value, g_cfg.hud_alt_htal_view);
    } else if (strcmp(key, "rear_camera_mirror") == 0) {
        g_cfg.rear_camera_mirror = parse_bool(value, g_cfg.rear_camera_mirror);
    } else if (strcmp(key, "targeting_animation_trail_ms") == 0) {
        g_cfg.hud_targeting_animation_trail_ms = (float)atof(value);
    } else if (strcmp(key, "targeting_animation_duration") == 0) {
        g_cfg.hud_targeting_animation_duration = (float)atof(value);
    } else if (strcmp(key, "targeting_animation_turns") == 0) {
        g_cfg.hud_targeting_animation_turns = (float)atof(value);
    } else if (strcmp(key, "radar_stroke_width") == 0) {
        g_cfg.hud_radar_stroke_width = (float)atof(value);
    }
}

void mw2er_config_load_file(const char *path)
{
    FILE *f;
    char line[512];
    int section = 0;

    if (path == NULL || path[0] == '\0') {
        return;
    }
    f = fopen(path, "rb");
    if (f == NULL) {
        char msg[256];
        snprintf(msg, sizeof(msg), "mw2renderer: config missing %s (using defaults)", path);
        mw2er_log(msg);
        return;
    }
    while (fgets(line, (int)sizeof(line), f)) {
        char *hash;
        char *eq;
        trim(line);
        if (line[0] == '\0') {
            continue;
        }
        hash = strchr(line, '#');
        if (hash == line) {
            continue;
        }
        if (hash) {
            *hash = '\0';
            trim(line);
        }
        if (line[0] == '[') {
            section = _stricmp(line, "[renderer]") == 0 ? 1
                : (_stricmp(line, "[HUD]") == 0 ? 2 : 0);
            continue;
        }
        if (!section) {
            continue;
        }
        eq = strchr(line, '=');
        if (eq == NULL) {
            continue;
        }
        *eq = '\0';
        trim(line);
        trim(eq + 1);
        if (section == 1) {
            apply_key(line, eq + 1);
        } else {
            apply_hud_key(line, eq + 1);
        }
    }
    fclose(f);
    clamp_loaded();
    {
        char msg[256];
        snprintf(
            msg,
            sizeof(msg),
            "mw2renderer: config %s aa=%s fov=%.1f line=%.2f gaps=%d",
            path,
            g_cfg.antialiasing,
            g_cfg.max_horizontal_fov_degrees,
            g_cfg.ssaa_line_width,
            g_cfg.reduce_terrain_gaps);
        mw2er_log(msg);
    }
}

void mw2er_config_load_from_mod_dir(const char *mod_dir)
{
    const char *satellite = getenv("MW2ER_SATELLITE");
    g_cfg.force_satellite = satellite != NULL && satellite[0] == '1';
    const char *env = getenv("MW2ER_RENDERER_CONF");
    if (env && env[0]) {
        mw2er_config_load_file(env);
        return;
    }
    if (mod_dir && mod_dir[0]) {
        char path[MW2ER_PATH_MAX];
        snprintf(path, sizeof(path), "%s/mod.conf", mod_dir);
        mw2er_config_load_file(path);
    }
}

const Mw2erRendererConfig &mw2er_config(void)
{
    return g_cfg;
}

int mw2er_config_ssaa_scale(void)
{
    return _stricmp(g_cfg.antialiasing, "ssaa_4x") == 0 ? 2 : 1;
}
