#version 330

uniform sampler2D u_palette;
uniform vec3 u_camera_position;
uniform vec3 u_camera_forward;
uniform float u_near_clip_plane;

in float v_palette_index;
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
    float palette_index = clamp(v_palette_index, 0.0, 255.0);
    float palette_u = (palette_index + 0.5) / 256.0;
    frag_color = vec4(texture(u_palette, vec2(palette_u, 0.5)).rgb, 1.0);
}
