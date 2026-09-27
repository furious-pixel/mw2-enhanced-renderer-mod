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
    // A wider low-opacity phosphor halo, without another render target.
    vec4 halo = glow * 0.40;
    halo += texture(u_source, v_uv + vec2(2.2, 0) * texel) * 0.10;
    halo += texture(u_source, v_uv - vec2(2.2, 0) * texel) * 0.10;
    halo += texture(u_source, v_uv + vec2(0, 1.4) * texel) * 0.08;
    halo += texture(u_source, v_uv - vec2(0, 1.4) * texel) * 0.08;
    halo += texture(u_source, v_uv + vec2(1.4, 0.9) * texel) * 0.06;
    halo += texture(u_source, v_uv + vec2(-1.4, 0.9) * texel) * 0.06;
    halo += texture(u_source, v_uv + vec2(1.4, -0.9) * texel) * 0.06;
    halo += texture(u_source, v_uv - vec2(1.4, 0.9) * texel) * 0.06;
    // Fade scanlines out when the output cannot resolve the source rows.
    float scan_strength = 0.44 * smoothstep(0.90, 2.10, u_scale);
    float scan = 1.0 - scan_strength *
        (0.5 - 0.5 * cos(6.2831853 * pixel.y));
    // Preserve average phosphor energy as scanline contrast increases.
    scan /= 1.0 - 0.5 * scan_strength;
    vec4 beam = core * 0.98 + glow * 0.25 + halo * 0.50;
    beam.a = core.a * 0.98 + glow.a * 0.20 + halo.a * 0.12;
    beam.a = min(beam.a, 1.0);
    // A small emissive contribution restores energy lost through filtering.
    // Stronger phosphor treatments also lift saturated colors slightly.
    float peak = max(beam.r, max(beam.g, beam.b));
    beam.rgb = mix(beam.rgb, vec3(peak), 0.07);
    beam.rgb = min(beam.rgb * 1.22 * scan, vec3(1.0));
    frag_color = beam;
}
