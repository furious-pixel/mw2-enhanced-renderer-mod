#version 330

@SCENE_UNIFORMS@

uniform sampler2D u_palette;
in float v_palette_index;
in float v_wireframe_fade;
in vec3 v_world_pos;
out vec4 frag_color;

@CONCEALMENT_FUNCTIONS@

void main() {
    if (
        u_near_clip_plane > 0.0
        && dot(v_world_pos - u_camera_position, u_camera_forward)
            < u_near_clip_plane
    ) {
        discard;
    }
    concealmentClip(v_world_pos);
    float palette_index = clamp(v_palette_index, 0.0, 255.0);
    float palette_u = (palette_index + 0.5) / 256.0;
    vec3 color = texture(u_palette, vec2(palette_u, 0.5)).rgb;
    frag_color = vec4(
        applyConcealment(color * v_wireframe_fade, v_world_pos),
        1.0
    );
}
