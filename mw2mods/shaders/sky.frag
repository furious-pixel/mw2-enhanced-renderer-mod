#version 330

uniform sampler2D u_palette;
uniform float u_palette_start;
uniform float u_palette_end;

in float v_palette_mix;
out vec4 frag_color;

void main() {
    float palette_u = mix(u_palette_start, u_palette_end, v_palette_mix);
    frag_color = vec4(texture(u_palette, vec2(palette_u, 0.5)).rgb, 1.0);
}
