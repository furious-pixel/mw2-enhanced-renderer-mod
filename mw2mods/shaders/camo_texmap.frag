#version 330

uniform sampler2D u_palette;
uniform sampler2D u_indexed_texture;
uniform ivec2 u_texture_size;

in vec2 v_uv;
in vec3 v_world_pos;
out vec4 frag_color;

@INDEXED_TEXMAP_FUNCTIONS@

vec4 paletteTexel(ivec2 texel_pos) {
    ivec2 size = max(u_texture_size, ivec2(1));
    texel_pos = ivec2(
        (texel_pos.x % size.x + size.x) % size.x,
        (texel_pos.y % size.y + size.y) % size.y
    );
    float palette_index = floor(
        texelFetch(u_indexed_texture, texel_pos, 0).r * 255.0 + 0.5
    );
    float palette_u = (clamp(palette_index, 0.0, 255.0) + 0.5) / 256.0;
    vec3 rgb = texture(u_palette, vec2(palette_u, 0.5)).rgb;
    return vec4(rgb, palette_index >= 254.5 ? 0.0 : 1.0);
}

float catmullRomWeight(float distance_value) {
    float x = abs(distance_value);
    if (x <= 1.0) {
        return 1.5 * x * x * x - 2.5 * x * x + 1.0;
    }
    if (x < 2.0) {
        return -0.5 * x * x * x + 2.5 * x * x - 4.0 * x + 2.0;
    }
    return 0.0;
}

vec4 sampleCamoBicubic(vec2 uv) {
    vec2 size = vec2(max(u_texture_size, ivec2(1)));
    vec2 sample_pos = fract(uv) * size - 0.5;
    ivec2 base = ivec2(floor(sample_pos));
    vec2 fraction = fract(sample_pos);
    vec4 accumulated = vec4(0.0);
    for (int y = -1; y <= 2; ++y) {
        float wy = catmullRomWeight(float(y) - fraction.y);
        for (int x = -1; x <= 2; ++x) {
            float wx = catmullRomWeight(float(x) - fraction.x);
            accumulated += paletteTexel(base + ivec2(x, y)) * (wx * wy);
        }
    }
    return clamp(accumulated, 0.0, 1.0);
}

void main() {
    if (indexedTexmapClipped(v_world_pos)) {
        discard;
    }
    vec4 sample_value = sampleCamoBicubic(v_uv);
    if (sample_value.a < 0.5) {
        discard;
    }
    frag_color = vec4(
        applyIndexedTexmapLighting(sample_value.rgb, v_world_pos),
        1.0
    );
}
