#version 330

uniform sampler2D u_bar;
in vec2 v_uv;
out vec4 frag_color;

void main() {
    vec3 color = texture(u_bar, v_uv).rgb;
    frag_color = vec4(color, 1.0);
}
