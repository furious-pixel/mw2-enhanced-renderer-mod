#version 330

in vec3 in_pos;
in vec3 in_other_pos;
in float in_billboard_flags;

uniform mat4 u_projection;
uniform vec2 u_viewport_size;
uniform vec3 u_camera_position;
uniform vec3 u_camera_right;
uniform vec3 u_camera_up;
uniform vec3 u_camera_forward;
uniform int u_satellite_billboard;

out vec2 v_uv;
out vec3 v_world_pos;

vec4 project_world(vec3 world_pos) {
    vec3 delta = world_pos - u_camera_position;
    vec3 view_pos = vec3(
        dot(delta, u_camera_right),
        dot(delta, u_camera_up),
        dot(delta, u_camera_forward)
    );
    return u_projection * vec4(view_pos, 1.0);
}

float safe_w(float value) {
    if (abs(value) >= 1e-6) {
        return value;
    }
    return value < 0.0 ? -1e-6 : 1e-6;
}

vec2 clip_to_screen(vec4 clip_pos) {
    float clip_w = safe_w(clip_pos.w);
    vec2 ndc = clip_pos.xy / clip_w;
    return vec2(
        (ndc.x * 0.5 + 0.5) * u_viewport_size.x,
        (ndc.y * 0.5 + 0.5) * u_viewport_size.y
    );
}

vec2 screen_to_ndc(vec2 screen_pos) {
    return vec2(
        (screen_pos.x / u_viewport_size.x) * 2.0 - 1.0,
        (screen_pos.y / u_viewport_size.y) * 2.0 - 1.0
    );
}

int billboard_corner(int vertex_id, bool use_order_b) {
    if (use_order_b) {
        if (vertex_id == 0 || vertex_id == 3) return 0;
        if (vertex_id == 1) return 1;
        if (vertex_id == 2 || vertex_id == 4) return 2;
        return 3;
    }
    if (vertex_id == 0 || vertex_id == 3) return 0;
    if (vertex_id == 1 || vertex_id == 5) return 2;
    if (vertex_id == 2) return 1;
    return 3;
}

void main() {
    int flags = int(in_billboard_flags + 0.5);
    bool mirror_u = (flags & 1) != 0;
    bool flip_winding = (flags & 2) != 0;
    bool satellite = u_satellite_billboard != 0;
    bool use_order_b = satellite ? !flip_winding : flip_winding;
    int corner = billboard_corner(gl_VertexID % 6, use_order_b);
    float u0 = mirror_u ? 1.0 : 0.0;
    float u1 = mirror_u ? 0.0 : 1.0;
    float edge_t;
    float side;
    vec2 uv;
    if (satellite) {
        if (corner == 0) {
            edge_t = 0.0; side = -1.0; uv = vec2(u0, 1.0);
        } else if (corner == 1) {
            edge_t = 1.0; side = -1.0; uv = vec2(u1, 1.0);
        } else if (corner == 2) {
            edge_t = 1.0; side = 1.0; uv = vec2(u1, 0.0);
        } else {
            edge_t = 0.0; side = 1.0; uv = vec2(u0, 0.0);
        }
    } else if (corner == 0) {
        edge_t = 0.0; side = -1.0; uv = vec2(u0, 0.0);
    } else if (corner == 1) {
        edge_t = 0.0; side = 1.0; uv = vec2(u1, 0.0);
    } else if (corner == 2) {
        edge_t = 1.0; side = 1.0; uv = vec2(u1, 1.0);
    } else {
        edge_t = 1.0; side = -1.0; uv = vec2(u0, 1.0);
    }

    vec4 clip_a = project_world(in_pos);
    vec4 clip_b = project_world(in_other_pos);
    vec2 screen_a = clip_to_screen(clip_a);
    vec2 screen_b = clip_to_screen(clip_b);
    vec2 spine = screen_b - screen_a;
    vec2 half_perp = vec2(-spine.y, spine.x) * 0.5;
    edge_t = clamp(edge_t, 0.0, 1.0);
    vec2 base_screen;
    vec4 base_clip;
    vec2 final_screen;
    if (u_satellite_billboard != 0) {
        ivec2 satellite_a = ivec2(screen_a);
        ivec2 satellite_b = ivec2(screen_b);
        float half_size = float(abs(
            (satellite_a.x - satellite_b.x) >> 1
        ));
        base_screen = vec2(satellite_a);
        // Native display-list ordering uses the sprite entry's
        // minimum view depth. Give the expanded square the
        // nearer control endpoint's depth so ground effects do
        // not disappear behind the terrain solely because A
        // lies at or slightly below it.
        base_clip = (
            clip_a.z / safe_w(clip_a.w)
            <= clip_b.z / safe_w(clip_b.w)
        ) ? clip_a : clip_b;
        final_screen = base_screen + vec2(
            edge_t * 2.0 - 1.0,
            side
        ) * half_size;
    } else {
        base_screen = mix(screen_a, screen_b, edge_t);
        base_clip = mix(clip_a, clip_b, edge_t);
        final_screen = base_screen + half_perp * side;
    }
    vec2 final_ndc = screen_to_ndc(final_screen);
    gl_Position = vec4(final_ndc * base_clip.w, base_clip.z, base_clip.w);
    v_uv = uv;
    v_world_pos = mix(in_pos, in_other_pos, edge_t);
}
