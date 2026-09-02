float finalShadeLevel(float lighting_state, vec3 world_pos) {
    const float POLICY_PACK_SCALE = 4096.0;
    float component_policy = floor(
        lighting_state / POLICY_PACK_SCALE
    );
    float lit_shade_before_fog = (
        lighting_state - component_policy * POLICY_PACK_SCALE
    );
    float fog_shade_loss = 0.0;
    if (u_fog_distance != 0.0) {
        fog_shade_loss = (
            length(world_pos - u_camera_position) * 4.0
            / u_fog_distance
        );
    }
    float scene_shade_level = clamp(
        lit_shade_before_fog - fog_shade_loss,
        0.0,
        15.0
    );
    if (component_policy > 15.0) {
        float component_damage = component_policy - 15.0;
        return scene_shade_level + (
            component_damage * (15.0 - scene_shade_level) / 15.0
        );
    }
    if (component_policy > 0.0) {
        return scene_shade_level * (16.0 - component_policy) / 16.0;
    }
    return scene_shade_level;
}
