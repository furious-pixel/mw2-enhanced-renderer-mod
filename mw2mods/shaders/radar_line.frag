#version 330

in vec2 v_pos;
flat in vec2 v_start;
flat in vec2 v_end;
in vec4 v_color;
uniform float u_stroke_width;
out vec4 frag_color;

void main() {
    vec2 segment = v_end - v_start;
    float segment_len2 = max(dot(segment, segment), 1e-6);
    float t = clamp(dot(v_pos - v_start, segment) / segment_len2, 0.0, 1.0);
    float distance_px = length(v_pos - (v_start + segment * t));
    float half_width = max(0.25, u_stroke_width * 0.5);
    float coverage = clamp(half_width + 0.5 - distance_px, 0.0, 1.0);
    if (coverage <= 0.0) {
        discard;
    }
    float alpha = v_color.a * coverage;
    frag_color = vec4(v_color.rgb * alpha, alpha);
}
