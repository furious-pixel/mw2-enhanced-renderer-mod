#version 330

in vec3 in_pos;

uniform mat4 u_projection;
uniform vec3 u_camera_position;
uniform vec3 u_camera_right;
uniform vec3 u_camera_up;
uniform vec3 u_camera_forward;

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
}
