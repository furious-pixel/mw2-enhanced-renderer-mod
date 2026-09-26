#version 330

uniform sampler2D u_source;
uniform float u_scale;
in vec2 v_uv;
out vec4 frag_color;

void main() {
    vec2 size = vec2(textureSize(u_source, 0));
    vec2 texel = 1.0 / size;
    vec2 pixel = v_uv * size - 0.5;
    // A smooth nonlinear reconstruction keeps pixel centers crisp without
    // snapping the destination size or the colored segment boundaries.
    vec2 fraction = fract(pixel);
    vec2 shaped = mix(fraction, smoothstep(0.0, 1.0, fraction),
                     smoothstep(1.0, 2.0, u_scale) * 0.65);
    vec4 core = texture(u_source, (floor(pixel) + 0.5 + shaped) * texel);
    // A compact anisotropic phosphor footprint: wider horizontally, with a
    // faint vertical halo. Filter premultiplied RGBA to avoid dark fringes.
    vec4 glow = texture(u_source, v_uv) * 0.28;
    glow += texture(u_source, v_uv + vec2(0.8, 0) * texel) * 0.18;
    glow += texture(u_source, v_uv - vec2(0.8, 0) * texel) * 0.18;
    glow += texture(u_source, v_uv + vec2(1.6, 0) * texel) * 0.07;
    glow += texture(u_source, v_uv - vec2(1.6, 0) * texel) * 0.07;
    glow += texture(u_source, v_uv + vec2(0, 0.7) * texel) * 0.11;
    glow += texture(u_source, v_uv - vec2(0, 0.7) * texel) * 0.11;
    // Fade scanlines out when the output cannot resolve the source rows.
    float scan = 1.0 - 0.10 * smoothstep(1.25, 2.5, u_scale) *
        (0.5 - 0.5 * cos(6.2831853 * pixel.y));
    vec4 beam = core * 0.94 + glow * 0.18;
    beam.a = min(beam.a, 1.0);
    beam.rgb = min(beam.rgb * scan, vec3(beam.a));
    frag_color = beam;
}
