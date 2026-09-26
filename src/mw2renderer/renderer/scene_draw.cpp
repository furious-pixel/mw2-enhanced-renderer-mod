#include "scene_draw.h"
#include "config.h"
#include "hud.h"
#include "mw2er_internal.h"
#include "gl_program.h"
#include "scene_uniforms.h"
#include "scene_extract.h"
#include "texture.h"
#include "startup_trace.h"

#include "gl_api.h"

#include <chrono>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

enum {
    SKY_RADIUS = 200,
    SKY_SEGMENTS = 64,
    SKY_STACKS = 16,
    GRADIENT_VERTICAL_SEGMENTS = 14,
    ROTOR_DISC_SEGMENTS = 64
};

/* Renderer-wide texture-unit contract. Keep units 0-3 available to draw
 * calls; resource uploads use the same unit as their eventual sampler and
 * must restore unit 0 before returning to palette-based drawing. */
enum TextureUnit {
    TEXTURE_UNIT_PALETTE = 0,   /* RGB palette sampled by indexed passes */
    TEXTURE_UNIT_INDEXED = 1,   /* nearest-sampled CEL/index texture */
    TEXTURE_UNIT_PRIMITIVE = 2, /* gl_PrimitiveID-indexed metadata */
    TEXTURE_UNIT_RESERVED = 3   /* future draw input, not upload scratch */
};

static const float k_proj_base_height = 768.0f;

struct MeshBuf {
    GLuint vao;
    GLuint vbo;
    GLsizei count; /* vertices */
};

/* One draw pass: linked program + named uniform locations. Blend / depth /
 * cull are applied in the draw function â€” rotors and HUD later change those
 * while keeping the same program and the same cached textures. */
struct SkyPass {
    GlProgram prog;
    GLint u_y_scale;
    GLint u_palette;
    GLint u_palette_start;
    GLint u_palette_end;
};

struct Mode4Pass {
    GlProgram prog;
    GLint u_palette;
};

struct BillboardPass {
    GlProgram prog;
    GLint u_palette;
    GLint u_indexed_texture;
};

struct TexmapPass {
    GlProgram prog;
    GLint u_uv_scale;
    GLint u_palette;
    GLint u_indexed_texture;
    GLint u_remap_kind;
    GLint u_dark_ratio;
    GLint u_fog_terminal_color;
    GLint u_s8_ratio;
    GLint u_texture_role;
    GLint u_texture_size;
    GLint u_rotor_enhanced;
    GLint u_rotor_texture_size;
    GLint u_primitive_lighting;
    GLint u_rotor_lighting;
    GLint u_canonical_rotor;
    GLint u_rotor_center;
    GLint u_rotor_axis_u;
    GLint u_rotor_axis_v;
};

struct RotorOutlinePass {
    GlProgram prog;
    GLint u_rotor_center;
    GLint u_rotor_axis_u;
    GLint u_rotor_axis_v;
    GLint u_palette;
    GLint u_palette_index;
};

struct IndexedGeoPass {
    GlProgram prog;
    GLint u_palette;
    GLint u_primitive_palette;
    GLint u_constant_palette;
};

struct IndexedBuf {
    GLuint vao;
    GLuint vbo;
    GLuint ebo;
    GLuint prim_tex;
    size_t vbo_bytes;
    size_t ebo_bytes;
    int prim_w;
};

struct WireBuf {
    GLuint vao_occ;
    GLuint vao_line;
    GLuint vbo;
    GLuint occ_ebo;
    GLuint line_ebo;
    GLuint line_prim;
    size_t vbo_bytes;
    size_t occ_bytes;
    size_t line_bytes;
    int prim_w;
};

struct GeoPass {
    GlProgram prog;
    GLint u_point_size;
    GLint u_palette;
};

struct OccluderPass {
    GlProgram prog;
    GLint u_palette;
    GLint u_palette_index;
};

/* Per-desc indexed view of a shared xyz-uv VBO (Python SharedDynamicIndexedMeshSet). */
struct DescIndexed {
    GLuint vao;
    GLuint ebo;
    GLuint prim_tex;
    size_t ebo_bytes;
    int prim_w;
};

struct StreamBuf {
    GLuint vao;
    GLuint vbo;
    size_t cap;
};

struct PartGpu {
    MeshBuf tris;
    MeshBuf flats;
    MeshBuf points;
    MeshBuf lines;
    IndexedBuf idx_flat;
    IndexedBuf idx_tex; /* shared texmap VBO; EBO unused */
    WireBuf wire;
    DescIndexed texmap[MW2ER_MAX_DESC];
    StreamBuf billboard[MW2ER_MAX_DESC];
    /* [role][late effect]. The late column reuses the same uploaded meshes. */
    std::vector<uint16_t> texmap_streams[2][2]; /* ordinary/camo, normal/late */
    std::vector<uint16_t> billboard_streams[2]; /* normal, late */
    std::vector<IndexedBuf> aero_fan;
    size_t tri_vbo_bytes;
    size_t flat_vbo_bytes;
    size_t point_vbo_bytes;
    size_t line_vbo_bytes;
};

struct CelGpu {
    int resource_id;
    int w;
    int h;
    int wrap;
    int role;
    GLuint tex;
    const uint8_t *source_pixels;
};

struct SceneGpu {
    int ready;
    SkyPass sky;
    GeoPass geo;
    IndexedGeoPass idx_geo;
    Mode4Pass mode4;
    BillboardPass billboards;
    TexmapPass texmaps;
    TexmapPass camo;
    TexmapPass rotor;
    RotorOutlinePass rotor_outline;
    OccluderPass occluder;
    GLuint palette_tex;
    GLuint uniform_buffers[3];
    bool frame_uniforms_dirty;
    MeshBuf sky_mesh;
    MeshBuf gradient;
    IndexedBuf rotor_disc;
    /* Python indexed_texture_cache: one GL texture per CEL image.
     * Key is (resource_id, w, h, wrap, enhancement_role), matching
     * Python signature ("CEL", rid, w, h, pixels) + wrap. */
    std::vector<CelGpu> cel;
    /* Python indexed_textures[desc]: current CEL for that descriptor slot. */
    struct {
        int valid;
        GLuint tex;
        int width;
        int height;
        int enhancement_role_id;
        float enhanced_uv_scale;
        int remap_kind_id;
        float dark_ratio[3];
        float fog_terminal[3];
        float s8_ratio[3];
        int resource_id;
        int animated_effect;
    } desc[MW2ER_MAX_DESC];
};

static SceneGpu g_gpu;
/* Primary and (only when needed) ordinary MFD geometry share all textures. */
struct SceneGeometry {
    Mw2erSceneExtract ex;
    PartGpu part[MW2ER_PART_COUNT];
    int force_upload;
    int changed;
};
static SceneGeometry g_primary, g_mfd;
static uint32_t g_required_geometry;
static Mw2erRenderView g_primary_view = MW2ER_VIEW_NONE;
static double g_last_extract_ms;
static double g_last_draw_ms;
static int g_gpu_mission_reset_pending;
static int g_retained_workload;
static int g_debug_groups_requested;
static float g_line_range_max = 1.0f;
static Mw2erResolvedTexture g_resolved_desc[MW2ER_MAX_DESC];
static Mw2erMaterialResult g_resolved_desc_status[MW2ER_MAX_DESC];
// Bounded, once-per-reason diagnostics for each descriptor in this mission.
static uint8_t g_reported_material_issues[MW2ER_MAX_DESC];
static uint32_t g_unavailable_materials, g_unsupported_materials;

static double now_ms(void)
{
    using clock = std::chrono::steady_clock;
    return std::chrono::duration<double, std::milli>(
               clock::now().time_since_epoch())
        .count();
}

void mw2er_scene_last_cpu_timing(double *extract_ms, double *draw_submit_ms)
{
    if (extract_ms) {
        *extract_ms = g_last_extract_ms;
    }
    if (draw_submit_ms) {
        *draw_submit_ms = g_last_draw_ms;
    }
}

static void set1i(GLint loc, int v)
{
    if (loc >= 0) {
        glUniform1i(loc, v);
    }
}

static void set1f(GLint loc, float v)
{
    if (loc >= 0) {
        glUniform1f(loc, v);
    }
}

static void set2f(GLint loc, float x, float y)
{
    if (loc >= 0) {
        glUniform2f(loc, x, y);
    }
}

static void set2i(GLint loc, int x, int y)
{
    if (loc >= 0) {
        glUniform2i(loc, x, y);
    }
}

static void set3f(GLint loc, const float *v)
{
    if (loc >= 0) {
        glUniform3fv(loc, 1, v);
    }
}

static int desc_ok(int desc)
{
    return desc >= 0 && desc < MW2ER_MAX_DESC;
}

static void mesh_destroy(MeshBuf &m)
{
    if (m.vao) {
        glDeleteVertexArrays(1, &m.vao);
    }
    if (m.vbo) {
        glDeleteBuffers(1, &m.vbo);
    }
    memset(&m, 0, sizeof(m));
}

static void indexed_destroy(IndexedBuf &m)
{
    if (m.vao) {
        glDeleteVertexArrays(1, &m.vao);
    }
    if (m.vbo) {
        glDeleteBuffers(1, &m.vbo);
    }
    if (m.ebo) {
        glDeleteBuffers(1, &m.ebo);
    }
    if (m.prim_tex) {
        glDeleteTextures(1, &m.prim_tex);
    }
    memset(&m, 0, sizeof(m));
}

static void wire_destroy(WireBuf &m)
{
    if (m.vao_occ) {
        glDeleteVertexArrays(1, &m.vao_occ);
    }
    if (m.vao_line) {
        glDeleteVertexArrays(1, &m.vao_line);
    }
    if (m.vbo) {
        glDeleteBuffers(1, &m.vbo);
    }
    if (m.occ_ebo) {
        glDeleteBuffers(1, &m.occ_ebo);
    }
    if (m.line_ebo) {
        glDeleteBuffers(1, &m.line_ebo);
    }
    if (m.line_prim) {
        glDeleteTextures(1, &m.line_prim);
    }
    memset(&m, 0, sizeof(m));
}

static void indexed_create_pos(IndexedBuf &m)
{
    indexed_destroy(m);
    glGenVertexArrays(1, &m.vao);
    glGenBuffers(1, &m.vbo);
    glGenBuffers(1, &m.ebo);
    glBindVertexArray(m.vao);
    glBindBuffer(GL_ARRAY_BUFFER, m.vbo);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 12, (void *)0);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, m.ebo);
    glBindVertexArray(0);
}

static void indexed_create_pos_uv(IndexedBuf &m)
{
    indexed_destroy(m);
    glGenVertexArrays(1, &m.vao);
    glGenBuffers(1, &m.vbo);
    glGenBuffers(1, &m.ebo);
    glBindVertexArray(m.vao);
    glBindBuffer(GL_ARRAY_BUFFER, m.vbo);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 20, (void *)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 20, (void *)(3 * sizeof(float)));
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, m.ebo);
    glBindVertexArray(0);
}

static void wire_destroy_occ(WireBuf &m)
{
    if (m.vao_occ) {
        glDeleteVertexArrays(1, &m.vao_occ);
    }
    if (m.occ_ebo) {
        glDeleteBuffers(1, &m.occ_ebo);
    }
    m.vao_occ = 0;
    m.occ_ebo = 0;
    m.occ_bytes = 0;
}

static void wire_destroy_line(WireBuf &m)
{
    if (m.vao_line) {
        glDeleteVertexArrays(1, &m.vao_line);
    }
    if (m.line_ebo) {
        glDeleteBuffers(1, &m.line_ebo);
    }
    if (m.line_prim) {
        glDeleteTextures(1, &m.line_prim);
    }
    m.vao_line = 0;
    m.line_ebo = 0;
    m.line_prim = 0;
    m.line_bytes = 0;
    m.prim_w = 0;
}

static void wire_ensure_vbo(WireBuf &m)
{
    if (m.vbo != 0) {
        return;
    }
    glGenBuffers(1, &m.vbo);
}

static void wire_ensure_occ(WireBuf &m)
{
    wire_ensure_vbo(m);
    if (m.vao_occ != 0) {
        return;
    }
    glGenBuffers(1, &m.occ_ebo);
    glGenVertexArrays(1, &m.vao_occ);
    glBindVertexArray(m.vao_occ);
    glBindBuffer(GL_ARRAY_BUFFER, m.vbo);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 12, (void *)0);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, m.occ_ebo);
    glBindVertexArray(0);
}

static void wire_ensure_line(WireBuf &m)
{
    wire_ensure_vbo(m);
    if (m.vao_line != 0) {
        return;
    }
    glGenBuffers(1, &m.line_ebo);
    glGenVertexArrays(1, &m.vao_line);
    glBindVertexArray(m.vao_line);
    glBindBuffer(GL_ARRAY_BUFFER, m.vbo);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 12, (void *)0);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, m.line_ebo);
    glBindVertexArray(0);
}

static void desc_indexed_destroy(DescIndexed &m)
{
    if (m.vao) {
        glDeleteVertexArrays(1, &m.vao);
    }
    if (m.ebo) {
        glDeleteBuffers(1, &m.ebo);
    }
    if (m.prim_tex) {
        glDeleteTextures(1, &m.prim_tex);
    }
    memset(&m, 0, sizeof(m));
}

static void desc_indexed_ensure(DescIndexed &m, GLuint shared_vbo)
{
    if (m.vao != 0) {
        return;
    }
    glGenVertexArrays(1, &m.vao);
    glGenBuffers(1, &m.ebo);
    glBindVertexArray(m.vao);
    glBindBuffer(GL_ARRAY_BUFFER, shared_vbo);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 20, (void *)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(
        1, 2, GL_FLOAT, GL_FALSE, 20, (void *)(3 * sizeof(float)));
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, m.ebo);
    glBindVertexArray(0);
}

static void stream_buf_destroy(StreamBuf &m)
{
    if (m.vao) {
        glDeleteVertexArrays(1, &m.vao);
    }
    if (m.vbo) {
        glDeleteBuffers(1, &m.vbo);
    }
    memset(&m, 0, sizeof(m));
}

static void stream_buf_ensure_billboard(StreamBuf &m)
{
    if (m.vao != 0) {
        return;
    }
    glGenVertexArrays(1, &m.vao);
    glGenBuffers(1, &m.vbo);
    glBindVertexArray(m.vao);
    glBindBuffer(GL_ARRAY_BUFFER, m.vbo);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 28, (void *)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(
        1, 3, GL_FLOAT, GL_FALSE, 28, (void *)(3 * sizeof(float)));
    glEnableVertexAttribArray(2);
    glVertexAttribPointer(
        2, 1, GL_FLOAT, GL_FALSE, 28, (void *)(6 * sizeof(float)));
    glBindVertexArray(0);
}

static void mesh_ensure_xyz_pal(MeshBuf &m)
{
    if (m.vao != 0) {
        return;
    }
    glGenVertexArrays(1, &m.vao);
    glGenBuffers(1, &m.vbo);
    glBindVertexArray(m.vao);
    glBindBuffer(GL_ARRAY_BUFFER, m.vbo);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 16, (void *)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 1, GL_FLOAT, GL_FALSE, 16, (void *)(3 * sizeof(float)));
    glBindVertexArray(0);
}

static void mesh_ensure_mode4(MeshBuf &m)
{
    if (m.vao != 0) {
        return;
    }
    glGenVertexArrays(1, &m.vao);
    glGenBuffers(1, &m.vbo);
    glBindVertexArray(m.vao);
    glBindBuffer(GL_ARRAY_BUFFER, m.vbo);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 20, (void *)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 1, GL_FLOAT, GL_FALSE, 20, (void *)(3 * sizeof(float)));
    glEnableVertexAttribArray(2);
    glVertexAttribPointer(2, 1, GL_FLOAT, GL_FALSE, 20, (void *)(4 * sizeof(float)));
    glBindVertexArray(0);
}

static void indexed_ensure_pos(IndexedBuf &m)
{
    if (m.vao != 0) {
        return;
    }
    indexed_create_pos(m);
}

static void shared_tex_vbo_ensure(IndexedBuf &m)
{
    if (m.vbo != 0) {
        return;
    }
    glGenBuffers(1, &m.vbo);
}

static void part_gpu_create(PartGpu &p)
{
    p = PartGpu{};
}

static void part_gpu_destroy(PartGpu &p)
{
    mesh_destroy(p.tris);
    mesh_destroy(p.flats);
    mesh_destroy(p.points);
    mesh_destroy(p.lines);
    indexed_destroy(p.idx_flat);
    indexed_destroy(p.idx_tex);
    wire_destroy(p.wire);
    for (int i = 0; i < MW2ER_MAX_DESC; ++i) {
        desc_indexed_destroy(p.texmap[i]);
        stream_buf_destroy(p.billboard[i]);
    }
    for (size_t i = 0; i < p.aero_fan.size(); ++i) {
        indexed_destroy(p.aero_fan[i]);
    }
    p = PartGpu{};
}

static int mesh_create(MeshBuf &m, const float *data, size_t floats)
{
    mesh_destroy(m);
    glGenVertexArrays(1, &m.vao);
    glGenBuffers(1, &m.vbo);
    glBindVertexArray(m.vao);
    glBindBuffer(GL_ARRAY_BUFFER, m.vbo);
    glBufferData(
        GL_ARRAY_BUFFER,
        (GLsizeiptr)(floats * sizeof(float)),
        data,
        GL_STATIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 16, (void *)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 1, GL_FLOAT, GL_FALSE, 16, (void *)(3 * sizeof(float)));
    glBindVertexArray(0);
    m.count = (GLsizei)(floats / 4);
    return 1;
}

static int make_path(char *out, size_t out_size, const char *dir, const char *file)
{
    if (dir == NULL || dir[0] == '\0') {
        mw2er_set_error("shader_dir is empty");
        return 0;
    }
    snprintf(out, out_size, "%s/%s", dir, file);
    return 1;
}

static bool load_shader(GlProgram &p, const char *vert_name, const char *frag_name)
{
    char vert[MW2ER_PATH_MAX];
    char frag[MW2ER_PATH_MAX];
    const char *dir = mw2er_shader_dir();
    if (!make_path(vert, sizeof(vert), dir, vert_name) ||
        !make_path(frag, sizeof(frag), dir, frag_name)) {
        return false;
    }
    return p.load(vert, frag);
}

static bool load_sky_pass(SkyPass &p)
{
    if (!load_shader(p.prog, "sky.vert", "sky.frag")) {
        return false;
    }
    p.u_y_scale = p.prog.loc("u_y_scale");
    p.u_palette = p.prog.loc("u_palette");
    p.u_palette_start = p.prog.loc("u_palette_start");
    p.u_palette_end = p.prog.loc("u_palette_end");
    p.prog.use();
    set1i(p.u_palette, TEXTURE_UNIT_PALETTE);
    glUseProgram(0);
    return true;
}

static bool load_mode4_pass(Mode4Pass &p)
{
    if (!load_shader(p.prog, "mode4.vert", "mode4.frag")) {
        return false;
    }
    p.u_palette = p.prog.loc("u_palette");
    p.prog.use();
    set1i(p.u_palette, TEXTURE_UNIT_PALETTE);
    glUseProgram(0);
    return true;
}

static bool load_billboard_pass(BillboardPass &p)
{
    if (!load_shader(p.prog, "textured.vert", "textured.frag")) {
        return false;
    }
    p.u_palette = p.prog.loc("u_palette");
    p.u_indexed_texture = p.prog.loc("u_indexed_texture");
    p.prog.use();
    set1i(p.u_palette, TEXTURE_UNIT_PALETTE);
    set1i(p.u_indexed_texture, TEXTURE_UNIT_INDEXED);
    glUseProgram(0);
    return true;
}

static bool load_texmap_pass(
    TexmapPass &p, const char *vert_name, const char *frag_name)
{
    if (!load_shader(p.prog, vert_name, frag_name)) {
        return false;
    }
    p.u_uv_scale = p.prog.loc("u_uv_scale");
    p.u_palette = p.prog.loc("u_palette");
    p.u_indexed_texture = p.prog.loc("u_indexed_texture");
    p.u_remap_kind = p.prog.loc("u_remap_kind");
    p.u_dark_ratio = p.prog.loc("u_dark_ratio");
    p.u_fog_terminal_color = p.prog.loc("u_fog_terminal_color");
    p.u_s8_ratio = p.prog.loc("u_s8_ratio");
    p.u_texture_role = p.prog.loc("u_texture_role");
    p.u_texture_size = p.prog.loc("u_texture_size");
    p.u_rotor_enhanced = p.prog.loc("u_rotor_enhanced");
    p.u_rotor_texture_size = p.prog.loc("u_rotor_texture_size");
    p.u_primitive_lighting = p.prog.loc("u_primitive_lighting");
    p.u_rotor_lighting = p.prog.loc("u_rotor_lighting");
    p.u_canonical_rotor = p.prog.loc("u_canonical_rotor");
    p.u_rotor_center = p.prog.loc("u_rotor_center");
    p.u_rotor_axis_u = p.prog.loc("u_rotor_axis_u");
    p.u_rotor_axis_v = p.prog.loc("u_rotor_axis_v");
    p.prog.use();
    set1i(p.u_palette, TEXTURE_UNIT_PALETTE);
    set1i(p.u_indexed_texture, TEXTURE_UNIT_INDEXED);
    set1i(p.u_primitive_lighting, TEXTURE_UNIT_PRIMITIVE);
    set1i(p.u_canonical_rotor, 0);
    glUseProgram(0);
    return true;
}

static bool load_rotor_outline_pass(RotorOutlinePass &p)
{
    if (!load_shader(p.prog, "rotor_outline.vert", "rotor_outline.frag")) {
        return false;
    }
    p.u_rotor_center = p.prog.loc("u_rotor_center");
    p.u_rotor_axis_u = p.prog.loc("u_rotor_axis_u");
    p.u_rotor_axis_v = p.prog.loc("u_rotor_axis_v");
    p.u_palette = p.prog.loc("u_palette");
    p.u_palette_index = p.prog.loc("u_palette_index");
    p.prog.use();
    set1i(p.u_palette, TEXTURE_UNIT_PALETTE);
    glUseProgram(0);
    return true;
}

static bool load_indexed_geo_pass(IndexedGeoPass &p)
{
    if (!load_shader(p.prog, "indexed_geometry.vert", "indexed_geometry.frag")) {
        return false;
    }
    p.u_palette = p.prog.loc("u_palette");
    p.u_primitive_palette = p.prog.loc("u_primitive_palette");
    p.u_constant_palette = p.prog.loc("u_constant_palette");
    p.prog.use();
    set1i(p.u_palette, TEXTURE_UNIT_PALETTE);
    set1i(p.u_primitive_palette, TEXTURE_UNIT_PRIMITIVE);
    glUseProgram(0);
    return true;
}

static bool load_occluder_pass(OccluderPass &p)
{
    if (!load_shader(p.prog, "wireframe_occluder.vert", "wireframe_occluder.frag")) {
        return false;
    }
    p.u_palette = p.prog.loc("u_palette");
    p.u_palette_index = p.prog.loc("u_palette_index");
    p.prog.use();
    set1i(p.u_palette, TEXTURE_UNIT_PALETTE);
    set1f(p.u_palette_index, 0.0f);
    glUseProgram(0);
    return true;
}

static bool load_geo_pass(GeoPass &p)
{
    if (!load_shader(p.prog, "geometry.vert", "geometry.frag")) {
        return false;
    }
    p.u_point_size = p.prog.loc("u_point_size");
    p.u_palette = p.prog.loc("u_palette");
    p.prog.use();
    set1i(p.u_palette, TEXTURE_UNIT_PALETTE);
    glUseProgram(0);
    return true;
}

static int build_sky_meshes(void)
{
    VertStream sky;
    VertStream grad;
    int stack;
    int ring;
    int seg;

    for (stack = 0; stack < SKY_STACKS; ++stack) {
        float elev0 = ((float)stack / (float)SKY_STACKS) * (float)(M_PI * 0.5);
        float elev1 = ((float)(stack + 1) / (float)SKY_STACKS) * (float)(M_PI * 0.5);
        float y0 = sinf(elev0) * (float)SKY_RADIUS;
        float y1 = sinf(elev1) * (float)SKY_RADIUS;
        float r0 = cosf(elev0) * (float)SKY_RADIUS;
        float r1 = cosf(elev1) * (float)SKY_RADIUS;
        for (seg = 0; seg < SKY_SEGMENTS; ++seg) {
            float a0 = ((float)seg / (float)SKY_SEGMENTS) * (float)(M_PI * 2.0);
            float a1 = ((float)(seg + 1) / (float)SKY_SEGMENTS) * (float)(M_PI * 2.0);
            float p00x = sinf(a0) * r0, p00z = cosf(a0) * r0;
            float p01x = sinf(a1) * r0, p01z = cosf(a1) * r0;
            float p10x = sinf(a0) * r1, p10z = cosf(a0) * r1;
            float p11x = sinf(a1) * r1, p11z = cosf(a1) * r1;
            sky.emit({p00x, y0, p00z, 0.0f});
            sky.emit({p10x, y1, p10z, 0.0f});
            sky.emit({p11x, y1, p11z, 0.0f});
            sky.emit({p00x, y0, p00z, 0.0f});
            sky.emit({p11x, y1, p11z, 0.0f});
            sky.emit({p01x, y0, p01z, 0.0f});
        }
    }
    for (ring = 0; ring < GRADIENT_VERTICAL_SEGMENTS; ++ring) {
        float t0 = (float)ring / (float)GRADIENT_VERTICAL_SEGMENTS;
        float t1 = (float)(ring + 1) / (float)GRADIENT_VERTICAL_SEGMENTS;
        float y0 = 1.0f - t0;
        float y1 = 1.0f - t1;
        for (seg = 0; seg < SKY_SEGMENTS; ++seg) {
            float a0 = ((float)seg / (float)SKY_SEGMENTS) * (float)(M_PI * 2.0);
            float a1 = ((float)(seg + 1) / (float)SKY_SEGMENTS) * (float)(M_PI * 2.0);
            float p00x = sinf(a0) * (float)SKY_RADIUS;
            float p00z = cosf(a0) * (float)SKY_RADIUS;
            float p01x = sinf(a1) * (float)SKY_RADIUS;
            float p01z = cosf(a1) * (float)SKY_RADIUS;
            float p10x = sinf(a0) * (float)SKY_RADIUS;
            float p10z = cosf(a0) * (float)SKY_RADIUS;
            float p11x = sinf(a1) * (float)SKY_RADIUS;
            float p11z = cosf(a1) * (float)SKY_RADIUS;
            grad.emit({p00x, y0, p00z, t0});
            grad.emit({p10x, y1, p10z, t1});
            grad.emit({p11x, y1, p11z, t1});
            grad.emit({p00x, y0, p00z, t0});
            grad.emit({p11x, y1, p11z, t1});
            grad.emit({p01x, y0, p01z, t0});
        }
    }
    return mesh_create(g_gpu.sky_mesh, sky.ptr(), sky.floats()) &&
           mesh_create(g_gpu.gradient, grad.ptr(), grad.floats());
}

static int build_rotor_disc(void)
{
    /* Center, one complete rim, then rim[0] again to close GL_TRIANGLE_FAN.
     * GL_LINE_LOOP uses only the 64 unique rim vertices. */
    float vertices[(ROTOR_DISC_SEGMENTS + 2) * 5];
    int cursor = 0;
    const float uv_radius = 0.49f;
    vertices[cursor++] = 0.0f;
    vertices[cursor++] = 0.0f;
    vertices[cursor++] = 0.0f;
    vertices[cursor++] = 0.5f;
    vertices[cursor++] = 0.5f;
    for (int i = 0; i <= ROTOR_DISC_SEGMENTS; ++i) {
        int rim = i % ROTOR_DISC_SEGMENTS;
        float angle = (float)rim * (float)(2.0 * M_PI) /
                      (float)ROTOR_DISC_SEGMENTS;
        float c = cosf(angle);
        float s = sinf(angle);
        vertices[cursor++] = c;
        vertices[cursor++] = s;
        vertices[cursor++] = 0.0f;
        vertices[cursor++] = 0.5f + uv_radius * c;
        vertices[cursor++] = 0.5f + uv_radius * s;
    }
    indexed_create_pos_uv(g_gpu.rotor_disc);
    glBindBuffer(GL_ARRAY_BUFFER, g_gpu.rotor_disc.vbo);
    glBufferData(
        GL_ARRAY_BUFFER,
        (GLsizeiptr)sizeof(vertices),
        vertices,
        GL_STATIC_DRAW);
    g_gpu.rotor_disc.vbo_bytes = sizeof(vertices);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    return 1;
}

float mw2er_scene_output_focal(const Mw2erCamera &cam,
                               int32_t width, int32_t height)
{
    width = width < 1 ? 1 : width;
    height = height < 1 ? 1 : height;
    float focal = cam.focal_length_pixels * (float)height / k_proj_base_height;
    if (focal < 1.0f) focal = 1.0f;
    float maximum = mw2er_config().max_horizontal_fov_degrees;
    if (maximum < 30.0f) maximum = 30.0f;
    if (maximum > 170.0f) maximum = 170.0f;
    const float max_radians = maximum * (float)M_PI / 180.0f;
    if (2.0f * atanf((float)width / (2.0f * focal)) > max_radians)
        focal = (float)width / (2.0f * tanf(max_radians * 0.5f));
    return focal;
}

static void make_projection(const Mw2erCamera &cam, int32_t width, int32_t height, float *out16)
{
    float aspect;
    float output_focal;
    float f;
    float sx;
    float sy;
    float near_p;
    float far_p;
    float z_scale;
    float z_offset;

    width = width < 1 ? 1 : width;
    height = height < 1 ? 1 : height;
    aspect = (float)width / (float)height;
    if (cam.projection_ortho) {
        float hw = cam.ortho_half_width;
        float hh = cam.ortho_half_height;
        if (hw < 1e-6f) {
            hw = 1e-6f;
        }
        if (hh < 1e-6f) {
            hh = hw;
        }
        if (cam.satellite_view) {
            /* Keep the native 1024x768 vertical range and reveal more world
             * horizontally. Do not apply the perspective horizontal-FOV cap
             * or preserve a fixed horizontal crop for this view. */
            hw = hh * aspect;
        }
        near_p = cam.near_plane < 1e-7f ? 1e-7f : cam.near_plane;
        far_p = cam.far_plane < near_p * 1.001f ? near_p * 1.001f : cam.far_plane;
        memset(out16, 0, 16 * sizeof(float));
        out16[0] = 1.0f / hw;
        out16[5] = 1.0f / hh;
        out16[10] = 2.0f / (far_p - near_p);
        out16[14] = -(far_p + near_p) / (far_p - near_p);
        out16[15] = 1.0f;
        return;
    }
    if (cam.pane_projection) {
        const float fx = cam.focal_length_pixels < 1.0f ? 1.0f : cam.focal_length_pixels;
        const float fy = fx * (cam.projection_aspect_scale > 0.0f
            ? cam.projection_aspect_scale : 1.0f);
        const float cx = cam.projection_center_x;
        const float cy = cam.projection_center_y;
        near_p = cam.near_plane < 1e-7f ? 1e-7f : cam.near_plane;
        far_p = cam.far_plane < near_p * 1.001f ? near_p * 1.001f : cam.far_plane;
        z_scale = far_p / (far_p - near_p);
        z_offset = -(near_p * far_p) / (far_p - near_p);
        memset(out16, 0, 16 * sizeof(float));
        out16[0] = 2.0f * fx / (float)width;
        out16[5] = 2.0f * fy / (float)height;
        out16[8] = 2.0f * cx / (float)width - 1.0f;
        out16[9] = 1.0f - 2.0f * cy / (float)height;
        out16[10] = z_scale;
        out16[11] = 1.0f;
        out16[14] = z_offset;
        return;
    }
    output_focal = mw2er_scene_output_focal(cam, width, height);
    f = 2.0f * output_focal / (float)height;
    sx = f / aspect;
    sy = f;
    near_p = cam.near_plane < 1e-7f ? 1e-7f : cam.near_plane;
    far_p = cam.far_plane < near_p * 1.001f ? near_p * 1.001f : cam.far_plane;
    z_scale = far_p / (far_p - near_p);
    z_offset = -(near_p * far_p) / (far_p - near_p);
    memset(out16, 0, 16 * sizeof(float));
    out16[0] = sx;
    out16[5] = sy;
    out16[10] = z_scale;
    out16[11] = 1.0f;
    out16[14] = z_offset;
}

static float palette_u(int index)
{
    if (index < 0) {
        index = 0;
    }
    if (index > 255) {
        index = 255;
    }
    return ((float)index + 0.5f) / 256.0f;
}

int32_t mw2er_scene_resources_init(void)
{
    mw2er_scene_resources_shutdown();
    glGenBuffers(3, g_gpu.uniform_buffers);
    g_gpu.frame_uniforms_dirty = true;
    if (!load_sky_pass(g_gpu.sky)) {
        return MW2ER_ERR_GL;
    }
    if (!load_geo_pass(g_gpu.geo)) {
        return MW2ER_ERR_GL;
    }
    if (!load_indexed_geo_pass(g_gpu.idx_geo)) {
        return MW2ER_ERR_GL;
    }
    if (!load_mode4_pass(g_gpu.mode4)) {
        return MW2ER_ERR_GL;
    }
    if (!load_billboard_pass(g_gpu.billboards)) {
        return MW2ER_ERR_GL;
    }
    if (!load_texmap_pass(
            g_gpu.texmaps, "indexed_texmap.vert", "indexed_texmap.frag")) {
        return MW2ER_ERR_GL;
    }
    if (!load_texmap_pass(
            g_gpu.camo, "indexed_texmap.vert", "camo_texmap.frag")) {
        return MW2ER_ERR_GL;
    }
    if (!load_texmap_pass(g_gpu.rotor, "rotor.vert", "rotor.frag")) {
        return MW2ER_ERR_GL;
    }
    if (!load_rotor_outline_pass(g_gpu.rotor_outline)) {
        return MW2ER_ERR_GL;
    }
    if (!load_occluder_pass(g_gpu.occluder)) {
        return MW2ER_ERR_GL;
    }
    if (!build_sky_meshes()) {
        return MW2ER_ERR_GL;
    }
    {
        GLfloat range[2] = {1.0f, 1.0f};
        glGetFloatv(GL_ALIASED_LINE_WIDTH_RANGE, range);
        g_line_range_max = range[1] < 1.0f ? 1.0f : range[1];
    }
    glActiveTexture(GL_TEXTURE0 + TEXTURE_UNIT_PALETTE);
    glGenTextures(1, &g_gpu.palette_tex);
    glBindTexture(GL_TEXTURE_2D, g_gpu.palette_tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexImage2D(
        GL_TEXTURE_2D, 0, GL_RGB32F, 256, 1, 0, GL_RGB, GL_FLOAT, NULL);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    glBindTexture(GL_TEXTURE_2D, 0);
    for (SceneGeometry *scene : {&g_primary, &g_mfd}) {
        for (PartGpu &part : scene->part) part_gpu_create(part);
        scene->force_upload = 1;
    }
    g_gpu.ready = 1;
    return MW2ER_OK;
}

static void clear_gpu_textures(void);

void mw2er_scene_resources_shutdown(void)
{
    glDeleteBuffers(3, g_gpu.uniform_buffers);
    memset(g_gpu.uniform_buffers, 0, sizeof(g_gpu.uniform_buffers));
    g_gpu.frame_uniforms_dirty = true;
    mesh_destroy(g_gpu.sky_mesh);
    mesh_destroy(g_gpu.gradient);
    indexed_destroy(g_gpu.rotor_disc);
    for (SceneGeometry *scene : {&g_primary, &g_mfd}) {
        for (PartGpu &part : scene->part) part_gpu_destroy(part);
        scene->force_upload = 1;
    }
    g_gpu.sky.prog.destroy();
    g_gpu.geo.prog.destroy();
    g_gpu.idx_geo.prog.destroy();
    g_gpu.mode4.prog.destroy();
    g_gpu.billboards.prog.destroy();
    g_gpu.texmaps.prog.destroy();
    g_gpu.camo.prog.destroy();
    g_gpu.rotor.prog.destroy();
    g_gpu.rotor_outline.prog.destroy();
    g_gpu.occluder.prog.destroy();
    if (g_gpu.palette_tex) {
        glDeleteTextures(1, &g_gpu.palette_tex);
        g_gpu.palette_tex = 0;
    }
    clear_gpu_textures();
    g_gpu.ready = 0;
}

/* Upload the current palette and leave it bound for palette-based draws. */
static void upload_and_bind_palette(const float *rgb)
{
    glActiveTexture(GL_TEXTURE0 + TEXTURE_UNIT_PALETTE);
    glBindTexture(GL_TEXTURE_2D, g_gpu.palette_tex);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexSubImage2D(
        GL_TEXTURE_2D, 0, 0, 0, 256, 1, GL_RGB, GL_FLOAT, rgb);
}

static size_t grow_cap(size_t cap, size_t bytes)
{
    if (cap < 4096) {
        cap = 4096;
    }
    while (cap < bytes) {
        cap *= 2;
    }
    return cap;
}

static void upload_stream(
    GLuint vbo, size_t *cap, const float *data, size_t bytes)
{
    glBindBuffer(GL_ARRAY_BUFFER, vbo);
    if (bytes > *cap) {
        *cap = grow_cap(*cap, bytes);
    }
    glBufferData(GL_ARRAY_BUFFER, (GLsizeiptr)*cap, NULL, GL_DYNAMIC_DRAW);
    glBufferSubData(GL_ARRAY_BUFFER, 0, (GLsizeiptr)bytes, data);
}

static void upload_ebo(
    GLuint vao, size_t *cap, const uint32_t *data, size_t bytes)
{
    /* The owning VAO permanently records its EBO at creation. */
    glBindVertexArray(vao);
    if (bytes > *cap) {
        *cap = grow_cap(*cap, bytes);
    }
    glBufferData(
        GL_ELEMENT_ARRAY_BUFFER, (GLsizeiptr)*cap, NULL, GL_DYNAMIC_DRAW);
    glBufferSubData(GL_ELEMENT_ARRAY_BUFFER, 0, (GLsizeiptr)bytes, data);
}

static void upload_prim_tex(GLuint *tex, int *width, const float *data, int n)
{
    if (n <= 0 || data == NULL) {
        return;
    }
    if (*tex == 0) {
        glGenTextures(1, tex);
        glBindTexture(GL_TEXTURE_2D, *tex);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    } else {
        glBindTexture(GL_TEXTURE_2D, *tex);
    }
    if (*width != n) {
        glTexImage2D(
            GL_TEXTURE_2D,
            0,
            GL_R32F,
            n,
            1,
            0,
            GL_RED,
            GL_FLOAT,
            data);
        *width = n;
    } else {
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, n, 1, GL_RED, GL_FLOAT, data);
    }
}

static int partition_has_geometry(const Mw2erGeomPartition &p)
{
    if (p.tris.floats() >= 15 ||
        p.flats.floats() >= 12 ||
        p.indexed_flat_indices.count() >= 3 ||
        p.texmap_verts.floats() >= 5 ||
        p.wire_verts.floats() >= 6 ||
        p.points.floats() >= 4 ||
        p.lines.floats() >= 8 ||
        p.used_desc_n > 0 ||
        !p.rotor_draws.empty()) {
        return 1;
    }
    return 0;
}

static void bind_prim_unit(GLuint tex)
{
    glActiveTexture(GL_TEXTURE0 + TEXTURE_UNIT_PRIMITIVE);
    glBindTexture(GL_TEXTURE_2D, tex);
}

/* Python DynamicMesh.update: create on first non-empty write, release on empty. */
static void sync_part_gpu(PartGpu &gpu, const Mw2erGeomPartition &gp)
{
    if (gp.tris.floats() >= 15) {
        mesh_ensure_mode4(gpu.tris);
        upload_stream(
            gpu.tris.vbo,
            &gpu.tri_vbo_bytes,
            gp.tris.ptr(),
            gp.tris.floats() * sizeof(float));
    } else {
        mesh_destroy(gpu.tris);
        gpu.tri_vbo_bytes = 0;
    }

    if (gp.flats.floats() >= 12) {
        mesh_ensure_xyz_pal(gpu.flats);
        upload_stream(
            gpu.flats.vbo,
            &gpu.flat_vbo_bytes,
            gp.flats.ptr(),
            gp.flats.floats() * sizeof(float));
    } else {
        mesh_destroy(gpu.flats);
        gpu.flat_vbo_bytes = 0;
    }

    if (gp.indexed_flat_indices.count() >= 3 &&
        gp.indexed_flat_verts.floats() >= 9) {
        indexed_ensure_pos(gpu.idx_flat);
        upload_stream(
            gpu.idx_flat.vbo,
            &gpu.idx_flat.vbo_bytes,
            gp.indexed_flat_verts.ptr(),
            gp.indexed_flat_verts.floats() * sizeof(float));
        upload_ebo(
            gpu.idx_flat.vao,
            &gpu.idx_flat.ebo_bytes,
            gp.indexed_flat_indices.ptr(),
            gp.indexed_flat_indices.count() * sizeof(uint32_t));
        upload_prim_tex(
            &gpu.idx_flat.prim_tex,
            &gpu.idx_flat.prim_w,
            gp.indexed_flat_palette.ptr(),
            (int)gp.indexed_flat_palette.floats());
    } else {
        indexed_destroy(gpu.idx_flat);
    }

    if (gp.points.floats() >= 4) {
        mesh_ensure_xyz_pal(gpu.points);
        upload_stream(
            gpu.points.vbo,
            &gpu.point_vbo_bytes,
            gp.points.ptr(),
            gp.points.floats() * sizeof(float));
    } else {
        mesh_destroy(gpu.points);
        gpu.point_vbo_bytes = 0;
    }

    if (gp.lines.floats() >= 8) {
        mesh_ensure_xyz_pal(gpu.lines);
        upload_stream(
            gpu.lines.vbo,
            &gpu.line_vbo_bytes,
            gp.lines.ptr(),
            gp.lines.floats() * sizeof(float));
    } else {
        mesh_destroy(gpu.lines);
        gpu.line_vbo_bytes = 0;
    }

    int have_occ =
        gp.wire_occ_indices.count() >= 3 && gp.wire_verts.floats() >= 9;
    int have_line =
        gp.wire_line_indices.count() >= 2 && gp.wire_verts.floats() >= 6;
    if (!have_occ && !have_line) {
        wire_destroy(gpu.wire);
    } else {
        size_t vbytes = gp.wire_verts.floats() * sizeof(float);
        wire_ensure_vbo(gpu.wire);
        upload_stream(
            gpu.wire.vbo, &gpu.wire.vbo_bytes, gp.wire_verts.ptr(), vbytes);
        if (have_occ) {
            wire_ensure_occ(gpu.wire);
            upload_ebo(
                gpu.wire.vao_occ,
                &gpu.wire.occ_bytes,
                gp.wire_occ_indices.ptr(),
                gp.wire_occ_indices.count() * sizeof(uint32_t));
        } else {
            wire_destroy_occ(gpu.wire);
        }
        if (have_line) {
            wire_ensure_line(gpu.wire);
            upload_ebo(
                gpu.wire.vao_line,
                &gpu.wire.line_bytes,
                gp.wire_line_indices.ptr(),
                gp.wire_line_indices.count() * sizeof(uint32_t));
            if (gp.wire_palette_index < 0) {
                upload_prim_tex(
                    &gpu.wire.line_prim,
                    &gpu.wire.prim_w,
                    gp.wire_line_palette.ptr(),
                    (int)gp.wire_line_palette.floats());
            } else {
                if (gpu.wire.line_prim) glDeleteTextures(1, &gpu.wire.line_prim);
                gpu.wire.line_prim = 0;
                gpu.wire.prim_w = 0;
            }
        } else {
            wire_destroy_line(gpu.wire);
        }
    }

    if (gp.texmap_verts.floats() >= 5) {
        shared_tex_vbo_ensure(gpu.idx_tex);
        upload_stream(
            gpu.idx_tex.vbo,
            &gpu.idx_tex.vbo_bytes,
            gp.texmap_verts.ptr(),
            gp.texmap_verts.floats() * sizeof(float));
        for (size_t si = 0; si < gp.desc_streams.size(); ++si) {
            const Mw2erDescStreams &streams = gp.desc_streams[si];
            int d = streams.desc;
            if (!desc_ok(d)) {
                continue;
            }
            if (streams.texmap_indices.count() >= 3) {
                desc_indexed_ensure(gpu.texmap[d], gpu.idx_tex.vbo);
                upload_ebo(
                    gpu.texmap[d].vao,
                    &gpu.texmap[d].ebo_bytes,
                    streams.texmap_indices.ptr(),
                    streams.texmap_indices.count() * sizeof(uint32_t));
                upload_prim_tex(
                    &gpu.texmap[d].prim_tex,
                    &gpu.texmap[d].prim_w,
                    streams.texmap_lighting.ptr(),
                    (int)streams.texmap_lighting.floats());
            } else if (gpu.texmap[d].vao != 0) {
                desc_indexed_destroy(gpu.texmap[d]);
            }
        }
    } else {
        for (int d = 0; d < MW2ER_MAX_DESC; ++d) {
            if (gpu.texmap[d].vao != 0) {
                desc_indexed_destroy(gpu.texmap[d]);
            }
        }
        if (gpu.idx_tex.vbo) {
            glDeleteBuffers(1, &gpu.idx_tex.vbo);
            gpu.idx_tex.vbo = 0;
            gpu.idx_tex.vbo_bytes = 0;
        }
    }

    for (size_t si = 0; si < gp.desc_streams.size(); ++si) {
        const Mw2erDescStreams &streams = gp.desc_streams[si];
        int d = streams.desc;
        if (!desc_ok(d)) {
            continue;
        }
        if (streams.billboards.floats() >= 42) {
            stream_buf_ensure_billboard(gpu.billboard[d]);
            upload_stream(
                gpu.billboard[d].vbo,
                &gpu.billboard[d].cap,
                streams.billboards.ptr(),
                streams.billboards.floats() * sizeof(float));
        } else if (gpu.billboard[d].vao != 0) {
            stream_buf_destroy(gpu.billboard[d]);
        }
    }

    size_t nfan = gp.aero_fans.size();
    if (gpu.aero_fan.size() < nfan) {
        gpu.aero_fan.resize(nfan);
    }
    for (size_t i = 0; i < nfan; ++i) {
        const Mw2erAeroFanMesh &mesh = gp.aero_fans[i];
        if (mesh.indices.count() < 3 || mesh.verts.floats() < 15) {
            indexed_destroy(gpu.aero_fan[i]);
            continue;
        }
        if (gpu.aero_fan[i].vao == 0) {
            indexed_create_pos_uv(gpu.aero_fan[i]);
        }
        upload_stream(
            gpu.aero_fan[i].vbo,
            &gpu.aero_fan[i].vbo_bytes,
            mesh.verts.ptr(),
            mesh.verts.floats() * sizeof(float));
        upload_ebo(
            gpu.aero_fan[i].vao,
            &gpu.aero_fan[i].ebo_bytes,
            mesh.indices.ptr(),
            mesh.indices.count() * sizeof(uint32_t));
        upload_prim_tex(
            &gpu.aero_fan[i].prim_tex,
            &gpu.aero_fan[i].prim_w,
            mesh.primitive_lighting.ptr(),
            (int)mesh.primitive_lighting.floats());
    }
    for (size_t i = nfan; i < gpu.aero_fan.size(); ++i) {
        if (gpu.aero_fan[i].vao != 0) {
            indexed_destroy(gpu.aero_fan[i]);
        }
    }
}

static void prepare_part_draw_spans(
    PartGpu &gpu, const Mw2erGeomPartition &gp, int satellite_view)
{
    for (int role = 0; role < 2; ++role) {
        gpu.texmap_streams[role][0].clear();
        gpu.texmap_streams[role][1].clear();
    }
    gpu.billboard_streams[0].clear();
    gpu.billboard_streams[1].clear();
    for (size_t si = 0; si < gp.desc_streams.size(); ++si) {
        const Mw2erDescStreams &streams = gp.desc_streams[si];
        const int desc = streams.desc;
        if (!desc_ok(desc) || !g_gpu.desc[desc].valid) {
            continue;
        }
        const int late =
            satellite_view && g_gpu.desc[desc].animated_effect;
        if (streams.texmap_indices.count() >= 3) {
            const int camo = g_gpu.desc[desc].enhancement_role_id == 1;
            gpu.texmap_streams[camo][late].push_back((uint16_t)si);
        }
        if (streams.billboards.floats() >= 42) {
            gpu.billboard_streams[late].push_back((uint16_t)si);
        }
    }
}

/* Cache each usable CEL independently. Any future array-texture optimization
 * must preserve drawing available animation frames while others are missing. */
static GLuint cel_gpu_tex(const Mw2erResolvedTexture &tex)
{
    int w = tex.width;
    int h = tex.height;
    int wrap = tex.wrap;
    int rid = tex.resource_id;
    int role = tex.enhancement_role_id;
    for (size_t i = 0; i < g_gpu.cel.size(); ++i) {
        if (g_gpu.cel[i].resource_id == rid && g_gpu.cel[i].w == w &&
            g_gpu.cel[i].h == h && g_gpu.cel[i].wrap == wrap &&
            g_gpu.cel[i].role == role && g_gpu.cel[i].source_pixels == tex.source_pixels) {
            return g_gpu.cel[i].tex;
        }
    }
    if (tex.pixels == NULL || w <= 0 || h <= 0) {
        return 0;
    }
    const Mw2erStartupScope trace(MW2ER_STARTUP_GPU_CEL);
    // Allocate the owning slot before creating a GL name: vector growth can throw.
    g_gpu.cel.push_back({rid, w, h, wrap, role, 0, tex.source_pixels});
    GLuint &id = g_gpu.cel.back().tex;
    glGenTextures(1, &id);
    glBindTexture(GL_TEXTURE_2D, id);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(
        GL_TEXTURE_2D,
        GL_TEXTURE_WRAP_S,
        wrap ? GL_REPEAT : GL_CLAMP_TO_EDGE);
    glTexParameteri(
        GL_TEXTURE_2D,
        GL_TEXTURE_WRAP_T,
        wrap ? GL_REPEAT : GL_CLAMP_TO_EDGE);
    glTexImage2D(
        GL_TEXTURE_2D,
        0,
        GL_R8,
        w,
        h,
        0,
        GL_RED,
        GL_UNSIGNED_BYTE,
        tex.pixels);
    return id;
}

static void bind_slot_texture(int desc)
{
    if (!desc_ok(desc)) {
        glBindTexture(GL_TEXTURE_2D, 0);
        return;
    }
    glBindTexture(GL_TEXTURE_2D, g_gpu.desc[desc].tex);
}

static void capture_partition_materials(const Mem &mem, const Mw2erGeomPartition &gp)
{
    for (int ui = 0; ui < gp.used_desc_n; ++ui) {
        const int desc = gp.used_desc[ui];
        if (g_resolved_desc_status[desc] != Mw2erMaterialResult::Unvisited) continue;
        Mw2erResolvedTexture &tex = g_resolved_desc[desc];
        const auto result = mw2er_texture_resolve(mem, desc, tex);
        g_resolved_desc_status[desc] = result;
        if (result == Mw2erMaterialResult::Unavailable ||
            result == Mw2erMaterialResult::Invalid ||
            result == Mw2erMaterialResult::Unsupported) {
            if (result == Mw2erMaterialResult::Unsupported) ++g_unsupported_materials;
            else ++g_unavailable_materials;
            const uint8_t issue = uint8_t(1u << (unsigned)result);
            if (!(g_reported_material_issues[desc] & issue)) {
                g_reported_material_issues[desc] |= issue;
                const char *reason = result == Mw2erMaterialResult::Unavailable ? "unavailable inputs" :
                    result == Mw2erMaterialResult::Invalid ? "invalid descriptor" : "unsupported material";
                char message[192];
                snprintf(message, sizeof(message),
                    "mw2renderer: skipping descriptor %d (CEL %d): %s; continuing frame",
                    desc, tex.resource_id, reason);
                mw2er_log(message);
            }
        }
    }
}

static void capture_desc_slots(const Mem &mem)
{
    mw2er_texture_begin_frame(mem, g_primary.ex.palette_rgb);
    memset(g_resolved_desc_status, 0, sizeof(g_resolved_desc_status));
    g_unavailable_materials = g_unsupported_materials = 0;
    const auto primary = mw2er_view_policy(g_primary_view, g_primary.ex.camera.camera_mode);
    const auto mfd_view = mw2er_hud_mfd_render_view();
    const auto mfd = mw2er_view_policy(mfd_view, -1);
    uint32_t primary_parts = primary.part_mask;
    if (mfd_view != MW2ER_VIEW_NONE && mfd.extraction == primary.extraction)
        primary_parts |= mfd.part_mask;
    for (int part = 0; part < MW2ER_PART_COUNT; ++part) {
        if (primary_parts & (1u << part)) capture_partition_materials(mem, g_primary.ex.part[part]);
        if (mfd_view != MW2ER_VIEW_NONE && mfd.extraction != primary.extraction &&
            (mfd.part_mask & (1u << part))) capture_partition_materials(mem, g_mfd.ex.part[part]);
    }
    mw2er_startup_materials(g_unavailable_materials, g_unsupported_materials);
}

static void sync_desc_slots(void)
{
    /* CEL creation needs a binding point on GL 3.3. Use the indexed-image
     * unit it will occupy during draws, never the palette unit. Every draw
     * binds its own descriptor texture, so the final unit-1 binding is not
     * part of the retained render state. */
    glActiveTexture(GL_TEXTURE0 + TEXTURE_UNIT_INDEXED);
    for (int desc = 0; desc < MW2ER_MAX_DESC; ++desc) {
        if (g_resolved_desc_status[desc] != Mw2erMaterialResult::Drawable) {
            g_gpu.desc[desc].valid = 0;
            g_gpu.desc[desc].tex = 0;
            continue;
        }
        const Mw2erResolvedTexture &tex = g_resolved_desc[desc];
        GLuint id = cel_gpu_tex(tex);
        if (id == 0) {
            g_gpu.desc[desc].valid = 0;
            g_gpu.desc[desc].tex = 0;
            continue;
        }
        g_gpu.desc[desc].valid = 1;
        g_gpu.desc[desc].tex = id;
        g_gpu.desc[desc].width = tex.width;
        g_gpu.desc[desc].height = tex.height;
        g_gpu.desc[desc].enhancement_role_id = tex.enhancement_role_id;
        g_gpu.desc[desc].enhanced_uv_scale = tex.enhanced_uv_scale;
        g_gpu.desc[desc].remap_kind_id = tex.remap_kind_id;
        g_gpu.desc[desc].dark_ratio[0] = tex.dark_ratio[0];
        g_gpu.desc[desc].dark_ratio[1] = tex.dark_ratio[1];
        g_gpu.desc[desc].dark_ratio[2] = tex.dark_ratio[2];
        g_gpu.desc[desc].fog_terminal[0] = tex.fog_terminal[0];
        g_gpu.desc[desc].fog_terminal[1] = tex.fog_terminal[1];
        g_gpu.desc[desc].fog_terminal[2] = tex.fog_terminal[2];
        g_gpu.desc[desc].s8_ratio[0] = tex.s8_ratio[0];
        g_gpu.desc[desc].s8_ratio[1] = tex.s8_ratio[1];
        g_gpu.desc[desc].s8_ratio[2] = tex.s8_ratio[2];
        g_gpu.desc[desc].resource_id = tex.resource_id;
        g_gpu.desc[desc].animated_effect = tex.animated_effect;
    }
}

static void clear_gpu_textures(void)
{
    for (size_t i = 0; i < g_gpu.cel.size(); ++i) {
        if (g_gpu.cel[i].tex) {
            glDeleteTextures(1, &g_gpu.cel[i].tex);
        }
    }
    g_gpu.cel.clear();
    memset(g_gpu.desc, 0, sizeof(g_gpu.desc));
}

void mw2er_scene_process_init(void)
{
    const char *workload = getenv("MW2ER_WORKLOAD");
    g_retained_workload = workload != NULL && strcmp(workload, "retained") == 0;
    g_debug_groups_requested = getenv("MW2ER_GL_DEBUG_GROUPS") != NULL;
}

void mw2er_scene_mission_reset(bool loading)
{
    g_required_geometry = 0;
    g_primary_view = MW2ER_VIEW_NONE;
    g_gpu.frame_uniforms_dirty = true;
    memset(g_resolved_desc_status, 0, sizeof(g_resolved_desc_status));
    memset(g_reported_material_issues, 0, sizeof(g_reported_material_issues));
    g_unavailable_materials = g_unsupported_materials = 0;
    for (SceneGeometry *scene : {&g_primary, &g_mfd}) {
        mw2er_extract_free(scene->ex);
        scene->force_upload = 1;
        scene->changed = 0;
    }
    mw2er_texture_free_cache();
    mw2er_extract_mission_reset(loading);
    g_gpu_mission_reset_pending = 1;
    g_last_extract_ms = 0.0;
    g_last_draw_ms = 0.0;
}

void mw2er_scene_process_shutdown(void)
{
    mw2er_scene_mission_reset();
}

static const int k_primary_part_order[] = {
    MW2ER_PART_STATIC,
    MW2ER_PART_SCENE,
    MW2ER_PART_VIEW_EXCLUDED,
    MW2ER_PART_ENTITY,
    MW2ER_PART_COCKPIT,
    MW2ER_PART_TARGET,
};
static const int k_primary_part_order_count =
    (int)(sizeof(k_primary_part_order) / sizeof(k_primary_part_order[0]));
// Update one small shared row at a partition boundary, not per program.
static void set_draw_uniforms(float clip_near)
{
    SceneDrawUniforms draw{};
    draw.clip[0] = clip_near;
    glBindBuffer(GL_UNIFORM_BUFFER, g_gpu.uniform_buffers[SCENE_DRAW_BINDING]);
    glBufferData(GL_UNIFORM_BUFFER, sizeof(draw), &draw, GL_STREAM_DRAW);
    glBindBuffer(GL_UNIFORM_BUFFER, 0);
}

static float set_scene_raster_scale(int32_t logical_h, int sample_scale)
{
    float point_raster =
        (logical_h < 1 ? 1.0f : (float)logical_h / 768.0f);
    if (point_raster < 1.0f) {
        point_raster = 1.0f;
    }
    point_raster *= (float)sample_scale;
    float line_raster = point_raster;
    if (sample_scale > 1) {
        line_raster *= mw2er_config().ssaa_line_width;
    }
    if (line_raster > g_line_range_max) {
        line_raster = g_line_range_max;
    }
    glLineWidth(line_raster);
    glPointSize(point_raster);
    return point_raster;
}

static void draw_wire_occluder(
    const Mw2erGeomPartition &gp,
    PartGpu &gpu,
    int polygon_offset)
{
    if (gpu.wire.vao_occ == 0 ||
        gp.wire_occ_indices.count() < 3 || gp.wire_verts.floats() < 9) {
        return;
    }
    if (polygon_offset) {
        glEnable(GL_POLYGON_OFFSET_FILL);
        glPolygonOffset(1.0f, 1.0f);
    }
    g_gpu.occluder.prog.use();
    glBindVertexArray(gpu.wire.vao_occ);
    glDrawElements(
        GL_TRIANGLES,
        (GLsizei)gp.wire_occ_indices.count(),
        GL_UNSIGNED_INT,
        0);
    if (polygon_offset) {
        glDisable(GL_POLYGON_OFFSET_FILL);
    }
}

static void draw_texmap_stream(
    TexmapPass &p,
    const Mw2erDescStreams &streams,
    DescIndexed &mesh,
    int desc)
{
    glActiveTexture(GL_TEXTURE0 + TEXTURE_UNIT_INDEXED);
    bind_slot_texture(desc);
    set1i(p.u_texture_role, g_gpu.desc[desc].enhancement_role_id);
    set2i(p.u_texture_size, g_gpu.desc[desc].width, g_gpu.desc[desc].height);
    const float uv_scale = g_gpu.desc[desc].enhancement_role_id != 0
                               ? g_gpu.desc[desc].enhanced_uv_scale
                               : 1.0f;
    set2f(
        p.u_uv_scale,
        uv_scale / (float)g_gpu.desc[desc].width,
        uv_scale / (float)g_gpu.desc[desc].height);
    set1i(p.u_remap_kind, g_gpu.desc[desc].remap_kind_id);
    set3f(p.u_dark_ratio, g_gpu.desc[desc].dark_ratio);
    set3f(p.u_fog_terminal_color, g_gpu.desc[desc].fog_terminal);
    set3f(p.u_s8_ratio, g_gpu.desc[desc].s8_ratio);
    glBindVertexArray(mesh.vao);
    bind_prim_unit(mesh.prim_tex);
    glDrawElements(
        GL_TRIANGLES,
        (GLsizei)streams.texmap_indices.count(),
        GL_UNSIGNED_INT,
        0);
}

static void draw_satellite_effects(
    const Mw2erGeomPartition &gp,
    PartGpu &gpu)
{
    if (gpu.billboard_streams[1].empty() &&
        gpu.texmap_streams[0][1].empty() &&
        gpu.texmap_streams[1][1].empty()) {
        return;
    }

    /* The game painter-sorts these effects after opaque satellite geometry.
     * Keep their normal indexed shaders and transparent-texel discard, but do
     * not let terrain depth suppress explosions and weapon billboards. */
    glDisable(GL_CULL_FACE);
    glDisable(GL_DEPTH_TEST);
    glDepthMask(GL_FALSE);

    for (int camo_pass = 0; camo_pass < 2; ++camo_pass) {
        const std::vector<uint16_t> &span = gpu.texmap_streams[camo_pass][1];
        if (span.empty()) {
            continue;
        }
        TexmapPass &tm = camo_pass ? g_gpu.camo : g_gpu.texmaps;
        tm.prog.use();
        for (size_t i = 0; i < span.size(); ++i) {
            const Mw2erDescStreams &streams = gp.desc_streams[span[i]];
            if (!desc_ok(streams.desc)) {
                continue;
            }
            draw_texmap_stream(
                tm, streams, gpu.texmap[streams.desc], streams.desc);
        }
    }

    if (!gpu.billboard_streams[1].empty()) {
        g_gpu.billboards.prog.use();
        glActiveTexture(GL_TEXTURE0 + TEXTURE_UNIT_INDEXED);
        for (size_t i = 0; i < gpu.billboard_streams[1].size(); ++i) {
            const Mw2erDescStreams &streams =
                gp.desc_streams[gpu.billboard_streams[1][i]];
            if (!desc_ok(streams.desc)) {
                continue;
            }
            bind_slot_texture(streams.desc);
            StreamBuf &bm = gpu.billboard[streams.desc];
            glBindVertexArray(bm.vao);
            glDrawArrays(
                GL_TRIANGLES,
                0,
                (GLsizei)(streams.billboards.floats() / 7));
        }
    }

    glDepthMask(GL_TRUE);
    glEnable(GL_DEPTH_TEST);
    glDepthFunc(GL_LEQUAL);
    glEnable(GL_CULL_FACE);
}

int32_t mw2er_scene_capture(Mw2erRenderView primary_view)
{
    const Mw2erMemoryView *view = mw2er_frame_mem();
    if (view == NULL) {
        mw2er_set_error("scene capture: no memory view");
        return MW2ER_ERR_MEMORY_VIEW;
    }
    if (g_retained_workload && g_required_geometry) {
        g_last_extract_ms = 0.0;
        g_primary.changed = g_mfd.changed = 0;
        return MW2ER_OK;
    }
    const double t0 = now_ms();
    g_required_geometry = 0;
    g_primary_view = primary_view;
    const Mw2erRenderView mfd_view = mw2er_hud_mfd_render_view();
    const uint32_t primary_geometry = mw2er_view_policy(g_primary_view, -1).extraction;
    uint32_t needed = primary_geometry;
    if (mfd_view != MW2ER_VIEW_NONE) needed |= mw2er_view_policy(mfd_view, -1).extraction;
    if (!mw2er_extract_scene(*view, g_primary.ex, 1, g_primary_view)) {
        return MW2ER_ERR_GENERIC;
    }
    g_primary.changed = 1;
    g_gpu.frame_uniforms_dirty = true;
    if (needed != primary_geometry) {
        if (!mw2er_extract_scene(*view, g_mfd.ex, 1, mfd_view,
                                 g_primary.ex.palette_rgb)) {
            return MW2ER_ERR_GENERIC;
        }
        g_mfd.changed = 1;
    }
    g_required_geometry = needed;
    capture_desc_slots(Mem::from(*view));
    g_last_extract_ms = now_ms() - t0;
    return MW2ER_OK;
}

Mw2erSceneExtract *mw2er_scene_extract_current(void)
{
    return g_required_geometry ? &g_primary.ex : NULL;
}

int32_t mw2er_scene_capture_target(uint32_t root, uint32_t entity,
                                   int display_mode,
                                   const Mw2erCamera *camera)
{
    const Mw2erMemoryView *view = mw2er_frame_mem();
    Mw2erSceneExtract *primary = mw2er_scene_extract_current();
    if (view == NULL || primary == NULL || camera == NULL) {
        return MW2ER_ERR_NOT_READY;
    }
    if (!mw2er_extract_target(
            *view, *primary, root, entity, display_mode, *camera)) {
        return MW2ER_ERR_GENERIC;
    }
    g_primary.changed = 1;
    capture_partition_materials(Mem::from(*view), primary->part[MW2ER_PART_TARGET]);
    mw2er_startup_materials(g_unavailable_materials, g_unsupported_materials);
    return MW2ER_OK;
}

static int32_t draw_scene(SceneGeometry &scene, const Mw2erCamera &camera,
                          const Mw2erViewPolicy &view_policy,
                          int32_t logical_w, int32_t logical_h,
                          int32_t scene_w, int32_t scene_h)
{
    float proj[16];
    int emit_mode4 = 1;
    int diagnostics = mw2er_config().enable_diagnostic_logging;
    int sample_scale = scene_h > logical_h && logical_h > 0 ? scene_h / logical_h : 1;

    if (!g_gpu.ready) {
        mw2er_set_error("scene gpu not ready");
        return MW2ER_ERR_NOT_READY;
    }
    if (g_required_geometry == 0) {
        mw2er_set_error("scene draw: no captured frame");
        return MW2ER_ERR_NOT_READY;
    }
    if (g_gpu_mission_reset_pending) {
        clear_gpu_textures();
        g_gpu_mission_reset_pending = 0;
    }
    const int force_upload = scene.force_upload;
    const Mw2erSceneExtract &ex = scene.ex;
    PartGpu *gpu_parts = scene.part;
    const int upload_frame = scene.changed || force_upload;
    const uint32_t part_mask = view_policy.part_mask;
    {
        double t0 = now_ms();
    upload_and_bind_palette(ex.palette_rgb);
    make_projection(camera, logical_w, logical_h, proj);
    if (g_gpu.frame_uniforms_dirty) {
        SceneFrameUniforms frame{};
        frame.lighting[0] = g_primary.ex.lighting.fog_distance_world;
        glBindBuffer(GL_UNIFORM_BUFFER, g_gpu.uniform_buffers[SCENE_FRAME_BINDING]);
        glBufferData(GL_UNIFORM_BUFFER, sizeof(frame), &frame, GL_STREAM_DRAW);
        g_gpu.frame_uniforms_dirty = false;
    }
    SceneViewUniforms view{};
    memcpy(view.projection, proj, sizeof(proj));
    memcpy(view.position, camera.position, sizeof(camera.position));
    memcpy(view.right, camera.right, sizeof(camera.right));
    memcpy(view.up, camera.up, sizeof(camera.up));
    memcpy(view.forward, camera.forward, sizeof(camera.forward));
    view.viewport[0] = (float)scene_w;
    view.viewport[1] = (float)scene_h;
    view.viewport[2] = (float)camera.satellite_view;
    view.imaging[0] = camera.imaging_fade_start;
    view.imaging[1] = camera.imaging_fade_end;
    glBindBuffer(GL_UNIFORM_BUFFER, g_gpu.uniform_buffers[SCENE_VIEW_BINDING]);
    glBufferData(GL_UNIFORM_BUFFER, sizeof(view), &view, GL_STREAM_DRAW);
    set_draw_uniforms(camera.clip_near_plane);
    float previous_clip_near = camera.clip_near_plane;
    for (GLuint binding = 0; binding < 3; ++binding)
        glBindBufferBase(GL_UNIFORM_BUFFER, binding, g_gpu.uniform_buffers[binding]);
    glBindBuffer(GL_UNIFORM_BUFFER, 0);

    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_CULL_FACE);
    glDisable(GL_BLEND);
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    glDepthMask(GL_TRUE);
    // Only the main scene takes the indexed imaging/background wipe.
    const bool wipe = !view_policy.auxiliary && !camera.satellite_view &&
        (camera.imaging_wireframe || ex.background_wipe);
    const bool target = part_mask == (1u << MW2ER_PART_TARGET);
    const bool ground = !target && (ex.ground_visible || camera.satellite_view);
    // Match empty HUD panes: index 0 is normally black but follows palette effects.
    const int clear_index = wipe ? ex.fill_palette_index :
        (ground ? ex.ground_palette_index : 0);
    const float *clear = ex.palette_rgb + 3 * clear_index;
    glClearColor(clear[0], clear[1], clear[2], 1.0f);
    glClearDepth(1.0);
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);

    if (ex.sky_visible && !wipe && !camera.satellite_view &&
        (part_mask & (1u << MW2ER_PART_SCENE))) {
        SkyPass &sky = g_gpu.sky;
        glDisable(GL_DEPTH_TEST);
        sky.prog.use();
        set1f(sky.u_y_scale, 1.0f);
        set1f(sky.u_palette_start, palette_u(ex.sky_palette_index));
        set1f(sky.u_palette_end, palette_u(ex.sky_palette_index));
        glBindVertexArray(g_gpu.sky_mesh.vao);
        glDrawArrays(GL_TRIANGLES, 0, g_gpu.sky_mesh.count);
        if (ex.draw_gradient) {
            float gh = (float)ex.gradient_height * (float)SKY_RADIUS / 512.0f;
            if (gh > 0.0f) {
                set1f(sky.u_y_scale, gh);
                set1f(
                    sky.u_palette_end,
                    palette_u((int)ex.ground_palette_index - 1));
                glBindVertexArray(g_gpu.gradient.vao);
                glDrawArrays(GL_TRIANGLES, 0, g_gpu.gradient.count);
            }
        }
    }

    float point_raster = 1.0f;
    if (emit_mode4) {
        point_raster = set_scene_raster_scale(logical_h, sample_scale);
        if (upload_frame) {
            sync_desc_slots();
        }
    }

    static const char *part_debug_names[MW2ER_PART_COUNT] = {
        "mw2renderer/static",
        "mw2renderer/scene",
        "mw2renderer/entity",
        "mw2renderer/cockpit",
        "mw2renderer/view_excluded",
        "mw2renderer/target",
    };
    if (upload_frame) {
        glActiveTexture(GL_TEXTURE0 + TEXTURE_UNIT_PRIMITIVE);
        for (int pid = 0; pid < MW2ER_PART_COUNT; ++pid) {
            int upload = 1;
            if (pid == MW2ER_PART_STATIC && ex.static_reused &&
                !force_upload) {
                upload = 0;
            }
            if (upload) {
                sync_part_gpu(gpu_parts[pid], ex.part[pid]);
            }
            prepare_part_draw_spans(
                gpu_parts[pid], ex.part[pid], camera.satellite_view);
        }
    }
    /* All uploads accept byte alignment. Restore the shared-context default
     * once, rather than toggling alignment for every texture. */
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);

    /* Common opaque-geometry contract. Individual families below only alter
     * culling, blending, or depth writes when their plan requires it. */
    if (view_policy.depth_test) glEnable(GL_DEPTH_TEST);
    else glDisable(GL_DEPTH_TEST);
    glDepthFunc(GL_LEQUAL);
    glDepthMask(GL_TRUE);
    glCullFace(GL_BACK);
    glFrontFace(GL_CCW);
    glDisable(GL_POLYGON_OFFSET_FILL);

    /* Match the Python imaging pass: establish depth for the complete world
     * before emitting any world wire lines. Cockpit remains a separate final
     * pass with its zero near clip. */
    if (camera.imaging_wireframe && view_policy.depth_test) {
        glEnable(GL_CULL_FACE);
        for (int oi = 0; oi < k_primary_part_order_count; ++oi) {
            const int pid = k_primary_part_order[oi];
            if ((part_mask & (1u << pid)) == 0 || pid == MW2ER_PART_COCKPIT)
                continue;
            draw_wire_occluder(
                ex.part[pid],
                gpu_parts[pid],
                1);
        }
    }

    for (int oi = 0; oi < k_primary_part_order_count; ++oi) {
    const int pid = k_primary_part_order[oi];
    if ((part_mask & (1u << pid)) == 0) {
        continue;
    }
    const Mw2erGeomPartition &gp = ex.part[pid];
    PartGpu &gpu = gpu_parts[pid];
    if (!partition_has_geometry(gp)) {
        continue;
    }
    const int debug_group =
        g_debug_groups_requested && glPushDebugGroup != NULL;
    if (debug_group) {
        glPushDebugGroup(
            GL_DEBUG_SOURCE_APPLICATION,
            (GLuint)pid,
            -1,
            part_debug_names[pid]);
    }
    const float clip_near =
        pid == MW2ER_PART_COCKPIT ? 0.0f : camera.clip_near_plane;
    if (clip_near != previous_clip_near) {
        set_draw_uniforms(clip_near);
        previous_clip_near = clip_near;
    }
    const int have_culled_geometry =
        gp.wire_occ_indices.count() >= 3 || gp.tris.floats() >= 15 ||
        gp.flats.floats() >= 12 || gp.indexed_flat_indices.count() >= 3 ||
        gp.texmap_verts.floats() >= 5;
    /* Satellite opaque solids and black wire occluders retain back-face
     * culling. Only billboards/effects are double-sided. Disabling culling for
     * whole satellite partitions exposes authored coplanar back faces and can
     * look like terrain depth fighting. */
    if (have_culled_geometry) {
        glEnable(GL_CULL_FACE);
    }
    if (emit_mode4 && view_policy.depth_test &&
        (!camera.imaging_wireframe || pid == MW2ER_PART_COCKPIT)) {
        draw_wire_occluder(
            gp,
            gpu,
            camera.imaging_wireframe || camera.satellite_view);
    }

    if (emit_mode4 && gp.tris.floats() >= 15) {
        Mode4Pass &m4 = g_gpu.mode4;
        g_gpu.mode4.prog.use();
        glBindVertexArray(gpu.tris.vao);
        glDrawArrays(GL_TRIANGLES, 0, (GLsizei)(gp.tris.floats() / 5));
        if (diagnostics) {
            char msg[128];
            snprintf(
                msg,
                sizeof(msg),
                "mw2renderer: mode4 nodes=%u tree=%u lod=%u tris=%u",
                ex.node_count,
                ex.tree_node_count,
                ex.lod_node_count,
                gp.tri_count / 3);
            mw2er_log(msg);
        }
    }

    if (emit_mode4 && gp.flats.floats() >= 12) {
        GeoPass &geo = g_gpu.geo;
        g_gpu.geo.prog.use();
        set1f(geo.u_point_size, 1.0f);
        glBindVertexArray(gpu.flats.vao);
        glDrawArrays(GL_TRIANGLES, 0, (GLsizei)(gp.flats.floats() / 4));
    }

    if (emit_mode4 && gp.indexed_flat_indices.count() >= 3 &&
        gp.indexed_flat_verts.floats() >= 9) {
        IndexedGeoPass &ig = g_gpu.idx_geo;
        g_gpu.idx_geo.prog.use();
        set1f(ig.u_constant_palette, -1.0f);
        glBindVertexArray(gpu.idx_flat.vao);
        bind_prim_unit(gpu.idx_flat.prim_tex);
        glDrawElements(
            GL_TRIANGLES,
            (GLsizei)gp.indexed_flat_indices.count(),
            GL_UNSIGNED_INT,
            0);
    }

    if (emit_mode4) {
        uint32_t bb_sprites = 0;
        uint32_t tex_verts = 0;
        int cull_disabled = 0;
        for (int camo_pass = 0; camo_pass < 2; ++camo_pass) {
            const std::vector<uint16_t> &span = gpu.texmap_streams[camo_pass][0];
            if (span.empty()) {
                continue;
            }
            TexmapPass &tm = camo_pass ? g_gpu.camo : g_gpu.texmaps;
            tm.prog.use();
            for (size_t i = 0; i < span.size(); ++i) {
                const Mw2erDescStreams &streams = gp.desc_streams[span[i]];
                const int desc = streams.desc;
                if (!desc_ok(desc)) {
                    continue;
                }
                draw_texmap_stream(tm, streams, gpu.texmap[desc], desc);
                if (diagnostics) {
                    tex_verts += (uint32_t)streams.texmap_indices.count();
                }
            }
        }

        if (!gpu.billboard_streams[0].empty()) {
            glDisable(GL_CULL_FACE);
            cull_disabled = 1;
            g_gpu.billboards.prog.use();
            glActiveTexture(GL_TEXTURE0 + TEXTURE_UNIT_INDEXED);
        }
        for (size_t i = 0; i < gpu.billboard_streams[0].size(); ++i) {
            const Mw2erDescStreams &streams =
                gp.desc_streams[gpu.billboard_streams[0][i]];
            const int desc = streams.desc;
            if (!desc_ok(desc)) {
                continue;
            }
            bind_slot_texture(desc);
            StreamBuf &bm = gpu.billboard[desc];
            glBindVertexArray(bm.vao);
            glDrawArrays(
                GL_TRIANGLES,
                0,
                (GLsizei)(streams.billboards.floats() / 7));
            if (diagnostics) {
                bb_sprites += (uint32_t)(streams.billboards.floats() / 42);
            }
        }
        if (diagnostics) {
            char msg[128];
            snprintf(
                msg,
                sizeof(msg),
                "mw2renderer: texmap idx=%u billboards=%u flats=%u lines=%u points=%u rotors=%u",
                tex_verts,
                bb_sprites,
                gp.flat_count,
                gp.line_count,
                gp.point_count,
                (uint32_t)gp.rotor_draws.size());
            mw2er_log(msg);
        }

        if (gp.points.floats() >= 4 || gp.lines.floats() >= 8) {
            GeoPass &geo = g_gpu.geo;
            if (!cull_disabled) {
                glDisable(GL_CULL_FACE);
                cull_disabled = 1;
            }
            g_gpu.geo.prog.use();
            set1f(geo.u_point_size, point_raster);
            if (gp.points.floats() >= 4) {
                glBindVertexArray(gpu.points.vao);
                glDrawArrays(GL_POINTS, 0, (GLsizei)(gp.points.floats() / 4));
            }
            if (gp.lines.floats() >= 8) {
                glBindVertexArray(gpu.lines.vao);
                glDrawArrays(GL_LINES, 0, (GLsizei)(gp.lines.floats() / 4));
            }
        }

        if (gp.wire_line_indices.count() >= 2 &&
            gp.wire_verts.floats() >= 6) {
            IndexedGeoPass &ig = g_gpu.idx_geo;
            if (!cull_disabled) {
                glDisable(GL_CULL_FACE);
                cull_disabled = 1;
            }
            g_gpu.idx_geo.prog.use();
            set1f(g_gpu.idx_geo.u_constant_palette, (float)gp.wire_palette_index);
            glBindVertexArray(gpu.wire.vao_line);
            if (gp.wire_palette_index < 0) bind_prim_unit(gpu.wire.line_prim);
            glDrawElements(
                GL_LINES,
                (GLsizei)gp.wire_line_indices.count(),
                GL_UNSIGNED_INT,
                0);
        }

        if (!gp.rotor_draws.empty()) {
            TexmapPass &tm = g_gpu.rotor;
            static std::vector<int> order;
            int nrot = (int)gp.rotor_draws.size();
            int have_outline = 0;
            int have_solid = 0;
            int need_disc = 0;
            order.resize((size_t)nrot);
            const float *cam = camera.position;
            for (int i = 0; i < nrot; ++i) {
                order[i] = i;
                const Mw2erRotorDraw &batch = gp.rotor_draws[i];
                if (batch.outline_only) {
                    if (batch.effect == MW2ER_ROTOR_HELI) {
                        have_outline = 1;
                        need_disc = 1;
                    }
                    continue;
                }
                if (batch.desc < 0 || batch.desc >= MW2ER_MAX_DESC ||
                    !g_gpu.desc[batch.desc].valid) {
                    continue;
                }
                if (batch.effect == MW2ER_ROTOR_HELI) {
                    have_solid = 1;
                    need_disc = 1;
                } else if (batch.effect == MW2ER_ROTOR_FAN &&
                           batch.aero_mesh < gp.aero_fans.size()) {
                    const Mw2erAeroFanMesh &fan =
                        gp.aero_fans[batch.aero_mesh];
                    if (fan.indices.count() >= 3 && fan.verts.floats() >= 15 &&
                        batch.aero_mesh < gpu.aero_fan.size()) {
                        have_solid = 1;
                    }
                }
            }
            if (need_disc && g_gpu.rotor_disc.vao == 0 && !build_rotor_disc()) {
                return MW2ER_ERR_GL;
            }
            for (int a = 1; a < nrot; ++a) {
                int key = order[a];
                int b = a;
                const float *kc = gp.rotor_draws[key].center;
                float kdx = kc[0] - cam[0];
                float kdy = kc[1] - cam[1];
                float kdz = kc[2] - cam[2];
                float kd = kdx * kdx + kdy * kdy + kdz * kdz;
                while (b > 0) {
                    const float *pc = gp.rotor_draws[order[b - 1]].center;
                    float pdx = pc[0] - cam[0];
                    float pdy = pc[1] - cam[1];
                    float pdz = pc[2] - cam[2];
                    float pd = pdx * pdx + pdy * pdy + pdz * pdz;
                    if (pd >= kd) {
                        break;
                    }
                    order[b] = order[b - 1];
                    b -= 1;
                }
                order[b] = key;
            }

            /* Satellite and enhanced-imaging helicopter discs are outlines
             * only. They share the canonical rim and never emit occluder
             * triangles. */
            if (have_outline) {
                RotorOutlinePass &outline = g_gpu.rotor_outline;
                g_gpu.rotor_outline.prog.use();
                if (!cull_disabled) {
                    glDisable(GL_CULL_FACE);
                    cull_disabled = 1;
                }
                glBindVertexArray(g_gpu.rotor_disc.vao);
                for (int oi = 0; oi < nrot; ++oi) {
                    const Mw2erRotorDraw &batch = gp.rotor_draws[order[oi]];
                    if (!batch.outline_only ||
                        batch.effect != MW2ER_ROTOR_HELI) {
                        continue;
                    }
                    set3f(outline.u_rotor_center, batch.center);
                    set3f(outline.u_rotor_axis_u, batch.axis_u);
                    set3f(outline.u_rotor_axis_v, batch.axis_v);
                    set1f(outline.u_palette_index, batch.outline_palette);
                    glDrawArrays(GL_LINE_LOOP, 1, ROTOR_DISC_SEGMENTS);
                }
            }

            if (have_solid) {
                glEnable(GL_BLEND);
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
                glDepthMask(GL_FALSE);
                /* Two coplanar discs, opposite winding. Keep backface cull so
                 * only the facing texture is drawn, matching Python. */
                glEnable(GL_CULL_FACE);
                tm.prog.use();
                for (int oi = 0; oi < nrot; ++oi) {
                    const Mw2erRotorDraw &batch = gp.rotor_draws[order[oi]];
                    if (batch.outline_only) {
                        continue;
                    }
                    const Mw2erAeroFanMesh *fan = NULL;
                    if (batch.effect == MW2ER_ROTOR_FAN) {
                        if (batch.aero_mesh >= gp.aero_fans.size()) {
                            continue;
                        }
                        fan = &gp.aero_fans[batch.aero_mesh];
                        if (fan->indices.count() < 3 ||
                            fan->verts.floats() < 15) {
                            continue;
                        }
                    }
                    if (batch.desc < 0 || batch.desc >= MW2ER_MAX_DESC ||
                        !g_gpu.desc[batch.desc].valid) {
                        continue;
                    }
                    int enhanced = 1;
                    if (batch.effect == MW2ER_ROTOR_FAN &&
                        !is_aero_lift_fan_resource(
                            g_gpu.desc[batch.desc].resource_id)) {
                        enhanced = 0;
                    }
                    glActiveTexture(GL_TEXTURE0 + TEXTURE_UNIT_INDEXED);
                    bind_slot_texture(batch.desc);
                    set1i(tm.u_rotor_enhanced, enhanced);
                    set1i(tm.u_texture_role, 0);
                    set2i(
                        tm.u_texture_size,
                        g_gpu.desc[batch.desc].width,
                        g_gpu.desc[batch.desc].height);
                    set2i(
                        tm.u_rotor_texture_size,
                        g_gpu.desc[batch.desc].width,
                        g_gpu.desc[batch.desc].height);
                    if (batch.normalized_uv) {
                        set2f(tm.u_uv_scale, 1.0f, 1.0f);
                    } else {
                        set2f(
                            tm.u_uv_scale,
                            1.0f / (float)g_gpu.desc[batch.desc].width,
                            1.0f / (float)g_gpu.desc[batch.desc].height);
                    }
                    set1i(
                        tm.u_remap_kind,
                        g_gpu.desc[batch.desc].remap_kind_id);
                    set3f(tm.u_dark_ratio, g_gpu.desc[batch.desc].dark_ratio);
                    set3f(
                        tm.u_fog_terminal_color,
                        g_gpu.desc[batch.desc].fog_terminal);
                    set3f(tm.u_s8_ratio, g_gpu.desc[batch.desc].s8_ratio);
                    if (batch.effect == MW2ER_ROTOR_HELI) {
                        set1i(tm.u_canonical_rotor, 1);
                        set3f(tm.u_rotor_center, batch.center);
                        set3f(tm.u_rotor_axis_u, batch.axis_u);
                        set3f(tm.u_rotor_axis_v, batch.axis_v);
                        set1f(tm.u_rotor_lighting, batch.lighting);
                        glBindVertexArray(g_gpu.rotor_disc.vao);
                        glDrawArrays(
                            GL_TRIANGLE_FAN,
                            0,
                            ROTOR_DISC_SEGMENTS + 2);
                    } else {
                        size_t fan_slot = batch.aero_mesh;
                        if (fan_slot >= gpu.aero_fan.size()) {
                            continue;
                        }
                        IndexedBuf &rm = gpu.aero_fan[fan_slot];
                        set1i(tm.u_canonical_rotor, 0);
                        glBindVertexArray(rm.vao);
                        bind_prim_unit(rm.prim_tex);
                        glDrawElements(
                            GL_TRIANGLES,
                            (GLsizei)fan->indices.count(),
                            GL_UNSIGNED_INT,
                            0);
                    }
                }
                set1i(tm.u_rotor_enhanced, 0);
                set1i(tm.u_canonical_rotor, 0);
                glDepthMask(GL_TRUE);
                glDisable(GL_BLEND);
            }
        }

        if (camera.satellite_view) {
            draw_satellite_effects(gp, gpu);
        }
    }
    if (debug_group) {
        glPopDebugGroup();
    }
    }

    for (GLuint binding = 0; binding < 3; ++binding)
        glBindBufferBase(GL_UNIFORM_BUFFER, binding, 0);
    glBindBuffer(GL_UNIFORM_BUFFER, 0);
    glBindVertexArray(0);
    glUseProgram(0);
    glActiveTexture(GL_TEXTURE0 + TEXTURE_UNIT_PALETTE);
    glBindTexture(GL_TEXTURE_2D, 0);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_CULL_FACE);
        scene.force_upload = 0;
        scene.changed = 0;
        if (!view_policy.auxiliary) {
            g_last_draw_ms = now_ms() - t0;
        }
    }
    return MW2ER_OK;
}


int32_t mw2er_scene_draw(int32_t logical_w, int32_t logical_h,
                         int32_t scene_w, int32_t scene_h)
{
    return draw_scene(g_primary, g_primary.ex.camera,
        mw2er_view_policy(g_primary_view, g_primary.ex.camera.camera_mode),
        logical_w, logical_h, scene_w, scene_h);
}

int32_t mw2er_scene_draw_view(const Mw2erCamera *camera,
                              int32_t logical_w, int32_t logical_h,
                              int32_t scene_w, int32_t scene_h,
                              Mw2erRenderView render_view)
{
    if (camera == NULL || render_view == MW2ER_VIEW_NONE) {
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    const Mw2erViewPolicy policy = mw2er_view_policy(render_view, camera->camera_mode);
    if ((g_required_geometry & policy.extraction) == 0) return MW2ER_ERR_NOT_READY;
    SceneGeometry &scene = policy.extraction ==
        mw2er_view_policy(g_primary_view, -1).extraction ? g_primary : g_mfd;
    return draw_scene(scene, *camera, policy, logical_w, logical_h, scene_w, scene_h);
}

int32_t mw2er_scene_draw_target(const Mw2erCamera *camera,
                                int32_t logical_w, int32_t logical_h,
                                int32_t scene_w, int32_t scene_h)
{
    if (camera == NULL) return MW2ER_ERR_INVALID_ARGUMENT;
    const Mw2erViewPolicy target = {MW2ER_EXTRACT_ORDINARY, 1u << MW2ER_PART_TARGET, 1, 1};
    return draw_scene(g_primary, *camera, target, logical_w, logical_h, scene_w, scene_h);
}
