const float CONCEAL_SKY_RADIUS = 200.0;

float concealDistanceSquared(vec3 world_pos) {
    vec3 delta = world_pos - u_camera_position;
    return dot(delta, delta);
}

bool concealClipped(vec3 world_pos) {
    return scene_clip.y > 0.0 && u_conceal_far > 0.0
        && concealDistanceSquared(world_pos) >= u_conceal_far * u_conceal_far;
}

float concealAmount(vec3 world_pos) {
    if (scene_clip.y <= 0.0 || u_conceal_far <= 0.0) {
        return 0.0;
    }
    float distance_squared = concealDistanceSquared(world_pos);
    if (distance_squared >= u_conceal_far * u_conceal_far) {
        return 1.0;
    }
    if (distance_squared <= u_conceal_fade_start * u_conceal_fade_start) {
        return 0.0;
    }
    return (sqrt(distance_squared) - u_conceal_fade_start)
        / max(u_conceal_far - u_conceal_fade_start, 1e-6);
}

void concealmentClip(vec3 world_pos) {
    if (concealClipped(world_pos)) {
        discard;
    }
}

vec3 concealBackdropColor(vec3 world_pos) {
    if (u_conceal_sky_visible <= 0.0) {
        return texture(u_palette, vec2(u_conceal_ground_u, 0.5)).rgb;
    }
    vec3 dir = world_pos - u_camera_position;
    float dir_len = length(dir);
    if (dir_len <= 1e-8) {
        return texture(u_palette, vec2(u_conceal_ground_u, 0.5)).rgb;
    }
    dir /= dir_len;
    float horiz = length(dir.xz);
    if (horiz <= 1e-8) {
        float palette_u = dir.y > 0.0 ? u_conceal_sky_u : u_conceal_ground_u;
        return texture(u_palette, vec2(palette_u, 0.5)).rgb;
    }
    if (u_conceal_draw_gradient <= 0.0 || u_conceal_gradient_height <= 1e-8) {
        float palette_u = dir.y > 0.0 ? u_conceal_sky_u : u_conceal_ground_u;
        return texture(u_palette, vec2(palette_u, 0.5)).rgb;
    }
    float hit_y = dir.y / horiz * CONCEAL_SKY_RADIUS;
    if (hit_y >= u_conceal_gradient_height) {
        return texture(u_palette, vec2(u_conceal_sky_u, 0.5)).rgb;
    }
    if (hit_y <= 0.0) {
        return texture(u_palette, vec2(u_conceal_ground_u, 0.5)).rgb;
    }
    float gradient_t = 1.0 - hit_y / u_conceal_gradient_height;
    float palette_u = mix(
        u_conceal_sky_u,
        u_conceal_gradient_end_u,
        gradient_t
    );
    return texture(u_palette, vec2(palette_u, 0.5)).rgb;
}

vec3 applyConcealment(vec3 rgb, vec3 world_pos) {
    float t = concealAmount(world_pos);
    if (t <= 0.0) {
        return rgb;
    }
    return mix(rgb, concealBackdropColor(world_pos), t);
}
