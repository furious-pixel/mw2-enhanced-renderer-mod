#version 330

@SCENE_UNIFORMS@

in vec3 in_pos;
in vec2 in_uv;

uniform vec2 u_uv_scale;
uniform int u_canonical_rotor;
uniform vec3 u_rotor_center;
uniform vec3 u_rotor_axis_u;
uniform vec3 u_rotor_axis_v;

out vec2 v_uv;
out vec3 v_world_pos;

void main() {
    vec3 world_pos = in_pos;
    if (u_canonical_rotor != 0) {
        world_pos = (
            u_rotor_center
            + in_pos.x * u_rotor_axis_u
            + in_pos.y * u_rotor_axis_v
        );
    }
    vec3 delta = world_pos - u_camera_position;
    vec3 view_pos = vec3(
        dot(delta, u_camera_right),
        dot(delta, u_camera_up),
        dot(delta, u_camera_forward)
    );
    gl_Position = u_projection * vec4(view_pos, 1.0);
    v_uv = in_uv * u_uv_scale;
    v_world_pos = world_pos;
}
