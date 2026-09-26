// Shared std140 ABI: renderer/scene_uniforms.h. OpenGL 3.3 assigns binding
// points at link time; do not use the later GLSL layout(binding=...) syntax.
layout(std140) uniform SceneFrame {
    vec4 scene_lighting;
};
layout(std140) uniform SceneView {
    mat4 u_projection;
    vec4 scene_camera_position;
    vec4 scene_camera_right;
    vec4 scene_camera_up;
    vec4 scene_camera_forward;
    vec4 scene_viewport;
    vec4 scene_imaging;
};
layout(std140) uniform SceneDraw {
    vec4 scene_clip;
};

#define u_fog_distance scene_lighting.x
#define u_camera_position scene_camera_position.xyz
#define u_camera_right scene_camera_right.xyz
#define u_camera_up scene_camera_up.xyz
#define u_camera_forward scene_camera_forward.xyz
#define u_viewport_size scene_viewport.xy
#define u_satellite_billboard int(scene_viewport.z)
#define u_wireframe_fade_start scene_imaging.x
#define u_wireframe_fade_end scene_imaging.y
#define u_near_clip_plane scene_clip.x
