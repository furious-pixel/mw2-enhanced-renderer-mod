#include "texture.h"
#include "config.h"
#include "mw2er_internal.h"
#include "resource.h"
#include "startup_trace.h"

#include <stdlib.h>
#include <string.h>
#include <vector>

enum {
    ADDR_TEXTURE_DESCRIPTOR_TABLE = 0x0013DFE0,
    ADDR_TEXTURE_CELL_TABLE = 0x0013FBE0,
    ADDR_TEXTURE_REMAP_TABLE_PTR = 0x000A6DCC,
    TEXTURE_REMAP_TABLE_COUNT = 16,
    TEXTURE_REMAP_TABLE_SIZE = 256,
    TEXTURE_DESCRIPTOR_STRIDE = 14,
    TEXTURE_DESCRIPTOR_COUNT = 512,
    TEXTURE_CELL_PAGE_STRIDE = 256,
    TEXTURE_CELL_SUB_ENTRY_STRIDE = 8,
    TEXTURE_CELL_SUB_ENTRY_COUNT = 32,
    CEL_HEADER_SIZE = 0x14,
    CEL_CELL_POINTER_OFFSET = 0x10,
    MAX_TEXTURE_DIMENSION = 1024,
    MAX_TEXTURE_BYTES = 1024 * 1024
};

struct RemapClassification {
    int remap_kind_id;
    float dark_ratio[3];
    float fog_terminal[3];
    float s8_ratio[3];
};

struct CelCacheEntry {
    int resource_id;
    int width;
    int height;
    std::vector<uint8_t> enhanced_pixels;
    int enhanced_w;
    int enhanced_h;
    int enhancement_role_id;
    uint32_t hist[256];
    uint32_t n_opaque;
    uint64_t remap_revision = 0;
    uint64_t classification_revision = 0;
    bool proven_identity = false;
    RemapClassification classification = {};
};

static std::vector<CelCacheEntry> g_cels;
static uint8_t g_remap[16][256];
static uint8_t g_palette[256 * 3];
static bool g_identity_indices[256];
static uint64_t g_remap_revision;
static uint64_t g_classification_revision;
static int g_remap_ready;

static int16_t load_i16(const uint8_t *p)
{
    int16_t v;
    memcpy(&v, p, 2);
    return v;
}

static uint16_t load_u16(const uint8_t *p)
{
    uint16_t v;
    memcpy(&v, p, 2);
    return v;
}

static uint32_t load_u32(const uint8_t *p)
{
    uint32_t v;
    memcpy(&v, p, 4);
    return v;
}

void mw2er_texture_free_cache(void)
{
    g_cels.clear();
    g_remap_ready = 0;
}

static CelCacheEntry *cel_find(int resource_id)
{
    for (size_t i = 0; i < g_cels.size(); ++i) {
        CelCacheEntry &e = g_cels[i];
        if (e.resource_id == resource_id) {
            return &e;
        }
    }
    return NULL;
}

static CelCacheEntry *cel_alloc(void)
{
    g_cels.emplace_back();
    return &g_cels.back();
}

static float rgb_lum(int r, int g, int b)
{
    return 0.299f * (float)r + 0.587f * (float)g + 0.114f * (float)b;
}

static void pal_rgb(const uint8_t *pal, int idx, int *r, int *g, int *b)
{
    int o = (idx & 255) * 3;
    *r = pal[o];
    *g = pal[o + 1];
    *b = pal[o + 2];
}

static float pal_lum(const uint8_t *pal, int idx)
{
    int r, g, b;
    pal_rgb(pal, idx, &r, &g, &b);
    return rgb_lum(r, g, b);
}

static int pal_manhattan(const uint8_t *pal, int a, int b)
{
    int ar, ag, ab, br, bg, bb;
    pal_rgb(pal, a, &ar, &ag, &ab);
    pal_rgb(pal, b, &br, &bg, &bb);
    return abs(ar - br) + abs(ag - bg) + abs(ab - bb);
}

static void pal_ratio(const uint8_t *pal, int remapped, int base, float *out)
{
    int rr, rg, rb, br, bg, bb;
    pal_rgb(pal, remapped, &rr, &rg, &rb);
    pal_rgb(pal, base, &br, &bg, &bb);
    out[0] = br <= 0 ? 0.0f : (float)rr / (float)br;
    out[1] = bg <= 0 ? 0.0f : (float)rg / (float)bg;
    out[2] = bb <= 0 ? 0.0f : (float)rb / (float)bb;
    if (out[0] < 0.0f) {
        out[0] = 0.0f;
    }
    if (out[1] < 0.0f) {
        out[1] = 0.0f;
    }
    if (out[2] < 0.0f) {
        out[2] = 0.0f;
    }
}

static float rgb_sqerr(int r, int g, int b, float pr, float pg, float pb)
{
    float dr = (float)r - pr;
    float dg = (float)g - pg;
    float db = (float)b - pb;
    return dr * dr + dg * dg + db * db;
}

static int select_fog_category(
    int bright_idx,
    const uint8_t *pal,
    const uint8_t remap[16][256],
    int term_r,
    int term_g,
    int term_b,
    const float *s8_ratio)
{
    int shade;
    float mse_linear = 0.0f;
    float mse_split = 0.0f;
    int br, bg, bb;
    float mid[3];

    if (s8_ratio[0] >= 1.0f || s8_ratio[1] >= 1.0f || s8_ratio[2] >= 1.0f) {
        return 3;
    }
    pal_rgb(pal, bright_idx, &br, &bg, &bb);
    mid[0] = (float)br * s8_ratio[0];
    mid[1] = (float)bg * s8_ratio[1];
    mid[2] = (float)bb * s8_ratio[2];
    for (shade = 1; shade < 15; ++shade) {
        int gr, gg, gb;
        float t = (float)shade / 15.0f;
        float lr, lg, lb;
        float sr, sg, sb;
        pal_rgb(pal, remap[shade][bright_idx], &gr, &gg, &gb);
        lr = (float)term_r + ((float)br - (float)term_r) * t;
        lg = (float)term_g + ((float)bg - (float)term_g) * t;
        lb = (float)term_b + ((float)bb - (float)term_b) * t;
        if (t >= (8.0f / 15.0f)) {
            float u = (t - (8.0f / 15.0f)) / (7.0f / 15.0f);
            sr = mid[0] + ((float)br - mid[0]) * u;
            sg = mid[1] + ((float)bg - mid[1]) * u;
            sb = mid[2] + ((float)bb - mid[2]) * u;
        } else {
            float u = t / (8.0f / 15.0f);
            sr = (float)term_r + (mid[0] - (float)term_r) * u;
            sg = (float)term_g + (mid[1] - (float)term_g) * u;
            sb = (float)term_b + (mid[2] - (float)term_b) * u;
        }
        mse_linear += rgb_sqerr(gr, gg, gb, lr, lg, lb);
        mse_split += rgb_sqerr(gr, gg, gb, sr, sg, sb);
    }
    return mse_split < mse_linear ? 4 : 3;
}

static void classify_remap(
    const uint32_t *hist,
    uint32_t n_opaque,
    const uint8_t *pal,
    RemapClassification *out)
{
    int used[256];
    int n_used = 0;
    int i;
    int bright_idx = 0;
    float bright_lum = -1.0f;
    double total_dist = 0.0;
    int s0_colors[256][3];
    int n_s0 = 0;
    float s0_lums[256];
    int s0_counts[256];
    float s15_lums[256];
    float max_lum;
    int rgb_spread;
    float s0_min = 1e9f, s0_max = -1e9f, s15_min = 1e9f, s15_max = -1e9f;
    float contrast;
    int kind = 0;
    int term_r, term_g, term_b;
    int chroma;

    out->remap_kind_id = 0;
    out->dark_ratio[0] = out->dark_ratio[1] = out->dark_ratio[2] = 0.0f;
    out->fog_terminal[0] = out->fog_terminal[1] = out->fog_terminal[2] = 0.0f;
    out->s8_ratio[0] = out->s8_ratio[1] = out->s8_ratio[2] = 0.0f;
    if (n_opaque == 0) {
        return;
    }
    for (i = 0; i < 255; ++i) {
        if (hist[i] != 0) {
            used[n_used++] = i;
            if (pal_lum(pal, i) > bright_lum) {
                bright_lum = pal_lum(pal, i);
                bright_idx = i;
            }
        }
    }
    if (n_used == 0) {
        return;
    }
    for (i = 0; i < n_used; ++i) {
        int idx = used[i];
        int m0 = g_remap[0][idx];
        int m15 = g_remap[15][idx];
        if (m0 != m15) {
            total_dist += (double)hist[idx] * (double)pal_manhattan(pal, m0, m15);
        }
    }
    if ((total_dist / (double)n_opaque) <= 2.0) {
        return;
    }
    for (i = 0; i < n_used; ++i) {
        int idx = used[i];
        int s0 = g_remap[0][idx];
        int s15 = g_remap[15][idx];
        int r, g, b;
        int c;
        int found = 0;
        float lum;
        pal_rgb(pal, s0, &r, &g, &b);
        lum = rgb_lum(r, g, b);
        for (c = 0; c < n_s0; ++c) {
            if (s0_colors[c][0] == r && s0_colors[c][1] == g && s0_colors[c][2] == b) {
                found = 1;
                break;
            }
        }
        if (!found && n_s0 < 256) {
            s0_colors[n_s0][0] = r;
            s0_colors[n_s0][1] = g;
            s0_colors[n_s0][2] = b;
            n_s0 += 1;
        }
        s0_lums[i] = lum;
        s0_counts[i] = (int)hist[idx];
        s15_lums[i] = pal_lum(pal, s15);
        if (lum < s0_min) {
            s0_min = lum;
        }
        if (lum > s0_max) {
            s0_max = lum;
        }
        if (s15_lums[i] < s15_min) {
            s15_min = s15_lums[i];
        }
        if (s15_lums[i] > s15_max) {
            s15_max = s15_lums[i];
        }
    }
    {
        /* 0.99 weighted percentile of s0 luminance */
        int order[256];
        uint32_t cum = 0;
        double target = (double)n_opaque * 0.99;
        for (i = 0; i < n_used; ++i) {
            order[i] = i;
        }
        /* insertion sort by luminance */
        {
            int a, b;
            for (a = 1; a < n_used; ++a) {
                int key = order[a];
                b = a;
                while (b > 0 && s0_lums[order[b - 1]] > s0_lums[key]) {
                    order[b] = order[b - 1];
                    b -= 1;
                }
                order[b] = key;
            }
        }
        max_lum = s0_lums[order[n_used - 1]];
        for (i = 0; i < n_used; ++i) {
            cum += (uint32_t)s0_counts[order[i]];
            if ((double)cum >= target) {
                max_lum = s0_lums[order[i]];
                break;
            }
        }
    }
    {
        int ch;
        int mn[3] = {255, 255, 255};
        int mx[3] = {0, 0, 0};
        int c;
        for (c = 0; c < n_s0; ++c) {
            for (ch = 0; ch < 3; ++ch) {
                if (s0_colors[c][ch] < mn[ch]) {
                    mn[ch] = s0_colors[c][ch];
                }
                if (s0_colors[c][ch] > mx[ch]) {
                    mx[ch] = s0_colors[c][ch];
                }
            }
        }
        rgb_spread = mx[0] - mn[0];
        if (mx[1] - mn[1] > rgb_spread) {
            rgb_spread = mx[1] - mn[1];
        }
        if (mx[2] - mn[2] > rgb_spread) {
            rgb_spread = mx[2] - mn[2];
        }
    }
    contrast = (s15_max - s15_min) <= 0.0f ? 1e30f : (s0_max - s0_min) / (s15_max - s15_min);
    pal_rgb(pal, g_remap[0][bright_idx], &term_r, &term_g, &term_b);
    chroma = term_r;
    if (term_g > chroma) {
        chroma = term_g;
    }
    if (term_b > chroma) {
        chroma = term_b;
    }
    {
        int mn = term_r;
        if (term_g < mn) {
            mn = term_g;
        }
        if (term_b < mn) {
            mn = term_b;
        }
        chroma -= mn;
    }
    if (n_s0 >= 2 && max_lum <= 35.0f) {
        kind = 2;
    } else if (n_s0 == 1 && max_lum <= 15.0f && chroma <= 8) {
        kind = 1;
    } else if (max_lum <= 15.0f && chroma > 8) {
        kind = 3;
    } else if (contrast < 0.15f) {
        kind = 3;
    } else if (rgb_spread <= 15) {
        kind = 3;
    } else {
        kind = 3;
    }
    pal_ratio(pal, g_remap[0][bright_idx], bright_idx, out->dark_ratio);
    out->fog_terminal[0] = (float)term_r / 255.0f;
    out->fog_terminal[1] = (float)term_g / 255.0f;
    out->fog_terminal[2] = (float)term_b / 255.0f;
    pal_ratio(pal, g_remap[8][bright_idx], bright_idx, out->s8_ratio);
    if (kind == 3) {
        kind = select_fog_category(
            bright_idx, pal, g_remap, term_r, term_g, term_b, out->s8_ratio);
    }
    out->remap_kind_id = kind;
}

void mw2er_texture_begin_frame(const Mem &mem, const uint8_t *palette_rgb)
{
    const uint32_t ptr = mem.u32_rel(ADDR_TEXTURE_REMAP_TABLE_PTR);
    const uint8_t *raw = ptr ? mem.view(ptr, sizeof(g_remap)) : nullptr;
    if (raw == nullptr) {
        g_remap_ready = 0;
        mw2er_startup_texture_context(false, g_remap_revision, g_classification_revision);
        return;
    }
    const bool remap_changed = !g_remap_ready ||
        memcmp(g_remap, raw, sizeof(g_remap)) != 0;
    const bool palette_changed = !g_remap_ready ||
        memcmp(g_palette, palette_rgb, sizeof(g_palette)) != 0;
    if (remap_changed) {
        memcpy(g_remap, raw, sizeof(g_remap));
        ++g_remap_revision;
        for (int i = 0; i < 256; ++i) {
            g_identity_indices[i] = true;
            for (int shade = 0; shade < 16; ++shade) {
                if (g_remap[shade][i] != i) {
                    g_identity_indices[i] = false;
                    break;
                }
            }
        }
    }
    if (palette_changed)
        memcpy(g_palette, palette_rgb, sizeof(g_palette));
    if (remap_changed || palette_changed)
        ++g_classification_revision;
    g_remap_ready = 1;
    mw2er_startup_texture_context(true, g_remap_revision, g_classification_revision);
}

static const RemapClassification &cached_remap(CelCacheEntry &cel)
{
    if (cel.remap_revision != g_remap_revision) {
        cel.proven_identity = true;
        for (int i = 0; i < 255; ++i) {
            if (cel.hist[i] && !g_identity_indices[i]) {
                cel.proven_identity = false;
                break;
            }
        }
        cel.remap_revision = g_remap_revision;
        if (cel.proven_identity)
            cel.classification = {};
    }
    // RGB identity during a black fade is not proof of an identity mapping.
    if (!cel.proven_identity &&
        cel.classification_revision != g_classification_revision) {
        const Mw2erStartupScope trace(MW2ER_STARTUP_CLASSIFY);
        classify_remap(cel.hist, cel.n_opaque, g_palette, &cel.classification);
        cel.classification_revision = g_classification_revision;
    } else if (mw2er_startup_work) {
        ++mw2er_startup_work->count[cel.proven_identity
            ? MW2ER_STARTUP_IDENTITY : MW2ER_STARTUP_CLASSIFY_HIT];
    }
    return cel.classification;
}

static CelCacheEntry *load_cel(
    const Mem &mem,
    int resource_id,
    uint32_t data_ptr)
{
    CelCacheEntry *ent;
    const Mw2erResourceAsset *asset;
    uint32_t header_addr;
    uint8_t header[CEL_HEADER_SIZE];
    int width;
    int height;
    int pixel_count;

    ent = cel_find(resource_id);
    if (ent != NULL) {
        return ent;
    }
    asset = mw2er_resource_find(MW2ER_RESOURCE_CEL, (uint32_t)resource_id);
    if (asset == NULL) {
        if (!mw2er_resources_allow_guest_fallback()) return NULL;
        if (data_ptr == 0 || data_ptr < CEL_CELL_POINTER_OFFSET) {
            return NULL;
        }
        header_addr = data_ptr - CEL_CELL_POINTER_OFFSET;
        if (!mem.read(header_addr, header, CEL_HEADER_SIZE)) {
            return NULL;
        }
        if (header[0] != 'C' || header[1] != 'E' || header[2] != 'L' ||
            header[3] != 0) {
            return NULL;
        }
        width = (int)load_u16(header + 0x10);
        height = (int)load_u16(header + 0x12);
        if (width <= 0 || height <= 0 || width > MAX_TEXTURE_DIMENSION ||
            height > MAX_TEXTURE_DIMENSION) {
            return NULL;
        }
        const uint32_t pixel_bytes = (uint32_t)width * (uint32_t)height;
        if (pixel_bytes > MAX_TEXTURE_BYTES) {
            return NULL;
        }
        const uint8_t *pixels = mem.view(
            header_addr + CEL_HEADER_SIZE, pixel_bytes);
        if (pixels == NULL || !mw2er_resource_retain_cel(
                (uint32_t)resource_id,
                (uint16_t)width,
                (uint16_t)height,
                pixels,
                pixel_bytes)) {
            return NULL;
        }
        asset = mw2er_resource_find(MW2ER_RESOURCE_CEL, (uint32_t)resource_id);
    }
    if (asset == NULL) {
        return NULL;
    }
    const Mw2erStartupScope trace(MW2ER_STARTUP_HIST);
    ent = cel_alloc();
    ent->enhanced_pixels.clear();
    ent->resource_id = resource_id;
    ent->width = asset->width;
    ent->height = asset->height;
    ent->enhanced_w = 0;
    ent->enhanced_h = 0;
    ent->enhancement_role_id = 0;
    ent->n_opaque = 0;
    memset(ent->hist, 0, sizeof(ent->hist));
    pixel_count = (int)asset->bytes.size();
    for (int i = 0; i < pixel_count; ++i) {
        uint8_t p = asset->bytes[(size_t)i];
        if (p != 0xFF) {
            ent->hist[p] += 1;
            ent->n_opaque += 1;
        }
    }
    return ent;
}

static int select_sub(int selector, int *subs, int n_subs)
{
    int i;
    int chosen = -1;
    if (n_subs <= 0) {
        return -1;
    }
    for (i = 0; i < n_subs; ++i) {
        if (subs[i] == selector) {
            return selector;
        }
    }
    for (i = 0; i < n_subs; ++i) {
        if (subs[i] <= selector) {
            chosen = subs[i];
        } else {
            break;
        }
    }
    if (chosen >= 0) {
        return chosen;
    }
    return subs[0];
}

static int enhancement_role_for_resource(int resource_id)
{
    if (resource_id == 610 || resource_id == 611 || resource_id == 612 ||
        resource_id == 613 || resource_id == 614) {
        return 1;
    }
    if (resource_id == 706) {
        return 2;
    }
    return 0;
}

static void ensure_mirrored_atlas(
    CelCacheEntry &cel,
    const uint8_t *src,
    int role)
{
    if (cel.enhancement_role_id == role && !cel.enhanced_pixels.empty()) {
        return;
    }
    const Mw2erStartupScope trace(MW2ER_STARTUP_ATLAS);
    int w = cel.width;
    int h = cel.height;
    cel.enhanced_w = w * 2;
    cel.enhanced_h = h * 2;
    cel.enhancement_role_id = role;
    cel.enhanced_pixels.resize((size_t)cel.enhanced_w * (size_t)cel.enhanced_h);
    uint8_t *dst = cel.enhanced_pixels.data();
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            uint8_t p = src[y * w + x];
            uint8_t pr = src[y * w + (w - 1 - x)];
            uint8_t pb = src[(h - 1 - y) * w + x];
            uint8_t pbr = src[(h - 1 - y) * w + (w - 1 - x)];
            dst[y * cel.enhanced_w + x] = p;
            dst[y * cel.enhanced_w + w + x] = pr;
            dst[(h + y) * cel.enhanced_w + x] = pb;
            dst[(h + y) * cel.enhanced_w + w + x] = pbr;
        }
    }
}

int is_aero_lift_fan_resource(int resource_id)
{
    return resource_id >= 0x01A7 && resource_id <= 0x01A7 + 4;
}

int mw2er_is_imaging_effect_resource(int resource_id)
{
    return (resource_id >= 1 && resource_id <= 413) ||
        (resource_id >= 428 && resource_id <= 593) ||
        (resource_id >= 634 && resource_id <= 647);
}

int mw2er_desc_is_imaging_effect(const Mem &mem, int desc_idx)
{
    uint8_t desc[TEXTURE_DESCRIPTOR_STRIDE];
    uint8_t cell[TEXTURE_CELL_PAGE_STRIDE];
    if (desc_idx < 0 || desc_idx >= TEXTURE_DESCRIPTOR_COUNT ||
        !mem.read_rel(
            ADDR_TEXTURE_DESCRIPTOR_TABLE +
                (uint32_t)desc_idx * TEXTURE_DESCRIPTOR_STRIDE,
            desc,
            sizeof(desc))) {
        return 0;
    }
    const int page = load_i16(desc);
    if (page < 0 || page >= 512 ||
        !mem.read_rel(
            ADDR_TEXTURE_CELL_TABLE + (uint32_t)page * TEXTURE_CELL_PAGE_STRIDE,
            cell,
            sizeof(cell))) {
        return 0;
    }
    int found = 0;
    for (int i = 0; i < TEXTURE_CELL_SUB_ENTRY_COUNT; ++i) {
        const int rid = load_i16(cell + i * TEXTURE_CELL_SUB_ENTRY_STRIDE);
        if (rid < 1) {
            continue;
        }
        found = 1;
        if (!mw2er_is_imaging_effect_resource(rid)) {
            return 0;
        }
    }
    return found;
}

int mw2er_desc_draw_allowed(const Mem &mem, int desc_idx)
{
    uint8_t desc[TEXTURE_DESCRIPTOR_STRIDE];
    int page;
    int state;
    int active;

    if (desc_idx < 0 || desc_idx >= TEXTURE_DESCRIPTOR_COUNT) {
        return 0;
    }
    if (!mem.read_rel(
            ADDR_TEXTURE_DESCRIPTOR_TABLE +
                (uint32_t)desc_idx * TEXTURE_DESCRIPTOR_STRIDE,
            desc,
            TEXTURE_DESCRIPTOR_STRIDE)) {
        return 0;
    }
    page = load_i16(desc + 0);
    state = load_u16(desc + 6);
    active = load_i16(desc + 8);
    if (page < 0 || page >= 512 || state == 0 || active < 0) {
        return 0;
    }
    return 1;
}

int mw2er_texture_resolve(
    const Mem &mem,
    int desc_idx,
    Mw2erResolvedTexture &out)
{
    uint8_t desc[TEXTURE_DESCRIPTOR_STRIDE];
    uint8_t cell[TEXTURE_CELL_PAGE_STRIDE];
    int page;
    int selector;
    int animation_interval;
    int state;
    int active;
    int subs[32];
    int n_subs = 0;
    int sub;
    CelCacheEntry *cel;

    memset(&out, 0, sizeof(out));
    if (desc_idx < 0 || desc_idx >= TEXTURE_DESCRIPTOR_COUNT) {
        return 0;
    }
    if (!mem.read_rel(
            ADDR_TEXTURE_DESCRIPTOR_TABLE +
                (uint32_t)desc_idx * TEXTURE_DESCRIPTOR_STRIDE,
            desc,
            TEXTURE_DESCRIPTOR_STRIDE)) {
        return 0;
    }
    page = load_i16(desc + 0);
    selector = load_u16(desc + 2);
    animation_interval = load_u16(desc + 4);
    state = load_u16(desc + 6);
    active = load_i16(desc + 8);
    if (page < 0 || page >= 512 || state == 0 || active < 0) {
        return 0;
    }
    if (!mem.read_rel(
            ADDR_TEXTURE_CELL_TABLE + (uint32_t)page * TEXTURE_CELL_PAGE_STRIDE,
            cell,
            TEXTURE_CELL_PAGE_STRIDE)) {
        return 0;
    }
    for (int i = 0; i < TEXTURE_CELL_SUB_ENTRY_COUNT; ++i) {
        int rid = load_i16(cell + i * TEXTURE_CELL_SUB_ENTRY_STRIDE);
        if (rid >= 1) {
            subs[n_subs++] = i;
        }
    }
    sub = select_sub(selector, subs, n_subs);
    if (sub < 0) {
        return 0;
    }
    {
        int rid = load_i16(cell + sub * TEXTURE_CELL_SUB_ENTRY_STRIDE);
        uint32_t ptr = load_u32(cell + sub * TEXTURE_CELL_SUB_ENTRY_STRIDE + 4);
        cel = load_cel(mem, rid, ptr);
    }
    if (cel == NULL) {
        return 0;
    }
    const Mw2erResourceAsset *asset = mw2er_resource_find(
        MW2ER_RESOURCE_CEL, (uint32_t)cel->resource_id);
    if (asset == NULL) {
        return 0;
    }
    out.valid = 1;
    out.width = cel->width;
    out.height = cel->height;
    out.pixels = asset->bytes.data();
    out.wrap = desc_idx >= 0x100;
    out.discard_ff = desc_idx < 0x100;
    out.resource_id = cel->resource_id;
    out.animated_effect = animation_interval != 0 || selector != 0 || n_subs > 1;
    out.enhancement_role_id = 0;
    out.enhanced_uv_scale = 1.0f;
    if (desc_idx >= 0x100 && g_remap_ready) {
        const RemapClassification &remap = cached_remap(*cel);
        out.remap_kind_id = remap.remap_kind_id;
        memcpy(out.dark_ratio, remap.dark_ratio, sizeof(out.dark_ratio));
        memcpy(out.fog_terminal, remap.fog_terminal, sizeof(out.fog_terminal));
        memcpy(out.s8_ratio, remap.s8_ratio, sizeof(out.s8_ratio));
    }
    if (desc_idx >= 0x100) {
        int role = enhancement_role_for_resource(cel->resource_id);
        const Mw2erRendererConfig &cfg = mw2er_config();
        if (role == 1 && !cfg.enhanced_mech_textures) {
            role = 0;
        }
        if (role == 2 && !cfg.enhanced_dropship_textures) {
            role = 0;
        }
        if (role != 0) {
            ensure_mirrored_atlas(*cel, asset->bytes.data(), role);
            out.pixels = cel->enhanced_pixels.data();
            out.width = cel->enhanced_w;
            out.height = cel->enhanced_h;
            out.wrap = 1;
            out.enhancement_role_id = role;
            out.enhanced_uv_scale = (role == 1)
                ? cfg.enhanced_mech_texture_uv_scale
                : cfg.enhanced_dropship_texture_uv_scale;
        }
    }
    return 1;
}
