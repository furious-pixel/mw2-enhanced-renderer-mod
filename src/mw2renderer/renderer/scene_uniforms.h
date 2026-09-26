#ifndef MW2ER_SCENE_UNIFORMS_H
#define MW2ER_SCENE_UNIFORMS_H

#include <cstddef>

// std140 contract shared with scene_uniforms.glsl. All rows are 16 bytes;
// matrices are column-major. These binding points belong to the renderer.
enum { SCENE_FRAME_BINDING = 0, SCENE_VIEW_BINDING = 1, SCENE_DRAW_BINDING = 2 };

struct alignas(16) SceneFrameUniforms {
    float lighting[4]; // fog distance, unused
};

struct alignas(16) SceneViewUniforms {
    float projection[16];
    float position[4];
    float right[4];
    float up[4];
    float forward[4];
    float viewport[4]; // raster width, height, satellite flag, unused
    float imaging[4]; // wireframe fade start, end, unused
};

struct alignas(16) SceneDrawUniforms {
    float clip[4]; // near clip, unused
};

static_assert(sizeof(SceneFrameUniforms) == 16);
static_assert(sizeof(SceneViewUniforms) == 160);
static_assert(offsetof(SceneViewUniforms, position) == 64);
static_assert(offsetof(SceneViewUniforms, viewport) == 128);
static_assert(offsetof(SceneViewUniforms, imaging) == 144);
static_assert(sizeof(SceneDrawUniforms) == 16);

#endif
