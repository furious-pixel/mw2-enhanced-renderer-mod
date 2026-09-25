#ifndef MW2ER_SCENE_EXTRACT_H
#define MW2ER_SCENE_EXTRACT_H

#include "mw2er_abi.h"
#include "texture.h"
#include "vert_stream.h"

#include <stdint.h>
#include <unordered_set>
#include <utility>
#include <vector>

enum { MW2ER_ROTOR_HELI = 0, MW2ER_ROTOR_FAN = 1 };

struct Mw2erCamera {
    float position[3];
    int32_t position_fixed[3];
    float right[3];
    float up[3];
    float forward[3];
    int32_t forward_fixed[3];
    float focal_length_pixels;
    int pane_projection;
    float projection_aspect_scale;
    float projection_center_x;
    float projection_center_y;
    int32_t camera_mode;
    float near_plane;
    float far_plane;
    float clip_near_plane;
    int32_t far_depth_fixed;
    int satellite_view;
    int projection_ortho;
    float ortho_half_width;
    float ortho_half_height;
    uint32_t satellite_colors[11];
    int32_t satellite_width_fixed;
    int32_t satellite_shade_bias;
    int32_t satellite_shade_divisor;
    int imaging_active;
    int imaging_wireframe;
    int preserve_imaging_effects;
    float imaging_fade_start;
    float imaging_fade_end;
    int viewport_w;
    int viewport_h;
};

struct Mw2erLighting {
    int32_t light[3];
    int directional;
    int32_t ambient;
    int32_t fog_distance;
    float fog_distance_world;
    int32_t component_lighting_mode;
};

struct Mw2erRotorDraw {
    int desc;
    int effect;
    int normalized_uv;
    int outline_only;
    uint32_t aero_mesh;
    float center[3];
    /* Helicopter discs reference the canonical unit circle. These two
     * world-space vectors include radius as well as orientation. */
    float axis_u[3];
    float axis_v[3];
    float lighting;
    float outline_palette;
};

struct Mw2erAeroFanMesh {
    VertStream verts; /* x y z u v */
    IndexStream indices;
    VertStream primitive_lighting; /* 1 float per triangle */
};

struct Mw2erDescStreams {
    int desc;
    IndexStream texmap_indices;
    VertStream texmap_lighting;
    VertStream billboards;
};

enum {
    MW2ER_PART_STATIC = 0,
    MW2ER_PART_SCENE = 1,
    MW2ER_PART_ENTITY = 2,
    MW2ER_PART_COCKPIT = 3,
    MW2ER_PART_VIEW_EXCLUDED = 4,
    MW2ER_PART_TARGET = 5,
    MW2ER_PART_COUNT = 6
};

enum Mw2erRenderView {
    MW2ER_VIEW_NONE = 0,
    MW2ER_VIEW_NORMAL,
    MW2ER_VIEW_ENHANCED,
    MW2ER_VIEW_XRAY,
    MW2ER_VIEW_SATELLITE,
    MW2ER_VIEW_MFD_REAR,
    MW2ER_VIEW_MFD_DOWN,
    MW2ER_VIEW_MFD_WEAPON
};

enum Mw2erExtractionType {
    MW2ER_EXTRACT_ORDINARY = 1,
    MW2ER_EXTRACT_IMAGING = 2,
    MW2ER_EXTRACT_SATELLITE = 4
};

struct Mw2erViewPolicy {
    Mw2erExtractionType extraction;
    uint32_t part_mask;
    int auxiliary;
    int depth_test;
};

/* One GPU owner. Static is retained until policy/mission changes.
 *
 * Descriptor streams are sparse geometry groups keyed by the game's
 * 512-slot descriptor table (not CEL files; those are global). Same desc in
 * static vs scene is different verts/lighting, so grouping is per partition.
 * Records persist across resets so their nested vector capacity is reused;
 * used_desc[] is the current live subset. GL textures are global. */
struct Mw2erGeomPartition {
    VertStream tris; /* mode4: x y z c_in lighting_state */
    VertStream flats; /* satellite solid: x y z palette */
    VertStream indexed_flat_verts; /* x y z */
    IndexStream indexed_flat_indices;
    VertStream indexed_flat_palette;
    VertStream texmap_verts; /* x y z u v, shared */
    std::vector<Mw2erDescStreams> desc_streams;
    uint16_t desc_stream_slot[MW2ER_MAX_DESC]{}; /* record index + 1; 0 = absent */
    VertStream wire_verts;
    IndexStream wire_occ_indices;
    IndexStream wire_line_indices;
    VertStream wire_line_palette;
    VertStream points;
    VertStream lines;
    std::vector<Mw2erRotorDraw> rotor_draws;
    std::vector<Mw2erAeroFanMesh> aero_fans;
    uint8_t desc_needed[MW2ER_MAX_DESC]{};
    uint16_t used_desc[MW2ER_MAX_DESC]{};
    int used_desc_n = 0;
    uint32_t tri_count = 0;
    uint32_t flat_count = 0;
    uint32_t point_count = 0;
    uint32_t line_count = 0;

    Mw2erDescStreams *find_desc_streams(int desc)
    {
        if (desc < 0 || desc >= MW2ER_MAX_DESC) {
            return nullptr;
        }
        uint16_t slot = desc_stream_slot[desc];
        return slot != 0 ? &desc_streams[(size_t)slot - 1] : nullptr;
    }

    Mw2erDescStreams *ensure_desc_streams(int desc)
    {
        if (desc < 0 || desc >= MW2ER_MAX_DESC) {
            return nullptr;
        }
        uint16_t &slot = desc_stream_slot[desc];
        if (slot == 0) {
            Mw2erDescStreams streams{};
            streams.desc = desc;
            desc_streams.push_back(std::move(streams));
            slot = (uint16_t)desc_streams.size();
        }
        return &desc_streams[(size_t)slot - 1];
    }
};

struct Mw2erSceneExtract {
    uint8_t palette_rgb[768];
    uint8_t sky_palette_index;
    uint8_t ground_palette_index;
    int sky_visible;
    int ground_visible;
    int draw_gradient;
    uint32_t gradient_height;
    float ground_color[3];
    Mw2erCamera camera;
    char mission_name[32];
    Mw2erLighting lighting;
    Mw2erGeomPartition part[MW2ER_PART_COUNT];
    uint64_t static_policy = UINT64_MAX;
    std::unordered_set<uint32_t> static_block_ids;
    int static_reused; /* 1 = CPU static partition kept, skip static GPU upload */
    uint32_t node_count;
    uint32_t tree_node_count;
    uint32_t lod_node_count;
};

void mw2er_partition_reset(Mw2erGeomPartition &p);

void mw2er_extract_reset(Mw2erSceneExtract &ex);
void mw2er_extract_free(Mw2erSceneExtract &ex);
int mw2er_extract_scene(const Mw2erMemoryView &view, Mw2erSceneExtract &ex,
                         int emit_geometry, Mw2erRenderView render_view);
int mw2er_extract_target(const Mw2erMemoryView &view, Mw2erSceneExtract &ex,
                         uint32_t root, uint32_t entity, int display_mode,
                         const Mw2erCamera &camera);
void mw2er_extract_mission_reset(void);
Mw2erRenderView mw2er_primary_render_view(const Mw2erMemoryView &view);
Mw2erViewPolicy mw2er_view_policy(Mw2erRenderView view, int camera_mode);

#endif
