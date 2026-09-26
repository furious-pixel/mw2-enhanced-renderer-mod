#version 330

uniform vec2 u_viewport_size;
uniform vec2 u_origin;
uniform vec2 u_size;
out vec2 v_uv;

void main() {
    const vec2 corners[6] = vec2[6](
        vec2(0, 0), vec2(1, 0), vec2(0, 1),
        vec2(0, 1), vec2(1, 0), vec2(1, 1));
    vec2 corner = corners[gl_VertexID];
    vec2 position = u_origin + corner * u_size;
    gl_Position = vec4(position.x / u_viewport_size.x * 2.0 - 1.0,
                       1.0 - position.y / u_viewport_size.y * 2.0, 0, 1);
    v_uv = vec2(corner.x, 1.0 - corner.y);
}
