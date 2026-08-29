#version 330

uniform sampler2D u_palette;
uniform sampler2D u_indexed_texture;
uniform int u_target_lighting_enabled;
uniform vec3 u_camera_position;
uniform vec3 u_camera_forward;
uniform float u_near_clip_plane;
uniform vec3 u_target_key_direction;
uniform vec3 u_target_fill_direction;
uniform vec3 u_target_rim_direction;

in vec2 v_uv;
in vec3 v_world_pos;
out vec4 frag_color;

vec3 apply_target_lighting(vec3 base_rgb, vec3 world_pos) {
    vec3 normal = normalize(cross(dFdx(world_pos), dFdy(world_pos)));
    vec3 to_camera = normalize(u_camera_position - world_pos);
    if (dot(normal, to_camera) < 0.0) {
        normal = -normal;
    }
    float light = 0.02;
    light += 0.52 * max(dot(normal, u_target_key_direction), 0.0);
    light += 0.06 * max(dot(normal, u_target_fill_direction), 0.0);
    light += 0.12 * max(dot(normal, u_target_rim_direction), 0.0);
    return base_rgb * clamp(light, 0.0, 1.0);
}

void main() {
    if (
        u_near_clip_plane > 0.0
        && dot(v_world_pos - u_camera_position, u_camera_forward)
            < u_near_clip_plane
    ) {
        discard;
    }
    float palette_index = floor(texture(u_indexed_texture, v_uv).r * 255.0 + 0.5);
    if (palette_index >= @TRANSPARENT_PALETTE_INDEX@) {
        discard;
    }
    float palette_u = (clamp(palette_index, 0.0, 255.0) + 0.5) / 256.0;
    vec3 rgb = texture(u_palette, vec2(palette_u, 0.5)).rgb;
    if (u_target_lighting_enabled != 0) {
        rgb = apply_target_lighting(rgb, v_world_pos);
    }
    frag_color = vec4(rgb, 1.0);
}
