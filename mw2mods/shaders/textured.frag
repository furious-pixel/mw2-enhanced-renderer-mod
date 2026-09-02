#version 330

uniform sampler2D u_palette;
uniform sampler2D u_indexed_texture;
uniform vec3 u_camera_position;
uniform vec3 u_camera_forward;
uniform float u_near_clip_plane;

in vec2 v_uv;
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
    if (palette_index >= @TRANSPARENT_PALETTE_INDEX@) {
        discard;
    }
    float palette_u = (clamp(palette_index, 0.0, 255.0) + 0.5) / 256.0;
    vec3 rgb = texture(u_palette, vec2(palette_u, 0.5)).rgb;
    frag_color = vec4(rgb, 1.0);
}
