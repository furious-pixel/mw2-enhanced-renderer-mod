#version 330

uniform vec4 u_color;
out vec4 frag_color;

void main() {
    frag_color = vec4(u_color.rgb * u_color.a, u_color.a);
}
