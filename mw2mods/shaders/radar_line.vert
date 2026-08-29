#version 330

uniform vec2 u_viewport_size;
in vec2 in_pos;
in vec2 in_start;
in vec2 in_end;
in vec4 in_color;
out vec2 v_pos;
flat out vec2 v_start;
flat out vec2 v_end;
out vec4 v_color;

void main() {
    vec2 ndc = vec2(
        (in_pos.x / u_viewport_size.x) * 2.0 - 1.0,
        1.0 - (in_pos.y / u_viewport_size.y) * 2.0
    );
    gl_Position = vec4(ndc, 0.0, 1.0);
    v_pos = in_pos;
    v_start = in_start;
    v_end = in_end;
    v_color = in_color;
}
