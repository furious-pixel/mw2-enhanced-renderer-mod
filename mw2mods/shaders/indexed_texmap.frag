#version 330

uniform sampler2D u_palette;
uniform sampler2D u_indexed_texture;

in vec2 v_uv;
in vec3 v_world_pos;
out vec4 frag_color;

@INDEXED_TEXMAP_FUNCTIONS@

void main() {
    if (indexedTexmapClipped(v_world_pos)) {
        discard;
    }
    float palette_index = floor(
        texture(u_indexed_texture, v_uv).r * 255.0 + 0.5
    );
    if (palette_index >= 254.5) {
        discard;
    }
    float palette_u = (clamp(palette_index, 0.0, 255.0) + 0.5) / 256.0;
    vec3 base_rgb = texture(u_palette, vec2(palette_u, 0.5)).rgb;
    frag_color = vec4(
        applyIndexedTexmapLighting(base_rgb, v_world_pos),
        1.0
    );
}
