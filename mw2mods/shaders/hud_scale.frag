#version 330

uniform sampler2D u_palette;

flat in int v_index;
out vec4 frag_color;

void main() {
    vec3 color = texelFetch(u_palette, ivec2(v_index, 0), 0).rgb;
    frag_color = vec4(color, 1.0);
}
