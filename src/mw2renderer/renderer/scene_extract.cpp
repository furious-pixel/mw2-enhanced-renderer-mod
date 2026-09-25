#include "scene_extract.h"
#include "config.h"
#include "mw2er_internal.h"
#include "resource.h"
#include "terrain_gap.h"
#include "texture.h"
#include "startup_trace.h"

#ifdef _MSC_VER
#define MW2ER_STRICMP _stricmp
#else
#include <strings.h>
#define MW2ER_STRICMP strcasecmp
#endif

#include <algorithm>
#include <chrono>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

enum {
    ADDR_NODE_LIST_HEADER = 0x00104580,
    ADDR_CAMERA_POSITION = 0x0015FFA4,
    ADDR_CAMERA_ROTATION = 0x000A705C,
    ADDR_CAMERA_MODE = 0x000A6FB4,
    ADDR_CAMERA_NEAR_DEPTH = 0x0015FFC8,
    ADDR_CAMERA_FOCAL = 0x000A7020,
    ADDR_PALETTE = 0x000B5390,
    ADDR_SKY_PALETTE_INDEX = 0x000A6F74,
    ADDR_GROUND_PALETTE_INDEX = 0x000A6F78,
    ADDR_GRADIENT_ENABLE = 0x000A70F0,
    ADDR_SKY_VISIBLE = 0x000A7108,
    ADDR_GROUND_VISIBLE = 0x000A710C,
    ADDR_GRADIENT_BAND_ENABLE = 0x000A7110,
    ADDR_GRADIENT_HEIGHT = 0x000A7154,
    ADDR_FOG_DISTANCE = 0x000A7130,
    ADDR_IMAGING_ACTIVE = 0x000A7120,
    ADDR_CAMERA_FAR_DEPTH = 0x0015FF80,
    ADDR_COMPONENT_LIGHTING_MODE = 0x000A6F88,
    ADDR_SPECIAL_CAMERA_LATCH = 0x000A62B4,
    ADDR_ACTIVE_CAMERA = 0x000A70E8,
    ADDR_RADAR_CAMERA_NODE_FALLBACK = 0x000B4CFC,
    ADDR_SCENE_LIGHT_Z = 0x0015FFD0,
    ADDR_SCENE_LIGHT_IS_DIRECTIONAL = 0x0015FDC8,
    ADDR_AMBIENT = 0x0015FDCC,
    ADDR_SCENE_LIGHT_X = 0x0015FFD8,
    ADDR_SCENE_LIGHT_Y = 0x0015FFDC,
    ADDR_MODEL_TREE_ROOT_A = 0x000A55B0,
    ADDR_MODEL_TREE_ROOT_B = 0x000A55B4,
    ADDR_ENTITY_BODY_TABLE = 0x00108B00,
    ADDR_ENTITY_COUNT = 0x000A6270,
    ADDR_PRIMARY_CLASSIFICATION = 0x0010B631,
    ADDR_SECONDARY_CLASSIFICATION = 0x0010C6C4,
    ADDR_SECONDARY_IFF_REFERENCES = 0x00104B8C,
    ADDR_COMPONENT_DESCRIPTOR_TABLE = 0x001310B0,
    ADDR_COMPONENT_DESCRIPTOR_COUNT = 0x000A6DB0,
    ADDR_PLAYER_SLOT = 0x000A5918,
    ADDR_SATELLITE_SHADE_BIAS = 0x000A5660,
    ADDR_SATELLITE_SHADE_DIVISOR = 0x000A5668,
    ADDR_RADAR_MODE = 0x000B4D08,
    ADDR_RADAR_CONFIG_TABLE = 0x000B4CF0,
    ADDR_MISSION_NAME = 0x0016BB6C,
    MISSION_NAME_MAX_BYTES = 32
};

enum {
    HEADER_SIZE = 0x18,
    NODE_SIZE = 0x20,
    GEOMETRY_NODE_SIZE = 0x24,
    MODEL_TREE_NODE_READ_SIZE = 0x70,
    VERTEX_STRIDE = 0x2C,
    FACE_STRIDE = 0x24,
    ENTITY_MATRIX_OFFSET = 0x3C,
    ENTITY_TRANSLATION_OFFSET = 0x24,
    ENTITY_MATRIX_SIZE = 0x30,
    NODE_FLAG_TERRAIN_HI = 0x08,
    MAX_NODES = 1000,
    MAX_MODEL_TREE_NODES = 1000,
    MODEL_TREE_PENDING_CAP = 4096,
    MAX_VERTICES = 1000,
    MAX_FACES = 1000,
    MAX_FACE_VERTICES = 8,
    MAX_FACE_DATA_OFFSET = 0x20000,
    WTBO_HEADER_SIZE = 0x20,
    WTBO_VERTEX_STRIDE = 0x10,
    MAX_POLY_VERTICES = 4096,
    MAX_POLY_FACES = 4096,
    MAX_POLY_PAYLOAD = 4 * 1024 * 1024,
    DUMMY_RESOURCE_ID = 0x07B1,
    COMPONENT_DESCRIPTOR_STRIDE = 0x44,
    MAX_COMPONENT_DESCRIPTORS = 4096,
    PRIMARY_CLASSIFICATION_SLOTS = 16,
    PRIMARY_CLASSIFICATION_STRIDE = 0x26,
    PRIMARY_ENTITY_LIMIT = 4096,
    SRC_PRIMARY = 0,
    SRC_MODEL_TREE = 1,
    SRC_RENDERER_LOD = 2,
    SRC_TARGET = 3
};

enum {
    SATELLITE_OWNER_FIXED = 0,
    SATELLITE_OWNER_SOURCE = 1,
    SATELLITE_OWNER_FORCE_MODE4 = 2
};

enum {
    MODE_FLAT_UNLIT = 0,
    MODE_FLAT_LIT = 1,
    MODE_POLYLINE = 2,
    MODE_BILLBOARD = 3,
    MODE_ILLUMINATE = 4,
    MODE_TEXTURED_P = 5,
    MODE_TEXTURED_PRE = 6,
    MODE_TEXTURED_AFF = 7,
    MODE_SATELLITE_WIREFRAME = 8,
    MODE_SATELLITE_SOLID = 9
};

enum {
    ROTOR_DISC_SOURCE_VERTICES = 14,
    ROTOR_DISC_SOURCE_FACES = 2,
    ROTOR_DISC_SOURCE_FACE_VERTICES = 7,
    AERO_LIFT_FAN_DESC_INDEX = 0x128,
    EMBLEM_TEXTURE_SOURCE_INDEX = 0x14,
    WIREFRAME_DEFAULT_PALETTE = 0x08,
    ENTITY_MODEL_RADIUS_OFFSET = 0xE8,
    COLLISION_EFFECT_POOL = 0x00327100,
    COLLISION_EFFECT_SLOT_SIZE = 0x24,
    COLLISION_EFFECT_SLOT_COUNT = 256,
    MAX_RELOCATED_EFFECT_ID = 22
};

static const float k_lighting_policy_pack = 4096.0f;
static const float k_imaging_fade_width = 0.15f;
static const double k_angle_full_turn = (double)0x01680000;
static const int32_t k_satellite_pitch = 0x005A0000;
static const float k_satellite_reference_aspect = 1024.0f / 768.0f;
static const float k_satellite_far = 8.0f;
static const float k_cockpit_effect_margin = 1.10f;


static const float k_fixed_scale = 65536.0f;
static const float k_rot_scale = 536870912.0f; /* 2^29 */
static const float k_native_depth_mul = 4.0f;
static const float k_focal_base = 512.0f;

static int32_t g_sqrt_table[1024];
static int g_sqrt_ready = 0;

static const uint32_t k_sat_default_colors[11] = {
    0x0E, 0x0A, 0x06, 0x0F, 0x0B, 0xF5, 0x02, 0x03, 0xF9, 0xFF, 0xF0};

struct ExtractCtx {
    const Mem *mem;
    Mw2erSceneExtract *ex;
    Mw2erGeomPartition *part;
    int reuse_static;
    int auxiliary;
    int source;
    uint32_t node_addr;
    int camo;
    int emblem;
    int target_flat;
    uint32_t owner_addr;
    const uint8_t *matrix_override;
    std::vector<uint32_t> *emitted;
    std::unordered_map<uint32_t, int> *component_policy;
    std::unordered_map<uint32_t, uint8_t> *lod_skip;
    const uint32_t *visible_nodes;
    int visible_node_count;
    /* Cache only the guest lookup behind a component owner. Face flags and
     * modes remain per-face, matching the Python mesh-plan contract. */
    uint32_t satellite_owner;
    uint32_t satellite_owner_flags;
    int satellite_owner_action;
    int satellite_owner_valid;
    uint8_t imaging_effect_desc[MW2ER_MAX_DESC]; /* 0 unknown, 1 no, 2 yes */
};

struct FaceRec {
    uint16_t flags;
    uint16_t vcount;
    int mode;
    int32_t normal[3];
    int32_t indices[MAX_FACE_VERTICES];
    uint32_t owner;
    int skip;
};

struct TopologyCacheEntry {
    uint32_t block;
    uint16_t nvert;
    uint16_t nfaces;
    uint32_t face_off;
    uint32_t face_hash;
    int has_mode3;
    std::vector<FaceRec> recs;
};

struct WtboVertex {
    int32_t x;
    int32_t y;
    int32_t z;
    int16_t u;
    int16_t v;
};

struct WtboFace {
    uint16_t flags;
    uint16_t vcount;
    int32_t indices[MAX_FACE_VERTICES];
    int32_t normal[3];
};

struct WtboMesh {
    std::vector<WtboVertex> vertices;
    std::vector<WtboFace> faces;
};

struct SmoothMatrix {
    double rotation[9];
    double translation[3];
};

/* Player-model DFS scratch. Membership and smooth transforms are keyed by the
 * attached render-node (tree +0x6C), which is the scene-list node address. */
struct PlayerTreeNode {
    uint32_t parent;
    uint32_t child;
    uint32_t sibling;
    uint32_t geometry;
    int solved;
    int cockpit;
    double local_r[9];
    double local_t[3];
    double native_r[9];
    double native_t[3];
    double world_r[9];
    double world_t[3];
};

struct LodHistoryKey {
    uint32_t pointer;
    int32_t owner_id;
    uint8_t view_role;
    bool operator==(const LodHistoryKey &o) const
    {
        return pointer == o.pointer && owner_id == o.owner_id &&
            view_role == o.view_role;
    }
};

struct LodHistoryHash {
    size_t operator()(const LodHistoryKey &k) const
    {
        return (size_t)k.pointer ^ ((size_t)k.owner_id << 1) ^
            ((size_t)k.view_role << 3);
    }
};

static std::vector<TopologyCacheEntry> g_topo_cache;
static std::unordered_map<int32_t, WtboMesh> g_wtbo_meshes;
static std::unordered_map<LodHistoryKey, int, LodHistoryHash> g_lod_history;

struct EntityMaterials {
    int camo;
    int emblem;
};

static std::unordered_map<uint32_t, EntityMaterials> g_entity_materials;
static std::unordered_map<uint32_t, SmoothMatrix> g_smooth_matrices;
static std::unordered_set<uint32_t> g_cockpit_geometry;
static std::unordered_set<uint32_t> g_player_geometry;
static std::unordered_map<uint32_t, PlayerTreeNode> g_player_tree_nodes;
static uint32_t g_tree_pending[MODEL_TREE_PENDING_CAP];
static uint32_t g_player_model_root;
static uint32_t g_cockpit_model_root;
static double g_imaging_started_at = -1.0;
static int g_prev_imaging = 0;

static double now_seconds(void)
{
    using clock = std::chrono::steady_clock;
    return std::chrono::duration<double>(clock::now().time_since_epoch()).count();
}

static double g_cockpit_radius_fixed;
static double g_prev_cockpit_far_fixed;

struct CockpitEffectSlot {
    int used;
    uint32_t model_root;
    int32_t effect_id;
    int32_t raw_x;
    int32_t raw_y;
    int32_t raw_z;
    int has_transform;
    double head[3];
    double scale;
    double push[3];
    std::vector<uint32_t> nodes;
};

static CockpitEffectSlot g_effects[COLLISION_EFFECT_SLOT_COUNT];
static std::unordered_map<uint32_t, int> g_effect_node_slots;
static const int64_t (*g_precise_world_base)[3];
static double g_precise_world[MAX_VERTICES][3];
static uint32_t g_precise_world_count;

static int tree_push(uint32_t *stack, int *sp, uint32_t addr)
{
    if (addr == 0) {
        return 1;
    }
    if (stack == NULL || sp == NULL || *sp < 0 ||
        *sp >= MODEL_TREE_PENDING_CAP) {
        return 0;
    }
    stack[(*sp)++] = addr;
    return 1;
}

static int cockpit_camera(const Mw2erCamera &cam)
{
    return cam.camera_mode == 0 && !cam.satellite_view;
}

struct PreciseWorldClear {
    PreciseWorldClear()
    {
        g_precise_world_base = NULL;
        g_precise_world_count = 0;
    }
    ~PreciseWorldClear()
    {
        g_precise_world_base = NULL;
        g_precise_world_count = 0;
    }
};

Mw2erViewPolicy mw2er_view_policy(Mw2erRenderView view, int camera_mode)
{
    const uint32_t world =
        (1u << MW2ER_PART_STATIC) | (1u << MW2ER_PART_SCENE) |
        (1u << MW2ER_PART_ENTITY);
    const uint32_t player = 1u << MW2ER_PART_VIEW_EXCLUDED;
    const uint32_t primary = world |
        (camera_mode == 0 ? (1u << MW2ER_PART_COCKPIT) : player);
    switch (view) {
    case MW2ER_VIEW_ENHANCED:
        return {MW2ER_EXTRACT_IMAGING, primary, 0, 1};
    case MW2ER_VIEW_XRAY:
        return {MW2ER_EXTRACT_IMAGING, primary, 0, 0};
    case MW2ER_VIEW_SATELLITE:
        return {MW2ER_EXTRACT_SATELLITE, world | player, 0, 1};
    case MW2ER_VIEW_MFD_REAR:
    case MW2ER_VIEW_MFD_DOWN:
        return {MW2ER_EXTRACT_ORDINARY, world, 1, 1};
    case MW2ER_VIEW_MFD_WEAPON:
        return {MW2ER_EXTRACT_ORDINARY, world | player, 1, 1};
    default:
        return {MW2ER_EXTRACT_ORDINARY, primary, 0, 1};
    }
}

Mw2erRenderView mw2er_primary_render_view(const Mw2erMemoryView &view)
{
    const Mem mem = Mem::from(view);
    if (!mem.ok()) {
        return MW2ER_VIEW_NONE;
    }
    int satellite =
        mem.u8_rel(ADDR_RADAR_MODE) == 4 || mw2er_config().force_satellite;
    if (mem.i32_rel(ADDR_CAMERA_MODE) == 1 &&
        mem.i32_rel(ADDR_SPECIAL_CAMERA_LATCH) != 0) {
        satellite = 0;
    }
    if (satellite) {
        return MW2ER_VIEW_SATELLITE;
    }
    const uint32_t imaging = mem.u32_rel(ADDR_IMAGING_ACTIVE);
    if (imaging == 2) {
        return MW2ER_VIEW_XRAY;
    }
    if (imaging != 0) {
        return MW2ER_VIEW_ENHANCED;
    }
    return MW2ER_VIEW_NORMAL;
}

static double emitted_fixed(const int64_t vertex[3], int axis)
{
    if (g_precise_world_base != NULL) {
        const uintptr_t address = (uintptr_t)vertex;
        const uintptr_t base = (uintptr_t)g_precise_world_base;
        const size_t stride = sizeof(g_precise_world_base[0]);
        if (address >= base && (address - base) % stride == 0) {
            const size_t index = (address - base) / stride;
            if (index < g_precise_world_count) {
                return g_precise_world[index][axis];
            }
        }
    }
    return (double)vertex[axis];
}

static void emit_closed_polyline(
    Mw2erGeomPartition &g,
    const int64_t world[][3],
    const int32_t *indices,
    int count,
    float palette);
static void emit_palette_vertex(VertStream &s, const int64_t world[3], float palette);

static uint64_t static_policy_key(const Mw2erSceneExtract &ex)
{
    uint64_t k = 0;
    k |= (uint64_t)(ex.camera.imaging_wireframe ? 1 : 0);
    k |= (uint64_t)(ex.camera.satellite_view ? 2 : 0);
    k |= (uint64_t)(ex.camera.preserve_imaging_effects ? 4 : 0);
    k |= (uint64_t)(mw2er_config().reduce_terrain_gaps ? 8 : 0);
    return k;
}

static void mark_desc(Mw2erGeomPartition &p, int desc)
{
    if (desc < 0 || desc >= MW2ER_MAX_DESC) {
        return;
    }
    if (p.desc_needed[desc]) {
        return;
    }
    p.desc_needed[desc] = 1;
    p.used_desc[p.used_desc_n++] = (uint16_t)desc;
}

void mw2er_partition_reset(Mw2erGeomPartition &p)
{
    p.tris.clear();
    p.flats.clear();
    p.indexed_flat_verts.clear();
    p.indexed_flat_indices.clear();
    p.indexed_flat_palette.clear();
    p.texmap_verts.clear();
    p.wire_verts.clear();
    p.wire_occ_indices.clear();
    p.wire_line_indices.clear();
    p.wire_line_palette.clear();
    p.points.clear();
    p.lines.clear();
    p.tri_count = 0;
    p.flat_count = 0;
    p.point_count = 0;
    p.line_count = 0;
    for (int i = 0; i < p.used_desc_n; ++i) {
        int d = p.used_desc[i];
        p.desc_needed[d] = 0;
        Mw2erDescStreams *streams = p.find_desc_streams(d);
        if (streams != NULL) {
            streams->billboards.clear();
            streams->texmap_indices.clear();
            streams->texmap_lighting.clear();
        }
    }
    p.used_desc_n = 0;
    p.rotor_draws.clear();
    p.aero_fans.clear();
}

void mw2er_extract_mission_reset(void)
{
    g_topo_cache.clear();
    g_wtbo_meshes.clear();
    g_lod_history.clear();
    g_entity_materials.clear();
    g_smooth_matrices.clear();
    g_cockpit_geometry.clear();
    g_player_geometry.clear();
    g_player_tree_nodes.clear();
    g_player_model_root = 0;
    g_cockpit_model_root = 0;
    g_imaging_started_at = -1.0;
    g_prev_imaging = 0;
    g_cockpit_radius_fixed = 0.0;
    g_prev_cockpit_far_fixed = 0.0;
    g_effect_node_slots.clear();
    for (int i = 0; i < COLLISION_EFFECT_SLOT_COUNT; ++i) {
        g_effects[i].used = 0;
        g_effects[i].has_transform = 0;
        g_effects[i].nodes.clear();
    }
}

static const uint8_t *wtbo_blob(int resource_id, uint32_t *size)
{
    const Mw2erResourceAsset *asset = mw2er_resource_find(
        MW2ER_RESOURCE_POLY, (uint32_t)resource_id);
    if (asset != NULL) {
        if (size) {
            *size = (uint32_t)asset->bytes.size();
        }
        return asset->bytes.data();
    }
    return NULL;
}

static void init_sqrt_table(void)
{
    if (g_sqrt_ready) {
        return;
    }
    for (int i = 0; i < 1024; ++i) {
        int v = (int)(1024.0 * sqrt((double)(i < 1 ? 1 : i)));
        g_sqrt_table[i] = v < 1 ? 1 : v;
    }
    g_sqrt_ready = 1;
}

static int32_t load_i32(const uint8_t *p)
{
    int32_t v;
    memcpy(&v, p, 4);
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

void mw2er_extract_reset(Mw2erSceneExtract &ex)
{
    for (int i = 0; i < MW2ER_PART_COUNT; ++i) {
        mw2er_partition_reset(ex.part[i]);
    }
    ex.static_policy = UINT64_MAX;
    ex.static_block_ids.clear();
    ex.static_reused = 0;
    ex.node_count = 0;
    ex.tree_node_count = 0;
    ex.lod_node_count = 0;
}

void mw2er_extract_free(Mw2erSceneExtract &ex)
{
    ex = Mw2erSceneExtract();
}

static int32_t trunc_div_i64(int64_t num, int64_t den)
{
    if (den == 0) {
        return 0;
    }
    return (int32_t)(num / den);
}

static int bit_length_u64(uint64_t value)
{
    int n = 0;
    while (value != 0) {
        n += 1;
        value >>= 1;
    }
    return n;
}

static int light_table(
    int64_t lx,
    int64_t ly,
    int64_t lz,
    int64_t dot_shifted,
    int64_t *dot_norm,
    int64_t *table_val)
{
    uint64_t ax = (uint64_t)(lx < 0 ? -lx : lx);
    uint64_t ay = (uint64_t)(ly < 0 ? -ly : ly);
    uint64_t az = (uint64_t)(lz < 0 ? -lz : lz);
    uint64_t light_or = ax | ay | az;
    int norm_shift;
    int64_t lx8;
    int64_t ly8;
    int64_t lz8;
    int64_t mag_sq;
    int64_t table_idx;
    int64_t entry_idx;

    if (light_or == 0) {
        *dot_norm = 1;
        *table_val = 1;
        return 1;
    }
    norm_shift = bit_length_u64(light_or) - 1 - 7;
    if (norm_shift < 0) {
        norm_shift = 0;
    }
    lx8 = (int64_t)((ax >> norm_shift) & 0xFFu);
    ly8 = (int64_t)((ay >> norm_shift) & 0xFFu);
    lz8 = (int64_t)((az >> norm_shift) & 0xFFu);
    mag_sq = lx8 * lx8 + ly8 * ly8 + lz8 * lz8;
    table_idx = (mag_sq >> 7) & 0xFFFE;
    entry_idx = table_idx / 2;
    if (entry_idx < 1) {
        entry_idx = 1;
    }
    if (entry_idx > 1023) {
        entry_idx = 1023;
    }
    *dot_norm = dot_shifted >> norm_shift;
    *table_val = g_sqrt_table[entry_idx];
    return 1;
}

static void face_light_vector(
    const Mw2erLighting &lighting,
    const int64_t world[][3],
    int first_index,
    int64_t *lx,
    int64_t *ly,
    int64_t *lz)
{
    if (lighting.directional) {
        *lx = lighting.light[0];
        *ly = lighting.light[1];
        *lz = lighting.light[2];
    } else {
        *lx = (int64_t)lighting.light[0] - world[first_index][0];
        *ly = (int64_t)lighting.light[1] - world[first_index][1];
        *lz = (int64_t)lighting.light[2] - world[first_index][2];
    }
}

static void read_lighting_state(const Mem &mem, Mw2erLighting &lighting)
{
    lighting.light[0] = mem.i32_rel(ADDR_SCENE_LIGHT_X);
    lighting.light[1] = mem.i32_rel(ADDR_SCENE_LIGHT_Y);
    lighting.light[2] = mem.i32_rel(ADDR_SCENE_LIGHT_Z);
    lighting.directional =
        mem.i32_rel(ADDR_SCENE_LIGHT_IS_DIRECTIONAL) != 0;
    lighting.ambient = mem.i32_rel(ADDR_AMBIENT);
    lighting.fog_distance = mem.i32_rel(ADDR_FOG_DISTANCE);
    lighting.component_lighting_mode =
        mem.i32_rel(ADDR_COMPONENT_LIGHTING_MODE);
    lighting.fog_distance_world =
        (float)lighting.fog_distance / k_fixed_scale;
}

static float mode57_lit_shade_before_fog(
    int first_index,
    const int32_t *normal,
    const int64_t world[][3],
    const Mw2erLighting &lighting)
{
    int64_t lx;
    int64_t ly;
    int64_t lz;
    int64_t dot_shifted;
    int64_t dot_norm;
    int64_t table_val;
    double normalized;
    double ambient_adjusted;
    double lit;

    face_light_vector(lighting, world, first_index, &lx, &ly, &lz);
    if ((lx | ly | lz) == 0) {
        normalized = 1.0;
    } else {
        dot_shifted =
            (lx * (int64_t)normal[0] + ly * (int64_t)normal[1] +
             lz * (int64_t)normal[2]) >>
            16;
        light_table(lx, ly, lz, dot_shifted, &dot_norm, &table_val);
        normalized = (double)dot_norm / (double)table_val;
    }
    ambient_adjusted =
        normalized * (double)(128 - lighting.ambient) / 128.0 +
        (double)lighting.ambient;
    lit = 127.0 * ambient_adjusted / 1088.0;
    if (lit < 0.0) {
        lit = 0.0;
    }
    return (float)lit;
}

static float mode4_lit_shade_before_fog(
    int32_t face_flags,
    int first_index,
    const int32_t *normal,
    const int64_t world[][3],
    const Mw2erLighting &lighting)
{
    int64_t lx;
    int64_t ly;
    int64_t lz;
    int64_t dot_shifted;
    int64_t dot_norm;
    int64_t table_val;
    double normalized;
    double ambient_adjusted;
    double lit;

    face_light_vector(lighting, world, first_index, &lx, &ly, &lz);
    dot_shifted =
        (lx * (int64_t)normal[0] + ly * (int64_t)normal[1] +
         lz * (int64_t)normal[2]) >>
        16;
    light_table(lx, ly, lz, dot_shifted, &dot_norm, &table_val);
    normalized = (double)dot_norm / (double)table_val;
    ambient_adjusted =
        normalized * (double)(128 - lighting.ambient) / 128.0 +
        (double)lighting.ambient;
    lit = (double)((face_flags & 0xFF) >> 1) * ambient_adjusted / 1088.0;
    if (lit < 0.0) {
        lit = 0.0;
    }
    return (float)lit;
}

static int read_camera(const Mem &mem, Mw2erCamera &cam)
{
    int32_t raw[3];
    int32_t rot[9];
    int32_t focal_fixed;
    int32_t near_depth;

    memset(&cam, 0, sizeof(cam));
    cam.near_plane = 0.001f;
    cam.far_plane = 1000.0f;
    cam.camera_mode = mem.i32_rel(ADDR_CAMERA_MODE);
    raw[0] = mem.i32_rel(ADDR_CAMERA_POSITION + 0);
    raw[1] = mem.i32_rel(ADDR_CAMERA_POSITION + 4);
    raw[2] = mem.i32_rel(ADDR_CAMERA_POSITION + 8);
    cam.position_fixed[0] = raw[1];
    cam.position_fixed[1] = raw[0];
    cam.position_fixed[2] = raw[2];
    cam.position[0] = (float)cam.position_fixed[0] / k_fixed_scale;
    cam.position[1] = (float)cam.position_fixed[1] / k_fixed_scale;
    cam.position[2] = (float)cam.position_fixed[2] / k_fixed_scale;
    for (int i = 0; i < 9; ++i) {
        rot[i] = mem.i32_rel(ADDR_CAMERA_ROTATION + (uint32_t)i * 4u);
    }
    cam.right[0] = (float)rot[0] / k_rot_scale;
    cam.right[1] = (float)rot[1] / k_rot_scale;
    cam.right[2] = (float)rot[2] / k_rot_scale;
    cam.up[0] = (float)rot[3] / k_rot_scale;
    cam.up[1] = (float)rot[4] / k_rot_scale;
    cam.up[2] = (float)rot[5] / k_rot_scale;
    cam.forward[0] = (float)rot[6] / k_rot_scale;
    cam.forward[1] = (float)rot[7] / k_rot_scale;
    cam.forward[2] = (float)rot[8] / k_rot_scale;
    cam.forward_fixed[0] = rot[6];
    cam.forward_fixed[1] = rot[7];
    cam.forward_fixed[2] = rot[8];
    focal_fixed = mem.i32_rel(ADDR_CAMERA_FOCAL);
    if (focal_fixed <= 0) {
        focal_fixed = 65536;
    }
    if (focal_fixed < 0x8000) {
        focal_fixed = 0x8000;
    }
    if (focal_fixed > 0x100000) {
        focal_fixed = 0x100000;
    }
    cam.focal_length_pixels = k_focal_base * ((float)focal_fixed / k_fixed_scale);
    near_depth = mem.i32_rel(ADDR_CAMERA_NEAR_DEPTH);
    cam.far_depth_fixed = mem.i32_rel(ADDR_CAMERA_FAR_DEPTH);
    if (cam.camera_mode == 0 && near_depth > 0) {
        cam.clip_near_plane =
            (float)near_depth / (k_fixed_scale * k_native_depth_mul);
    }
    {
        const Mw2erViewport *vp = mw2er_frame_vp();
        cam.viewport_w = vp ? vp->mod_w : 1024;
        cam.viewport_h = vp ? vp->mod_h : 768;
    }
    return 1;
}

static void emit_billboard(
    Mw2erGeomPartition &g,
    const Mem &mem,
    int desc,
    const int64_t a[3],
    const int64_t b[3],
    float flags)
{
    if (desc < 0 || desc >= MW2ER_MAX_DESC) {
        return;
    }
    if (!mw2er_desc_draw_allowed(mem, desc)) {
        return;
    }
    float ax = (float)(emitted_fixed(a, 0) / k_fixed_scale);
    float ay = (float)(emitted_fixed(a, 1) / k_fixed_scale);
    float az = (float)(emitted_fixed(a, 2) / k_fixed_scale);
    float bx = (float)(emitted_fixed(b, 0) / k_fixed_scale);
    float by = (float)(emitted_fixed(b, 1) / k_fixed_scale);
    float bz = (float)(emitted_fixed(b, 2) / k_fixed_scale);
    mark_desc(g, desc);
    Mw2erDescStreams *streams = g.ensure_desc_streams(desc);
    if (streams == nullptr) {
        return;
    }
    VertStream &s = streams->billboards;
    for (int corner = 0; corner < 6; ++corner) {
        s.emit({ax, ay, az, bx, by, bz, flags});
    }
}

static void emit_world_xyz(
    VertStream &s, const int64_t world[][3], int nvert)
{
    for (int i = 0; i < nvert; ++i) {
        s.emit({
            (float)(emitted_fixed(world[i], 0) / k_fixed_scale),
            (float)(emitted_fixed(world[i], 1) / k_fixed_scale),
            (float)(emitted_fixed(world[i], 2) / k_fixed_scale)});
    }
}

static void emit_world_xyz_uv(
    VertStream &s,
    const int64_t world[][3],
    const int32_t uv[][2],
    int nvert)
{
    for (int i = 0; i < nvert; ++i) {
        s.emit({
            (float)(emitted_fixed(world[i], 0) / k_fixed_scale),
            (float)(emitted_fixed(world[i], 1) / k_fixed_scale),
            (float)(emitted_fixed(world[i], 2) / k_fixed_scale),
            (float)uv[i][0],
            (float)uv[i][1]});
    }
}

static void ensure_xyz_once(
    int *have,
    uint32_t *base,
    VertStream &dst,
    const int64_t world[][3],
    int nvert)
{
    if (*have) {
        return;
    }
    *base = (uint32_t)(dst.floats() / 3);
    emit_world_xyz(dst, world, nvert);
    *have = 1;
}

static void ensure_xyz_uv_once(
    int *have,
    uint32_t *base,
    VertStream &dst,
    const int64_t world[][3],
    const int32_t uv[][2],
    int nvert)
{
    if (*have) {
        return;
    }
    *base = (uint32_t)(dst.floats() / 5);
    emit_world_xyz_uv(dst, world, uv, nvert);
    *have = 1;
}

static uint32_t aero_fan_map_vert(
    Mw2erAeroFanMesh &mesh,
    int vi,
    const int64_t world[][3],
    const int32_t uv[][2],
    int *remap,
    uint8_t *usedv,
    double *cx,
    double *cy,
    double *cz,
    int *nused)
{
    if (remap[vi] < 0) {
        remap[vi] = (int)(mesh.verts.floats() / 5);
        float row[5] = {
            (float)(emitted_fixed(world[vi], 0) / k_fixed_scale),
            (float)(emitted_fixed(world[vi], 1) / k_fixed_scale),
            (float)(emitted_fixed(world[vi], 2) / k_fixed_scale),
            (float)uv[vi][0],
            (float)uv[vi][1]};
        mesh.verts.emit(row, 5);
        if (!usedv[vi]) {
            usedv[vi] = 1;
            *cx += (double)world[vi][0];
            *cy += (double)world[vi][1];
            *cz += (double)world[vi][2];
            *nused += 1;
        }
    }
    return (uint32_t)remap[vi];
}

static void rotate_basis3(float *b, const double *m)
{
    double v0 = b[0];
    double v1 = b[1];
    double v2 = b[2];
    b[0] = (float)(v0 * m[0] + v1 * m[1] + v2 * m[2]);
    b[1] = (float)(v0 * m[3] + v1 * m[4] + v2 * m[5]);
    b[2] = (float)(v0 * m[6] + v1 * m[7] + v2 * m[8]);
}

static void emit_indexed_fan(
    IndexStream &indices,
    VertStream *prim,
    uint32_t vertex_base,
    const int32_t *face_indices,
    int vcount,
    float prim_value)
{
    if (vcount < 3) {
        return;
    }
    uint32_t a = vertex_base + (uint32_t)face_indices[0];
    for (int i = 1; i + 1 < vcount; ++i) {
        indices.tri(
            a,
            vertex_base + (uint32_t)face_indices[i],
            vertex_base + (uint32_t)face_indices[i + 1]);
        if (prim != NULL) {
            prim->emit({prim_value});
        }
    }
}

static void emit_indexed_loop(
    IndexStream &indices,
    VertStream &palette,
    uint32_t vertex_base,
    const int32_t *face_indices,
    int vcount,
    float pal)
{
    if (vcount < 2) {
        return;
    }
    for (int i = 0; i < vcount; ++i) {
        int a = face_indices[i];
        int b = face_indices[(i + 1) % vcount];
        indices.line(
            vertex_base + (uint32_t)a, vertex_base + (uint32_t)b);
        palette.emit({pal});
    }
}

static void emit_mode4_vertex(
    Mw2erGeomPartition &g,
    const int64_t world[3],
    float c_in,
    float lighting_state)
{
    g.tris.emit({
        (float)(emitted_fixed(world, 0) / k_fixed_scale),
        (float)(emitted_fixed(world, 1) / k_fixed_scale),
        (float)(emitted_fixed(world, 2) / k_fixed_scale),
        c_in,
        lighting_state});
    g.tri_count += 1;
}

static float pack_lighting_state(float lit_shade, int policy)
{
    return (float)policy * k_lighting_policy_pack + lit_shade;
}

static int component_policy_for(
    ExtractCtx &ctx,
    uint32_t owner_addr)
{
    if (owner_addr == 0) {
        return 0;
    }
    if (ctx.component_policy != NULL) {
        auto it = ctx.component_policy->find(owner_addr);
        if (it != ctx.component_policy->end()) {
            return it->second;
        }
    }
    const Mem &mem = *ctx.mem;
    uint16_t component_state = mem.u16(owner_addr);
    uint16_t owner_class_flags = mem.u16(owner_addr + 2);
    int component_damage = (component_state & 0x00F0) >> 4;
    int eligible =
        (owner_class_flags & 0x0100) != 0 ||
        (owner_class_flags & 0x00F0) == 0x0050;
    int policy = eligible ? component_damage : 0;
    if (policy && ctx.ex->lighting.component_lighting_mode != 0) {
        policy += 15;
    }
    if (ctx.component_policy != NULL) {
        (*ctx.component_policy)[owner_addr] = policy;
    }
    return policy;
}

static void apply_target_flat_material(
    const ExtractCtx &ctx, uint32_t owner, uint16_t *flags, int *mode)
{
    if (!ctx.target_flat || flags == NULL || mode == NULL ||
        (*mode != MODE_TEXTURED_P && *mode != MODE_TEXTURED_PRE &&
         *mode != MODE_TEXTURED_AFF)) {
        return;
    }
    const uint16_t owner_class = owner ? ctx.mem->u16(owner + 2) : 0;
    int lighting_scale = -1;
    /* The target portrait's local 0x0B00 material policy converts only
     * matching textured faces. The source S field remains the palette family;
     * 0xA0/0xD0 supplies only the lighting/fog scale used by mode 1. */
    if ((owner_class & 0x0100) || (owner_class & 0x00F0) == 0x0050) {
        lighting_scale = 0xA0;
    } else if (owner_class & 0x0A00) {
        lighting_scale = 0xD0;
    }
    if (lighting_scale >= 0) {
        *flags = (uint16_t)((*flags & 0x0F00) | lighting_scale);
        *mode = MODE_FLAT_LIT;
    }
}

static float wireframe_palette_index(const Mem &mem, uint32_t owner_addr)
{
    if (owner_addr == 0) {
        return (float)WIREFRAME_DEFAULT_PALETTE;
    }
    uint16_t owner_type = mem.u16(owner_addr);
    uint16_t owner_flags = mem.u16(owner_addr + 2);
    int ah = (owner_flags >> 8) & 0xFF;
    if (ah & 0x02) {
        return 7.0f;
    }
    if (ah & 0x04) {
        return 11.0f;
    }
    if (ah & 0x01) {
        int type_nibble = (owner_type & 0x00F0) >> 4;
        if (type_nibble < 1) {
            return 7.0f;
        }
        return type_nibble >= 12 ? 11.0f : 3.0f;
    }
    return (float)WIREFRAME_DEFAULT_PALETTE;
}

static uint32_t hash_bytes(const uint8_t *p, uint32_t n)
{
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < n; ++i) {
        h ^= p[i];
        h *= 16777619u;
    }
    return h;
}

static void emit_imaging_face(
    ExtractCtx &ctx,
    const FaceRec &r,
    uint32_t wire_base)
{
    Mw2erGeomPartition &g = *ctx.part;
    float pal = wireframe_palette_index(*ctx.mem, r.owner);
    emit_indexed_loop(
        g.wire_line_indices,
        g.wire_line_palette,
        wire_base,
        r.indices,
        r.vcount,
        pal);
    emit_indexed_fan(
        g.wire_occ_indices, NULL, wire_base, r.indices, r.vcount, 0.0f);
}

static int imaging_keep_textured(ExtractCtx &ctx, int desc)
{
    if (!ctx.ex->camera.preserve_imaging_effects) {
        return 0;
    }
    if (desc < 0 || desc >= MW2ER_MAX_DESC) {
        return 0;
    }
    uint8_t &cached = ctx.imaging_effect_desc[desc];
    if (cached == 0) {
        cached = (uint8_t)(mw2er_desc_is_imaging_effect(*ctx.mem, desc) + 1);
    }
    return cached == 2;
}

static void emit_palette_vertex(VertStream &s, const int64_t world[3], float palette)
{
    s.emit({
        (float)(emitted_fixed(world, 0) / k_fixed_scale),
        (float)(emitted_fixed(world, 1) / k_fixed_scale),
        (float)(emitted_fixed(world, 2) / k_fixed_scale),
        palette});
}

static float mode0_palette(uint16_t face_flags)
{
    return (float)((face_flags >> 4) & 0xFF);
}

static float mode2_palette(uint16_t face_flags)
{
    return (float)((face_flags & 0xFF0) >> 4);
}

static int32_t mode1_shade_offset(
    uint16_t face_flags,
    int count,
    const int32_t *indices,
    const int32_t *normal,
    const int64_t world[][3],
    const Mw2erLighting &lighting,
    const Mw2erCamera &camera)
{
    int64_t lx;
    int64_t ly;
    int64_t lz;
    int64_t dot_shifted;
    int64_t dot_norm;
    int64_t table_val;
    int64_t brightness;
    int64_t remapped;
    int64_t half_base;
    int64_t contribution;
    int first = indices[0];

    face_light_vector(lighting, world, first, &lx, &ly, &lz);
    dot_shifted =
        (lx * (int64_t)normal[0] + ly * (int64_t)normal[1] +
         lz * (int64_t)normal[2]) >>
        16;
    light_table(lx, ly, lz, dot_shifted, &dot_norm, &table_val);
    if (table_val == 0) {
        return 1;
    }
    brightness = trunc_div_i64(dot_norm, table_val);
    remapped =
        ((brightness * (int64_t)(128 - lighting.ambient)) >> 7) + lighting.ambient;
    half_base = (int64_t)(face_flags & 0xFF) >> 1;
    contribution = trunc_div_i64(half_base * remapped, 1088);
    /* Mode 1 uses this camera's maximum signed forward depth, not Euclidean
     * distance. */
    if (lighting.fog_distance != 0) {
        int64_t face_depth_x4 = 0;
        for (int i = 0; i < count; ++i) {
            const int vi = indices[i];
            const int64_t dx =
                world[vi][0] - (int64_t)camera.position_fixed[0];
            const int64_t dy =
                world[vi][1] - (int64_t)camera.position_fixed[1];
            const int64_t dz =
                world[vi][2] - (int64_t)camera.position_fixed[2];
            const int64_t depth_dot =
                dx * (int64_t)camera.forward_fixed[0] +
                dy * (int64_t)camera.forward_fixed[1] +
                dz * (int64_t)camera.forward_fixed[2];
            const int64_t vertex_depth_x4 =
                (depth_dot + (INT64_C(1) << 26)) >> 27;
            if (vertex_depth_x4 > face_depth_x4) {
                face_depth_x4 = vertex_depth_x4;
            }
        }
        const int64_t fog_loss =
            (face_depth_x4 << 4) / (int64_t)lighting.fog_distance >> 4;
        contribution -= fog_loss;
    }
    if (contribution < 0) {
        contribution = 0;
    }
    if (contribution > 15) {
        contribution = 15;
    }
    return (int32_t)contribution;
}

static int32_t apply_component_damage_shading(
    int32_t scene_shade, int component_policy)
{
    if (component_policy <= 0) {
        return scene_shade;
    }
    if (component_policy > 15) {
        const int64_t damage = component_policy - 15;
        const int64_t remaining_q16 =
            ((INT64_C(15) - scene_shade) << 16) / 15;
        return (int32_t)(scene_shade +
            ((damage * remaining_q16 + INT64_C(0x8000)) >> 16));
    }
    const int64_t damage_scale_q16 =
        (INT64_C(16) - component_policy) << 12;
    return (int32_t)(((scene_shade * damage_scale_q16 + INT64_C(0x8000)) >> 16)
        & 0x0F);
}

static float mode1_palette(
    uint16_t face_flags,
    int count,
    const int32_t *indices,
    const int32_t *normal,
    const int64_t world[][3],
    const Mw2erLighting &lighting,
    const Mw2erCamera &camera,
    int component_policy)
{
    int32_t offset = mode1_shade_offset(
        face_flags, count, indices, normal, world, lighting, camera);
    offset = apply_component_damage_shading(offset, component_policy);
    return (float)((((face_flags >> 8) & 0xF) << 4) | (offset & 0xF));
}

static void emit_closed_polyline(
    Mw2erGeomPartition &g,
    const int64_t world[][3],
    const int32_t *indices,
    int count,
    float palette)
{
    if (count < 2) {
        return;
    }
    for (int i = 0; i < count; ++i) {
        int a = indices[i];
        int b = indices[(i + 1) % count];
        emit_palette_vertex(g.lines, world[a], palette);
        emit_palette_vertex(g.lines, world[b], palette);
        g.line_count += 1;
    }
}

static int vec3_normalize(double *v)
{
    double len = sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    if (!(len > 1e-6)) {
        return 0;
    }
    v[0] /= len;
    v[1] /= len;
    v[2] /= len;
    return 1;
}

static void vec3_cross(const double *a, const double *b, double *out)
{
    out[0] = a[1] * b[2] - a[2] * b[1];
    out[1] = a[2] * b[0] - a[0] * b[2];
    out[2] = a[0] * b[1] - a[1] * b[0];
}

static int emit_heli_rotor_draw(
    Mw2erGeomPartition &g,
    int desc,
    const int64_t world[][3],
    const int32_t *indices,
    int count,
    const int32_t *normal,
    float lighting,
    int outline_only,
    float outline_palette)
{
    if (count < 3) {
        return 0;
    }
    double src[8][3];
    double center[3] = {0, 0, 0};
    for (int i = 0; i < count; ++i) {
        src[i][0] = (double)world[indices[i]][0];
        src[i][1] = (double)world[indices[i]][1];
        src[i][2] = (double)world[indices[i]][2];
        center[0] += src[i][0];
        center[1] += src[i][1];
        center[2] += src[i][2];
    }
    center[0] /= (double)count;
    center[1] /= (double)count;
    center[2] /= (double)count;
    double radial0[3] = {
        src[0][0] - center[0], src[0][1] - center[1], src[0][2] - center[2]};
    double radius = 0.0;
    for (int i = 0; i < count; ++i) {
        double dx = src[i][0] - center[0];
        double dy = src[i][1] - center[1];
        double dz = src[i][2] - center[2];
        radius += sqrt(dx * dx + dy * dy + dz * dz);
    }
    radius /= (double)count;
    if (!(radius > 1e-6)) {
        return 0;
    }
    double e1[3] = {radial0[0], radial0[1], radial0[2]};
    if (!vec3_normalize(e1)) {
        return 0;
    }
    double n[3] = {(double)normal[0], (double)normal[1], (double)normal[2]};
    if (!vec3_normalize(n)) {
        double r1[3] = {
            src[1][0] - center[0], src[1][1] - center[1], src[1][2] - center[2]};
        vec3_cross(radial0, r1, n);
        if (!vec3_normalize(n)) {
            return 0;
        }
    }
    double e2[3];
    vec3_cross(n, e1, e2);
    if (!vec3_normalize(e2)) {
        return 0;
    }
    double radial1[3] = {
        src[1][0] - center[0], src[1][1] - center[1], src[1][2] - center[2]};
    if (radial1[0] * e2[0] + radial1[1] * e2[1] + radial1[2] * e2[2] < 0.0) {
        e2[0] = -e2[0];
        e2[1] = -e2[1];
        e2[2] = -e2[2];
    }

    Mw2erRotorDraw draw{};
    draw.desc = desc;
    draw.effect = MW2ER_ROTOR_HELI;
    draw.normalized_uv = 1;
    draw.center[0] = (float)(center[0] / k_fixed_scale);
    draw.center[1] = (float)(center[1] / k_fixed_scale);
    draw.center[2] = (float)(center[2] / k_fixed_scale);
    for (int i = 0; i < 3; ++i) {
        draw.axis_u[i] = (float)(radius * e1[i] / k_fixed_scale);
        draw.axis_v[i] = (float)(radius * e2[i] / k_fixed_scale);
    }
    draw.lighting = lighting;
    draw.outline_only = outline_only;
    draw.outline_palette = outline_palette;
    if (!outline_only) {
        mark_desc(g, desc);
    }
    g.rotor_draws.push_back(draw);
    return 1;
}

static int already_emitted(ExtractCtx &ctx, uint32_t block)
{
    if (block == 0 || ctx.emitted == NULL) {
        return 0;
    }
    for (size_t i = 0; i < ctx.emitted->size(); ++i) {
        if ((*ctx.emitted)[i] == block) {
            return 1;
        }
    }
    return 0;
}

static void mark_emitted(ExtractCtx &ctx, uint32_t block)
{
    if (ctx.emitted != NULL && block != 0) {
        ctx.emitted->push_back(block);
    }
}

static uint32_t satellite_force_mode4(uint32_t face_flags, uint32_t billboard_color)
{
    uint32_t mode_bits = face_flags & 0x7000;
    uint32_t base = (mode_bits == 0x3000)
        ? (billboard_color & 0xFF)
        : ((face_flags & 0x0F00) >> 4);
    return 0x4000 | (base & 0xFF);
}

static int satellite_iff_primary(const Mem &mem, uint32_t owner_addr)
{
    int index = 2;
    uint16_t entity_index = mem.u16(owner_addr + 0x14);
    int32_t entity_count = mem.i32_rel(ADDR_ENTITY_COUNT);
    int limit = entity_count < PRIMARY_ENTITY_LIMIT ? entity_count : PRIMARY_ENTITY_LIMIT;
    if (entity_index < (uint16_t)limit) {
        uint32_t body = mem.u32_rel(ADDR_ENTITY_BODY_TABLE + (uint32_t)entity_index * 4);
        if (body != 0) {
            uint32_t slot = mem.u32(body + 0x08);
            if (slot < PRIMARY_CLASSIFICATION_SLOTS) {
                index = (int)mem.u8_rel(
                    ADDR_PRIMARY_CLASSIFICATION + slot * PRIMARY_CLASSIFICATION_STRIDE);
            }
        }
    }
    if (index < 0) {
        index = 0;
    }
    if (index > 2) {
        index = 2;
    }
    return index;
}

static int satellite_iff_secondary(const Mem &mem, uint32_t owner_addr)
{
    int index = 2;
    uint16_t record_index = mem.u16(owner_addr + 0x14);
    if (record_index <= 0xFF) {
        int32_t reference = mem.i32_rel(
            ADDR_SECONDARY_IFF_REFERENCES + (uint32_t)record_index * 0x40);
        if (reference >= 0) {
            index = (int)mem.u8_rel(ADDR_SECONDARY_CLASSIFICATION + (uint32_t)reference);
        }
    }
    if (index < 0) {
        index = 0;
    }
    if (index > 2) {
        index = 2;
    }
    return index;
}

static uint32_t satellite_flags_from_owner(
    ExtractCtx &ctx,
    uint32_t source_flags,
    uint32_t owner_addr)
{
    const Mw2erCamera &cam = ctx.ex->camera;
    /* Owner/IFF state is component-granular but classification remains
     * face-granular because source flags still select preserve/forced modes. */
    if (!ctx.satellite_owner_valid || ctx.satellite_owner != owner_addr) {
        const uint16_t owner_class =
            owner_addr ? ctx.mem->u16(owner_addr + 0x02) : 0;
        const uint32_t primary = owner_class & 0x0F00;
        const uint32_t secondary = owner_class & 0x00F0;
        const uint32_t *colors = cam.satellite_colors;
        ctx.satellite_owner = owner_addr;
        ctx.satellite_owner_flags = colors[10];
        ctx.satellite_owner_action = SATELLITE_OWNER_FORCE_MODE4;
        ctx.satellite_owner_valid = 1;
        if (primary == 0x0100) {
            ctx.satellite_owner_flags =
                colors[satellite_iff_primary(*ctx.mem, owner_addr)];
            ctx.satellite_owner_action = SATELLITE_OWNER_FIXED;
        } else if (primary == 0x0200) {
            ctx.satellite_owner_flags =
                colors[3 + satellite_iff_secondary(*ctx.mem, owner_addr)];
            ctx.satellite_owner_action = SATELLITE_OWNER_FIXED;
        } else if (primary == 0x0400) {
            ctx.satellite_owner_flags = colors[6];
            ctx.satellite_owner_action = SATELLITE_OWNER_FIXED;
        } else if (secondary == 0x0040) {
            ctx.satellite_owner_flags = colors[7];
            ctx.satellite_owner_action = SATELLITE_OWNER_FIXED;
        } else if (secondary == 0x0050) {
            ctx.satellite_owner_flags = colors[8];
            ctx.satellite_owner_action = SATELLITE_OWNER_FIXED;
        } else if (secondary == 0x0080) {
            ctx.satellite_owner_flags = colors[9];
            ctx.satellite_owner_action = SATELLITE_OWNER_FIXED;
        } else if (secondary == 0x0010 || secondary == 0x0020 ||
                   secondary == 0x0060) {
            ctx.satellite_owner_action = SATELLITE_OWNER_SOURCE;
        }
    }
    if (ctx.satellite_owner_action == SATELLITE_OWNER_FIXED) {
        return ctx.satellite_owner_flags;
    }
    if (ctx.satellite_owner_action == SATELLITE_OWNER_SOURCE) {
        return source_flags;
    }
    return satellite_force_mode4(source_flags, ctx.satellite_owner_flags);
}

static void satellite_classify_face(
    ExtractCtx &ctx,
    uint32_t owner_addr,
    uint16_t *face_flags,
    int *mode)
{
    uint32_t classified =
        satellite_flags_from_owner(ctx, *face_flags, owner_addr);
    *face_flags = (uint16_t)classified;
    *mode = (int)((classified & 0x7000) >> 12);
    if (*mode == 0) {
        *mode = MODE_SATELLITE_WIREFRAME;
    } else if (*mode == MODE_ILLUMINATE) {
        *mode = MODE_SATELLITE_SOLID;
    } else if (*mode == MODE_FLAT_LIT) {
        *mode = MODE_FLAT_UNLIT;
        *face_flags = (uint16_t)((classified & 0xFF) << 4);
    }
}

static void emit_satellite_wire_face(
    Mw2erGeomPartition &part,
    const int64_t world[][3],
    int nvert,
    const int32_t *indices,
    int vcount,
    uint16_t face_flags,
    int *have_wire,
    uint32_t *wire_base)
{
    ensure_xyz_once(have_wire, wire_base, part.wire_verts, world, nvert);
    emit_indexed_loop(
        part.wire_line_indices,
        part.wire_line_palette,
        *wire_base,
        indices,
        vcount,
        (float)(face_flags & 0xFF));
    emit_indexed_fan(
        part.wire_occ_indices, NULL, *wire_base, indices, vcount, 0.0f);
}

static void emit_satellite_solid_face(
    Mw2erGeomPartition &part,
    const Mw2erCamera &camera,
    const int64_t world[][3],
    const int32_t *indices,
    int vcount,
    uint16_t face_flags)
{
    const int family = face_flags & 0xF0;
    float palette[MAX_FACE_VERTICES];
    for (int i = 0; i < vcount; ++i) {
        const int vi = indices[i];
        int shade = 15;
        if (camera.satellite_shade_divisor != 0) {
            const int64_t dx = world[vi][0] - camera.position_fixed[0];
            const int64_t dy = world[vi][1] - camera.position_fixed[1];
            const int64_t dz = world[vi][2] - camera.position_fixed[2];
            const int64_t depth_dot =
                dx * camera.forward_fixed[0] +
                dy * camera.forward_fixed[1] +
                dz * camera.forward_fixed[2];
            const int64_t half = (int64_t)1 << 26;
            const int64_t depth = depth_dot >= 0
                ? (depth_dot + half) >> 27
                : -(((-depth_dot) + half) >> 27);
            const int64_t num =
                (int64_t)camera.satellite_width_fixed -
                (depth >> 2) -
                (int64_t)camera.satellite_shade_bias;
            const int32_t ratio = trunc_div_i64(
                num << 16, camera.satellite_shade_divisor);
            int64_t offset = ((int64_t)ratio * 16 + 0x8000) >> 16;
            if (offset < 0) {
                offset = 0;
            }
            if (offset > 15) {
                offset = 15;
            }
            shade = (int)offset;
        }
        palette[i] = (float)(family | shade);
    }
    for (int i = 1; i + 1 < vcount; ++i) {
        const int corners[3] = {0, i, i + 1};
        for (int c = 0; c < 3; ++c) {
            const int corner = corners[c];
            emit_palette_vertex(
                part.flats, world[indices[corner]], palette[corner]);
            part.flat_count += 1;
        }
    }
}

static int satellite_node_omitted(const ExtractCtx &ctx, uint32_t flags)
{
    uint32_t node_class = (flags >> 16) & 0xFFFF;
    uint32_t primary = node_class & 0x0F00;
    uint32_t secondary = node_class & 0x00F0;
    if (primary == 0x0100) {
        uint16_t entity_index = ctx.mem->u16(ctx.node_addr + 0x14);
        uint32_t entity = ctx.mem->u32_rel(
            ADDR_ENTITY_BODY_TABLE + (uint32_t)entity_index * 4);
        if (entity == 0) {
            return 1;
        }
        uint16_t entity_flags = ctx.mem->u16(entity + 0x14);
        if (entity_flags & 0x0016) {
            return 1;
        }
    } else if (secondary == 0x0030 || secondary == 0x0070) {
        return 1;
    }
    return (flags & 0x1000) != 0;
}

static int extract_block(ExtractCtx &ctx, uint32_t flags, uint32_t entity_ref, uint32_t block_data)
{
    PreciseWorldClear precise_world_clear;
    const Mem &mem = *ctx.mem;
    Mw2erSceneExtract &ex = *ctx.ex;
    uint8_t header[HEADER_SIZE];
    uint16_t nvert;
    uint16_t nfaces;
    uint32_t face_off;
    uint32_t expected;
    uint32_t flag_hi;
    uint32_t state_flag;
    int is_terrain;
    int base_pid;
    int static_candidate;
    int has_mode3 = 0;
    uint8_t verts[VERTEX_STRIDE * MAX_VERTICES];
    uint8_t faces[FACE_STRIDE * MAX_FACES];
    int64_t world[MAX_VERTICES][3];
    uint8_t c_in[MAX_VERTICES];
    int32_t uv[MAX_VERTICES][2];
    uint8_t matrix[ENTITY_MATRIX_SIZE];
    int have_matrix = 0;
    uint32_t bb_keys[256];
    int bb_n = 0;

    if (block_data == 0) {
        return 0;
    }
    if (ctx.lod_skip != NULL && ctx.lod_skip->count(ctx.node_addr)) {
        return 0;
    }
    if (ctx.ex->camera.satellite_view && satellite_node_omitted(ctx, flags)) {
        return 0;
    }
    if (already_emitted(ctx, block_data)) {
        return 0;
    }
    if (!mem.read(block_data, header, HEADER_SIZE)) {
        return 0;
    }
    nvert = load_u16(header + 4);
    nfaces = load_u16(header + 6);
    face_off = load_u32(header + 8);
    expected = HEADER_SIZE + (uint32_t)nvert * VERTEX_STRIDE;
    if (nvert < 1 || nvert > MAX_VERTICES || nfaces < 1 || nfaces > MAX_FACES ||
        face_off < expected || face_off > MAX_FACE_DATA_OFFSET) {
        return 0;
    }
    mark_emitted(ctx, block_data);
    flag_hi = (flags >> 24) & 0xFFu;
    is_terrain = flag_hi == NODE_FLAG_TERRAIN_HI;
    const int is_cockpit = g_cockpit_geometry.count(ctx.node_addr) != 0;
    const int is_player = !is_cockpit &&
        g_player_geometry.count(ctx.node_addr) != 0;
    SmoothMatrix smooth_copy{};
    const SmoothMatrix *smooth = NULL;
    if (!ctx.auxiliary) {
        auto smooth_it = g_smooth_matrices.find(ctx.node_addr);
        if (smooth_it != g_smooth_matrices.end()) {
            smooth_copy = smooth_it->second;
            smooth = &smooth_copy;
        }
    }
    state_flag = load_u32(header);
    base_pid = MW2ER_PART_SCENE;
    if (ctx.source == SRC_RENDERER_LOD) {
        base_pid = MW2ER_PART_ENTITY;
    } else if (ctx.source == SRC_TARGET) {
        base_pid = MW2ER_PART_TARGET;
    } else if (is_cockpit) {
        base_pid = MW2ER_PART_COCKPIT;
    } else if (is_player) {
        base_pid = MW2ER_PART_VIEW_EXCLUDED;
    }
    static_candidate =
        is_terrain && state_flag != 0 && base_pid == MW2ER_PART_SCENE;
    if (static_candidate && ctx.reuse_static &&
        ex.static_block_ids.count(block_data)) {
        return 1;
    }
    /* Billboard topology makes a terrain block dynamic; choose static only
       after topology classification so it can never enter retained geometry. */
    ctx.part = &ctx.ex->part[base_pid];
    if (!mem.read(
            block_data + HEADER_SIZE,
            verts,
            (uint32_t)nvert * VERTEX_STRIDE)) {
        return 0;
    }
    if (ctx.matrix_override != NULL) {
        memcpy(matrix, ctx.matrix_override, ENTITY_MATRIX_SIZE);
        have_matrix = 1;
    } else if (!is_terrain && entity_ref != 0 && ctx.source != SRC_MODEL_TREE) {
        if (smooth == NULL) {
            have_matrix = mem.read(
                entity_ref + ENTITY_MATRIX_OFFSET,
                matrix,
                ENTITY_MATRIX_SIZE);
        }
    }
    g_precise_world_base = NULL;
    g_precise_world_count = 0;
    for (uint32_t vi = 0; vi < nvert; ++vi) {
        const uint8_t *v = verts + vi * VERTEX_STRIDE;
        c_in[vi] = v[0x18];
        uv[vi][0] = load_i32(v + 0x18);
        uv[vi][1] = load_i32(v + 0x1C);
        if (smooth != NULL && ctx.matrix_override == NULL) {
            double x = (double)load_i32(v + 0);
            double y = (double)load_i32(v + 4);
            double z = (double)load_i32(v + 8);
            g_precise_world[vi][0] =
                smooth->rotation[0] * x + smooth->rotation[1] * y +
                smooth->rotation[2] * z + smooth->translation[0];
            g_precise_world[vi][1] =
                smooth->rotation[3] * x + smooth->rotation[4] * y +
                smooth->rotation[5] * z + smooth->translation[1];
            g_precise_world[vi][2] =
                smooth->rotation[6] * x + smooth->rotation[7] * y +
                smooth->rotation[8] * z + smooth->translation[2];
            world[vi][0] = (int64_t)llround(g_precise_world[vi][0]);
            world[vi][1] = (int64_t)llround(g_precise_world[vi][1]);
            world[vi][2] = (int64_t)llround(g_precise_world[vi][2]);
        } else if (have_matrix) {
            int64_t x = load_i32(v + 0);
            int64_t y = load_i32(v + 4);
            int64_t z = load_i32(v + 8);
            int64_t r0 = load_i32(matrix + 0x00);
            int64_t r1 = load_i32(matrix + 0x04);
            int64_t r2 = load_i32(matrix + 0x08);
            int64_t r3 = load_i32(matrix + 0x0C);
            int64_t r4 = load_i32(matrix + 0x10);
            int64_t r5 = load_i32(matrix + 0x14);
            int64_t r6 = load_i32(matrix + 0x18);
            int64_t r7 = load_i32(matrix + 0x1C);
            int64_t r8 = load_i32(matrix + 0x20);
            int64_t tx = load_i32(matrix + ENTITY_TRANSLATION_OFFSET + 0);
            int64_t ty = load_i32(matrix + ENTITY_TRANSLATION_OFFSET + 4);
            int64_t tz = load_i32(matrix + ENTITY_TRANSLATION_OFFSET + 8);
            world[vi][0] = ((r0 * x + r1 * y + r2 * z) >> 29) + tx;
            world[vi][1] = ((r3 * x + r4 * y + r5 * z) >> 29) + ty;
            world[vi][2] = ((r6 * x + r7 * y + r8 * z) >> 29) + tz;
        } else {
            world[vi][0] = load_i32(v + 0x0C);
            world[vi][1] = load_i32(v + 0x10);
            world[vi][2] = load_i32(v + 0x14);
        }
    }
    if (smooth != NULL && ctx.matrix_override == NULL) {
        g_precise_world_base = world;
        g_precise_world_count = nvert;
    }
    if (smooth != NULL) {
        double hx = (double)ex.camera.position_fixed[0];
        double hy = (double)ex.camera.position_fixed[1];
        double hz = (double)ex.camera.position_fixed[2];
        double fx = ex.camera.forward[0];
        double fy = ex.camera.forward[1];
        double fz = ex.camera.forward[2];
        for (uint32_t vi = 0; vi < nvert; ++vi) {
            double dx = (double)world[vi][0] - hx;
            double dy = (double)world[vi][1] - hy;
            double dz = (double)world[vi][2] - hz;
            double depth = dx * fx + dy * fy + dz * fz;
            if (depth > g_cockpit_radius_fixed) {
                g_cockpit_radius_fixed = depth;
            }
        }
    }
    if (!ctx.auxiliary) {
        auto eit = g_effect_node_slots.find(ctx.node_addr);
        if (eit != g_effect_node_slots.end()) {
            CockpitEffectSlot &slot = g_effects[eit->second];
            if (!slot.has_transform && ex.camera.camera_mode == 0 &&
                g_prev_cockpit_far_fixed > 0.0) {
                double hx = (double)ex.camera.position_fixed[0];
                double hy = (double)ex.camera.position_fixed[1];
                double hz = (double)ex.camera.position_fixed[2];
                double fx = ex.camera.forward[0];
                double fy = ex.camera.forward[1];
                double fz = ex.camera.forward[2];
                double ax = (double)slot.raw_y - hx;
                double ay = (double)slot.raw_x - hy;
                double az = (double)slot.raw_z - hz;
                double anchor = ax * fx + ay * fy + az * fz;
                double target = g_prev_cockpit_far_fixed * k_cockpit_effect_margin;
                slot.scale = 1.0;
                slot.push[0] = slot.push[1] = slot.push[2] = 0.0;
                if (anchor < target) {
                    if (anchor > 1.0e-6) {
                        slot.scale = target / anchor;
                    } else {
                        slot.push[0] = fx * (target - anchor);
                        slot.push[1] = fy * (target - anchor);
                        slot.push[2] = fz * (target - anchor);
                    }
                }
                slot.head[0] = hx;
                slot.head[1] = hy;
                slot.head[2] = hz;
                slot.has_transform = 1;
            }
            if (slot.has_transform &&
                (slot.scale != 1.0 || slot.push[0] != 0.0 ||
                 slot.push[1] != 0.0 || slot.push[2] != 0.0)) {
                for (uint32_t vi = 0; vi < nvert; ++vi) {
                    double x = (double)world[vi][0] - slot.head[0];
                    double y = (double)world[vi][1] - slot.head[1];
                    double z = (double)world[vi][2] - slot.head[2];
                    world[vi][0] = (int64_t)(
                        slot.head[0] + x * slot.scale + slot.push[0]);
                    world[vi][1] = (int64_t)(
                        slot.head[1] + y * slot.scale + slot.push[1]);
                    world[vi][2] = (int64_t)(
                        slot.head[2] + z * slot.scale + slot.push[2]);
                }
            }
        }
    }
    if (is_terrain && mw2er_config().reduce_terrain_gaps) {
        int64_t sum_x = 0;
        int64_t sum_z = 0;
        float dx = 0.0f;
        float dz = 0.0f;
        for (uint32_t vi = 0; vi < nvert; ++vi) {
            sum_x += world[vi][0];
            sum_z += world[vi][2];
        }
        if (mw2er_terrain_delta(
                ex.mission_name, nvert, nfaces, sum_x, sum_z, &dx, &dz)) {
            int64_t addx = (int64_t)(dx * (double)k_fixed_scale + (dx >= 0 ? 0.5 : -0.5));
            int64_t addz = (int64_t)(dz * (double)k_fixed_scale + (dz >= 0 ? 0.5 : -0.5));
            for (uint32_t vi = 0; vi < nvert; ++vi) {
                world[vi][0] += addx;
                world[vi][2] += addz;
            }
        }
    }
    if (!mem.read(
            block_data + face_off, faces, (uint32_t)nfaces * FACE_STRIDE)) {
        return 1;
    }

    FaceRec recs[MAX_FACES];
    int nrec = 0;
    uint32_t face_hash = hash_bytes(faces, (uint32_t)nfaces * FACE_STRIDE);
    int topo_hit = 0;
    int cache_topo = (ctx.camo < 0 && ctx.emblem < 0);
    if (!cache_topo) {
        face_hash = 0;
    }
    for (size_t ti = 0; cache_topo && ti < g_topo_cache.size(); ++ti) {
        TopologyCacheEntry &te = g_topo_cache[ti];
        if (te.block == block_data && te.nvert == nvert && te.nfaces == nfaces &&
            te.face_off == face_off && te.face_hash == face_hash &&
            te.recs.size() <= MAX_FACES) {
            nrec = (int)te.recs.size();
            for (int i = 0; i < nrec; ++i) {
                recs[i] = te.recs[i];
            }
            has_mode3 = te.has_mode3;
            topo_hit = 1;
            break;
        }
    }
    if (!topo_hit) for (uint32_t fi = 0; fi < nfaces && nrec < MAX_FACES; ++fi) {
        const uint8_t *fh = faces + fi * FACE_STRIDE;
        FaceRec &r = recs[nrec];
        r.flags = load_u16(fh + 0);
        r.vcount = load_u16(fh + 2) & 0xFF;
        int m = (r.flags >> 12) & 0xF;
        int textured = (m == MODE_TEXTURED_P || m == MODE_TEXTURED_PRE ||
            m == MODE_TEXTURED_AFF);
        if (textured && ctx.camo >= 0 && (r.flags & 0xFF) == 0) {
            r.flags = (uint16_t)((r.flags & 0xFF00) | (ctx.camo & 0xFF));
        }
        if (textured && ctx.emblem >= 0 &&
            (r.flags & 0xFF) == EMBLEM_TEXTURE_SOURCE_INDEX) {
            r.flags = (uint16_t)((r.flags & 0xFF00) | (ctx.emblem & 0xFF));
        }
        r.mode = (r.flags >> 12) & 0xF;
        r.owner = load_u32(fh + 0x20);
        r.skip = 0;
        uint32_t index_offset = load_u32(fh + 4);
        uint8_t idx_bytes[MAX_FACE_VERTICES];
        if (r.vcount < 1 || r.vcount > MAX_FACE_VERTICES) {
            continue;
        }
        r.normal[0] = load_i32(fh + 0x14);
        r.normal[1] = load_i32(fh + 0x18);
        r.normal[2] = load_i32(fh + 0x1C);
        uint32_t index_addr =
            block_data + face_off + fi * FACE_STRIDE + index_offset;
        if (!mem.read(index_addr, idx_bytes, r.vcount)) {
            continue;
        }
        int bad = 0;
        for (int i = 0; i < r.vcount; ++i) {
            if (idx_bytes[i] >= nvert) {
                bad = 1;
                break;
            }
            r.indices[i] = idx_bytes[i];
        }
        if (bad) {
            continue;
        }
        if (r.mode == MODE_BILLBOARD) {
            has_mode3 = 1;
        }
        nrec += 1;
    }
    if (!topo_hit && cache_topo && nrec > 0) {
        if (g_topo_cache.size() >= 8192) {
            g_topo_cache.erase(
                g_topo_cache.begin(),
                g_topo_cache.begin() + (g_topo_cache.size() / 4));
        }
        TopologyCacheEntry te;
        te.block = block_data;
        te.nvert = nvert;
        te.nfaces = nfaces;
        te.face_off = face_off;
        te.face_hash = face_hash;
        te.has_mode3 = has_mode3;
        te.recs.assign(recs, recs + nrec);
        g_topo_cache.push_back(std::move(te));
    }

    if (static_candidate && !has_mode3) {
        ctx.part = &ctx.ex->part[MW2ER_PART_STATIC];
        if (ex.static_block_ids.insert(block_data).second && ctx.reuse_static) {
            ctx.ex->static_reused = 0;
        }
    }

    int classify = ctx.ex->camera.satellite_view && !is_terrain;
    if (classify) {
        for (int fi = 0; fi < nrec; ++fi) {
            satellite_classify_face(
                ctx,
                recs[fi].owner,
                &recs[fi].flags,
                &recs[fi].mode);
        }
    }

    int rotor_ok = 0;
    if (mw2er_config().enhanced_heli_rotors &&
        nvert == ROTOR_DISC_SOURCE_VERTICES &&
        nrec == ROTOR_DISC_SOURCE_FACES) {
        int sets[2][8];
        int setn[2] = {0, 0};
        int unique_ok = 1;
        for (int fi = 0; fi < 2; ++fi) {
            int rotor_face_mode =
                recs[fi].mode == MODE_TEXTURED_P ||
                recs[fi].mode == MODE_TEXTURED_AFF ||
                ctx.ex->camera.satellite_view;
            if (!rotor_face_mode ||
                recs[fi].vcount != ROTOR_DISC_SOURCE_FACE_VERTICES) {
                unique_ok = 0;
                break;
            }
            for (int i = 0; i < recs[fi].vcount; ++i) {
                int idx = recs[fi].indices[i];
                int seen = 0;
                for (int k = 0; k < setn[fi]; ++k) {
                    if (sets[fi][k] == idx) {
                        seen = 1;
                        break;
                    }
                }
                if (seen) {
                    unique_ok = 0;
                    break;
                }
                sets[fi][setn[fi]++] = idx;
            }
        }
        if (unique_ok && setn[0] == 7 && setn[1] == 7) {
            int overlap = 0;
            int present[14];
            memset(present, 0, sizeof(present));
            for (int fi = 0; fi < 2; ++fi) {
                for (int k = 0; k < 7; ++k) {
                    int idx = sets[fi][k];
                    for (int j = 0; j < 7; ++j) {
                        if (fi == 0) {
                            break;
                        }
                        if (sets[0][j] == idx) {
                            overlap = 1;
                        }
                    }
                    if (idx >= 0 && idx < 14) {
                        present[idx] = 1;
                    }
                }
            }
            int all = 1;
            for (int i = 0; i < 14; ++i) {
                if (!present[i]) {
                    all = 0;
                    break;
                }
            }
            if (!overlap && all) {
                rotor_ok = 1;
            }
        }
    }

    int fan_skip[MAX_FACES];
    memset(fan_skip, 0, sizeof(fan_skip));
    if (!rotor_ok && !ctx.ex->camera.satellite_view &&
        !ex.camera.imaging_wireframe &&
        mw2er_config().enhanced_aero_lift_fans) {
        for (int fi = 0; fi < nrec; ++fi) {
            if ((recs[fi].mode == MODE_TEXTURED_P ||
                 recs[fi].mode == MODE_TEXTURED_AFF) &&
                ((recs[fi].flags & 0xFF) + 0x100) == AERO_LIFT_FAN_DESC_INDEX) {
                fan_skip[fi] = 1;
            }
        }
    }

    if (rotor_ok) {
        for (int fi = 0; fi < nrec; ++fi) {
            recs[fi].skip = 1;
            if ((ex.camera.imaging_wireframe || ex.camera.satellite_view) &&
                fi != 0) {
                continue;
            }
            int desc = ((int)recs[fi].flags & 0xFF) + 0x100;
            float lighting = pack_lighting_state(
                mode57_lit_shade_before_fog(
                    recs[fi].indices[0], recs[fi].normal, world, ex.lighting),
                component_policy_for(ctx, recs[fi].owner));
            emit_heli_rotor_draw(
                *ctx.part,
                desc,
                world,
                recs[fi].indices,
                recs[fi].vcount,
                recs[fi].normal,
                lighting,
                ex.camera.imaging_wireframe || ex.camera.satellite_view,
                ex.camera.satellite_view
                    ? (float)(recs[fi].flags & 0xFF)
                    : wireframe_palette_index(mem, recs[fi].owner));
        }
    } else {
        int fan_n = 0;
        for (int fi = 0; fi < nrec; ++fi) {
            if (fan_skip[fi]) {
                fan_n += 1;
            }
        }
        if (fan_n > 0) {
            Mw2erAeroFanMesh mesh;
            Mw2erRotorDraw draw{};
            int desc = AERO_LIFT_FAN_DESC_INDEX;
            draw.desc = desc;
            draw.effect = MW2ER_ROTOR_FAN;
            draw.normalized_uv = 0;
            double cx = 0, cy = 0, cz = 0;
            int nused = 0;
            uint8_t usedv[MAX_VERTICES];
            int remap[MAX_VERTICES];
            memset(usedv, 0, sizeof(usedv));
            for (int i = 0; i < MAX_VERTICES; ++i) {
                remap[i] = -1;
            }
            for (int fi = 0; fi < nrec; ++fi) {
                if (!fan_skip[fi]) {
                    continue;
                }
                recs[fi].skip = 1;
                float lighting = pack_lighting_state(
                    mode57_lit_shade_before_fog(
                        recs[fi].indices[0], recs[fi].normal, world, ex.lighting),
                    component_policy_for(ctx, recs[fi].owner));
                if (recs[fi].vcount >= 3) {
                    uint32_t a = aero_fan_map_vert(
                        mesh,
                        recs[fi].indices[0],
                        world,
                        uv,
                        remap,
                        usedv,
                        &cx,
                        &cy,
                        &cz,
                        &nused);
                    for (int i = 1; i + 1 < recs[fi].vcount; ++i) {
                        mesh.indices.tri(
                            a,
                            aero_fan_map_vert(
                                mesh,
                                recs[fi].indices[i],
                                world,
                                uv,
                                remap,
                                usedv,
                                &cx,
                                &cy,
                                &cz,
                                &nused),
                            aero_fan_map_vert(
                                mesh,
                                recs[fi].indices[i + 1],
                                world,
                                uv,
                                remap,
                                usedv,
                                &cx,
                                &cy,
                                &cz,
                                &nused));
                        mesh.primitive_lighting.emit({lighting});
                    }
                }
            }
            if (nused > 0 && mesh.indices.count() >= 3) {
                draw.center[0] = (float)(cx / (double)nused / k_fixed_scale);
                draw.center[1] = (float)(cy / (double)nused / k_fixed_scale);
                draw.center[2] = (float)(cz / (double)nused / k_fixed_scale);
                draw.aero_mesh = (uint32_t)ctx.part->aero_fans.size();
                mark_desc(*ctx.part, desc);
                ctx.part->aero_fans.push_back(std::move(mesh));
                ctx.part->rotor_draws.push_back(draw);
            }
        }
    }

    uint32_t flat_base = 0;
    uint32_t tex_base = 0;
    uint32_t wire_base = 0;
    int have_flat = 0;
    int have_tex = 0;
    int have_wire = 0;


    for (int fi = 0; fi < nrec; ++fi) {
        if (recs[fi].skip) {
            continue;
        }
        uint16_t face_flags = recs[fi].flags;
        uint16_t vcount = recs[fi].vcount;
        int mode = recs[fi].mode;
        const int32_t *normal = recs[fi].normal;
        const int32_t *indices = recs[fi].indices;
        float lighting_state;
        int policy = component_policy_for(ctx, recs[fi].owner);
        apply_target_flat_material(
            ctx, recs[fi].owner, &face_flags, &mode);

        if (ex.camera.imaging_wireframe && vcount >= 3 &&
            mode != MODE_POLYLINE) {
            int keep = 0;
            if (mode == MODE_BILLBOARD) {
                keep = ex.camera.preserve_imaging_effects;
            } else if (
                mode == MODE_TEXTURED_P || mode == MODE_TEXTURED_PRE ||
                mode == MODE_TEXTURED_AFF) {
                keep = imaging_keep_textured(
                    ctx, ((int)face_flags & 0xFF) + 0x100);
            }
            if (!keep) {
                ensure_xyz_once(
                    &have_wire,
                    &wire_base,
                    ctx.part->wire_verts,
                    world,
                    nvert);
                emit_imaging_face(ctx, recs[fi], wire_base);
                continue;
            }
        }

        if (vcount == 1) {
            const uint8_t *v = verts + indices[0] * VERTEX_STRIDE;
            // Suppress empty black origin markers exposed by cockpit smoothing.
            if (smooth != NULL && nvert == 1 && nfaces == 1 &&
                (face_flags & 0xFFF0u) == 0x1000u &&
                load_i32(v + 0x00) == 0 && load_i32(v + 0x04) == 0 &&
                load_i32(v + 0x08) == 0 && load_i32(v + 0x0C) == 0 &&
                load_i32(v + 0x10) == 0 && load_i32(v + 0x14) == 0) {
                continue;
            }
            emit_palette_vertex(
                ctx.part->points, world[indices[0]], mode0_palette(face_flags));
            ctx.part->point_count += 1;
            continue;
        }
        if (vcount == 2) {
            emit_palette_vertex(
                ctx.part->lines, world[indices[0]], mode0_palette(face_flags));
            emit_palette_vertex(
                ctx.part->lines, world[indices[1]], mode0_palette(face_flags));
            ctx.part->line_count += 1;
            continue;
        }
        if (mode == MODE_POLYLINE) {
            emit_closed_polyline(
                *ctx.part, world, indices, vcount, mode2_palette(face_flags));
            continue;
        }
        if (mode == MODE_SATELLITE_WIREFRAME) {
            emit_satellite_wire_face(
                *ctx.part,
                world,
                nvert,
                indices,
                vcount,
                face_flags,
                &have_wire,
                &wire_base);
            continue;
        }
        if (mode == MODE_FLAT_UNLIT || mode == MODE_FLAT_LIT) {
            float pal = (mode == MODE_FLAT_UNLIT)
                ? mode0_palette(face_flags)
                : mode1_palette(
                      face_flags,
                      vcount,
                      indices,
                      normal,
                      world,
                      ex.lighting,
                      ex.camera,
                      policy);
            ensure_xyz_once(
                &have_flat,
                &flat_base,
                ctx.part->indexed_flat_verts,
                world,
                nvert);
            size_t before = ctx.part->indexed_flat_indices.count();
            emit_indexed_fan(
                ctx.part->indexed_flat_indices,
                &ctx.part->indexed_flat_palette,
                flat_base,
                indices,
                vcount,
                pal);
            ctx.part->flat_count +=
                (uint32_t)((ctx.part->indexed_flat_indices.count() - before) / 3);
            continue;
        }
        if (mode == MODE_SATELLITE_SOLID) {
            emit_satellite_solid_face(
                *ctx.part,
                ex.camera,
                world,
                indices,
                vcount,
                face_flags);
            continue;
        }
        if (mode == MODE_BILLBOARD) {
            int desc = ((int)face_flags & 0xFF0) >> 4;
            int a = -1, b = -1, c_idx = -1;
            int max_u = 0, max_v = 0;
            int mirror_u = 0;
            int flip_winding = 0;
            if (vcount < 3) {
                continue;
            }
            for (int i = 0; i < vcount; ++i) {
                int uu = uv[indices[i]][0];
                int vv = uv[indices[i]][1];
                if (uu > max_u) {
                    max_u = uu;
                }
                if (vv > max_v) {
                    max_v = vv;
                }
                if (uu < 0) {
                    mirror_u = 1;
                }
            }
            for (int i = 0; i < vcount; ++i) {
                int uu = uv[indices[i]][0];
                int vv = uv[indices[i]][1];
                if (uu == 0 && vv == 0) {
                    a = indices[i];
                }
                if (uu == 0 && vv == max_v) {
                    b = indices[i];
                }
                if (uu == max_u && vv == 0) {
                    c_idx = indices[i];
                }
            }
            if (vcount >= 3) {
                int ax = uv[indices[0]][0], ay = uv[indices[0]][1];
                int bx = uv[indices[1]][0], by = uv[indices[1]][1];
                int cx = uv[indices[2]][0], cy = uv[indices[2]][1];
                int area2 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
                flip_winding = area2 < 0;
            }
            /* Ordinary billboards use authored O/V as their spine. The
             * satellite rasterizer instead centers on V=(0,max_v) and derives
             * its square size from U=(max_u,0), matching the game's overhead
             * effect expansion and the Python renderer. */
            int anchor = ex.camera.satellite_view ? b : a;
            int other = ex.camera.satellite_view ? c_idx : b;
            if (anchor < 0 || other < 0) {
                anchor = indices[0];
                other = vcount >= 3 ? indices[2] : indices[1];
            }
            {
                uint32_t key =
                    ((uint32_t)desc << 20) ^
                    ((uint32_t)anchor << 10) ^ (uint32_t)other;
                int dup = 0;
                for (int k = 0; k < bb_n; ++k) {
                    if (bb_keys[k] == key) {
                        dup = 1;
                        break;
                    }
                }
                if (dup) {
                    continue;
                }
                if (bb_n < 256) {
                    bb_keys[bb_n++] = key;
                }
            }
            emit_billboard(
                *ctx.part,
                *ctx.mem,
                desc,
                world[anchor],
                world[other],
                (float)(mirror_u | (flip_winding << 1)));
            continue;
        }
        if (mode == MODE_TEXTURED_P || mode == MODE_TEXTURED_PRE ||
            mode == MODE_TEXTURED_AFF) {
            int desc = ((int)face_flags & 0xFF) + 0x100;
            if (vcount < 3 || desc < 0 || desc >= MW2ER_MAX_DESC) {
                continue;
            }
            lighting_state = pack_lighting_state(
                mode57_lit_shade_before_fog(
                    indices[0], normal, world, ex.lighting),
                policy);
            ensure_xyz_uv_once(
                &have_tex,
                &tex_base,
                ctx.part->texmap_verts,
                world,
                uv,
                nvert);
            mark_desc(*ctx.part, desc);
            Mw2erDescStreams *streams = ctx.part->ensure_desc_streams(desc);
            if (streams == nullptr) {
                continue;
            }
            emit_indexed_fan(
                streams->texmap_indices,
                &streams->texmap_lighting,
                tex_base,
                indices,
                vcount,
                lighting_state);
            continue;
        }
        if (vcount < 3) {
            continue;
        }
        lighting_state = pack_lighting_state(
            mode4_lit_shade_before_fog(
                (int32_t)face_flags,
                indices[0],
                normal,
                world,
                ex.lighting),
            policy);
        for (int i = 1; i + 1 < vcount; ++i) {
            int corners[3] = {indices[0], indices[i], indices[i + 1]};
            for (int c = 0; c < 3; ++c) {
                int vi = corners[c];
                emit_mode4_vertex(*ctx.part, world[vi], (float)c_in[vi], lighting_state);
            }
        }
    }
    g_precise_world_base = NULL;
    g_precise_world_count = 0;
    return 1;
}

static int wtbo_face_stride(int vertex_count)
{
    int raw = 4 + vertex_count * 2;
    int padded = ((raw + 5) / 6) * 6;
    return padded < 12 ? 12 : padded;
}

static const WtboMesh *compiled_wtbo(
    int32_t resource_id,
    const uint8_t *blob,
    uint32_t blob_size)
{
    auto found = g_wtbo_meshes.find(resource_id);
    if (found != g_wtbo_meshes.end()) {
        return &found->second;
    }
    if (blob == NULL || blob_size < WTBO_HEADER_SIZE ||
        memcmp(blob, "WTBO", 4) != 0) {
        return NULL;
    }
    const uint16_t nvert = load_u16(blob + 0x18);
    const uint16_t nfaces = load_u16(blob + 0x1A);
    const uint32_t vert_bytes = (uint32_t)nvert * WTBO_VERTEX_STRIDE;
    if (nvert < 3 || nvert > MAX_VERTICES || nfaces < 1 ||
        nfaces > MAX_FACES || WTBO_HEADER_SIZE + vert_bytes > blob_size) {
        return NULL;
    }
    const Mw2erStartupScope trace(MW2ER_STARTUP_POLY);
    WtboMesh mesh;
    mesh.vertices.resize(nvert);
    const uint8_t *raw_vertex = blob + WTBO_HEADER_SIZE;
    for (uint16_t i = 0; i < nvert; ++i, raw_vertex += WTBO_VERTEX_STRIDE) {
        WtboVertex &v = mesh.vertices[i];
        v.x = load_i32(raw_vertex + 0);
        v.y = load_i32(raw_vertex + 4);
        v.z = load_i32(raw_vertex + 8);
        memcpy(&v.u, raw_vertex + 12, 2);
        memcpy(&v.v, raw_vertex + 14, 2);
    }
    mesh.faces.reserve(nfaces);
    uint32_t off = WTBO_HEADER_SIZE + vert_bytes;
    for (uint16_t fi = 0; fi < nfaces; ++fi) {
        if (off > blob_size || blob_size - off < 4) return NULL;
        const uint16_t count = load_u16(blob + off + 2);
        const uint32_t stride = (uint32_t)wtbo_face_stride(count);
        if (stride > blob_size || off > blob_size - stride) {
            return NULL;
        }
        if (count < 3 || count > MAX_FACE_VERTICES) return NULL;
        const int source_mode = (load_u16(blob + off) >> 12) & 0xF;
        if (source_mode != MODE_FLAT_UNLIT && source_mode != MODE_FLAT_LIT &&
            source_mode != MODE_ILLUMINATE && source_mode != MODE_TEXTURED_P &&
            source_mode != MODE_TEXTURED_PRE && source_mode != MODE_TEXTURED_AFF)
            return NULL;
        {
            WtboFace face = {};
            face.flags = load_u16(blob + off);
            face.vcount = count;
            int valid = 1;
            for (uint16_t i = 0; i < count; ++i) {
                face.indices[i] = load_u16(blob + off + 4 + i * 2);
                if (face.indices[i] >= nvert) {
                    valid = 0;
                    break;
                }
            }
            if (!valid) return NULL;
            {
                const WtboVertex &p0 = mesh.vertices[face.indices[0]];
                for (uint16_t i = 1; i + 1 < count; ++i) {
                    const WtboVertex &pa = mesh.vertices[face.indices[i]];
                    const WtboVertex &pb = mesh.vertices[face.indices[i + 1]];
                    const int64_t ax = (int64_t)pa.x - p0.x;
                    const int64_t ay = (int64_t)pa.y - p0.y;
                    const int64_t az = (int64_t)pa.z - p0.z;
                    const int64_t bx = (int64_t)pb.x - p0.x;
                    const int64_t by = (int64_t)pb.y - p0.y;
                    const int64_t bz = (int64_t)pb.z - p0.z;
                    const int64_t nx = az * by - ay * bz;
                    const int64_t ny = ax * bz - az * bx;
                    const int64_t nz = ay * bx - ax * by;
                    if (nx == 0 && ny == 0 && nz == 0) {
                        continue;
                    }
                    const double len = sqrt(
                        (double)nx * nx + (double)ny * ny + (double)nz * nz);
                    const double scale = 536870912.0 / len;
                    face.normal[0] = (int32_t)(nx * scale + (nx >= 0 ? 0.5 : -0.5));
                    face.normal[1] = (int32_t)(ny * scale + (ny >= 0 ? 0.5 : -0.5));
                    face.normal[2] = (int32_t)(nz * scale + (nz >= 0 ? 0.5 : -0.5));
                    break;
                }
                mesh.faces.push_back(face);
            }
        }
        off += stride;
    }
    return &g_wtbo_meshes.emplace(resource_id, std::move(mesh)).first->second;
}

static int extract_wtbo(
    ExtractCtx &ctx,
    uint32_t flags,
    int32_t resource_id,
    const uint8_t *blob,
    uint32_t blob_size)
{
    const WtboMesh *mesh = compiled_wtbo(resource_id, blob, blob_size);
    if (mesh == NULL || mesh->vertices.empty() || mesh->faces.empty()) {
        return 0;
    }
    const Mem &mem = *ctx.mem;
    Mw2erSceneExtract &ex = *ctx.ex;
    if (ctx.part == NULL) {
        ctx.part = &ex.part[
            ctx.source == SRC_RENDERER_LOD ? MW2ER_PART_ENTITY : MW2ER_PART_SCENE];
    }
    const uint16_t nvert = (uint16_t)mesh->vertices.size();
    uint8_t matrix[ENTITY_MATRIX_SIZE];
    int have_matrix = 0;
    if (ctx.matrix_override != NULL) {
        memcpy(matrix, ctx.matrix_override, ENTITY_MATRIX_SIZE);
        have_matrix = 1;
    }
    int64_t world[MAX_VERTICES][3];
    uint8_t c_in[MAX_VERTICES];
    int32_t uv[MAX_VERTICES][2];
    for (uint32_t vi = 0; vi < nvert; ++vi) {
        const WtboVertex &v = mesh->vertices[vi];
        uv[vi][0] = v.u;
        uv[vi][1] = v.v;
        c_in[vi] = (uint8_t)(v.u & 0xFF);
        if (have_matrix) {
            int64_t r0 = load_i32(matrix + 0x00);
            int64_t r1 = load_i32(matrix + 0x04);
            int64_t r2 = load_i32(matrix + 0x08);
            int64_t r3 = load_i32(matrix + 0x0C);
            int64_t r4 = load_i32(matrix + 0x10);
            int64_t r5 = load_i32(matrix + 0x14);
            int64_t r6 = load_i32(matrix + 0x18);
            int64_t r7 = load_i32(matrix + 0x1C);
            int64_t r8 = load_i32(matrix + 0x20);
            int64_t tx = load_i32(matrix + ENTITY_TRANSLATION_OFFSET + 0);
            int64_t ty = load_i32(matrix + ENTITY_TRANSLATION_OFFSET + 4);
            int64_t tz = load_i32(matrix + ENTITY_TRANSLATION_OFFSET + 8);
            world[vi][0] = ((r0 * v.x + r1 * v.y + r2 * v.z) >> 29) + tx;
            world[vi][1] = ((r3 * v.x + r4 * v.y + r5 * v.z) >> 29) + ty;
            world[vi][2] = ((r6 * v.x + r7 * v.y + r8 * v.z) >> 29) + tz;
        } else {
            world[vi][0] = v.x;
            world[vi][1] = v.y;
            world[vi][2] = v.z;
        }
    }

    uint32_t flat_base = 0;
    uint32_t tex_base = 0;
    uint32_t wire_base = 0;
    int have_flat = 0;
    int have_tex = 0;
    int have_wire = 0;

    const int policy = component_policy_for(ctx, ctx.owner_addr);
    for (const WtboFace &face : mesh->faces) {
        uint16_t face_flags = face.flags;
        const int vcount = face.vcount;
        {
            int m = (face_flags >> 12) & 0xF;
            int textured = (m == MODE_TEXTURED_P || m == MODE_TEXTURED_PRE ||
                m == MODE_TEXTURED_AFF);
            if (textured && ctx.camo >= 0 && (face_flags & 0xFF) == 0) {
                face_flags = (uint16_t)((face_flags & 0xFF00) | (ctx.camo & 0xFF));
            }
            if (textured && ctx.emblem >= 0 &&
                (face_flags & 0xFF) == EMBLEM_TEXTURE_SOURCE_INDEX) {
                face_flags = (uint16_t)((face_flags & 0xFF00) | (ctx.emblem & 0xFF));
            }
        }
        const int32_t *indices = face.indices;
        int mode = (face_flags >> 12) & 0xF;
        apply_target_flat_material(
            ctx, ctx.owner_addr, &face_flags, &mode);
        if (ex.camera.satellite_view) {
            /* Renderer-selected LOD replaces only the face source. View policy
             * still comes from the owning component/submesh, exactly as it
             * does for the game's ordinary geometry blocks; otherwise mechs
             * leak their normal textured modes into satellite imaging. */
            satellite_classify_face(
                ctx, ctx.owner_addr, &face_flags, &mode);
        }
        if (ex.camera.imaging_wireframe && vcount >= 3 &&
            mode != MODE_POLYLINE) {
            int keep = 0;
            if (mode == MODE_BILLBOARD) {
                keep = ex.camera.preserve_imaging_effects;
            } else if (
                mode == MODE_TEXTURED_P || mode == MODE_TEXTURED_PRE ||
                mode == MODE_TEXTURED_AFF) {
                keep = imaging_keep_textured(
                    ctx, ((int)face_flags & 0xFF) + 0x100);
            }
            if (!keep) {
                FaceRec rec;
                rec.flags = face_flags;
                rec.vcount = vcount;
                rec.mode = mode;
                rec.owner = ctx.owner_addr;
                rec.skip = 0;
                for (int i = 0; i < vcount; ++i) {
                    rec.indices[i] = indices[i];
                }
                ensure_xyz_once(
                    &have_wire,
                    &wire_base,
                    ctx.part->wire_verts,
                    world,
                    nvert);
                emit_imaging_face(ctx, rec, wire_base);
                continue;
            }
        }
        if (mode == MODE_SATELLITE_WIREFRAME) {
            emit_satellite_wire_face(
                *ctx.part,
                world,
                nvert,
                indices,
                vcount,
                face_flags,
                &have_wire,
                &wire_base);
            continue;
        }
        if (mode == MODE_SATELLITE_SOLID) {
            emit_satellite_solid_face(
                *ctx.part,
                ex.camera,
                world,
                indices,
                vcount,
                face_flags);
            continue;
        }
        int32_t normal[3] = {face.normal[0], face.normal[1], face.normal[2]};
        if (have_matrix) {
            normal[0] = (int32_t)(((int64_t)load_i32(matrix + 0x00) * face.normal[0] +
                (int64_t)load_i32(matrix + 0x04) * face.normal[1] +
                (int64_t)load_i32(matrix + 0x08) * face.normal[2]) >> 29);
            normal[1] = (int32_t)(((int64_t)load_i32(matrix + 0x0C) * face.normal[0] +
                (int64_t)load_i32(matrix + 0x10) * face.normal[1] +
                (int64_t)load_i32(matrix + 0x14) * face.normal[2]) >> 29);
            normal[2] = (int32_t)(((int64_t)load_i32(matrix + 0x18) * face.normal[0] +
                (int64_t)load_i32(matrix + 0x1C) * face.normal[1] +
                (int64_t)load_i32(matrix + 0x20) * face.normal[2]) >> 29);
        }
        if (mode == MODE_FLAT_UNLIT || mode == MODE_FLAT_LIT) {
            float pal = (mode == MODE_FLAT_UNLIT)
                ? mode0_palette(face_flags)
                : mode1_palette(
                      face_flags, vcount, indices, normal, world, ex.lighting,
                      ex.camera, policy);
            ensure_xyz_once(
                &have_flat,
                &flat_base,
                ctx.part->indexed_flat_verts,
                world,
                nvert);
            size_t before = ctx.part->indexed_flat_indices.count();
            emit_indexed_fan(
                ctx.part->indexed_flat_indices,
                &ctx.part->indexed_flat_palette,
                flat_base,
                indices,
                vcount,
                pal);
            ctx.part->flat_count +=
                (uint32_t)((ctx.part->indexed_flat_indices.count() - before) / 3);
            continue;
        }
        if (mode == MODE_TEXTURED_P || mode == MODE_TEXTURED_PRE ||
            mode == MODE_TEXTURED_AFF) {
            int desc = ((int)face_flags & 0xFF) + 0x100;
            if (desc < 0 || desc >= MW2ER_MAX_DESC) {
                continue;
            }
            float lighting_state = pack_lighting_state(
                mode57_lit_shade_before_fog(
                    indices[0], normal, world, ex.lighting),
                policy);
            ensure_xyz_uv_once(
                &have_tex,
                &tex_base,
                ctx.part->texmap_verts,
                world,
                uv,
                nvert);
            mark_desc(*ctx.part, desc);
            Mw2erDescStreams *streams = ctx.part->ensure_desc_streams(desc);
            if (streams == nullptr) {
                continue;
            }
            emit_indexed_fan(
                streams->texmap_indices,
                &streams->texmap_lighting,
                tex_base,
                indices,
                vcount,
                lighting_state);
            continue;
        }
        if (vcount >= 3 && mode == MODE_ILLUMINATE) {
            float lighting_state = pack_lighting_state(
                mode4_lit_shade_before_fog(
                    (int32_t)face_flags, indices[0], normal, world, ex.lighting),
                policy);
            for (int i = 1; i + 1 < vcount; ++i) {
                int corners[3] = {indices[0], indices[i], indices[i + 1]};
                for (int c = 0; c < 3; ++c) {
                    emit_mode4_vertex(
                        *ctx.part, world[corners[c]], (float)c_in[corners[c]], lighting_state);
                }
            }
        }
    }
    (void)mem;
    return 1;
}

static void extract_tree_geom(ExtractCtx &ctx, uint32_t tree, uint32_t geom)
{
    uint8_t gb[GEOMETRY_NODE_SIZE];
    if (!ctx.mem->read(geom, gb, GEOMETRY_NODE_SIZE)) {
        return;
    }
    uint32_t flags = load_u32(gb + 0);
    /* The target helper prepares its drawable block at +0x1C. The ordinary
     * model-tree walk probes +0x20 first, but doing that for a target can pick
     * an alternate block whose live N2 normals do not describe this capture. */
    uint32_t blocks[2] = {
        ctx.source == SRC_TARGET ? load_u32(gb + 0x1C) : load_u32(gb + 0x20),
        ctx.source == SRC_TARGET ? 0 : load_u32(gb + 0x1C)};
    for (int i = 0; i < 2; ++i) {
        if (blocks[i] == 0) {
            continue;
        }
        if (already_emitted(ctx, blocks[i])) {
            return;
        }
        ctx.node_addr = geom;
        uint32_t transform = ctx.mem->u32(geom + 0x18);
        const uint8_t *matrix = NULL;
        if (ctx.source == SRC_TARGET) {
            matrix = ctx.mem->view(
                (transform ? transform : tree) + ENTITY_MATRIX_OFFSET,
                ENTITY_MATRIX_SIZE);
        }
        ctx.matrix_override = matrix;
        if (extract_block(ctx, flags, 0, blocks[i])) {
            ctx.ex->tree_node_count += 1;
            return;
        }
    }
}

static void walk_model_tree(ExtractCtx &ctx, uint32_t root, int node_limit)
{
    uint32_t stack[4096];
    int sp = 0;
    uint32_t seen[4096];
    int seen_n = 0;
    if (root == 0 || node_limit < 1) {
        return;
    }
    if (node_limit > 4096) {
        node_limit = 4096;
    }
    stack[sp++] = root;
    while (sp > 0 && seen_n < node_limit) {
        uint32_t tree_addr = stack[--sp];
        if (tree_addr == 0) {
            continue;
        }
        int dup = 0;
        for (int i = 0; i < seen_n; ++i) {
            if (seen[i] == tree_addr) {
                dup = 1;
                break;
            }
        }
        if (dup) {
            continue;
        }
        seen[seen_n++] = tree_addr;
        uint8_t tb[MODEL_TREE_NODE_READ_SIZE];
        if (!ctx.mem->read(tree_addr, tb, MODEL_TREE_NODE_READ_SIZE)) {
            continue;
        }
        uint32_t child = load_u32(tb + 0x04);
        uint32_t sibling = load_u32(tb + 0x08);
        uint32_t geom = load_u32(tb + 0x6C);
        if (sibling && sp < 4096) {
            stack[sp++] = sibling;
        }
        if (child && sp < 4096) {
            stack[sp++] = child;
        }
        if (geom) {
            extract_tree_geom(ctx, tree_addr, geom);
        }
    }
}

static int projected_detail(
    float projected_height,
    const float *thresholds,
    int previous,
    float hysteresis)
{
    if (previous < 0 || previous > 3) {
        for (int detail = 0; detail < 3; ++detail) {
            if (projected_height >= thresholds[detail]) {
                return detail;
            }
        }
        return 3;
    }
    int detail = previous;
    while (detail > 0 &&
        projected_height >= thresholds[detail - 1] * (1.0f + hysteresis)) {
        detail -= 1;
    }
    while (detail < 3 &&
        projected_height < thresholds[detail] * (1.0f - hysteresis)) {
        detail += 1;
    }
    return detail;
}

static EntityMaterials scan_entity_materials(
    const Mem &mem,
    int32_t descriptor_count,
    int32_t owner_id)
{
    int camo_counts[8] = {};
    int emblem_counts[256] = {};
    for (int32_t i = 0; i < descriptor_count; ++i) {
        const uint32_t base = ADDR_COMPONENT_DESCRIPTOR_TABLE +
            (uint32_t)i * COMPONENT_DESCRIPTOR_STRIDE;
        if (mem.i32_rel(base) != owner_id) {
            continue;
        }
        const uint32_t node = mem.u32_rel(base + 0x24);
        const uint32_t block = node ? mem.u32(node + 0x1C) : 0;
        if (block == 0) {
            continue;
        }
        uint8_t header[HEADER_SIZE];
        if (!mem.read(block, header, sizeof(header))) {
            continue;
        }
        const uint16_t face_count = load_u16(header + 0x06);
        const uint32_t face_data = load_u32(header + 0x08);
        if (face_count == 0 || face_count > MAX_POLY_FACES ||
            face_data > MAX_FACE_DATA_OFFSET) {
            continue;
        }
        const int32_t installed_detail = mem.i32_rel(base + 0x04);
        const uint8_t *authored = NULL;
        uint32_t authored_size = 0;
        uint32_t authored_offset = 0;
        if (installed_detail >= 0 && installed_detail < 5) {
            const int32_t resource_id = mem.i32_rel(
                base + 0x08 + (uint32_t)installed_detail * 4);
            authored = wtbo_blob(resource_id, &authored_size);
            if (authored != NULL && authored_size >= WTBO_HEADER_SIZE &&
                load_u16(authored + 0x1A) == face_count) {
                authored_offset = WTBO_HEADER_SIZE +
                    (uint32_t)load_u16(authored + 0x18) * WTBO_VERTEX_STRIDE;
            } else {
                authored = NULL;
            }
        }
        for (uint16_t face = 0; face < face_count; ++face) {
            const uint16_t flags = mem.u16(
                block + face_data + (uint32_t)face * FACE_STRIDE);
            const int mode = (flags >> 12) & 0xF;
            const int texture = flags & 0xFF;
            if ((mode == MODE_TEXTURED_P || mode == MODE_TEXTURED_PRE ||
                 mode == MODE_TEXTURED_AFF) && texture < 8) {
                ++camo_counts[texture];
            }
            if (authored != NULL) {
                if (authored_offset + 4 > authored_size) {
                    authored = NULL;
                    continue;
                }
                const uint16_t authored_flags = load_u16(authored + authored_offset);
                const uint16_t count = load_u16(authored + authored_offset + 2);
                const uint32_t stride = (uint32_t)wtbo_face_stride(count);
                if (stride > authored_size ||
                    authored_offset > authored_size - stride) {
                    authored = NULL;
                    continue;
                }
                authored_offset += stride;
                if (((authored_flags >> 12) & 0xF) == mode &&
                    (mode == MODE_TEXTURED_P || mode == MODE_TEXTURED_PRE ||
                     mode == MODE_TEXTURED_AFF) &&
                    (authored_flags & 0xFF) == EMBLEM_TEXTURE_SOURCE_INDEX) {
                    ++emblem_counts[texture];
                }
            }
        }
    }
    EntityMaterials result = {0, EMBLEM_TEXTURE_SOURCE_INDEX};
    for (int i = 1; i < 8; ++i) {
        if (camo_counts[i] > camo_counts[result.camo]) {
            result.camo = i;
        }
    }
    for (int i = 0; i < 256; ++i) {
        if (emblem_counts[i] > emblem_counts[result.emblem]) {
            result.emblem = i;
        }
    }
    return result;
}

static EntityMaterials entity_materials(
    const Mem &mem,
    int32_t descriptor_count,
    uint32_t entity,
    int32_t owner_id)
{
    const auto found = g_entity_materials.find(entity);
    if (found != g_entity_materials.end()) {
        return found->second;
    }
    const EntityMaterials materials =
        scan_entity_materials(mem, descriptor_count, owner_id);
    g_entity_materials[entity] = materials;
    return materials;
}

static void extract_renderer_lod(ExtractCtx &ctx)
{
    if (!mw2er_resources_have_type(MW2ER_RESOURCE_POLY)) {
        return;
    }
    if (MW2ER_STRICMP(mw2er_config().entity_lod_selection, "native") == 0) {
        return;
    }
    const Mem &mem = *ctx.mem;
    int32_t desc_count = mem.i32_rel(ADDR_COMPONENT_DESCRIPTOR_COUNT);
    int32_t entity_count = mem.i32_rel(ADDR_ENTITY_COUNT);
    if (desc_count < 1 || desc_count > MAX_COMPONENT_DESCRIPTORS) {
        return;
    }
    if (entity_count < 0 || entity_count > PRIMARY_ENTITY_LIMIT) {
        return;
    }
    struct EntityBinding {
        uint32_t pointer;
        int32_t owner_id;
        int index;
        int selected_detail;
    };
    EntityBinding entities[PRIMARY_ENTITY_LIMIT];
    int live_entities = 0;
    for (int ei = 0; ei < entity_count; ++ei) {
        const uint32_t pointer = mem.u32_rel(
            ADDR_ENTITY_BODY_TABLE + (uint32_t)ei * 4);
        if (pointer != 0) {
            entities[live_entities++] = {
                pointer, mem.i32(pointer + 0x04), ei, -1};
        }
    }
    std::sort(
        entities,
        entities + live_entities,
        [](const EntityBinding &a, const EntityBinding &b) {
            return a.owner_id < b.owner_id;
        });
    const int32_t player_slot = mem.i32_rel(ADDR_PLAYER_SLOT);
    const Mw2erRendererConfig &cfg = mw2er_config();
    int forced = -1;
    if (MW2ER_STRICMP(cfg.entity_lod_selection, "detail0") == 0) {
        forced = 0;
    } else if (MW2ER_STRICMP(cfg.entity_lod_selection, "detail1") == 0) {
        forced = 1;
    } else if (MW2ER_STRICMP(cfg.entity_lod_selection, "detail2") == 0) {
        forced = 2;
    } else if (MW2ER_STRICMP(cfg.entity_lod_selection, "detail3") == 0) {
        forced = 3;
    }
    ctx.source = SRC_RENDERER_LOD;
    ctx.part = &ctx.ex->part[MW2ER_PART_ENTITY];
    for (int di = 0; di < desc_count; ++di) {
        uint32_t dbase = ADDR_COMPONENT_DESCRIPTOR_TABLE + (uint32_t)di * COMPONENT_DESCRIPTOR_STRIDE;
        int32_t owner_id = mem.i32_rel(dbase + 0x00);
        uint32_t installed_node = mem.u32_rel(dbase + 0x24);
        uint32_t tree = mem.u32_rel(dbase + 0x28);
        if (tree == 0 || installed_node == 0) {
            continue;
        }
        int node_seen = 0;
        for (int i = 0; i < ctx.visible_node_count; ++i) {
            if (ctx.visible_nodes[i] == installed_node) {
                node_seen = 1;
                break;
            }
        }
        if (!node_seen) {
            continue;
        }
        EntityBinding key = {0, owner_id, 0, 0};
        EntityBinding *entity = std::lower_bound(
            entities,
            entities + live_entities,
            key,
            [](const EntityBinding &a, const EntityBinding &b) {
                return a.owner_id < b.owner_id;
            });
        if (entity == entities + live_entities || entity->owner_id != owner_id ||
            (entity + 1 != entities + live_entities &&
             (entity + 1)->owner_id == owner_id)) {
            continue;
        }
        const uint32_t ep = entity->pointer;
        const int player = entity->index == player_slot;
        if (player && cockpit_camera(ctx.ex->camera) &&
            g_cockpit_geometry.count(installed_node)) {
            continue;
        }
        int detail = entity->selected_detail;
        if (detail < 0) {
            detail = 0;
            if (forced >= 0) {
                detail = forced;
            } else {
            uint32_t model_pointer = mem.u32(ep + 0x20);
            int32_t radius = 0;
            if (model_pointer) {
                radius = mem.i32(model_pointer + ENTITY_MODEL_RADIUS_OFFSET);
                if (radius < 0) {
                    radius = -radius;
                }
            }
            if (radius <= 0) {
                detail = 0;
            } else {
                int32_t pos[3] = {
                    mem.i32(ep + 0x50), mem.i32(ep + 0x54), mem.i32(ep + 0x58)};
                double dx = (double)pos[0] - ctx.ex->camera.position_fixed[0];
                double dy = (double)pos[1] - ctx.ex->camera.position_fixed[1];
                double dz = (double)pos[2] - ctx.ex->camera.position_fixed[2];
                double radial = sqrt(dx * dx + dy * dy + dz * dz);
                float projected = 0.0f;
                int vw = ctx.ex->camera.viewport_w > 0 ? ctx.ex->camera.viewport_w : 1024;
                int vh = ctx.ex->camera.viewport_h > 0 ? ctx.ex->camera.viewport_h : 768;
                if (ctx.ex->camera.projection_ortho) {
                    float half_h = ctx.ex->camera.ortho_half_height;
                    if (half_h < 1.0f / 65536.0f) {
                        half_h = 1.0f / 65536.0f;
                    }
                    projected = (2.0f * (float)radius / k_fixed_scale) *
                        (float)vh / half_h;
                } else {
                    float output_focal = ctx.ex->camera.focal_length_pixels *
                        (float)vh / 768.0f;
                    if (output_focal < 1.0f) {
                        output_focal = 1.0f;
                    }
                    float hfov = 2.0f * atanf((float)vw / (2.0f * output_focal));
                    float max_rad = cfg.max_horizontal_fov_degrees *
                        3.14159265f / 180.0f;
                    if (hfov > max_rad) {
                        output_focal = (float)vw / (2.0f * tanf(max_rad * 0.5f));
                    }
                    projected = 2.0f * (float)radius * output_focal /
                        (float)(radial < 1.0 ? 1.0 : radial);
                }
                float t0 = cfg.entity_lod_detail0_pixels;
                float t1 = cfg.entity_lod_detail1_pixels;
                float t2 = cfg.entity_lod_detail2_pixels;
                if (t1 > t0) {
                    t1 = t0;
                }
                if (t2 > t1) {
                    t2 = t1;
                }
                float thresholds[3] = {t0, t1, t2};
                LodHistoryKey key;
                key.pointer = ep;
                key.owner_id = owner_id;
                key.view_role = ctx.ex->camera.satellite_view ? 1 : 0;
                int previous = -1;
                auto hit = g_lod_history.find(key);
                if (hit != g_lod_history.end()) {
                    previous = hit->second;
                }
                detail = projected_detail(
                    projected, thresholds, previous, cfg.entity_lod_hysteresis);
                g_lod_history[key] = detail;
            }
            }
            entity->selected_detail = detail;
        }
        int32_t resource_id = mem.i32_rel(dbase + 0x08 + (uint32_t)detail * 4);
        if (resource_id == 0 || resource_id == DUMMY_RESOURCE_ID) {
            continue;
        }
        uint32_t blob_size = 0;
        const uint8_t *blob = wtbo_blob(resource_id, &blob_size);
        if (blob == NULL) {
            continue;
        }
        uint8_t matrix[ENTITY_MATRIX_SIZE];
        if (!mem.read(tree + ENTITY_MATRIX_OFFSET, matrix, ENTITY_MATRIX_SIZE)) {
            continue;
        }
        uint16_t node_flags = 0;
        if (installed_node != 0) {
            node_flags = mem.u16(installed_node);
            if (node_flags & 0x1000) {
                continue;
            }
        }
        ctx.node_addr = installed_node ? installed_node : (0xC0000000u + (uint32_t)di * 0x10000u);
        ctx.matrix_override = matrix;
        ctx.owner_addr = installed_node;
        const EntityMaterials materials =
            entity_materials(mem, desc_count, ep, owner_id);
        ctx.camo = materials.camo;
        ctx.emblem = materials.emblem;
        ctx.part = &ctx.ex->part[
            player ? MW2ER_PART_VIEW_EXCLUDED : MW2ER_PART_ENTITY];
        if (extract_wtbo(ctx, node_flags, resource_id, blob, blob_size)) {
            if (ctx.lod_skip && installed_node) (*ctx.lod_skip)[installed_node] = 1;
            ctx.ex->lod_node_count += 1;
        }
        ctx.matrix_override = NULL;
        ctx.owner_addr = 0;
        ctx.camo = -1;
        ctx.emblem = -1;
    }
}

/* Entity target portraits use retained detail-0 POLY and live component
 * matrices. The game-prepared target tree is the fallback. Off-screen
 * targets are included; they are not gated on the main visible node list. */
static int extract_target_owned_lod(ExtractCtx &ctx, uint32_t entity)
{
    if (entity == 0 || !mw2er_resources_have_type(MW2ER_RESOURCE_POLY)) {
        return 0;
    }
    if (MW2ER_STRICMP(mw2er_config().entity_lod_selection, "native") == 0) {
        return 0;
    }
    const Mem &mem = *ctx.mem;
    const int32_t desc_count = mem.i32_rel(ADDR_COMPONENT_DESCRIPTOR_COUNT);
    if (desc_count < 1 || desc_count > MAX_COMPONENT_DESCRIPTORS) {
        return 0;
    }
    const int32_t owner_id = mem.i32(entity + 0x04);
    int emitted = 0;
    for (int di = 0; di < desc_count; ++di) {
        const uint32_t dbase =
            ADDR_COMPONENT_DESCRIPTOR_TABLE +
            (uint32_t)di * COMPONENT_DESCRIPTOR_STRIDE;
        if (mem.i32_rel(dbase + 0x00) != owner_id) {
            continue;
        }
        const uint32_t installed_node = mem.u32_rel(dbase + 0x24);
        const uint32_t tree = mem.u32_rel(dbase + 0x28);
        if (tree == 0 || installed_node == 0) {
            continue;
        }
        const uint16_t node_flags = mem.u16(installed_node);
        if (node_flags & 0x1000) {
            continue;
        }
        const int32_t resource_id = mem.i32_rel(dbase + 0x08);
        if (resource_id == 0 || resource_id == DUMMY_RESOURCE_ID) {
            return 0;
        }
        uint32_t blob_size = 0;
        const uint8_t *blob = wtbo_blob(resource_id, &blob_size);
        if (blob == NULL) {
            return 0;
        }
        uint8_t matrix[ENTITY_MATRIX_SIZE];
        if (!mem.read(tree + ENTITY_MATRIX_OFFSET, matrix, ENTITY_MATRIX_SIZE)) {
            return 0;
        }
        ctx.node_addr = installed_node;
        ctx.matrix_override = matrix;
        ctx.owner_addr = installed_node;
        const EntityMaterials materials =
            entity_materials(mem, desc_count, entity, owner_id);
        ctx.camo = materials.camo;
        ctx.emblem = materials.emblem;
        if (extract_wtbo(ctx, node_flags, resource_id, blob, blob_size)) {
            emitted += 1;
        } else {
            return 0;
        }
        ctx.matrix_override = NULL;
        ctx.owner_addr = 0;
        ctx.camo = -1;
        ctx.emblem = -1;
    }
    return emitted > 0;
}

static void load_satellite_params(const Mem &mem, Mw2erCamera &cam)
{
    memcpy(cam.satellite_colors, k_sat_default_colors, sizeof(k_sat_default_colors));
    cam.satellite_width_fixed = 0;
    cam.satellite_shade_bias = mem.i32_rel(ADDR_SATELLITE_SHADE_BIAS);
    cam.satellite_shade_divisor = mem.i32_rel(ADDR_SATELLITE_SHADE_DIVISOR);
    if (cam.satellite_shade_divisor == 0) {
        cam.satellite_shade_divisor = 1;
    }
    uint32_t config_table = mem.u32_rel(ADDR_RADAR_CONFIG_TABLE);
    if (config_table != 0) {
        uint32_t config = mem.u32(config_table + 16);
        if (config != 0) {
            cam.satellite_width_fixed = mem.i32(config + 0x18);
            uint32_t color_config = mem.u32(config + 0x74);
            if (color_config != 0) {
                uint32_t raw[11];
                if (mem.read(color_config, raw, sizeof(raw))) {
                    memcpy(cam.satellite_colors, raw, sizeof(raw));
                }
            }
        }
    }
    cam.satellite_view = 1;
    if (cam.satellite_width_fixed <= 0) {
        return;
    }
    int32_t center[3] = {
        cam.position_fixed[0], cam.position_fixed[1], cam.position_fixed[2]};
    if (mem.i32_rel(ADDR_RADAR_CAMERA_NODE_FALLBACK) == 0) {
        int32_t player_slot = mem.i32_rel(ADDR_PLAYER_SLOT);
        uint32_t player_entity =
            mem.u32_rel(ADDR_ENTITY_BODY_TABLE + (uint32_t)player_slot * 4);
        if (player_entity) {
            uint32_t camera_node = mem.u32(player_entity + 0x44);
            if (camera_node) {
                center[0] = mem.i32(camera_node + 0x60);
                center[1] = mem.i32(camera_node + 0x64);
                center[2] = mem.i32(camera_node + 0x68);
            }
        }
    } else {
        uint32_t active = mem.u32_rel(ADDR_ACTIVE_CAMERA);
        if (active) {
            center[0] = mem.i32(active + 0);
            center[1] = mem.i32(active + 4);
            center[2] = mem.i32(active + 8);
        }
    }
    cam.position_fixed[0] = center[0];
    cam.position_fixed[1] = cam.satellite_width_fixed;
    cam.position_fixed[2] = center[2];
    cam.position[0] = (float)cam.position_fixed[0] / k_fixed_scale;
    cam.position[1] = (float)cam.position_fixed[1] / k_fixed_scale;
    cam.position[2] = (float)cam.position_fixed[2] / k_fixed_scale;
    {
        double pitch = (double)k_satellite_pitch * (6.283185307179586 / k_angle_full_turn);
        double sp = sin(pitch);
        double cp = cos(pitch);
        cam.forward[0] = 0.0f;
        cam.forward[1] = (float)-sp;
        cam.forward[2] = (float)cp;
        cam.right[0] = 1.0f;
        cam.right[1] = 0.0f;
        cam.right[2] = 0.0f;
        cam.up[0] = 0.0f;
        cam.up[1] = (float)cp;
        cam.up[2] = (float)sp;
        cam.forward_fixed[0] = (int32_t)(cam.forward[0] * k_rot_scale);
        cam.forward_fixed[1] = (int32_t)(cam.forward[1] * k_rot_scale);
        cam.forward_fixed[2] = (int32_t)(cam.forward[2] * k_rot_scale);
    }
    cam.projection_ortho = 1;
    cam.ortho_half_width =
        (float)cam.satellite_width_fixed / (2.0f * k_fixed_scale);
    /* Satellite range is authored for the 1024x768 view. Preserve that
     * vertical extent for every output; projection widens only the horizontal
     * extent on wider displays. Perspective FOV policy never applies here. */
    cam.ortho_half_height =
        cam.ortho_half_width / k_satellite_reference_aspect;
    /* These intentionally match the Python satellite view. The tight far
     * plane is important for useful depth precision; the small enhanced near
     * plane avoids clipping mountain peaks close to the overhead camera. */
    cam.near_plane = 0.001f;
    cam.far_plane = k_satellite_far;
    cam.focal_length_pixels = 1024.0f;
}

static void apply_imaging_state(Mw2erRenderView view, Mw2erCamera &cam)
{
    const int imaging = view == MW2ER_VIEW_ENHANCED || view == MW2ER_VIEW_XRAY;
    cam.imaging_active = imaging;
    cam.imaging_wireframe = imaging;
    cam.preserve_imaging_effects =
        imaging && mw2er_config().enhanced_enhanced_imaging;
    cam.imaging_fade_start = 0.0f;
    cam.imaging_fade_end = 0.0f;
    if (view != MW2ER_VIEW_ENHANCED) {
        g_imaging_started_at = -1.0;
        g_prev_imaging = 0;
        return;
    }
    double now = now_seconds();
    if (!g_prev_imaging || g_imaging_started_at < 0.0) {
        g_imaging_started_at = now;
    }
    g_prev_imaging = 1;
    float native_far = cam.far_plane;
    if (cam.far_depth_fixed > 0) {
        native_far = (float)cam.far_depth_fixed /
            (k_fixed_scale * k_native_depth_mul);
    }
    float ratio = mw2er_config().enhanced_imaging_distance_ratio;
    float target_start = native_far * ratio;
    if (target_start < 0.0f) {
        target_start = 0.0f;
    }
    float initial = cam.clip_near_plane > 0.0f ? cam.clip_near_plane : cam.near_plane;
    float reveal = mw2er_config().enhanced_imaging_reveal_time;
    float progress = 1.0f;
    if (reveal > 0.0f) {
        progress = (float)((now - g_imaging_started_at) / (double)reveal);
        if (progress < 0.0f) {
            progress = 0.0f;
        }
        if (progress > 1.0f) {
            progress = 1.0f;
        }
    }
    cam.imaging_fade_start = initial + (target_start - initial) * progress;
    cam.imaging_fade_end = cam.imaging_fade_start + k_imaging_fade_width;
}

static void update_cockpit_effects(const Mem &mem, const Mw2erCamera &cam)
{
    g_effect_node_slots.clear();
    if (cam.camera_mode != 0 || cam.satellite_view) {
        for (int i = 0; i < COLLISION_EFFECT_SLOT_COUNT; ++i) {
            g_effects[i].used = 0;
            g_effects[i].has_transform = 0;
            g_effects[i].nodes.clear();
        }
        return;
    }
    double hx = (double)cam.position_fixed[0];
    double hy = (double)cam.position_fixed[1];
    double hz = (double)cam.position_fixed[2];
    double radius = g_cockpit_radius_fixed;
    if (radius < 0.0) {
        radius = 0.0;
    }
    double radius_sq = (radius * k_cockpit_effect_margin) *
        (radius * k_cockpit_effect_margin);
    uint8_t pool[COLLISION_EFFECT_SLOT_SIZE * COLLISION_EFFECT_SLOT_COUNT];
    if (!mem.read_rel(COLLISION_EFFECT_POOL, pool, sizeof(pool))) {
        return;
    }
    for (int slot = 0; slot < COLLISION_EFFECT_SLOT_COUNT; ++slot) {
        uint8_t *rec = pool + slot * COLLISION_EFFECT_SLOT_SIZE;
        uint32_t active = load_u32(rec + 0x14);
        int32_t effect_id = active ? load_i32(rec + 0x20) : -1;
        uint32_t model_root = active ? load_u32(rec) : 0;
        if (!active || effect_id < 0 || effect_id > MAX_RELOCATED_EFFECT_ID ||
            model_root == 0 || radius_sq <= 0.0) {
            g_effects[slot].used = 0;
            g_effects[slot].has_transform = 0;
            g_effects[slot].nodes.clear();
            continue;
        }
        int32_t raw_x = load_i32(rec + 0x04);
        int32_t raw_y = load_i32(rec + 0x08);
        int32_t raw_z = load_i32(rec + 0x0C);
        double dx = (double)raw_y - hx;
        double dy = (double)raw_x - hy;
        double dz = (double)raw_z - hz;
        if (dx * dx + dy * dy + dz * dz > radius_sq) {
            g_effects[slot].used = 0;
            g_effects[slot].has_transform = 0;
            g_effects[slot].nodes.clear();
            continue;
        }
        CockpitEffectSlot &s = g_effects[slot];
        if (!s.used || s.model_root != model_root || s.effect_id != effect_id ||
            s.raw_x != raw_x || s.raw_y != raw_y || s.raw_z != raw_z) {
            s.used = 1;
            s.model_root = model_root;
            s.effect_id = effect_id;
            s.raw_x = raw_x;
            s.raw_y = raw_y;
            s.raw_z = raw_z;
            s.has_transform = 0;
            s.nodes.clear();
            uint32_t seen[MAX_MODEL_TREE_NODES];
            int sp = 0;
            int seen_n = 0;
            if (!tree_push(g_tree_pending, &sp, model_root)) {
                continue;
            }
            while (sp > 0 && seen_n < MAX_MODEL_TREE_NODES) {
                uint32_t addr = g_tree_pending[--sp];
                int dup = 0;
                for (int i = 0; i < seen_n; ++i) {
                    if (seen[i] == addr) {
                        dup = 1;
                        break;
                    }
                }
                if (dup || addr == 0) {
                    continue;
                }
                seen[seen_n++] = addr;
                uint8_t data[MODEL_TREE_NODE_READ_SIZE];
                if (!mem.read(addr, data, MODEL_TREE_NODE_READ_SIZE)) {
                    continue;
                }
                uint32_t child = load_u32(data + 0x04);
                uint32_t sibling = load_u32(data + 0x08);
                uint32_t geom = load_u32(data + 0x6C);
                if (geom) {
                    s.nodes.push_back(geom);
                }
                if (sibling && addr != model_root) {
                    tree_push(g_tree_pending, &sp, sibling);
                }
                if (child) {
                    tree_push(g_tree_pending, &sp, child);
                }
            }
        }
        for (size_t i = 0; i < s.nodes.size(); ++i) {
            g_effect_node_slots[s.nodes[i]] = slot;
        }
    }
}

static void mat_mul9(const double *a, const double *b, double *out)
{
    for (int r = 0; r < 3; ++r) {
        for (int c = 0; c < 3; ++c) {
            out[r * 3 + c] =
                a[r * 3 + 0] * b[0 * 3 + c] +
                a[r * 3 + 1] * b[1 * 3 + c] +
                a[r * 3 + 2] * b[2 * 3 + c];
        }
    }
}

static void mat_vec9(const double *m, const double *v, double *out)
{
    out[0] = m[0] * v[0] + m[1] * v[1] + m[2] * v[2];
    out[1] = m[3] * v[0] + m[4] * v[1] + m[5] * v[2];
    out[2] = m[6] * v[0] + m[7] * v[1] + m[8] * v[2];
}

static int mat_inverse9(const double *m, double *out)
{
    double a00 = m[0], a01 = m[1], a02 = m[2];
    double a10 = m[3], a11 = m[4], a12 = m[5];
    double a20 = m[6], a21 = m[7], a22 = m[8];
    double det =
        a00 * (a11 * a22 - a12 * a21) -
        a01 * (a10 * a22 - a12 * a20) +
        a02 * (a10 * a21 - a11 * a20);
    if (fabs(det) < 1e-12) {
        return 0;
    }
    double inv = 1.0 / det;
    out[0] = (a11 * a22 - a12 * a21) * inv;
    out[1] = (a02 * a21 - a01 * a22) * inv;
    out[2] = (a01 * a12 - a02 * a11) * inv;
    out[3] = (a12 * a20 - a10 * a22) * inv;
    out[4] = (a00 * a22 - a02 * a20) * inv;
    out[5] = (a02 * a10 - a00 * a12) * inv;
    out[6] = (a10 * a21 - a11 * a20) * inv;
    out[7] = (a01 * a20 - a00 * a21) * inv;
    out[8] = (a00 * a11 - a01 * a10) * inv;
    return 1;
}

static void mark_player_tree_incomplete(void)
{
    g_player_model_root = 0; /* Retry incomplete live topology next frame. */
}

static void prepare_player_model(const Mem &mem, Mw2erCamera &cam)
{
    const int smooth = cockpit_camera(cam);
    const int32_t player_slot = mem.i32_rel(ADDR_PLAYER_SLOT);
    const uint32_t entity = player_slot >= 0 && player_slot < PRIMARY_ENTITY_LIMIT
        ? mem.u32_rel(ADDR_ENTITY_BODY_TABLE + (uint32_t)player_slot * 4) : 0;
    const uint32_t mech = entity ? mem.u32(entity + 0x20) : 0;
    const uint32_t model_root = entity ? mem.u32(entity + 0x40) : 0;
    const uint32_t cockpit_root = mech ? mem.u32(mech + 0x60) : 0;
    if (!smooth && model_root == g_player_model_root &&
        cockpit_root == g_cockpit_model_root) {
        g_smooth_matrices.clear();
        g_cockpit_geometry.clear();
        g_player_tree_nodes.clear();
        return;
    }
    g_smooth_matrices.clear();
    g_player_model_root = model_root;
    g_cockpit_model_root = cockpit_root;
    g_player_geometry.clear();
    g_cockpit_geometry.clear();
    g_player_tree_nodes.clear();
    if (model_root == 0) {
        return;
    }
    if (g_player_tree_nodes.bucket_count() < (size_t)MAX_MODEL_TREE_NODES) {
        g_player_tree_nodes.reserve((size_t)MAX_MODEL_TREE_NODES);
    }
    if (g_player_geometry.bucket_count() < (size_t)MAX_MODEL_TREE_NODES) {
        g_player_geometry.reserve((size_t)MAX_MODEL_TREE_NODES);
        g_cockpit_geometry.reserve((size_t)MAX_MODEL_TREE_NODES);
    }
    const uint32_t camera_attachment = mem.u32(entity + 0x44);

    int sp = 0;
    if (!tree_push(g_tree_pending, &sp, model_root)) {
        mark_player_tree_incomplete();
        return;
    }
    while (sp > 0 && g_player_tree_nodes.size() < MAX_MODEL_TREE_NODES) {
        uint32_t addr = g_tree_pending[--sp];
        if (addr == 0 ||
            g_player_tree_nodes.find(addr) != g_player_tree_nodes.end()) {
            continue;
        }
        uint8_t data[MODEL_TREE_NODE_READ_SIZE];
        if (!mem.read(addr, data, MODEL_TREE_NODE_READ_SIZE)) {
            mark_player_tree_incomplete();
            continue;
        }
        PlayerTreeNode n{};
        n.parent = load_u32(data + 0x00);
        n.child = load_u32(data + 0x04);
        n.sibling = load_u32(data + 0x08);
        n.geometry = load_u32(data + 0x6C);
        if (n.geometry != 0) {
            g_player_geometry.insert(n.geometry);
        }
        for (int i = 0; i < 9; ++i) {
            n.local_r[i] = (double)load_i32(data + 0x0C + i * 4) / (double)k_rot_scale;
            n.native_r[i] = (double)load_i32(data + 0x3C + i * 4) / (double)k_rot_scale;
        }
        for (int i = 0; i < 3; ++i) {
            n.local_t[i] = (double)load_i32(data + 0x30 + i * 4);
            n.native_t[i] = (double)load_i32(data + 0x60 + i * 4);
        }
        const uint32_t sibling = n.sibling;
        const uint32_t child = n.child;
        g_player_tree_nodes.emplace(addr, n);
        if (sibling && addr != model_root) {
            if (!tree_push(g_tree_pending, &sp, sibling)) {
                mark_player_tree_incomplete();
            }
        }
        if (child) {
            if (!tree_push(g_tree_pending, &sp, child)) {
                mark_player_tree_incomplete();
            }
        }
    }
    if (!smooth) {
        return;
    }
    const int cockpit_found =
        cockpit_root != 0 &&
        g_player_tree_nodes.find(cockpit_root) != g_player_tree_nodes.end();
    if (!cockpit_found) {
        return;
    }

    sp = 0;
    if (!tree_push(g_tree_pending, &sp, cockpit_root)) {
        mark_player_tree_incomplete();
        return;
    }
    while (sp > 0) {
        uint32_t addr = g_tree_pending[--sp];
        auto it = g_player_tree_nodes.find(addr);
        if (addr == 0 || it == g_player_tree_nodes.end() || it->second.cockpit) {
            continue;
        }
        it->second.cockpit = 1;
        if (it->second.geometry != 0) {
            g_cockpit_geometry.insert(it->second.geometry);
        }
        uint32_t child = it->second.child;
        while (child) {
            auto cit = g_player_tree_nodes.find(child);
            if (cit == g_player_tree_nodes.end()) {
                break;
            }
            if (!tree_push(g_tree_pending, &sp, child)) {
                mark_player_tree_incomplete();
                break;
            }
            child = cit->second.sibling;
        }
    }
    for (auto &kv : g_player_tree_nodes) {
        uint32_t chain[64];
        int cn = 0;
        uint32_t a = kv.first;
        while (a && cn < 64) {
            auto it = g_player_tree_nodes.find(a);
            if (it == g_player_tree_nodes.end() || it->second.solved) {
                break;
            }
            chain[cn++] = a;
            a = it->second.parent;
        }
        while (cn > 0) {
            auto nit = g_player_tree_nodes.find(chain[--cn]);
            if (nit == g_player_tree_nodes.end() || nit->second.solved) {
                continue;
            }
            PlayerTreeNode &n = nit->second;
            auto pit = n.parent ? g_player_tree_nodes.find(n.parent)
                                : g_player_tree_nodes.end();
            if (pit != g_player_tree_nodes.end() && pit->second.solved) {
                PlayerTreeNode &p = pit->second;
                mat_mul9(p.world_r, n.local_r, n.world_r);
                double rt[3];
                mat_vec9(p.world_r, n.local_t, rt);
                n.world_t[0] = rt[0] + p.world_t[0];
                n.world_t[1] = rt[1] + p.world_t[1];
                n.world_t[2] = rt[2] + p.world_t[2];
            } else {
                memcpy(n.world_r, n.local_r, sizeof(n.world_r));
                memcpy(n.world_t, n.local_t, sizeof(n.world_t));
            }
            n.solved = 1;
        }
    }

    for (auto &kv : g_player_tree_nodes) {
        PlayerTreeNode &n = kv.second;
        if (!n.cockpit || !n.solved || n.geometry == 0) {
            continue;
        }
        SmoothMatrix &sm = g_smooth_matrices[n.geometry];
        memcpy(sm.rotation, n.world_r, sizeof(sm.rotation));
        memcpy(sm.translation, n.world_t, sizeof(sm.translation));
    }

    auto att_it = camera_attachment ? g_player_tree_nodes.find(camera_attachment)
                                    : g_player_tree_nodes.end();
    if (att_it != g_player_tree_nodes.end() && att_it->second.solved) {
        PlayerTreeNode &att = att_it->second;
        double inv_native[9];
        double corr[9];
        if (!mat_inverse9(att.native_r, inv_native)) {
            return;
        }
        mat_mul9(att.world_r, inv_native, corr);
        double pos[3] = {
            (double)cam.position_fixed[0],
            (double)cam.position_fixed[1],
            (double)cam.position_fixed[2]};
        double rel[3] = {
            pos[0] - att.native_t[0],
            pos[1] - att.native_t[1],
            pos[2] - att.native_t[2]};
        double crel[3];
        mat_vec9(corr, rel, crel);
        const double corrected[3] = {
            crel[0] + att.world_t[0],
            crel[1] + att.world_t[1],
            crel[2] + att.world_t[2]};
        cam.position[0] = (float)(corrected[0] / k_fixed_scale);
        cam.position[1] = (float)(corrected[1] / k_fixed_scale);
        cam.position[2] = (float)(corrected[2] / k_fixed_scale);
        cam.position_fixed[0] = (int32_t)llround(corrected[0]);
        cam.position_fixed[1] = (int32_t)llround(corrected[1]);
        cam.position_fixed[2] = (int32_t)llround(corrected[2]);
        rotate_basis3(cam.right, corr);
        rotate_basis3(cam.up, corr);
        rotate_basis3(cam.forward, corr);
        cam.forward_fixed[0] = (int32_t)(cam.forward[0] * k_rot_scale);
        cam.forward_fixed[1] = (int32_t)(cam.forward[1] * k_rot_scale);
        cam.forward_fixed[2] = (int32_t)(cam.forward[2] * k_rot_scale);
    }
}

int mw2er_extract_scene(const Mw2erMemoryView &view, Mw2erSceneExtract &ex,
                         int emit_geometry, Mw2erRenderView render_view)
{
    Mem mem = Mem::from(view);
    uint8_t raw_pal[768];
    uint32_t head;
    uint32_t node;
    uint32_t seen[MAX_NODES];
    int seen_n = 0;

    if (!mem.ok()) {
        mw2er_set_error("extract: bad memory view");
        return 0;
    }
    init_sqrt_table();
    memset(&ex.camera, 0, sizeof(ex.camera));
    read_camera(mem, ex.camera);
    const Mw2erViewPolicy view_policy =
        mw2er_view_policy(render_view, ex.camera.camera_mode);
    if (render_view == MW2ER_VIEW_SATELLITE) {
        load_satellite_params(mem, ex.camera);
    }
    if (!view_policy.auxiliary) {
        apply_imaging_state(render_view, ex.camera);
        prepare_player_model(mem, ex.camera);
        g_cockpit_radius_fixed = 0.0;
        update_cockpit_effects(mem, ex.camera);
    }
    char mission_name[MISSION_NAME_MAX_BYTES]{};
    {
        uint8_t raw_name[MISSION_NAME_MAX_BYTES];
        if (mem.read_rel(ADDR_MISSION_NAME, raw_name, MISSION_NAME_MAX_BYTES)) {
            for (int ni = 0; ni < MISSION_NAME_MAX_BYTES - 1; ++ni) {
                int v = raw_name[ni];
                if (v == 0 || v <= 0x20 || v > 0x7E) {
                    break;
                }
                mission_name[ni] = (char)v;
            }
        }
    }
    {
        int reuse = ex.static_policy == static_policy_key(ex) &&
            memcmp(ex.mission_name, mission_name, sizeof(mission_name)) == 0;
        memcpy(ex.mission_name, mission_name, sizeof(mission_name));
        for (int i = 0; i < MW2ER_PART_COUNT; ++i) {
            if (i == MW2ER_PART_STATIC && reuse) {
                continue;
            }
            mw2er_partition_reset(ex.part[i]);
        }
        if (!reuse) {
            ex.static_block_ids.clear();
            ex.static_policy = UINT64_MAX;
        }
        ex.static_reused = reuse;
        ex.node_count = 0;
        ex.tree_node_count = 0;
        ex.lod_node_count = 0;
    }
    read_lighting_state(mem, ex.lighting);
    if (ex.camera.satellite_view) {
        ex.lighting.fog_distance = 0;
        ex.lighting.fog_distance_world = 0.0f;
    }
    if (!mem.read_rel(ADDR_PALETTE, raw_pal, 768)) {
        memset(raw_pal, 0, sizeof(raw_pal));
    }
    for (int i = 0; i < 768; ++i) {
        uint8_t dac = (uint8_t)(raw_pal[i] & 0x3F);
        ex.palette_rgb[i] = (uint8_t)(((int)dac * 255) / 63);
    }
    ex.sky_palette_index = mem.u8_rel(ADDR_SKY_PALETTE_INDEX);
    ex.ground_palette_index = mem.u8_rel(ADDR_GROUND_PALETTE_INDEX);
    ex.sky_visible = mem.u32_rel(ADDR_SKY_VISIBLE) != 0;
    ex.ground_visible = mem.u32_rel(ADDR_GROUND_VISIBLE) != 0;
    ex.gradient_height = mem.u32_rel(ADDR_GRADIENT_HEIGHT);
    ex.draw_gradient =
        ex.sky_visible &&
        mem.u32_rel(ADDR_GRADIENT_ENABLE) != 0 &&
        mem.u32_rel(ADDR_GRADIENT_BAND_ENABLE) != 0 &&
        ex.gradient_height > 0 &&
        ex.ground_palette_index > ex.sky_palette_index;
    {
        int gi = ex.ground_palette_index;
        if (gi > 255) {
            gi = 255;
        }
        ex.ground_color[0] = ex.palette_rgb[gi * 3 + 0] / 255.0f;
        ex.ground_color[1] = ex.palette_rgb[gi * 3 + 1] / 255.0f;
        ex.ground_color[2] = ex.palette_rgb[gi * 3 + 2] / 255.0f;
    }
    if (!ex.ground_visible) {
        ex.ground_color[0] = 0.0f;
        ex.ground_color[1] = 0.0f;
        ex.ground_color[2] = 0.0f;
    }

    if (!emit_geometry) {
        return 1;
    }

    static std::vector<uint32_t> emitted;
    static std::unordered_map<uint32_t, int> component_policy;
    static std::unordered_map<uint32_t, uint8_t> lod_skip;
    emitted.clear();
    component_policy.clear();
    lod_skip.clear();
    ExtractCtx ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.mem = &mem;
    ctx.ex = &ex;
    ctx.source = SRC_PRIMARY;
    ctx.node_addr = 0;
    ctx.camo = -1;
    ctx.emblem = -1;
    ctx.owner_addr = 0;
    ctx.matrix_override = NULL;
    ctx.emitted = &emitted;
    ctx.component_policy = &component_policy;
    ctx.lod_skip = &lod_skip;
    ctx.reuse_static = ex.static_reused;
    ctx.auxiliary = view_policy.auxiliary;
    ctx.part = &ex.part[MW2ER_PART_SCENE];

    head = mem.u32_rel(ADDR_NODE_LIST_HEADER + 0x08);
    node = head;
    while (node != 0 && seen_n < MAX_NODES) {
        int duplicate = 0;
        for (int i = 0; i < seen_n; ++i) {
            if (seen[i] == node) {
                duplicate = 1;
                break;
            }
        }
        if (duplicate) {
            break;
        }
        seen[seen_n++] = node;
        node = mem.u32(node + 0x08);
    }
    ctx.visible_nodes = seen;
    ctx.visible_node_count = seen_n;
    /* Renderer-selected LOD extraction has entity-specific material, owner,
       transform, source, and partition state. Keep it out of the primary
       traversal's context. */
    ExtractCtx lod_ctx = ctx;
    extract_renderer_lod(lod_ctx);

    for (int seen_index = 0; seen_index < seen_n; ++seen_index) {
        uint8_t nb[NODE_SIZE];
        node = seen[seen_index];
        if (!mem.read(node, nb, NODE_SIZE)) {
            continue;
        }
        uint32_t flags = load_u32(nb + 0x00);
        uint32_t entity = load_u32(nb + 0x18);
        uint32_t block = load_u32(nb + 0x1C);
        ctx.node_addr = node;
        extract_block(ctx, flags, entity, block);
        ex.node_count += 1;
    }

    ctx.source = SRC_MODEL_TREE;
    ctx.node_addr = 0;
    ctx.matrix_override = NULL;
    walk_model_tree(ctx, mem.u32_rel(ADDR_MODEL_TREE_ROOT_A), MAX_MODEL_TREE_NODES);
    walk_model_tree(ctx, mem.u32_rel(ADDR_MODEL_TREE_ROOT_B), MAX_MODEL_TREE_NODES);

    ex.static_policy = static_policy_key(ex);
    if (!view_policy.auxiliary) {
        g_prev_cockpit_far_fixed = g_cockpit_radius_fixed;
    }
    return 1;
}

int mw2er_extract_target(const Mw2erMemoryView &view, Mw2erSceneExtract &ex,
                         uint32_t root, uint32_t entity, int display_mode,
                         const Mw2erCamera &camera)
{
    Mem mem = Mem::from(view);
    if (!mem.ok() || root == 0 || (display_mode != 1 && display_mode != 2)) {
        return 0;
    }
    mw2er_partition_reset(ex.part[MW2ER_PART_TARGET]);
    static std::vector<uint32_t> emitted;
    emitted.clear();
    ExtractCtx ctx{};
    ctx.mem = &mem;
    ctx.ex = &ex;
    ctx.part = &ex.part[MW2ER_PART_TARGET];
    ctx.source = SRC_TARGET;
    ctx.camo = -1;
    ctx.emblem = -1;
    /* Selector 1 remains the damage wireframe. Selector 2 is the native
     * target-local flat-lit solid portrait. */
    ctx.target_flat = display_mode == 2;
    ctx.emitted = &emitted;

    const Mw2erCamera saved = ex.camera;
    const Mw2erLighting saved_lighting = ex.lighting;
    ex.camera = camera;
    ex.camera.satellite_view = 0;
    /* Match Python's target-hook snapshot. These globals may be prepared for
     * the target helper after the primary scene was captured. */
    read_lighting_state(mem, ex.lighting);
    if (!extract_target_owned_lod(ctx, entity)) {
        // Discard partial replacement output before using the complete native tree.
        mw2er_partition_reset(ex.part[MW2ER_PART_TARGET]);
        ctx.matrix_override = NULL;
        ctx.owner_addr = 0;
        ctx.camo = ctx.emblem = -1;
        walk_model_tree(ctx, root, MAX_MODEL_TREE_NODES);
    }
    ex.camera = saved;
    ex.lighting = saved_lighting;
    return 1;
}
