#version 330

uniform vec2 u_center;
uniform vec2 u_radii;
uniform vec4 u_color;
uniform float u_stroke_width;
in vec2 v_pos;
out vec4 frag_color;

void main() {
    vec2 q = (v_pos - u_center) / u_radii;
    float q_length = length(q);
    vec2 gradient_terms = q / u_radii;
    float gradient = max(length(gradient_terms) / max(q_length, 1e-6), 1e-6);
    float distance_px = abs(q_length - 1.0) / gradient;
    float half_width = max(0.25, u_stroke_width * 0.5);
    float coverage = clamp(half_width + 0.5 - distance_px, 0.0, 1.0);
    if (coverage <= 0.0) {
        discard;
    }
    float alpha = u_color.a * coverage;
    frag_color = vec4(u_color.rgb * alpha, alpha);
}
