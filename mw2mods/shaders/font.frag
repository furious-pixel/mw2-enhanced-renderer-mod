#version 330

uniform sampler2D u_glyph;
uniform vec4 u_color;

in vec2 v_uv;
out vec4 frag_color;

void main() {
    float coverage = texture(u_glyph, v_uv).r;
    float alpha = u_color.a * coverage;
    frag_color = vec4(u_color.rgb * alpha, alpha);
}
