#version 330

in vec3 in_pos;
in float in_c_in;
in float in_contribution;

uniform mat4 u_projection;
uniform vec3 u_camera_position;
uniform vec3 u_camera_right;
uniform vec3 u_camera_up;
uniform vec3 u_camera_forward;
uniform float u_fog_distance;

out float v_palette_index;
out vec3 v_world_pos;

float enhanced_fog_atten(vec3 world_pos) {
    float fog_distance = max(u_fog_distance, 1e-6);
    vec3 delta = world_pos - u_camera_position;
    float camera_distance = length(delta) * 4.0;
    return camera_distance / fog_distance;
}

void main() {
    vec3 delta = in_pos - u_camera_position;
    vec3 view_pos = vec3(
        dot(delta, u_camera_right),
        dot(delta, u_camera_up),
        dot(delta, u_camera_forward)
    );
    gl_Position = u_projection * vec4(view_pos, 1.0);
    v_world_pos = in_pos;

    if (in_contribution < 0.0) {
        v_palette_index = in_c_in;
        return;
    }

    if (in_c_in < @MODE4_EMISSIVE_C_IN_THRESHOLD@) {
        v_palette_index = in_c_in;
        return;
    }

    float offset = clamp(
        in_contribution - enhanced_fog_atten(in_pos),
        0.0,
        15.0
    );
    float ramp_base = floor(in_c_in / 16.0) * 16.0;
    float ramp_value = mod(in_c_in, 16.0);
    v_palette_index = ramp_base + ramp_value * ((offset + 1.0) / 16.0);
}
