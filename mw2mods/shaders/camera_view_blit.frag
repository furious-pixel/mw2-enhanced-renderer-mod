#version 330

uniform sampler2D u_camera_view;
uniform bool u_resolve_satellite_damage;
uniform ivec2 u_source_logical_size;
uniform ivec2 u_destination_logical_size;
in vec2 v_uv;
out vec4 frag_color;

void main() {
    if (!u_resolve_satellite_damage) {
        frag_color = vec4(texture(u_camera_view, v_uv).rgb, 1.0);
        return;
    }

    // Produce one resolved value for each 2x2 destination block. The final
    // compositor's exact 2:1 linear sample then averages four identical
    // values instead of applying a second filter to the damaged image.
    ivec2 destination_pixel = ivec2(gl_FragCoord.xy) / 2;
    ivec2 source_pixel = ivec2(floor(
        (vec2(destination_pixel) + vec2(0.5))
        * vec2(u_source_logical_size)
        / vec2(u_destination_logical_size)
    ));
    source_pixel = clamp(
        source_pixel,
        ivec2(0),
        u_source_logical_size - ivec2(1)
    );
    vec2 resolve_uv = (
        vec2(source_pixel * 2) + vec2(1.0)
    ) / vec2(textureSize(u_camera_view, 0));
    vec3 resolved = texture(u_camera_view, resolve_uv).rgb;
    frag_color = vec4(resolved, 1.0);
}
