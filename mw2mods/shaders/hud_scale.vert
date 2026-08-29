#version 330

uniform vec2 u_viewport_size;
uniform vec2 u_origin;
uniform vec2 u_scale;

in vec2 in_pos;
in float in_index;
flat out int v_index;

void main() {
    vec2 position = u_origin + in_pos * u_scale;
    vec2 ndc = vec2(
        (position.x / u_viewport_size.x) * 2.0 - 1.0,
        1.0 - (position.y / u_viewport_size.y) * 2.0
    );
    gl_Position = vec4(ndc, 0.0, 1.0);
    v_index = int(round(in_index));
}
