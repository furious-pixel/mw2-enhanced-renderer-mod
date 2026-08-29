#version 330

in vec3 in_pos;
in vec2 in_uv;
in float in_contribution;

uniform mat4 u_projection;
uniform vec3 u_camera_position;
uniform vec3 u_camera_right;
uniform vec3 u_camera_up;
uniform vec3 u_camera_forward;
uniform float u_fog_distance;
uniform vec2 u_uv_scale;

out vec2 v_uv;
out float v_light_t;
out vec3 v_world_pos;

float enhanced_fog_atten(vec3 world_pos) {
    float fog_distance = max(u_fog_distance, 1e-6);
    vec3 delta = world_pos - u_camera_position;
    float view_depth = max(0.0, dot(delta, u_camera_forward)) * 4.0;
    return view_depth / fog_distance;
}

void main() {
    vec3 delta = in_pos - u_camera_position;
    vec3 view_pos = vec3(
        dot(delta, u_camera_right),
        dot(delta, u_camera_up),
        dot(delta, u_camera_forward)
    );
    gl_Position = u_projection * vec4(view_pos, 1.0);
    v_uv = in_uv * u_uv_scale;
    v_world_pos = in_pos;
    float offset = clamp(
        in_contribution - enhanced_fog_atten(in_pos),
        0.0,
        15.0
    );
    v_light_t = offset / 15.0;
}
