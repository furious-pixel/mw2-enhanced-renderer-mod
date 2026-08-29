#version 330

uniform sampler2D u_sprite;
uniform sampler2D u_palette;
uniform int u_override_index;
uniform float u_brightness;
in vec2 v_uv;
out vec4 frag_color;

void main() {
    vec2 indexed_alpha = texture(u_sprite, v_uv).rg;
    float alpha = indexed_alpha.g;
    if (alpha <= 0.0) {
        discard;
    }
    int palette_index = u_override_index >= 0
        ? u_override_index
        : int(round(indexed_alpha.r * 255.0));
    vec3 color = texelFetch(u_palette, ivec2(palette_index, 0), 0).rgb;
    frag_color = vec4(color * alpha * u_brightness, alpha);
}
