#version 330

@SCENE_UNIFORMS@

in vec3 in_pos;
in float in_palette_index;

uniform float u_point_size;

out float v_palette_index;
out float v_wireframe_fade;
out vec3 v_world_pos;

void main() {
    vec3 delta = in_pos - u_camera_position;
    vec3 view_pos = vec3(
        dot(delta, u_camera_right),
        dot(delta, u_camera_up),
        dot(delta, u_camera_forward)
    );
    gl_Position = u_projection * vec4(view_pos, 1.0);
    gl_PointSize = u_point_size;
    v_palette_index = in_palette_index;
    v_wireframe_fade = (
        u_wireframe_fade_end > u_wireframe_fade_start
        ? 1.0 - smoothstep(
            u_wireframe_fade_start,
            u_wireframe_fade_end,
            length(delta)
        )
        : 1.0
    );
    v_world_pos = in_pos;
}
