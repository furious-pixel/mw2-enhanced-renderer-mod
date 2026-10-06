#version 330

@SCENE_UNIFORMS@

uniform sampler2D u_palette;
uniform float u_palette_index;

in vec3 v_world_pos;
in float v_wireframe_fade;
out vec4 frag_color;

void main() {
    if (
        u_near_clip_plane > 0.0
        && dot(v_world_pos - u_camera_position, u_camera_forward)
            < u_near_clip_plane
    ) {
        discard;
    }
    float palette_u = (clamp(u_palette_index, 0.0, 255.0) + 0.5) / 256.0;
    vec3 color = texture(u_palette, vec2(palette_u, 0.5)).rgb;
    frag_color = vec4(color * v_wireframe_fade, 1.0);
}
