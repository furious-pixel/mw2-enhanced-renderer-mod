#ifndef MW2ER_GL_PROGRAM_H
#define MW2ER_GL_PROGRAM_H

#include "gl_api.h"

#include <string>
#include <unordered_map>

/*
 * Linked program plus every active uniform location, queried once after
 * link. This is not a material: textures, remap constants, and blend /
 * depth / cull stay with the texture cache and the draw pass.
 */
struct GlProgram {
    GLuint id;
    std::unordered_map<std::string, GLint> uniforms;

    GlProgram() : id(0) {}

    bool load(const char *vert_path, const char *frag_path);
    void destroy();
    bool ok() const { return id != 0; }
    void use() const;

    GLint loc(const char *name) const;
    void set(const char *name, int v) const;
    void set(const char *name, float v) const;
    void set2(const char *name, float x, float y) const;
    void set3(const char *name, const float *v) const;
    void set4x4(const char *name, const float *m) const;
};

#endif
