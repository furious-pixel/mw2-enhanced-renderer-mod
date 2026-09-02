#version 330

uniform sampler2D u_palette;
uniform sampler2D u_indexed_texture;
uniform ivec2 u_texture_size;

in vec2 v_uv;
in vec3 v_world_pos;
out vec4 frag_color;

@INDEXED_TEXMAP_FUNCTIONS@

float rotorHash(float p) {
    p = fract(p * 0.1031);
    p *= p + 33.33;
    p *= p + p;
    return fract(p);
}

float rotorNoise(float x) {
    float i = floor(x);
    float f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    return mix(rotorHash(i), rotorHash(i + 1.0), f);
}

vec4 keyedTexel(ivec2 texel_pos) {
    ivec2 size = max(u_texture_size, ivec2(1));
    texel_pos = clamp(texel_pos, ivec2(0), size - ivec2(1));
    float palette_index = floor(
        texelFetch(u_indexed_texture, texel_pos, 0).r * 255.0 + 0.5
    );
    float palette_u = (clamp(palette_index, 0.0, 255.0) + 0.5) / 256.0;
    vec3 rgb = texture(u_palette, vec2(palette_u, 0.5)).rgb;
    float alpha = (
        palette_index >= 254.5
        || (rgb.r > 0.94 && rgb.g > 0.94 && rgb.b > 0.94)
    ) ? 0.0 : 1.0;
    return vec4(rgb, alpha);
}

vec4 keyedLinear(vec2 uv) {
    vec2 size = vec2(max(u_texture_size, ivec2(1)));
    vec2 sample_pos = clamp(uv, vec2(0.001), vec2(0.999)) * size - 0.5;
    ivec2 base = ivec2(floor(sample_pos));
    vec2 f = fract(sample_pos);
    vec4 a = mix(keyedTexel(base), keyedTexel(base + ivec2(1, 0)), f.x);
    vec4 b = mix(
        keyedTexel(base + ivec2(0, 1)),
        keyedTexel(base + ivec2(1, 1)),
        f.x
    );
    return mix(a, b, f.y);
}

vec4 sampleRotor(vec2 uv) {
    vec2 p = uv * 2.0 - 1.0;
    float r = length(p);
    if (r > 1.05) {
        return vec4(0.0);
    }
    float theta = atan(p.y, p.x);
    float n = rotorNoise(r * 28.0) * 2.0 - 1.0;
    float n2 = rotorNoise(r * 75.6 + 17.3) * 2.0 - 1.0;
    float sample_radius = clamp(r + 0.04 * n + 0.014 * n2, 0.0, 1.05);
    float edge = 1.0;
    if (sample_radius > 0.97) {
        edge = clamp((1.0 - sample_radius) / 0.03, 0.0, 1.0);
    }
    if (sample_radius < 0.03) {
        edge *= sample_radius / 0.03;
    }

    const int SAMPLE_COUNT = 13;
    const float ARC = 0.2792526803;
    const float SIGMA = ARC / 2.2;
    vec3 accumulated_rgb = vec3(0.0);
    float accumulated_opaque = 0.0;
    float weight_sum = 0.0;
    for (int i = 0; i < SAMPLE_COUNT; ++i) {
        float t = (float(i) / float(SAMPLE_COUNT - 1)) * 2.0 - 1.0;
        float angle_delta = t * ARC;
        float normalized_delta = angle_delta / SIGMA;
        float weight = exp(-0.5 * normalized_delta * normalized_delta);
        vec2 sample_uv = vec2(0.5) + 0.5 * sample_radius * vec2(
            cos(theta + angle_delta),
            sin(theta + angle_delta)
        );
        vec4 sample_value = keyedLinear(sample_uv);
        accumulated_rgb += sample_value.rgb * sample_value.a * weight;
        accumulated_opaque += sample_value.a * weight;
        weight_sum += weight;
    }
    float opaque_fraction = accumulated_opaque / max(weight_sum, 1e-5);
    vec3 rgb = accumulated_rgb / max(accumulated_opaque, 1e-5);
    float alpha = pow(clamp(opaque_fraction, 0.0, 1.0), 1.5) * edge;
    return vec4(clamp(rgb * 1.05, 0.0, 1.0), clamp(alpha, 0.0, 1.0));
}

void main() {
    if (indexedTexmapClipped(v_world_pos)) {
        discard;
    }
    vec4 sample_value = sampleRotor(v_uv);
    if (sample_value.a < 0.01) {
        discard;
    }
    frag_color = vec4(
        applyIndexedTexmapLighting(sample_value.rgb, v_world_pos),
        sample_value.a
    );
}
