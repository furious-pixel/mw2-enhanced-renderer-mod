#version 330

uniform sampler2D u_palette;
uniform sampler2D u_indexed_texture;
uniform sampler2D u_primitive_lighting;
uniform int u_remap_kind;
uniform vec3 u_dark_ratio;
uniform vec3 u_fog_terminal_color;
uniform vec3 u_s8_ratio;
uniform vec3 u_camera_position;
uniform vec3 u_camera_forward;
uniform float u_fog_distance;
uniform float u_near_clip_plane;
uniform int u_rotor_enhanced;
uniform ivec2 u_rotor_texture_size;
uniform int u_texture_role;
uniform ivec2 u_texture_size;

in vec2 v_uv;
in vec3 v_world_pos;
out vec4 frag_color;

@SCENE_LIGHTING_FUNCTIONS@

float rotor_hash11(float p) {
    p = fract(p * 0.1031);
    p *= p + 33.33;
    p *= p + p;
    return fract(p);
}

float rotor_noise1(float x) {
    float i = floor(x);
    float f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    return mix(rotor_hash11(i), rotor_hash11(i + 1.0), f);
}

vec4 rotor_keyed_texel(ivec2 texel_pos) {
    ivec2 size = max(u_rotor_texture_size, ivec2(1));
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

vec4 rotor_keyed_linear(vec2 uv) {
    vec2 size = vec2(max(u_rotor_texture_size, ivec2(1)));
    vec2 sample_pos = clamp(uv, vec2(0.001), vec2(0.999)) * size - 0.5;
    ivec2 base = ivec2(floor(sample_pos));
    vec2 f = fract(sample_pos);
    vec4 a = mix(
        rotor_keyed_texel(base),
        rotor_keyed_texel(base + ivec2(1, 0)),
        f.x
    );
    vec4 b = mix(
        rotor_keyed_texel(base + ivec2(0, 1)),
        rotor_keyed_texel(base + ivec2(1, 1)),
        f.x
    );
    return mix(a, b, f.y);
}

vec4 sample_rotor_enhanced(vec2 uv) {
    vec2 p = uv * 2.0 - 1.0;
    float r = length(p);
    if (r > 1.05) {
        return vec4(0.0);
    }
    float theta = atan(p.y, p.x);
    float n = rotor_noise1(r * 28.0) * 2.0 - 1.0;
    float n2 = rotor_noise1(r * 75.6 + 17.3) * 2.0 - 1.0;
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
        vec4 sample_value = rotor_keyed_linear(sample_uv);
        accumulated_rgb += sample_value.rgb * sample_value.a * weight;
        accumulated_opaque += sample_value.a * weight;
        weight_sum += weight;
    }
    float opaque_fraction = accumulated_opaque / max(weight_sum, 1e-5);
    vec3 rgb = accumulated_rgb / max(accumulated_opaque, 1e-5);
    float alpha = pow(clamp(opaque_fraction, 0.0, 1.0), 1.5) * edge;
    return vec4(clamp(rgb * 1.05, 0.0, 1.0), clamp(alpha, 0.0, 1.0));
}

vec4 enhanced_palette_texel(ivec2 texel_pos) {
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

float catmull_rom_weight(float distance_value) {
    float x = abs(distance_value);
    if (x <= 1.0) {
        return 1.5 * x * x * x - 2.5 * x * x + 1.0;
    }
    if (x < 2.0) {
        return -0.5 * x * x * x + 2.5 * x * x - 4.0 * x + 2.0;
    }
    return 0.0;
}

vec4 sample_camo_bicubic(vec2 uv) {
    vec2 size = vec2(max(u_texture_size, ivec2(1)));
    vec2 sample_pos = fract(uv) * size - 0.5;
    ivec2 base = ivec2(floor(sample_pos));
    vec2 fraction = fract(sample_pos);
    vec4 accumulated = vec4(0.0);
    for (int y = -1; y <= 2; ++y) {
        float wy = catmull_rom_weight(float(y) - fraction.y);
        for (int x = -1; x <= 2; ++x) {
            float wx = catmull_rom_weight(float(x) - fraction.x);
            accumulated += enhanced_palette_texel(
                base + ivec2(x, y)
            ) * (wx * wy);
        }
    }
    return clamp(accumulated, 0.0, 1.0);
}

void main() {
    if (
        u_near_clip_plane > 0.0
        && dot(v_world_pos - u_camera_position, u_camera_forward)
            < u_near_clip_plane
    ) {
        discard;
    }
    vec3 base_rgb;
    float output_alpha = 1.0;
    if (u_rotor_enhanced != 0) {
        vec4 rotor_sample = sample_rotor_enhanced(v_uv);
        if (rotor_sample.a < 0.01) {
            discard;
        }
        base_rgb = rotor_sample.rgb;
        output_alpha = rotor_sample.a;
    } else if (u_texture_role == 1) {
        vec4 camo_sample = sample_camo_bicubic(v_uv);
        if (camo_sample.a < 0.5) {
            discard;
        }
        base_rgb = camo_sample.rgb;
    } else {
        float palette_index = floor(
            texture(u_indexed_texture, v_uv).r * 255.0 + 0.5
        );
        if (palette_index >= 254.5) {
            discard;
        }
        float palette_u = (
            clamp(palette_index, 0.0, 255.0) + 0.5
        ) / 256.0;
        base_rgb = texture(u_palette, vec2(palette_u, 0.5)).rgb;
    }
    float lighting_state = texelFetch(
        u_primitive_lighting,
        ivec2(gl_PrimitiveID, 0),
        0
    ).r;
    float final_shade_level = finalShadeLevel(
        lighting_state,
        v_world_pos
    );
    float light_t = clamp(final_shade_level / 15.0, 0.0, 1.0);
    vec3 rgb = base_rgb;
    if (u_remap_kind == 1 || u_remap_kind == 2) {
        vec3 factor = mix(u_dark_ratio, vec3(1.0), light_t);
        rgb = base_rgb * factor;
    } else if (u_remap_kind == 3) {
        rgb = mix(u_fog_terminal_color, base_rgb, light_t);
    } else if (u_remap_kind == 4) {
        vec3 mid = base_rgb * u_s8_ratio;
        if (light_t >= (8.0 / 15.0)) {
            float u = (light_t - (8.0 / 15.0)) / (7.0 / 15.0);
            rgb = mix(mid, base_rgb, u);
        } else {
            float u = light_t / (8.0 / 15.0);
            rgb = mix(u_fog_terminal_color, mid, u);
        }
    }
    frag_color = vec4(rgb, output_alpha);
}
