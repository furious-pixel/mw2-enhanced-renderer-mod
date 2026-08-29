#version 330

uniform vec2 u_viewport_size;
uniform vec2 u_origin;
uniform vec2 u_scale;

in vec2 in_pos;
in vec2 in_uv;

out vec2 v_uv;

void main() {
    vec2 position = in_pos * u_scale + u_origin;
    vec2 ndc = vec2(
        (position.x / u_viewport_size.x) * 2.0 - 1.0,
        1.0 - (position.y / u_viewport_size.y) * 2.0
    );
    gl_Position = vec4(ndc, 0.0, 1.0);
    v_uv = in_uv;
}
