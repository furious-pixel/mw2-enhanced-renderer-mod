uniform sampler2D u_primitive_lighting;
uniform int u_remap_kind;
uniform vec3 u_dark_ratio;
uniform vec3 u_fog_terminal_color;
uniform vec3 u_s8_ratio;
uniform vec3 u_camera_position;
uniform vec3 u_camera_forward;
uniform float u_fog_distance;
uniform float u_near_clip_plane;

@SCENE_LIGHTING_FUNCTIONS@

bool indexedTexmapClipped(vec3 world_pos) {
    return (
        u_near_clip_plane > 0.0
        && dot(world_pos - u_camera_position, u_camera_forward)
            < u_near_clip_plane
    );
}

vec3 applyIndexedTexmapLighting(vec3 base_rgb, vec3 world_pos) {
    float lighting_state = texelFetch(
        u_primitive_lighting,
        ivec2(gl_PrimitiveID, 0),
        0
    ).r;
    float final_shade_level = finalShadeLevel(lighting_state, world_pos);
    float light_t = clamp(final_shade_level / 15.0, 0.0, 1.0);
    if (u_remap_kind == 1 || u_remap_kind == 2) {
        return base_rgb * mix(u_dark_ratio, vec3(1.0), light_t);
    }
    if (u_remap_kind == 3) {
        return mix(u_fog_terminal_color, base_rgb, light_t);
    }
    if (u_remap_kind == 4) {
        vec3 mid = base_rgb * u_s8_ratio;
        if (light_t >= (8.0 / 15.0)) {
            float u = (light_t - (8.0 / 15.0)) / (7.0 / 15.0);
            return mix(mid, base_rgb, u);
        }
        float u = light_t / (8.0 / 15.0);
        return mix(u_fog_terminal_color, mid, u);
    }
    return base_rgb;
}
