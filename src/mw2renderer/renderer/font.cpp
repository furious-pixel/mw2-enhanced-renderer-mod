#include "font.h"

#include "gl_program.h"
#include "mw2er_internal.h"

#include "gl_api.h"

#include <ft2build.h>
#include FT_FREETYPE_H

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

enum {
    ATLAS_SIZE = 1024,
    ATLAS_PADDING = 1,
    INITIAL_VERTEX_BUFFER_SIZE = 4096,
};

struct AtlasPage {
    GLuint texture;
    int next_x;
    int next_y;
    int row_height;
};

struct AtlasGlyph {
    int page;
    float width;
    float height;
    float bearing_x;
    float bearing_y;
    float advance;
    float u0, v0, u1, v1;
};

struct FontVertex {
    float x, y, u, v;
};

struct GlyphBatchRange {
    int page;
    size_t first;
    size_t count;
};

struct TextLayoutSlot {
    std::string text;
    float letter_spacing;
    int size_px;
    float width;
    float height;
    int valid;
    std::vector<FontVertex> vertices;
    std::vector<GlyphBatchRange> batches;
};

struct RasterFont {
    FT_Face face;
    int size_px;
    float ascender;
    float descender;
    float line_height;
    std::unordered_map<uint32_t, AtlasGlyph> glyphs;
    std::unordered_map<uint64_t, float> kernings;

    RasterFont() : face(nullptr), size_px(0), ascender(0), descender(0),
                   line_height(0) {}
    ~RasterFont() { if (face) FT_Done_Face(face); }
};

struct FontState {
    FT_Library library;
    char font_path[MW2ER_PATH_MAX];
    GlProgram program;
    GLuint vao;
    GLuint vbo;
    size_t vbo_size;
    std::unordered_map<int, std::unique_ptr<RasterFont>> fonts;
    std::vector<AtlasPage> pages;
    std::vector<uint8_t> bitmap_scratch;
    TextLayoutSlot slots[MW2ER_FONT_SLOT_COUNT];
};

static FontState g_font;

static void set_font_error(const char *operation, FT_Error error)
{
    char message[160];
    snprintf(message, sizeof(message), "FreeType %s failed (%d)", operation, error);
    mw2er_set_error(message);
}

static void clear_slots()
{
    for (TextLayoutSlot &slot : g_font.slots) {
        slot.text.clear();
        slot.letter_spacing = 0.0f;
        slot.size_px = 0;
        slot.width = 0.0f;
        slot.height = 0.0f;
        slot.valid = 0;
        slot.vertices.clear();
        slot.batches.clear();
    }
}

static void destroy_fonts()
{
    g_font.fonts.clear();
}

static void destroy_gl()
{
    for (AtlasPage &page : g_font.pages) {
        if (page.texture) glDeleteTextures(1, &page.texture);
    }
    g_font.pages.clear();
    if (g_font.vbo) glDeleteBuffers(1, &g_font.vbo);
    if (g_font.vao) glDeleteVertexArrays(1, &g_font.vao);
    g_font.program.destroy();
    g_font.vbo = 0;
    g_font.vao = 0;
    g_font.vbo_size = 0;
    destroy_fonts();
    clear_slots();
}

static AtlasPage *new_atlas_page()
{
    static const std::vector<uint8_t> empty(ATLAS_SIZE * ATLAS_SIZE, 0);
    // Allocate the owning slot before creating a GL name: vector growth can throw.
    g_font.pages.push_back({});
    AtlasPage &page = g_font.pages.back();
    glGenTextures(1, &page.texture);
    glBindTexture(GL_TEXTURE_2D, page.texture);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_R8, ATLAS_SIZE, ATLAS_SIZE, 0,
                 GL_RED, GL_UNSIGNED_BYTE, empty.data());
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glBindTexture(GL_TEXTURE_2D, 0);
    page.next_x = ATLAS_PADDING;
    page.next_y = ATLAS_PADDING;
    page.row_height = 0;
    return &page;
}

static int place_glyph(int width, int height, const uint8_t *pixels, int pitch,
                       int &page_index, float &u0, float &v0, float &u1, float &v1)
{
    if (width <= 0 || height <= 0) {
        page_index = 0;
        u0 = v0 = u1 = v1 = 0.0f;
        return 1;
    }
    if (width + ATLAS_PADDING * 2 > ATLAS_SIZE ||
        height + ATLAS_PADDING * 2 > ATLAS_SIZE) {
        mw2er_set_error("glyph bitmap exceeds atlas size");
        return 0;
    }
    AtlasPage *page = nullptr;
    for (size_t i = 0; i < g_font.pages.size(); ++i) {
        AtlasPage &candidate = g_font.pages[i];
        int next_x = candidate.next_x;
        int next_y = candidate.next_y;
        int row_height = candidate.row_height;
        const int padded_width = width + ATLAS_PADDING * 2;
        const int padded_height = height + ATLAS_PADDING * 2;
        if (next_x + padded_width > ATLAS_SIZE) {
            next_x = ATLAS_PADDING;
            next_y += row_height;
            row_height = 0;
        }
        if (next_y + padded_height > ATLAS_SIZE) continue;
        candidate.next_x = next_x;
        candidate.next_y = next_y;
        candidate.row_height = row_height;
        page = &candidate;
        page_index = (int)i;
        break;
    }
    if (!page) {
        page = new_atlas_page();
        page_index = (int)g_font.pages.size() - 1;
    }
    const int x = page->next_x + ATLAS_PADDING;
    const int y = page->next_y + ATLAS_PADDING;
    const uint8_t *upload = pixels;
    const int abs_pitch = std::abs(pitch);
    if (abs_pitch <= 0) {
        mw2er_set_error("glyph bitmap pitch is invalid");
        return 0;
    }
    if (pitch != width) {
        g_font.bitmap_scratch.resize((size_t)width * height);
        const int row_bytes = std::min(width, abs_pitch);
        for (int row = 0; row < height; ++row) {
            const int source_row = pitch < 0 ? height - row - 1 : row;
            uint8_t *dst = g_font.bitmap_scratch.data() + (size_t)row * width;
            std::memcpy(dst, pixels + (size_t)source_row * abs_pitch,
                        (size_t)row_bytes);
            if (row_bytes < width) {
                std::memset(dst + row_bytes, 0, (size_t)(width - row_bytes));
            }
        }
        upload = g_font.bitmap_scratch.data();
    }
    glBindTexture(GL_TEXTURE_2D, page->texture);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexSubImage2D(GL_TEXTURE_2D, 0, x, y, width, height,
                    GL_RED, GL_UNSIGNED_BYTE, upload);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    glBindTexture(GL_TEXTURE_2D, 0);
    page->next_x += width + ATLAS_PADDING * 2;
    page->row_height = std::max(page->row_height, height + ATLAS_PADDING * 2);
    const float scale = 1.0f / ATLAS_SIZE;
    u0 = x * scale;
    v0 = y * scale;
    u1 = (x + width) * scale;
    v1 = (y + height) * scale;
    return 1;
}

static RasterFont *raster_font(int size_px)
{
    size_px = std::max(1, size_px);
    auto found = g_font.fonts.find(size_px);
    if (found != g_font.fonts.end()) return found->second.get();
    std::unique_ptr<RasterFont> font(new RasterFont());
    FT_Error error = FT_New_Face(g_font.library, g_font.font_path, 0,
                                 &font->face);
    if (error) {
        set_font_error("new face", error);
        return nullptr;
    }
    error = FT_Set_Pixel_Sizes(font->face, 0, (FT_UInt)size_px);
    if (error) {
        set_font_error("set pixel size", error);
        return nullptr;
    }
    font->size_px = size_px;
    font->ascender = font->face->size->metrics.ascender / 64.0f;
    font->descender = font->face->size->metrics.descender / 64.0f;
    font->line_height = font->face->size->metrics.height / 64.0f;
    RasterFont *result = font.get();
    g_font.fonts.emplace(size_px, std::move(font));
    return result;
}

static AtlasGlyph *glyph(RasterFont &font, uint32_t codepoint)
{
    auto found = font.glyphs.find(codepoint);
    if (found != font.glyphs.end()) return &found->second;
    const FT_Error error = FT_Load_Char(
        font.face, (FT_ULong)codepoint, FT_LOAD_RENDER | FT_LOAD_TARGET_NORMAL);
    if (error) {
        set_font_error("load glyph", error);
        return nullptr;
    }
    const FT_GlyphSlot slot = font.face->glyph;
    const FT_Bitmap &bitmap = slot->bitmap;
    if (bitmap.pixel_mode != FT_PIXEL_MODE_GRAY) {
        mw2er_set_error("unsupported glyph pixel mode");
        return nullptr;
    }
    AtlasGlyph value = {};
    if (!place_glyph((int)bitmap.width, (int)bitmap.rows, bitmap.buffer,
                     bitmap.pitch, value.page,
                     value.u0, value.v0, value.u1, value.v1)) return nullptr;
    value.width = (float)bitmap.width;
    value.height = (float)bitmap.rows;
    value.bearing_x = (float)slot->bitmap_left;
    value.bearing_y = (float)slot->bitmap_top;
    value.advance = slot->advance.x / 64.0f;
    auto inserted = font.glyphs.emplace(codepoint, value);
    return &inserted.first->second;
}

static float kerning(RasterFont &font, uint32_t left, uint32_t right)
{
    if (!left) return 0.0f;
    const uint64_t key = ((uint64_t)left << 32) | right;
    auto found = font.kernings.find(key);
    if (found != font.kernings.end()) return found->second;
    const FT_UInt left_index = FT_Get_Char_Index(font.face, left);
    const FT_UInt right_index = FT_Get_Char_Index(font.face, right);
    FT_Vector delta = {};
    float value = 0.0f;
    if (left_index && right_index &&
        !FT_Get_Kerning(font.face, left_index, right_index,
                        FT_KERNING_DEFAULT, &delta))
        value = delta.x / 64.0f;
    font.kernings.emplace(key, value);
    return value;
}

static const char *next_utf8(const char *text, uint32_t &codepoint)
{
    const uint8_t first = (uint8_t)*text++;
    if (first < 0x80) {
        codepoint = first;
        return text;
    }
    int extra;
    uint32_t value;
    if ((first & 0xE0) == 0xC0) { extra = 1; value = first & 0x1F; }
    else if ((first & 0xF0) == 0xE0) { extra = 2; value = first & 0x0F; }
    else if ((first & 0xF8) == 0xF0) { extra = 3; value = first & 0x07; }
    else { codepoint = 0xFFFD; return text; }
    for (int i = 0; i < extra; ++i) {
        const uint8_t byte = (uint8_t)*text;
        if (!byte || (byte & 0xC0) != 0x80) {
            codepoint = 0xFFFD;
            return text;
        }
        value = (value << 6) | (byte & 0x3F);
        ++text;
    }
    codepoint = value;
    return text;
}

static void append_quad(std::vector<FontVertex> &vertices,
                        const AtlasGlyph &g, float x, float y)
{
    const float x1 = x + g.width;
    const float y1 = y + g.height;
    vertices.push_back({x, y, g.u0, g.v0});
    vertices.push_back({x1, y, g.u1, g.v0});
    vertices.push_back({x, y1, g.u0, g.v1});
    vertices.push_back({x, y1, g.u0, g.v1});
    vertices.push_back({x1, y, g.u1, g.v0});
    vertices.push_back({x1, y1, g.u1, g.v1});
}

static int ensure_gl()
{
    if (g_font.program.ok() && g_font.vao && g_font.vbo) return 1;
    const std::string dir = mw2er_shader_dir();
    if (!g_font.program.load((dir + "/font.vert").c_str(),
                             (dir + "/font.frag").c_str())) return 0;
    glGenVertexArrays(1, &g_font.vao);
    glGenBuffers(1, &g_font.vbo);
    glBindVertexArray(g_font.vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_font.vbo);
    g_font.vbo_size = INITIAL_VERTEX_BUFFER_SIZE;
    glBufferData(GL_ARRAY_BUFFER, g_font.vbo_size, nullptr, GL_STREAM_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, sizeof(FontVertex), nullptr);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, sizeof(FontVertex),
                          (const void *)(2 * sizeof(float)));
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);
    g_font.program.use();
    g_font.program.set("u_glyph", 0);
    glUseProgram(0);
    return 1;
}

static int prepare_layout(int slot_index, const char *text, int size_px,
                          float letter_spacing, TextLayoutSlot *&out)
{
    if (slot_index < 0 || slot_index >= MW2ER_FONT_SLOT_COUNT || !text) {
        mw2er_set_error("invalid text layout slot");
        return 0;
    }
    if (!ensure_gl()) return 0;
    TextLayoutSlot &layout = g_font.slots[slot_index];
    if (layout.valid && layout.text == text && layout.size_px == size_px &&
        layout.letter_spacing == letter_spacing) {
        out = &layout;
        return 1;
    }
    RasterFont *full = raster_font(size_px);
    const int small_size = std::max(1, (int)std::nearbyint(size_px * 0.8));
    RasterFont *small = raster_font(small_size);
    if (!full || !small) return 0;
    layout.text = text;
    layout.letter_spacing = letter_spacing;
    layout.size_px = size_px;
    layout.width = 0.0f;
    layout.height = std::max(full->line_height, full->ascender - full->descender);
    layout.valid = 0;
    layout.vertices.clear();
    layout.batches.clear();
    const float baseline = full->ascender;
    uint32_t previous_codepoint = 0;
    RasterFont *previous_font = nullptr;
    const char *cursor = text;
    while (*cursor) {
        uint32_t source;
        cursor = next_utf8(cursor, source);
        uint32_t styled = source;
        RasterFont *current_font = full;
        if (source >= 'a' && source <= 'z') {
            styled = source - 'a' + 'A';
            current_font = small;
        }
        if (previous_font) {
            RasterFont *kern_font = previous_font->size_px <= current_font->size_px
                ? previous_font : current_font;
            layout.width += kerning(*kern_font, previous_codepoint, styled);
            layout.width += letter_spacing;
        }
        AtlasGlyph *g = glyph(*current_font, styled);
        if (!g) return 0;
        if (g->width > 0.0f && g->height > 0.0f) {
            if (layout.batches.empty() || layout.batches.back().page != g->page)
                layout.batches.push_back({g->page, layout.vertices.size(), 0});
            append_quad(layout.vertices, *g,
                        layout.width + g->bearing_x,
                        baseline - g->bearing_y);
            layout.batches.back().count += 6;
        }
        layout.width += g->advance;
        previous_codepoint = styled;
        previous_font = current_font;
    }
    layout.valid = 1;
    out = &layout;
    return 1;
}

} // namespace

int32_t mw2er_font_process_init(const char *mod_dir)
{
    mw2er_font_process_shutdown();
    if (!mod_dir || !mod_dir[0]) return MW2ER_ERR_INVALID_ARGUMENT;
    const int font_len = snprintf(g_font.font_path, sizeof(g_font.font_path),
                                  "%s/fonts/Squarish Sans CT Regular.ttf", mod_dir);
    if (font_len < 0 || font_len >= (int)sizeof(g_font.font_path)) {
        mw2er_set_error("font resource path is too long");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    FILE *font_file = fopen(g_font.font_path, "rb");
    if (!font_file) {
        mw2er_set_error("font resource is missing");
        return MW2ER_ERR_NOT_READY;
    }
    fclose(font_file);
    const FT_Error error = FT_Init_FreeType(&g_font.library);
    if (error) {
        set_font_error("initialization", error);
        mw2er_font_process_shutdown();
        return MW2ER_ERR_NOT_READY;
    }
    return MW2ER_OK;
}

void mw2er_font_process_shutdown(void)
{
    destroy_fonts();
    if (g_font.library) FT_Done_FreeType(g_font.library);
    g_font.library = nullptr;
    g_font.font_path[0] = '\0';
    clear_slots();
}

void mw2er_font_gl_reset(void)
{
    destroy_gl();
}

void mw2er_font_clear_slots(void)
{
    clear_slots();
}

int32_t mw2er_font_measure(int slot, const char *text, int size_px,
                           float letter_spacing, Mw2erTextMetrics *metrics)
{
    if (!metrics) return MW2ER_ERR_INVALID_ARGUMENT;
    TextLayoutSlot *layout = nullptr;
    if (!prepare_layout(slot, text, std::max(1, size_px),
                        letter_spacing, layout)) return MW2ER_ERR_GL;
    metrics->width = layout->width;
    metrics->height = layout->height;
    return MW2ER_OK;
}

int32_t mw2er_font_draw(int slot, const char *text, int size_px,
                        float letter_spacing, float x, float y,
                        float horizontal_scale, const float color[4],
                        int viewport_width, int viewport_height,
                        int opaque_background)
{
    if (!color || viewport_width <= 0 || viewport_height <= 0)
        return MW2ER_ERR_INVALID_ARGUMENT;
    TextLayoutSlot *layout = nullptr;
    if (!prepare_layout(slot, text, std::max(1, size_px),
                        letter_spacing, layout)) return MW2ER_ERR_GL;
    if (layout->batches.empty()) return MW2ER_OK;
    g_font.program.use();
    g_font.program.set2("u_viewport_size", (float)viewport_width,
                        (float)viewport_height);
    g_font.program.set2("u_origin", (float)std::nearbyint(x),
                        (float)std::nearbyint(y));
    g_font.program.set2("u_scale", horizontal_scale, 1.0f);
    const GLint color_location = g_font.program.loc("u_color");
    if (color_location >= 0) glUniform4fv(color_location, 1, color);
    glEnable(GL_BLEND);
    glBlendFuncSeparate(GL_ONE, GL_ONE_MINUS_SRC_ALPHA, GL_ONE,
                        opaque_background ? GL_ONE_MINUS_SRC_ALPHA : GL_ZERO);
    glActiveTexture(GL_TEXTURE0);
    glBindVertexArray(g_font.vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_font.vbo);
    for (const GlyphBatchRange &batch : layout->batches) {
        const size_t bytes = batch.count * sizeof(FontVertex);
        if (bytes > g_font.vbo_size) {
            while (g_font.vbo_size < bytes) g_font.vbo_size *= 2;
            glBufferData(GL_ARRAY_BUFFER, g_font.vbo_size, nullptr, GL_STREAM_DRAW);
        }
        glBufferSubData(GL_ARRAY_BUFFER, 0, bytes,
                        layout->vertices.data() + batch.first);
        glBindTexture(GL_TEXTURE_2D, g_font.pages[batch.page].texture);
        glDrawArrays(GL_TRIANGLES, 0, (GLsizei)batch.count);
    }
    glBindTexture(GL_TEXTURE_2D, 0);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);
    glDisable(GL_BLEND);
    glUseProgram(0);
    return MW2ER_OK;
}
