#include "gl_program.h"
#include "mw2er_internal.h"

#include <stdio.h>
#include <string>
#include <vector>

static std::string dirname_of(const char *path)
{
    std::string p = path ? path : "";
    size_t slash = p.find_last_of("/\\");
    if (slash == std::string::npos) {
        return std::string();
    }
    return p.substr(0, slash);
}

static void replace_all(std::string &s, const std::string &from, const std::string &to)
{
    size_t pos = 0;
    while ((pos = s.find(from, pos)) != std::string::npos) {
        s.replace(pos, from.size(), to);
        pos += to.size();
    }
}

static std::string read_file(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        mw2er_set_error("shader file open failed");
        return std::string();
    }
    if (fseek(f, 0, SEEK_END) != 0) {
        fclose(f);
        mw2er_set_error("shader seek failed");
        return std::string();
    }
    long size = ftell(f);
    if (size < 0) {
        fclose(f);
        mw2er_set_error("shader size failed");
        return std::string();
    }
    rewind(f);
    std::string buf;
    buf.resize((size_t)size);
    if (size > 0 && fread(&buf[0], 1, (size_t)size, f) != (size_t)size) {
        fclose(f);
        mw2er_set_error("shader read failed");
        return std::string();
    }
    fclose(f);
    return buf;
}

static GLuint compile_shader(GLenum type, const char *source)
{
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, NULL);
    glCompileShader(shader);
    GLint ok = 0;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[512];
        glGetShaderInfoLog(shader, (GLsizei)sizeof(log), NULL, log);
        mw2er_set_error(log[0] ? log : "shader compile failed");
        glDeleteShader(shader);
        return 0;
    }
    return shader;
}

static void cache_uniforms(GlProgram &p)
{
    GLint count = 0;
    glGetProgramiv(p.id, GL_ACTIVE_UNIFORMS, &count);
    p.uniforms.clear();
    for (GLint i = 0; i < count; ++i) {
        char name[128];
        GLsizei len = 0;
        GLint size = 0;
        GLenum type = 0;
        glGetActiveUniform(p.id, (GLuint)i, (GLsizei)sizeof(name), &len, &size, &type, name);
        if (len <= 0) {
            continue;
        }
        GLint loc = glGetUniformLocation(p.id, name);
        if (loc >= 0) {
            p.uniforms[std::string(name, (size_t)len)] = loc;
        }
    }
}

static std::string read_file_optional(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        return std::string();
    }
    fclose(f);
    return read_file(path);
}

static std::string apply_shader_tokens(std::string src, const char *path)
{
    std::string dir = dirname_of(path);
    std::string lighting;
    std::string common;
    if (!dir.empty()) {
        lighting = read_file_optional((dir + "/scene_lighting.glsl").c_str());
        common = read_file_optional((dir + "/indexed_texmap_common.glsl").c_str());
        if (!common.empty()) {
            replace_all(common, "@SCENE_LIGHTING_FUNCTIONS@", lighting);
        }
    }
    replace_all(src, "@SCENE_LIGHTING_FUNCTIONS@", lighting);
    replace_all(src, "@INDEXED_TEXMAP_FUNCTIONS@", common);
    replace_all(src, "@MODE4_EMISSIVE_C_IN_THRESHOLD@", "48.0");
    replace_all(src, "@TRANSPARENT_PALETTE_INDEX@", "255.0");
    return src;
}

bool GlProgram::load(const char *vert_path, const char *frag_path)
{
    destroy();
    std::string vert_src = apply_shader_tokens(read_file(vert_path), vert_path);
    std::string frag_src = apply_shader_tokens(read_file(frag_path), frag_path);
    if (vert_src.empty() || frag_src.empty()) {
        return false;
    }
    GLuint vs = compile_shader(GL_VERTEX_SHADER, vert_src.c_str());
    GLuint fs = compile_shader(GL_FRAGMENT_SHADER, frag_src.c_str());
    if (vs == 0 || fs == 0) {
        if (vs) {
            glDeleteShader(vs);
        }
        if (fs) {
            glDeleteShader(fs);
        }
        return false;
    }
    id = glCreateProgram();
    glAttachShader(id, vs);
    glAttachShader(id, fs);
    glBindAttribLocation(id, 0, "in_pos");
    glBindAttribLocation(id, 1, "in_palette_index");
    glBindAttribLocation(id, 1, "in_palette_mix");
    glBindAttribLocation(id, 1, "in_c_in");
    glBindAttribLocation(id, 2, "in_lighting_state");
    glBindAttribLocation(id, 1, "in_other_pos");
    glBindAttribLocation(id, 2, "in_billboard_flags");
    glBindAttribLocation(id, 1, "in_uv");
    glLinkProgram(id);
    glDeleteShader(vs);
    glDeleteShader(fs);
    GLint ok = 0;
    glGetProgramiv(id, GL_LINK_STATUS, &ok);
    if (!ok) {
        char log[512];
        glGetProgramInfoLog(id, (GLsizei)sizeof(log), NULL, log);
        mw2er_set_error(log[0] ? log : "shader link failed");
        glDeleteProgram(id);
        id = 0;
        return false;
    }
    cache_uniforms(*this);
    return true;
}

void GlProgram::destroy()
{
    if (id != 0) {
        glDeleteProgram(id);
        id = 0;
    }
    uniforms.clear();
}

void GlProgram::use() const
{
    glUseProgram(id);
}

GLint GlProgram::loc(const char *name) const
{
    auto it = uniforms.find(name);
    if (it == uniforms.end()) {
        return -1;
    }
    return it->second;
}

void GlProgram::set(const char *name, int v) const
{
    GLint l = loc(name);
    if (l >= 0) {
        glUniform1i(l, v);
    }
}

void GlProgram::set(const char *name, float v) const
{
    GLint l = loc(name);
    if (l >= 0) {
        glUniform1f(l, v);
    }
}

void GlProgram::set2(const char *name, float x, float y) const
{
    GLint l = loc(name);
    if (l >= 0) {
        glUniform2f(l, x, y);
    }
}

void GlProgram::set3(const char *name, const float *v) const
{
    GLint l = loc(name);
    if (l >= 0) {
        glUniform3fv(l, 1, v);
    }
}

void GlProgram::set4x4(const char *name, const float *m) const
{
    GLint l = loc(name);
    if (l >= 0) {
        glUniformMatrix4fv(l, 1, GL_FALSE, m);
    }
}
