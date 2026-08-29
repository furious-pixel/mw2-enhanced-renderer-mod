#version 330

uniform sampler2D u_scene;
uniform sampler2D u_overlay;
uniform sampler2D u_monitor_brightness;
uniform float u_fade_progress;

in vec2 v_uv;
out vec4 frag_color;

float applyMonitorBrightness(float component) {
    float position = clamp(component, 0.0, 1.0) * 63.0;
    int lower = int(floor(position));
    int upper = min(lower + 1, 63);
    float fraction = position - float(lower);
    float low_value = texelFetch(
        u_monitor_brightness,
        ivec2(lower, 0),
        0
    ).r;
    float high_value = texelFetch(
        u_monitor_brightness,
        ivec2(upper, 0),
        0
    ).r;
    return mix(low_value, high_value, fraction);
}

void main() {
    vec4 scene = texture(u_scene, v_uv);
    vec4 overlay = texture(u_overlay, v_uv);
    vec3 composed = overlay.rgb + scene.rgb * (1.0 - overlay.a);
    vec3 adjusted = vec3(
        applyMonitorBrightness(composed.r),
        applyMonitorBrightness(composed.g),
        applyMonitorBrightness(composed.b)
    );
    frag_color = vec4(adjusted * (1.0 - u_fade_progress), 1.0);
}
