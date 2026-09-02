#version 330

uniform sampler2D u_palette;
uniform sampler2D u_indexed_texture;
uniform int u_remap_kind;
uniform vec3 u_dark_ratio;
uniform vec3 u_fog_terminal_color;
uniform vec3 u_s8_ratio;
uniform vec3 u_camera_position;
uniform vec3 u_camera_forward;
uniform float u_near_clip_plane;

in vec2 v_uv;
in float v_light_t;
in vec3 v_world_pos;
out vec4 frag_color;

void main() {
    if (
        u_near_clip_plane > 0.0
        && dot(v_world_pos - u_camera_position, u_camera_forward)
            < u_near_clip_plane
    ) {
        discard;
    }
    float palette_index = floor(texture(u_indexed_texture, v_uv).r * 255.0 + 0.5);
    float palette_u = (clamp(palette_index, 0.0, 255.0) + 0.5) / 256.0;
    vec3 base_rgb = texture(u_palette, vec2(palette_u, 0.5)).rgb;
    if (base_rgb.r > 0.94 && base_rgb.g > 0.94 && base_rgb.b > 0.94) {
        discard;
    }
    float light_t = clamp(v_light_t, 0.0, 1.0);
    vec3 rgb = base_rgb;
    if (u_remap_kind == 1 || u_remap_kind == 2) {
        vec3 factor = mix(u_dark_ratio, vec3(1.0), light_t);
        rgb = base_rgb * factor;
    } else if (u_remap_kind == 3) {
        rgb = mix(u_fog_terminal_color, base_rgb, light_t);
    } else if (u_remap_kind == 4) {
        vec3 mid = base_rgb * u_s8_ratio;
        if (light_t >= (8.0 / 15.0)) {
            float u = (light_t - (8.0 / 15.0)) / (7.0 / 15.0);
            rgb = mix(mid, base_rgb, u);
        } else {
            float u = light_t / (8.0 / 15.0);
            rgb = mix(u_fog_terminal_color, mid, u);
        }
    }
    frag_color = vec4(rgb, 1.0);
}
