#version 330

@SCENE_UNIFORMS@

in vec3 in_pos;
in float in_palette_mix;

uniform float u_y_scale;

out float v_palette_mix;

void main() {
    vec3 model_pos = vec3(in_pos.x, in_pos.y * u_y_scale, in_pos.z);
    vec3 view_pos = vec3(
        dot(model_pos, u_camera_right),
        dot(model_pos, u_camera_up),
        dot(model_pos, u_camera_forward)
    );
    gl_Position = u_projection * vec4(view_pos, 1.0);
    v_palette_mix = in_palette_mix;
}
