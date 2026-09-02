#version 330

uniform sampler2D u_palette;
uniform vec3 u_camera_position;
uniform vec3 u_camera_forward;
uniform float u_near_clip_plane;
uniform float u_fog_distance;

in float v_palette_base;
in float v_palette_span;
flat in float v_lighting_state;
in vec3 v_world_pos;
out vec4 frag_color;

@SCENE_LIGHTING_FUNCTIONS@

void main() {
    if (
        u_near_clip_plane > 0.0
        && dot(v_world_pos - u_camera_position, u_camera_forward)
            < u_near_clip_plane
    ) {
        discard;
    }
    float palette_index = v_palette_base;
    if (v_lighting_state >= 0.0) {
        float final_shade_level = finalShadeLevel(
            v_lighting_state,
            v_world_pos
        );
        palette_index += v_palette_span * (
            (final_shade_level + 1.0) / 16.0
        );
    }
    palette_index = clamp(palette_index, 0.0, 255.0);
    float palette_u = (palette_index + 0.5) / 256.0;
    frag_color = vec4(texture(u_palette, vec2(palette_u, 0.5)).rgb, 1.0);
}
