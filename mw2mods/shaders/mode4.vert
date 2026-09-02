#version 330

in vec3 in_pos;
in float in_c_in;
in float in_lighting_state;

uniform mat4 u_projection;
uniform vec3 u_camera_position;
uniform vec3 u_camera_right;
uniform vec3 u_camera_up;
uniform vec3 u_camera_forward;
out float v_palette_base;
out float v_palette_span;
flat out float v_lighting_state;
out vec3 v_world_pos;

void main() {
    vec3 delta = in_pos - u_camera_position;
    vec3 view_pos = vec3(
        dot(delta, u_camera_right),
        dot(delta, u_camera_up),
        dot(delta, u_camera_forward)
    );
    gl_Position = u_projection * vec4(view_pos, 1.0);
    v_world_pos = in_pos;
    v_lighting_state = in_lighting_state;
    if (
        in_lighting_state < 0.0
        || in_c_in < @MODE4_EMISSIVE_C_IN_THRESHOLD@
    ) {
        v_palette_base = in_c_in;
        v_palette_span = 0.0;
    } else {
        v_palette_base = floor(in_c_in / 16.0) * 16.0;
        v_palette_span = mod(in_c_in, 16.0);
    }
}
