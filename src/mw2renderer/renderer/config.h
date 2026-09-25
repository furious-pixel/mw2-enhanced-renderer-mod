#ifndef MW2ER_CONFIG_H
#define MW2ER_CONFIG_H

/* [renderer] keys from Python mod.conf / mod_init schema. */

struct Mw2erRendererConfig {
    char antialiasing[16]; /* none | ssaa_4x */
    float ssaa_line_width;
    float max_horizontal_fov_degrees;
    char entity_lod_selection[24];
    float entity_lod_detail0_pixels;
    float entity_lod_detail1_pixels;
    float entity_lod_detail2_pixels;
    float entity_lod_hysteresis;
    int enable_diagnostic_logging;
    int load_resources_from_prj;
    int enhanced_enhanced_imaging;
    float enhanced_imaging_distance_ratio;
    float enhanced_imaging_reveal_time;
    int reduce_terrain_gaps;
    int enhanced_heli_rotors;
    int enhanced_aero_lift_fans;
    int enhanced_mech_textures;
    int enhanced_dropship_textures;
    float enhanced_mech_texture_uv_scale;
    float enhanced_dropship_texture_uv_scale;
    float hud_position_scaling;
    float hud_panel_scaling;
    float hud_viewport_scaling;
    float hud_font_scaling;
    float hud_target_marker_scaling;
    int hud_damage_wireframe_scale; // 0 = auto; otherwise original-pixel multiple
    float hud_middle_panel_vertical_position;
    float hud_top_widescreen_position;
    float hud_middle_widescreen_position;
    float hud_bottom_widescreen_position;
    char hud_compass_altimeter[16];
    char hud_power_meters[16];
    int hud_meter_dark_offset;
    int hud_meter_light_offset;
    int hud_meter_peak_offset;
    float hud_meter_peak_position;
    int hud_alt_throttle_indicator_position;
    char hud_htal_meters[16];
    int hud_alt_htal_view;
    int rear_camera_mirror;
    float hud_radar_stroke_width;
    float hud_targeting_animation_trail_ms;
    float hud_targeting_animation_duration;
    float hud_targeting_animation_turns;
    int force_satellite; /* process-launch diagnostic override */
};

void mw2er_config_reset_defaults(void);
void mw2er_config_load_file(const char *path);
void mw2er_config_load_from_mod_dir(const char *mod_dir);
const Mw2erRendererConfig &mw2er_config(void);
int mw2er_config_ssaa_scale(void); /* 2 for ssaa_4x, else 1 */

#endif
