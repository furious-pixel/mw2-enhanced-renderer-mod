import math
import struct
from array import array
from dataclasses import dataclass, field
from functools import partial

import numpy as np

from .geometry_debug import _empty_stats
from .mesh_plan import MeshPlan
from .entity_components import (
    EMBLEM_TEXTURE_SOURCE_INDEX,
    HIDDEN_DETAIL,
    select_entity_lod_details,
    snapshot_entity_component_frame,
)
from .entity_lod_assets import (
    DUMMY_RESOURCE_ID,
    WTBO_HEADER_SIZE,
    WTBO_VERTEX_STRIDE,
    wtbo_face_stride,
)
from .object_lod import (
    resolve_component_descriptor_index,
    snapshot_component_descriptors,
    snapshot_component_installed_nodes,
)
from .texture import _read_indexed_textures
from .terrain_gap_reduction import terrain_level_deltas
from .cockpit_effects import (
    cockpit_bounds_fixed,
    relocate_cockpit_effect_vertices,
)
from .geometry_numba import (
    analyze_mode57_faces,
    build_mode57_desc_offsets,
    assign_mode57_vertex_offsets,
    count_mode57_block_desc_entries,
    decode_geometry_vertex_asset,
    fill_mode57_block_desc_entries,
    fill_mode57_grouped_indices_and_lighting,
    fill_mode57_grouped_vertices,
    fill_mode57_shared_indices_and_lighting,
    fill_mode57_shared_vertices,
    update_mode57_grouped_lighting,
    update_mode57_grouped_vertices,
    update_mode57_shared_lighting,
    update_mode57_shared_vertices,
    update_mode4_vertices,
    update_indexed_flat_palettes,
    update_indexed_flat_vertices,
    build_face_triangle_offsets,
    build_wireframe_offsets,
    fill_indexed_flat_indices_and_palettes,
    fill_indexed_flat_vertices,
    fill_indexed_wireframe_buffers,
    fill_mode3_billboard_instances,
    fill_mode4_vertices,
    fill_satellite_mode4_vertices_batched,
    fill_wireframe_face_palettes,
    face_source_headers_equal,
    refresh_deferred_face_normals,
    transform_geometry_vertex_asset,
    transform_geometry_face_normals,
)


ADDR_NODE_LIST_HEADER = 0x00104580
ADDR_MODEL_TREE_ROOT_A = 0x000A55B0
ADDR_MODEL_TREE_ROOT_B = 0x000A55B4
ADDR_SCENE_LIGHT_Z = 0x0015FFD0
ADDR_SCENE_LIGHT_IS_DIRECTIONAL = 0x0015FDC8
ADDR_AMBIENT = 0x0015FDCC
ADDR_SCENE_LIGHT_X = 0x0015FFD8
ADDR_SCENE_LIGHT_Y = 0x0015FFDC
ADDR_FOG_DISTANCE = 0x000A7130
ADDR_COMPONENT_LIGHTING_MODE = 0x000A6F88
ADDR_ENTITY_BODY_TABLE = 0x00108B00
ADDR_ENTITY_COUNT = 0x000A6270
ADDR_PRIMARY_CLASSIFICATION = 0x0010B631
ADDR_SECONDARY_CLASSIFICATION = 0x0010C6C4
ADDR_SECONDARY_IFF_REFERENCES = 0x00104B8C

HEADER_SIZE = 0x18
BLOCK_VERTEX_LAYOUT_SIZE = 0x0C
NODE_SIZE = 0x20
GEOMETRY_NODE_SIZE = 0x24
MODEL_TREE_NODE_READ_SIZE = 0x70
VERTEX_STRIDE = 0x2C
FACE_STRIDE = 0x24
ENTITY_MATRIX_OFFSET = 0x3C
ENTITY_TRANSLATION_OFFSET = 0x24
ENTITY_MATRIX_SIZE = ENTITY_TRANSLATION_OFFSET + 12
NODE_FLAG_TERRAIN_HI = 0x08
MAX_NODES = 1000
MAX_MODEL_TREE_NODES = 1000
MAX_VERTICES = 1000
MAX_FACES = 1000
MAX_FACE_VERTICES = 8
MAX_FACE_DATA_OFFSET = 0x20000
MAX_FACE_INDEX_SPAN = 0x20000
MAX_TOPOLOGY_CACHE_ENTRIES = 8192
STABLE_TOPOLOGY_VOLATILE_THRESHOLD = 2
STABLE_TOPOLOGY_VOLATILE_PROBE_INTERVAL = 256
MODE_FLAT_UNLIT = 0
MODE_FLAT_LIT = 1
MODE_POLYLINE = 2
MODE_BILLBOARD = 3
MODE_ILLUMINATE = 4
MODE_TEXTURED_PERSPECTIVE = 5
MODE_TEXTURED_PREPROJECTED = 6
MODE_TEXTURED_AFFINE = 7
MODE_SATELLITE_WIREFRAME = 8
MODE_SATELLITE_SOLID = 9
TEXTURED_FACE_MODES = frozenset(
    (
        MODE_TEXTURED_PERSPECTIVE,
        MODE_TEXTURED_PREPROJECTED,
        MODE_TEXTURED_AFFINE,
    )
)
DEFERRED_PACKAGED_FACE_MODES = frozenset(
    (
        MODE_FLAT_UNLIT,
        MODE_FLAT_LIT,
        MODE_BILLBOARD,
        MODE_ILLUMINATE,
        MODE_TEXTURED_PERSPECTIVE,
        MODE_TEXTURED_PREPROJECTED,
        MODE_TEXTURED_AFFINE,
    )
)
SUPPORTED_FACE_MODES = (
    MODE_FLAT_UNLIT,
    MODE_FLAT_LIT,
    MODE_POLYLINE,
    MODE_BILLBOARD,
    MODE_ILLUMINATE,
    MODE_TEXTURED_PERSPECTIVE,
    MODE_TEXTURED_PREPROJECTED,
    MODE_TEXTURED_AFFINE,
    MODE_SATELLITE_WIREFRAME,
    MODE_SATELLITE_SOLID,
)
ENTITY_VERTEX_MODE_MATRIX = "matrix"
ENTITY_VERTEX_MODE_CACHED = "cached"
GEOMETRY_SOURCE_PRIMARY = 0
GEOMETRY_SOURCE_MODEL_TREE = 1
GEOMETRY_SOURCE_TARGET_TREE = 2
GEOMETRY_SOURCE_RENDERER_LOD = 3
MODE4_EMISSIVE_C_IN_THRESHOLD = 0x30
STATIC_CACHE_SCHEMA = 9
WIREFRAME_DEFAULT_PALETTE_INDEX = 0x08
TARGET_FLAT_MATERIAL_CLASS_MASK = 0x0B00
TARGET_MECH_LIGHTING_SCALE = 0xA0
TARGET_OTHER_LIGHTING_SCALE = 0xD0
PRIMARY_CLASSIFICATION_SLOTS = 16
PRIMARY_CLASSIFICATION_STRIDE = 0x26
PRIMARY_ENTITY_LIMIT = 4096
SATELLITE_DEFAULT_COLORS = (
    0x0E, 0x0A, 0x06,
    0x0F, 0x0B, 0xF5,
    0x02, 0x03, 0xF9, 0xFF,
    0xF0,
)

FIXED_16_16_SCALE = 65536.0
ROTATION_FIXED_SCALE = 1 << 29
INDEXED_GEOMETRY_VERTEX_FLOATS = 3
MODE4_VERTEX_FLOATS = 5
BILLBOARD_INSTANCE_FLOATS = 7
INDEXED_TEXMAP_VERTEX_FLOATS = 5
SQRT_TABLE = tuple(max(1, int(1024 * math.sqrt(max(1, index)))) for index in range(1024))
MODE57_DESC_TABLE_SIZE = 0x200
POLY_VERTEX_DTYPE = np.dtype(
    [("position", "<i4", (3,)), ("uv", "<i2", (2,))]
)
SATELLITE_NO_FOG_DISTANCE = 1 << 60
CONTINUOUS_LIGHTING_POLICY_PACK_SCALE = 4096.0
ROTOR_DISC_SOURCE_VERTICES = 14
ROTOR_DISC_SOURCE_FACES = 2
ROTOR_DISC_SOURCE_FACE_VERTICES = 7
ROTOR_DISC_SEGMENTS = 64
ROTOR_DISC_UV_RADIUS = 0.49
AERO_LIFT_FAN_DESC_INDEX = 0x128

DYNAMIC_PARTITION_SCENE = "scene"
DYNAMIC_PARTITION_ENTITY = "entity"
DYNAMIC_PARTITION_VIEW_EXCLUDED = "view_excluded"
DYNAMIC_PARTITION_COCKPIT = "cockpit"
DYNAMIC_PARTITION_NAMES = (
    DYNAMIC_PARTITION_SCENE,
    DYNAMIC_PARTITION_ENTITY,
    DYNAMIC_PARTITION_VIEW_EXCLUDED,
    DYNAMIC_PARTITION_COCKPIT,
)


_FLOAT_ARRAY = partial(array, "f")
_UINT16_ARRAY = partial(array, "H")
_UINT32_ARRAY = partial(array, "I")


@dataclass(slots=True, eq=False, repr=False)
class GeometryPartition:
    """One static or dynamic geometry output partition."""

    vertices: array = field(default_factory=_FLOAT_ARRAY)
    mode4_vertices: array = field(default_factory=_FLOAT_ARRAY)
    point_vertices: array = field(default_factory=_FLOAT_ARRAY)
    wireframe_indexed_vertices: array = field(default_factory=_FLOAT_ARRAY)
    wireframe_occluder_indices: array = field(default_factory=_UINT32_ARRAY)
    wireframe_line_indices: array = field(default_factory=_UINT32_ARRAY)
    wireframe_line_palette: array = field(default_factory=_FLOAT_ARRAY)
    line_vertices: array = field(default_factory=_FLOAT_ARRAY)
    indexed_vertices: array = field(default_factory=_FLOAT_ARRAY)
    indexed_indices: array = field(default_factory=_UINT16_ARRAY)
    indexed_primitive_palette: array = field(default_factory=_FLOAT_ARRAY)
    billboard_instances: dict = field(default_factory=dict)
    indexed_texmap_shared_vertices: array = field(default_factory=_FLOAT_ARRAY)
    indexed_texmap_vertices: dict = field(default_factory=dict)
    indexed_texmap_indices: dict = field(default_factory=dict)
    indexed_texmap_primitive_lighting: dict = field(default_factory=dict)
    rotor_batches: list = field(default_factory=list)

@dataclass(slots=True, eq=False, repr=False)
class DynamicGeometryBuildPartition:
    """Geometry buffers and deferred batches for one dynamic draw set."""

    batch_cache: object = None
    geometry: GeometryPartition = field(default_factory=GeometryPartition)
    indexed_flat: list = field(default_factory=list)
    mode4: list = field(default_factory=list)
    satellite_mode4: list = field(default_factory=list)
    mode57: list = field(default_factory=list)
    wireframe: list = field(default_factory=list)


class GeometryBuildState:
    __slots__ = (
        "camera",
        "lighting",
        "stats",
        "static_build",
        "dynamic_partitions",
        "node_partitions",
        "texture_requests",
        "static_signature",
        "emitted_blocks",
        "entity_vertex_mode",
        "static_cache",
        "static_cache_key",
        "static_cache_hit",
        "static_block_ids",
        "topology_cache",
        "topology_volatility",
        "texture_cache",
        "cache_static_final",
        "build_wireframe",
        "wireframe_only",
        "enhanced_wireframe_only",
        "cached_enhanced_wireframe",
        "preserve_enhanced_imaging_effects",
        "enhanced_imaging_effect_descriptors",
        "discovered_enhanced_imaging_effect_descriptors",
        "flat_textured_faces",
        "satellite_view",
        "excluded_node_addrs",
        "included_node_addrs",
        "satellite_iff_cache",
        "target_owner_class_cache",
        "wireframe_owner_palette_cache",
        "component_lighting_cache",
        "terrain_block_deltas",
        "lod_descriptor_snapshot",
        "entity_lod_store",
        "entity_component_frame",
        "renderer_entity_instances",
        "cockpit_effect_tracker",
        "previous_cockpit_far_depth_fixed",
        "cockpit_far_depth_fixed",
        "cockpit_radius_fixed",
    )

    def __init__(
        self,
        camera,
        lighting,
        entity_vertex_mode,
        static_cache=None,
        topology_cache=None,
        texture_cache=None,
        cache_static_final=False,
        build_wireframe=False,
        wireframe_only=False,
        flat_textured_faces=False,
        enhanced_wireframe_only=False,
        dynamic_batch_cache=None,
        topology_volatility=None,
        preserve_enhanced_imaging_effects=False,
        enhanced_imaging_effect_descriptors=None,
        entity_lod_store=None,
    ):
        self.camera = camera
        self.lighting = lighting
        self.stats = _empty_stats()
        self.static_build = DynamicGeometryBuildPartition()
        self.dynamic_partitions = {
            name: DynamicGeometryBuildPartition(
                (
                    dynamic_batch_cache.setdefault(name, {})
                    if dynamic_batch_cache is not None
                    else None
                )
            )
            for name in DYNAMIC_PARTITION_NAMES
        }
        self.texture_requests = set()
        self.static_signature = []
        self.emitted_blocks = set()
        self.entity_vertex_mode = entity_vertex_mode
        self.static_cache = static_cache
        self.topology_cache = topology_cache
        self.topology_volatility = topology_volatility
        self.texture_cache = texture_cache
        self.cache_static_final = bool(cache_static_final and static_cache is not None)
        self.build_wireframe = bool(build_wireframe)
        self.wireframe_only = bool(wireframe_only)
        self.enhanced_wireframe_only = bool(enhanced_wireframe_only)
        self.flat_textured_faces = bool(flat_textured_faces)
        self.satellite_view = bool(camera.get("satellite_view", False))
        self.excluded_node_addrs = frozenset(
            int(node_addr)
            for node_addr in camera.get("excluded_node_addrs", ())
            if int(node_addr) != 0
        )
        self.included_node_addrs = frozenset(
            int(node_addr)
            for node_addr in camera.get("included_node_addrs", ())
            if int(node_addr) != 0
        )
        self.node_partitions = {
            int(node_addr): DYNAMIC_PARTITION_VIEW_EXCLUDED
            for node_addr in camera.get("view_excluded_node_addrs", ())
            if int(node_addr) != 0
        }
        self.node_partitions.update(
            (
                int(node_addr),
                DYNAMIC_PARTITION_COCKPIT,
            )
            for node_addr in camera.get("cockpit_node_addrs", ())
            if int(node_addr) != 0
        )
        # Indexed enhanced imaging has a cached batched wireframe path. Whether
        # ordinary geometry is also retained for an MFD camera is independent.
        self.cached_enhanced_wireframe = bool(
            self.build_wireframe
            and not self.satellite_view
        )
        self.preserve_enhanced_imaging_effects = bool(
            preserve_enhanced_imaging_effects
            and self.cached_enhanced_wireframe
        )
        self.enhanced_imaging_effect_descriptors = frozenset(
            enhanced_imaging_effect_descriptors or ()
        )
        self.static_cache_key = (
            STATIC_CACHE_SCHEMA,
            bool(self.build_wireframe),
            bool(self.wireframe_only),
            bool(self.enhanced_wireframe_only),
            bool(preserve_enhanced_imaging_effects),
            tuple(sorted(self.enhanced_imaging_effect_descriptors)),
            bool(self.satellite_view),
            tuple(camera.get("satellite_primitive_gates", (True, True))),
        )
        self.static_cache_hit = False
        self.static_block_ids = set()
        if self.cache_static_final:
            cached_static = self.static_cache.get(self.static_cache_key)
            if cached_static is not None:
                static, texture_requests, block_ids = cached_static
                self.static_build.geometry = static
                self.texture_requests.update(texture_requests)
                self.static_block_ids.update(block_ids)
                self.static_cache_hit = True
            self.static_signature.append(self.static_cache_key)
        self.discovered_enhanced_imaging_effect_descriptors = (
            set()
            if enhanced_imaging_effect_descriptors is not None
            else None
        )
        self.satellite_iff_cache = {}
        self.target_owner_class_cache = {}
        self.wireframe_owner_palette_cache = {}
        self.component_lighting_cache = {}
        self.terrain_block_deltas = (
            terrain_level_deltas(camera.get("mission_name"))
            if camera.get("reduce_terrain_gaps")
            else None
        )
        self.lod_descriptor_snapshot = None
        self.entity_lod_store = entity_lod_store
        self.entity_component_frame = None
        self.renderer_entity_instances = ()
        self.cockpit_effect_tracker = camera.get("cockpit_effect_tracker")
        self.previous_cockpit_far_depth_fixed = float(
            camera.get("previous_cockpit_far_depth_fixed", 0.0)
        )
        self.cockpit_far_depth_fixed = 0.0
        self.cockpit_radius_fixed = 0.0


def _new_geometry_state(gamemem, camera, entity_vertex_mode, **options):
    entity_vertex_mode = (
        ENTITY_VERTEX_MODE_CACHED
        if str(entity_vertex_mode).strip().lower() == ENTITY_VERTEX_MODE_CACHED
        else ENTITY_VERTEX_MODE_MATRIX
    )
    state = GeometryBuildState(
        camera,
        _read_lighting_state(gamemem, camera),
        entity_vertex_mode,
        **options,
    )
    return state


def extract_geometry(
    gamemem,
    camera,
    *,
    entity_vertex_mode,
    **options,
):
    state = _new_geometry_state(
        gamemem,
        camera,
        entity_vertex_mode,
        **options,
    )
    _snapshot_component_descriptor_state(gamemem, state)

    if not _extract_primary_geometry_source(gamemem, state):
        return _build_geometry_result(state)

    _extract_secondary_geometry_sources(gamemem, state)

    if state.renderer_entity_instances:
        _extract_renderer_entity_lods(gamemem, state)

    return _finish_geometry_extraction(gamemem, camera, state)


def extract_renderer_entity_lod_view(
    gamemem,
    camera,
    entity_lod_store,
    texture_cache=None,
):
    """Build one view-specific entity partition from the shared frame snapshot."""
    view_key = str(camera.get("entity_lod_view_key", "auxiliary"))
    view_batch_cache = entity_lod_store.view_batch_caches.setdefault(
        view_key,
        {},
    )
    state = _new_geometry_state(
        gamemem,
        camera,
        ENTITY_VERTEX_MODE_MATRIX,
        texture_cache=texture_cache,
        cache_static_final=False,
        build_wireframe=bool(
            camera.get("entity_lod_build_wireframe", False)
        ),
        wireframe_only=bool(
            camera.get("entity_lod_wireframe_only", False)
        ),
        flat_textured_faces=bool(
            camera.get("entity_lod_flat_textured_faces", False)
        ),
        dynamic_batch_cache=view_batch_cache,
        entity_lod_store=entity_lod_store,
    )
    if _activate_entity_lod(gamemem, state, entity_lod_store.frame):
        _extract_renderer_entity_lods(gamemem, state)
    result = _finish_geometry_extraction(gamemem, camera, state)
    result["primary_partition"] = DYNAMIC_PARTITION_ENTITY
    return result


def _activate_entity_lod(gamemem, state, frame):
    if frame is None:
        return False
    if not select_entity_lod_details(
        gamemem,
        frame,
        state.entity_lod_store,
        state.camera,
        state.stats,
    ):
        return False
    state.entity_component_frame = frame
    state.renderer_entity_instances = _prepare_renderer_entity_lod_instances(
        gamemem,
        state,
    )
    return bool(state.renderer_entity_instances)


def _snapshot_component_descriptor_state(gamemem, state):
    snapshot = snapshot_component_descriptors(gamemem)
    state.lod_descriptor_snapshot = snapshot
    entity_lod = state.entity_lod_store
    if (
        entity_lod is not None
        and entity_lod.state == "READY"
        and state.camera["entity_lod_selection"] != "native"
        and entity_lod.frame is not None
    ):
        frame = snapshot_entity_component_frame(
            gamemem,
            snapshot,
            entity_lod.frame,
        )
        _activate_entity_lod(gamemem, state, frame)


def _extract_primary_geometry_source(gamemem, state):
    head = gamemem.read_reloc_u32(ADDR_NODE_LIST_HEADER + 0x08)

    seen = set()
    node_addr = head
    while node_addr and node_addr not in seen and len(seen) < MAX_NODES:
        seen.add(node_addr)
        node_bytes = gamemem.read_runtime_bytes(node_addr, NODE_SIZE)
        next_addr = _u32(node_bytes, 0x08)
        _extract_node(
            gamemem,
            state,
            node_addr,
            node_bytes,
            GEOMETRY_SOURCE_PRIMARY,
        )

        node_addr = next_addr

    return True


def extract_target_geometry(
    gamemem,
    camera,
    model_root,
    *,
    entity_vertex_mode,
    topology_cache=None,
    texture_cache=None,
    build_wireframe=False,
    wireframe_only=False,
    flat_textured_faces=False,
):
    state = _new_geometry_state(
        gamemem,
        camera,
        entity_vertex_mode,
        topology_cache=topology_cache,
        texture_cache=texture_cache,
        build_wireframe=build_wireframe,
        wireframe_only=wireframe_only,
        flat_textured_faces=flat_textured_faces,
    )
    _walk_model_tree_geometry(
        gamemem,
        state,
        model_root,
        source=GEOMETRY_SOURCE_TARGET_TREE,
        node_limit=4096,
    )
    return _finish_geometry_extraction(gamemem, camera, state)


def _finish_geometry_extraction(
    gamemem,
    camera,
    state,
):
    _finalize_geometry_partitions(state)
    if state.cache_static_final and not state.static_cache_hit:
        state.static_cache[state.static_cache_key] = (
            state.static_build.geometry,
            tuple(sorted(state.texture_requests)),
            frozenset(state.static_block_ids),
        )
    textures = _read_indexed_textures(
        gamemem,
        state.texture_requests,
        camera.get("palette_rgb"),
        state.texture_cache,
        state.stats,
        enhancement_options=camera,
        enhanced_imaging_effect_descriptors=(
            state.discovered_enhanced_imaging_effect_descriptors
        ),
    )
    return _build_geometry_result(state, textures)


def _build_geometry_result(state, textures=None):
    state.stats["vertices_emitted"] = _partition_vertex_count(
        state.static_build.geometry
    ) + sum(
        _partition_vertex_count(partition.geometry)
        for partition in state.dynamic_partitions.values()
    )
    discovered_effects = (
        state.discovered_enhanced_imaging_effect_descriptors or ()
    )
    return {
        "static": state.static_build.geometry,
        "partitions": {
            name: partition.geometry
            for name, partition in state.dynamic_partitions.items()
        },
        "primary_partition": DYNAMIC_PARTITION_SCENE,
        "textures": textures or {},
        "enhanced_imaging_effect_descriptors": frozenset(discovered_effects),
        "static_signature": tuple(state.static_signature),
        "stats": state.stats,
        "cockpit_far_depth_fixed": float(state.cockpit_far_depth_fixed),
        "cockpit_radius_fixed": float(state.cockpit_radius_fixed),
    }


def _partition_vertex_count(buffers):
    return (
        len(buffers.vertices) // 4
        + len(buffers.mode4_vertices) // MODE4_VERTEX_FLOATS
        + len(buffers.point_vertices) // 4
        + len(buffers.line_vertices) // 4
        + len(buffers.indexed_vertices) // INDEXED_GEOMETRY_VERTEX_FLOATS
        + len(buffers.wireframe_indexed_vertices) // INDEXED_GEOMETRY_VERTEX_FLOATS
        + _grouped_vertex_count(
            buffers.billboard_instances,
            BILLBOARD_INSTANCE_FLOATS,
        ) * 6
        + _grouped_vertex_count(
            buffers.indexed_texmap_vertices,
            INDEXED_TEXMAP_VERTEX_FLOATS,
        )
        + len(buffers.indexed_texmap_shared_vertices)
            // INDEXED_TEXMAP_VERTEX_FLOATS
        + sum(
            len(batch["vertices"]) // INDEXED_TEXMAP_VERTEX_FLOATS
            for batch in buffers.rotor_batches
        )
    )

def _u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _i32(data, offset):
    return struct.unpack_from("<i", data, offset)[0]


def _extract_secondary_geometry_sources(
    gamemem,
    state,
):
    for root_reloc in (ADDR_MODEL_TREE_ROOT_A, ADDR_MODEL_TREE_ROOT_B):
        root = gamemem.read_reloc_u32(root_reloc)
        if root:
            _walk_model_tree_geometry(
                gamemem,
                state,
                root,
            )


def _walk_model_tree_geometry(
    gamemem,
    state,
    root,
    source=GEOMETRY_SOURCE_MODEL_TREE,
    node_limit=MAX_MODEL_TREE_NODES,
):
    stack = [int(root or 0)]
    seen = set()
    while stack and len(seen) < int(node_limit):
        tree_addr = stack.pop()
        if not tree_addr or tree_addr in seen:
            continue
        seen.add(tree_addr)
        tree_bytes = gamemem.read_runtime_bytes(
            tree_addr,
            MODEL_TREE_NODE_READ_SIZE,
        )

        child = _u32(tree_bytes, 0x04)
        sibling = _u32(tree_bytes, 0x08)
        geom = _u32(tree_bytes, 0x6C)
        if sibling:
            stack.append(sibling)
        if child:
            stack.append(child)
        if geom:
            _extract_tree_geometry_node(
                gamemem,
                state,
                tree_addr,
                tree_bytes,
                geom,
                source,
            )


def _extract_tree_geometry_node(
    gamemem,
    state,
    tree_addr,
    tree_bytes,
    geom,
    source,
):
    geom = int(geom)
    geom_bytes = gamemem.read_runtime_bytes(geom, GEOMETRY_NODE_SIZE)
    flags = _u32(geom_bytes, 0x00)
    if source == GEOMETRY_SOURCE_TARGET_TREE:
        if flags & 0x1000:
            return
        entity_ref = _u32(geom_bytes, 0x18)
        matrix = None
        if entity_ref == 0:
            entity_ref = tree_addr
            matrix = memoryview(tree_bytes)[0x3C:0x6C]
        _extract_node_fields(
            gamemem,
            state,
            geom,
            flags,
            entity_ref,
            _u32(geom_bytes, 0x1C),
            source,
            entity_matrix_override=matrix,
        )
        return

    for block_data in (_u32(geom_bytes, 0x20), _u32(geom_bytes, 0x1C)):
        if not block_data:
            continue
        if int(block_data) in state.emitted_blocks:
            return
        if _extract_node_fields(
            gamemem,
            state,
            geom,
            flags,
            0,
            block_data,
            source,
        ):
            return


def _extract_node(
    gamemem,
    state,
    node_addr,
    node_bytes,
    source,
):
    return _extract_node_fields(
        gamemem,
        state,
        node_addr,
        _u32(node_bytes, 0x00),
        _u32(node_bytes, 0x18),
        _u32(node_bytes, 0x1C),
        source,
    )


def _satellite_node_omitted(gamemem, node_addr, flags):
    node_class = (int(flags) >> 16) & 0xFFFF
    primary = node_class & 0x0F00
    secondary = node_class & 0x00F0
    if primary == 0x0100:
        entity_index = int(gamemem.read_runtime_u16(int(node_addr) + 0x14))
        entity = int(
            gamemem.read_reloc_u32(
                ADDR_ENTITY_BODY_TABLE + entity_index * 4
            )
        )
        if entity == 0:
            return True
        # Only the mask matters, so an unsigned read is equivalent to the
        # native i16 test and is supported by the game-memory adapter.
        entity_flags = int(gamemem.read_runtime_u16(entity + 0x14))
        if entity_flags & 0x0016:
            return True
    elif secondary in (0x0030, 0x0070):
        return True
    return bool(int(flags) & 0x1000)


def _qualified_rotor_disc_faces(topology, nvert, nfaces):
    if (
        int(nvert) != ROTOR_DISC_SOURCE_VERTICES
        or int(nfaces) != ROTOR_DISC_SOURCE_FACES
    ):
        return ()
    faces = topology.get("faces", ())
    if len(faces) != ROTOR_DISC_SOURCE_FACES:
        return ()
    index_sets = []
    for face in faces:
        mode = int(face[2])
        indices = tuple(int(index) for index in face[3])
        if (
            mode not in (MODE_TEXTURED_PERSPECTIVE, MODE_TEXTURED_AFFINE)
            or len(indices) != ROTOR_DISC_SOURCE_FACE_VERTICES
            or len(set(indices)) != ROTOR_DISC_SOURCE_FACE_VERTICES
        ):
            return ()
        index_sets.append(set(indices))
    if (
        index_sets[0] & index_sets[1]
        or index_sets[0] | index_sets[1] != set(range(ROTOR_DISC_SOURCE_VERTICES))
    ):
        return ()
    return tuple(faces)


def _topology_without_face_ids(topology, excluded_face_ids):
    excluded_face_ids = frozenset(int(value) for value in excluded_face_ids)
    if not excluded_face_ids:
        return topology
    filtered = dict(topology)
    faces = tuple(
        face
        for face in topology.get("faces", ())
        if int(face[0]) not in excluded_face_ids
    )
    filtered["faces"] = faces
    filtered["vertex_requirements"] = _face_vertex_requirements(faces)
    filtered.pop("mesh_plan", None)
    if "emission_batch" in filtered:
        filtered["emission_batch"] = _build_satellite_emission_batch(
            filtered["faces"]
        )
    return filtered


def _rotor_disc_rim(world_vertices, indices, normal):
    source = np.asarray(
        [world_vertices[int(index)] for index in indices],
        dtype=np.float64,
    )
    center = np.mean(source, axis=0)
    radial = source - center
    radii = np.linalg.norm(radial, axis=1)
    radius = float(np.mean(radii))
    if not math.isfinite(radius) or radius <= 1e-6:
        return None

    e1 = radial[0]
    e1_length = float(np.linalg.norm(e1))
    if e1_length <= 1e-6:
        return None
    e1 = e1 / e1_length
    normal_vec = np.asarray(normal, dtype=np.float64)
    normal_length = float(np.linalg.norm(normal_vec))
    if normal_length <= 1e-6:
        normal_vec = np.cross(radial[0], radial[1])
        normal_length = float(np.linalg.norm(normal_vec))
    if normal_length <= 1e-6:
        return None
    normal_vec = normal_vec / normal_length
    e2 = np.cross(normal_vec, e1)
    e2_length = float(np.linalg.norm(e2))
    if e2_length <= 1e-6:
        return None
    e2 = e2 / e2_length
    if float(np.dot(radial[1], e2)) < 0.0:
        e2 = -e2

    angles = np.arange(ROTOR_DISC_SEGMENTS, dtype=np.float64)
    angles *= (2.0 * math.pi) / float(ROTOR_DISC_SEGMENTS)
    rim = (
        center[None, :]
        + radius
        * (
            np.cos(angles)[:, None] * e1[None, :]
            + np.sin(angles)[:, None] * e2[None, :]
        )
    )
    return center, rim


def _append_rotor_outline(vertices, rim, palette_index):
    for index in range(ROTOR_DISC_SEGMENTS):
        next_index = (index + 1) % ROTOR_DISC_SEGMENTS
        _append_geometry_vertex(vertices, rim[index], palette_index)
        _append_geometry_vertex(vertices, rim[next_index], palette_index)


def _pack_continuous_lighting_state(lit_shade_before_fog, component_policy):
    return (
        float(component_policy) * CONTINUOUS_LIGHTING_POLICY_PACK_SCALE
        + float(lit_shade_before_fog)
    )


def _emit_rotor_disc(
    source_faces,
    view_faces,
    world_vertices,
    lighting,
    buffers,
    texture_requests,
    component_policies,
    *,
    outline_only,
    outline_palette,
    emit_outline=False,
):
    view_by_id = {int(face[0]): face for face in view_faces}
    emitted_outline = False
    for source_face, component_policy in zip(source_faces, component_policies):
        face_id = int(source_face[0])
        view_face = view_by_id.get(face_id, source_face)
        indices = tuple(int(index) for index in source_face[3])
        rim_result = _rotor_disc_rim(
            world_vertices,
            indices,
            source_face[4],
        )
        if rim_result is None:
            continue
        center, rim = rim_result
        if outline_only or emit_outline:
            # The two stock faces occupy the same disc. A single closed rim is
            # sufficient and avoids drawing a doubled antialiased outline.
            if not emitted_outline:
                palette_index = (
                    int(outline_palette)
                    if outline_palette is not None
                    else int(view_face[5])
                )
                _append_rotor_outline(
                    buffers.line_vertices,
                    rim,
                    palette_index,
                )
                emitted_outline = True
            if outline_only:
                continue

        desc_idx = _mode57_texture_desc_index(source_face[1])
        texture_requests.add(desc_idx)
        vertices = array("f")
        indices_out = array("H")
        primitive_lighting = array("f")
        vertices.extend(
            (
                float(center[0]) / FIXED_16_16_SCALE,
                float(center[1]) / FIXED_16_16_SCALE,
                float(center[2]) / FIXED_16_16_SCALE,
                0.5,
                0.5,
            )
        )
        for index in range(ROTOR_DISC_SEGMENTS):
            angle = (2.0 * math.pi * index) / float(ROTOR_DISC_SEGMENTS)
            vertex = rim[index]
            vertices.extend(
                (
                    float(vertex[0]) / FIXED_16_16_SCALE,
                    float(vertex[1]) / FIXED_16_16_SCALE,
                    float(vertex[2]) / FIXED_16_16_SCALE,
                    0.5 + ROTOR_DISC_UV_RADIUS * math.cos(angle),
                    0.5 + ROTOR_DISC_UV_RADIUS * math.sin(angle),
                )
            )
        lit_shade_before_fog = float(
            _compute_face_light_level(
                source_face[4],
                world_vertices,
                indices,
                lighting,
                material_light_scale=0xFF,
                include_fog=False,
                continuous=True,
            )
        )
        lighting_state = _pack_continuous_lighting_state(
            lit_shade_before_fog,
            component_policy,
        )
        for index in range(ROTOR_DISC_SEGMENTS):
            indices_out.extend(
                (
                    0,
                    index + 1,
                    ((index + 1) % ROTOR_DISC_SEGMENTS) + 1,
                )
            )
            primitive_lighting.append(lighting_state)
        buffers.rotor_batches.append(
            {
                "effect": "heli_rotor",
                "desc_idx": int(desc_idx),
                "center": tuple(
                    float(value) / FIXED_16_16_SCALE for value in center
                ),
                "normalized_uv": True,
                "vertices": vertices,
                "indices": indices_out,
                "lighting": primitive_lighting,
            }
        )


def _aero_lift_fan_faces(topology, excluded_face_ids):
    excluded_face_ids = frozenset(int(value) for value in excluded_face_ids)
    return tuple(
        face
        for face in topology.get("faces", ())
        if (
            int(face[0]) not in excluded_face_ids
            and int(face[2]) in (
                MODE_TEXTURED_PERSPECTIVE,
                MODE_TEXTURED_AFFINE,
            )
            and _mode57_texture_desc_index(face[1])
            == AERO_LIFT_FAN_DESC_INDEX
        )
    )


def _emit_aero_lift_fan_batch(
    faces,
    world_vertices,
    control_uvs,
    lighting,
    buffers,
    texture_requests,
    component_policies,
):
    if not faces:
        return
    used_indices = sorted(
        {
            int(vertex_index)
            for face in faces
            for vertex_index in face[3]
        }
    )
    if not used_indices:
        return
    vertex_remap = {
        source_index: output_index
        for output_index, source_index in enumerate(used_indices)
    }
    vertices = array("f")
    indices_out = array("H")
    primitive_lighting = array("f")
    center = np.mean(
        np.asarray(
            [world_vertices[index] for index in used_indices],
            dtype=np.float64,
        ),
        axis=0,
    )
    for source_index in used_indices:
        world_vertex = world_vertices[source_index]
        uv = control_uvs[source_index]
        vertices.extend(
            (
                float(world_vertex[0]) / FIXED_16_16_SCALE,
                float(world_vertex[1]) / FIXED_16_16_SCALE,
                float(world_vertex[2]) / FIXED_16_16_SCALE,
                float(uv[0]),
                float(uv[1]),
            )
        )
    for face, component_policy in zip(faces, component_policies):
        face_indices = tuple(int(index) for index in face[3])
        lit_shade_before_fog = float(
            _compute_face_light_level(
                face[4],
                world_vertices,
                face_indices,
                lighting,
                material_light_scale=0xFF,
                include_fog=False,
                continuous=True,
            )
        )
        lighting_state = _pack_continuous_lighting_state(
            lit_shade_before_fog,
            component_policy,
        )
        for triangle in _triangle_fan_indices(face_indices):
            indices_out.extend(vertex_remap[index] for index in triangle)
            primitive_lighting.append(lighting_state)
    if not indices_out:
        return
    desc_idx = _mode57_texture_desc_index(faces[0][1])
    texture_requests.add(desc_idx)
    buffers.rotor_batches.append(
        {
            "effect": "aero_lift_fan",
            "desc_idx": int(desc_idx),
            "center": tuple(
                float(value) / FIXED_16_16_SCALE for value in center
            ),
            "normalized_uv": False,
            "vertices": vertices,
            "indices": indices_out,
            "lighting": primitive_lighting,
        }
    )


def _resolve_renderer_entity_face_flags(
    authored_flags,
    camo_texture,
    emblem_texture,
):
    authored_flags = int(authored_flags) & 0xFFFF
    mode = (authored_flags >> 12) & 0xF
    texture = authored_flags & 0xFF
    if mode in TEXTURED_FACE_MODES and texture == 0:
        texture = int(camo_texture) & 0xFF
    elif (
        mode in TEXTURED_FACE_MODES
        and texture == EMBLEM_TEXTURE_SOURCE_INDEX
    ):
        texture = int(emblem_texture) & 0xFF
    return (authored_flags & 0xFF00) | texture


def _prepare_renderer_entity_lod_instances(gamemem, state):
    entity_lod = state.entity_lod_store
    frame = state.entity_component_frame
    records = frame.descriptor_snapshot.records
    resource_ids = records["resource_ids"]
    entities = frame.entities
    instances = []
    live_component_nodes = None
    if state.camera.get("entity_lod_live_component_nodes", False):
        live_component_nodes = snapshot_component_installed_nodes(
            gamemem,
            len(frame.entity_indices),
        )
    for descriptor_index in range(len(frame.entity_indices)):
        entity_index = int(frame.entity_indices[descriptor_index])
        if entity_index < 0:
            continue
        detail = int(entities["selected_detail"][entity_index])
        if detail == HIDDEN_DETAIL:
            continue
        resource_id = int(resource_ids[descriptor_index, detail])
        if resource_id in (0, DUMMY_RESOURCE_ID):
            continue
        tree = int(records["model_tree_part"][descriptor_index])
        asset = entity_lod.assets.get(resource_id)
        if tree == 0 or asset is None:
            return ()
        camo = int(entities["camo"][entity_index])
        emblem = int(entities["emblem"][entity_index])
        installed_node = int(
            live_component_nodes[descriptor_index]
            if live_component_nodes is not None
            else records["installed_node"][descriptor_index]
        )
        key = (descriptor_index, resource_id, camo, emblem)
        compiled = entity_lod.compiled_assets.get(key)
        if compiled is None:
            compiled = _compile_renderer_poly_mesh_asset(
                asset,
                camo,
                emblem,
            )
            entity_lod.compiled_assets[key] = compiled
        instances.append((descriptor_index, tree, installed_node, compiled))
    return tuple(instances)


def _extract_renderer_entity_lods(gamemem, state):
    frame = state.entity_component_frame
    live_component_nodes = state.camera.get(
        "entity_lod_live_component_nodes",
        False,
    )
    for descriptor_index, tree, installed_node, compiled in (
        state.renderer_entity_instances
    ):
        node_seen = (
            installed_node != 0
            if live_component_nodes
            else int(frame.node_seen[descriptor_index]) != 0
        )
        node_flags = int(frame.node_flags[descriptor_index])
        if live_component_nodes and node_seen:
            node_flags = int(gamemem.read_runtime_u16(installed_node))
        if node_seen and node_flags & 0x1000:
            continue
        if not node_seen:
            # A missing installed node means the native game no longer owns
            # this component. Do not recreate a destroyed or detached part
            # from the renderer's retained exterior asset.
            continue
        matrix = gamemem.read_runtime_bytes(
            tree + ENTITY_MATRIX_OFFSET,
            ENTITY_MATRIX_SIZE,
        )
        _refresh_renderer_poly_normals(compiled, matrix)
        virtual_base = 0xC0000000 + descriptor_index * 0x10000
        node_addr = installed_node
        if node_addr == 0:
            node_addr = virtual_base + 0x8000
        _extract_node_fields(
            gamemem,
            state,
            node_addr,
            node_flags,
            tree,
            virtual_base,
            GEOMETRY_SOURCE_RENDERER_LOD,
            entity_matrix_override=matrix,
            mesh_asset=compiled,
            owner_addr_override=installed_node,
        )


def _compile_renderer_poly_mesh_asset(
    asset,
    camo_texture,
    emblem_texture,
):
    blob = asset
    vertex_count, face_count = struct.unpack_from("<HH", blob, 0x18)
    face_data_offset = HEADER_SIZE + vertex_count * VERTEX_STRIDE
    block_header = struct.pack(
        "<IHHIIII",
        0,
        vertex_count,
        face_count,
        face_data_offset,
        0,
        0,
        0,
    )
    vertex_records = np.frombuffer(
        blob,
        dtype=POLY_VERTEX_DTYPE,
        count=vertex_count,
        offset=WTBO_HEADER_SIZE,
    )
    local_vertices = np.ascontiguousarray(
        vertex_records["position"], dtype=np.int64
    )
    control_uvs = np.ascontiguousarray(vertex_records["uv"], dtype=np.int32)
    c_in_values = np.ascontiguousarray(
        control_uvs[:, 0] & np.int32(0xFF),
        dtype=np.uint8,
    )
    world_normals = np.zeros((face_count, 3), dtype=np.int64)
    face_edges_a = np.zeros((face_count, 3), dtype=np.int64)
    face_edges_b = np.zeros((face_count, 3), dtype=np.int64)
    faces = []
    wireframe_owner_addrs = {}
    source_face_flags = {}
    face_offset = WTBO_HEADER_SIZE + vertex_count * WTBO_VERTEX_STRIDE
    for face_index in range(face_count):
        authored_flags, count = struct.unpack_from("<HH", blob, face_offset)
        flags = _resolve_renderer_entity_face_flags(
            authored_flags,
            camo_texture,
            emblem_texture,
        )
        mode = (flags >> 12) & 0xF
        indices = struct.unpack_from(f"<{count}H", blob, face_offset + 4)
        face_offset += wtbo_face_stride(count)
        if mode not in SUPPORTED_FACE_MODES:
            continue
        if count < 3 or count > MAX_FACE_VERTICES:
            continue
        p0 = local_vertices[indices[0]]
        for corner in range(1, count - 1):
            edge_a = local_vertices[indices[corner]] - p0
            edge_b = local_vertices[indices[corner + 1]] - p0
            nx = int(edge_a[1]) * int(edge_b[2]) - int(edge_a[2]) * int(edge_b[1])
            ny = int(edge_a[2]) * int(edge_b[0]) - int(edge_a[0]) * int(edge_b[2])
            nz = int(edge_a[0]) * int(edge_b[1]) - int(edge_a[1]) * int(edge_b[0])
            if nx != 0 or ny != 0 or nz != 0:
                face_edges_a[face_index] = edge_a
                face_edges_b[face_index] = edge_b
                break
        faces.append(
            (
                face_index,
                flags,
                mode,
                indices,
                world_normals[face_index],
                WIREFRAME_DEFAULT_PALETTE_INDEX,
                0,
            )
        )
        wireframe_owner_addrs[face_index] = 0
        source_face_flags[face_index] = flags

    faces = tuple(faces)
    vertex_requirements = _face_vertex_requirements(faces)
    topology = {
        "block_header": block_header,
        "faces": faces,
        "wireframe_owner_addrs": wireframe_owner_addrs,
        "source_face_flags": source_face_flags,
        "vertex_requirements": vertex_requirements,
        "index_span_base": 0,
        "index_span": b"",
        "topology_volatile": False,
        "vertex_asset": {
            "key": bytes(block_header[:BLOCK_VERTEX_LAYOUT_SIZE]),
            "local_vertices": local_vertices,
            "cached_world_vertices": np.empty((0, 3), dtype=np.int64),
            "c_in_values": c_in_values,
            "control_uvs": control_uvs,
            "world_matrix": None,
            "world_vertices": None,
        },
        "renderer_lod_face_edges_a": face_edges_a,
        "renderer_lod_face_edges_b": face_edges_b,
        "renderer_lod_world_normals": world_normals,
    }
    return topology


def _refresh_renderer_poly_normals(topology, matrix):
    matrix_stamp = bytes(matrix[:ENTITY_TRANSLATION_OFFSET])
    if topology.get("renderer_lod_normal_matrix") == matrix_stamp:
        return
    world_normals = topology["renderer_lod_world_normals"]
    transform_geometry_face_normals(
        world_normals,
        topology["renderer_lod_face_edges_a"],
        topology["renderer_lod_face_edges_b"],
        np.frombuffer(matrix_stamp, dtype=np.uint8),
    )
    plan = _compiled_mesh_plan(topology)
    if len(plan.face_ids):
        np.copyto(plan.face_normals, world_normals[plan.face_ids])
    batch = _compiled_deferred_adapter(topology, plan)
    if batch["indexed_face_indices"].size:
        np.copyto(
            batch["indexed"][4],
            world_normals[batch["indexed_face_indices"]],
        )
    if batch["mode4_face_indices"].size:
        np.copyto(
            batch["mode4"][3],
            world_normals[batch["mode4_face_indices"]],
        )
    if batch["mode57_face_indices"].size:
        np.copyto(
            batch["mode57"][3],
            world_normals[batch["mode57_face_indices"]],
        )
    batch["normal_generation"] = int(batch.get("normal_generation", 0)) + 1
    topology["renderer_lod_normal_matrix"] = matrix_stamp


def _extract_node_fields(
    gamemem,
    state,
    node_addr,
    flags,
    entity_ref,
    block_data,
    source,
    entity_matrix_override=None,
    mesh_asset=None,
    owner_addr_override=None,
):
    stats = state.stats
    if (
        state.included_node_addrs
        and int(node_addr) not in state.included_node_addrs
    ):
        return False
    if int(node_addr) in state.excluded_node_addrs:
        return False
    lod_descriptor_index = resolve_component_descriptor_index(
        state.lod_descriptor_snapshot,
        node_addr,
        entity_ref,
    )
    component_frame = state.entity_component_frame
    component_entity_index = -1
    node_partition = state.node_partitions.get(
        int(node_addr),
        DYNAMIC_PARTITION_SCENE,
    )
    if component_frame is not None and lod_descriptor_index is not None:
        component_entity_index = int(
            component_frame.entity_indices[lod_descriptor_index]
        )
        component_frame.node_flags[lod_descriptor_index] = int(flags)
        component_frame.node_seen[lod_descriptor_index] = 1
    if (
        state.renderer_entity_instances
        and source in (
            GEOMETRY_SOURCE_PRIMARY,
            GEOMETRY_SOURCE_MODEL_TREE,
        )
        and component_entity_index >= 0
        and node_partition != DYNAMIC_PARTITION_COCKPIT
    ):
        return False

    if state.satellite_view and _satellite_node_omitted(
        gamemem,
        node_addr,
        flags,
    ):
        return False

    if block_data == 0:
        return False
    flag_hi = (flags >> 24) & 0xFF
    is_target_tree = source == GEOMETRY_SOURCE_TARGET_TREE
    is_terrain = flag_hi == NODE_FLAG_TERRAIN_HI and not is_target_tree
    trusted_mesh_asset = None
    if mesh_asset is not None and (
        is_terrain or source == GEOMETRY_SOURCE_RENDERER_LOD
    ):
        cached_header = mesh_asset["block_header"]
        if (
            source == GEOMETRY_SOURCE_RENDERER_LOD
            or _u32(cached_header, 0x00) != 0
        ):
            trusted_mesh_asset = mesh_asset

    if trusted_mesh_asset is None:
        block_header = gamemem.read_runtime_bytes(block_data, HEADER_SIZE)
    else:
        block_header = trusted_mesh_asset["block_header"]
    nvert = _u16(block_header, 0x04)
    nfaces = _u16(block_header, 0x06)
    face_data_offset = _u32(block_header, 0x08)
    expected_face_offset = HEADER_SIZE + nvert * VERTEX_STRIDE
    if (
        nvert < 1
        or nvert > MAX_VERTICES
        or nfaces < 1
        or nfaces > MAX_FACES
        or face_data_offset < expected_face_offset
        or face_data_offset > MAX_FACE_DATA_OFFSET
    ):
        return False

    if int(block_data) in state.emitted_blocks:
        return False
    state.emitted_blocks.add(int(block_data))

    state_flag = _u32(block_header, 0x00)
    state_static = state_flag != 0
    if state.static_cache_hit and int(block_data) in state.static_block_ids:
        return True
    # Renderer policy: retain the normal cached terrain path in satellite mode.
    # The native per-face terrain conversion is visually inferior and is not
    # needed for object/IFF fidelity.
    satellite_face_classification = state.satellite_view and not is_terrain
    cache_topology_for_node = (
        state.topology_cache
        if is_terrain and state_static
        else None
    )
    cache_stable_topology_for_node = (
        state.topology_cache if state.topology_cache is not None else None
    )
    cache_live_topology_for_node = (
        state.topology_cache
        if state.topology_cache is not None
        and cache_topology_for_node is None
        and not is_target_tree
        and (not satellite_face_classification or state.satellite_view)
        else None
    )

    lod_selected_resource = None
    if lod_descriptor_index is not None:
        descriptor = state.lod_descriptor_snapshot.records[lod_descriptor_index]
        installed_detail = int(descriptor["installed_detail"])
        if 0 <= installed_detail < 5:
            lod_selected_resource = int(
                descriptor["resource_ids"][installed_detail]
            )
    lod_resource_identity = ()
    if lod_selected_resource is not None:
        lod_resource_identity = (
            "poly_resource",
            int(lod_selected_resource),
        )

    face_base = block_data + face_data_offset
    topology_key = (
        "full",
        int(block_data),
        int(nvert),
        int(nfaces),
        int(face_data_offset),
        *lod_resource_identity,
    )
    stable_topology_key = (
        "stable",
        int(block_data),
        int(nvert),
        int(nfaces),
        int(face_data_offset),
        *lod_resource_identity,
    )
    topology_volatility = None
    if satellite_face_classification:
        topology_volatility = _topology_volatility_record(
            state,
            source,
            node_addr,
            entity_ref,
            stable_topology_key,
        )
    face_headers = None
    topology = (
        trusted_mesh_asset
        if trusted_mesh_asset is not None
        else None
    )
    if cache_topology_for_node is not None:
        topology = cache_topology_for_node.get(topology_key)
    if topology is None:
        if state.topology_cache is not None:
            stats["topology_cache_misses"] += 1
        face_headers = gamemem.read_runtime_bytes(
            face_base,
            nfaces * FACE_STRIDE,
        )
        live_topology_key = None
        if cache_live_topology_for_node is not None:
            live_topology_key = ("live", stable_topology_key)
            live_topology = None
            volatile_marker = cache_live_topology_for_node.get(
                ("stable_volatile", stable_topology_key),
            )
            owner_is_volatile = bool(
                topology_volatility
                and topology_volatility.get("volatile", False)
            )
            if volatile_marker is None and not owner_is_volatile:
                live_topology = cache_live_topology_for_node.get(
                    live_topology_key
                )
            if live_topology is not None and _live_topology_headers_match(
                live_topology,
                face_headers,
                nfaces,
            ):
                if (
                    lod_resource_identity
                    or _live_topology_index_span_matches(
                        gamemem,
                        live_topology.get("topology"),
                    )
                ):
                    topology = live_topology.get("topology")
                    if live_topology.get("face_headers") != face_headers:
                        _refresh_live_topology_normals(
                            topology,
                            face_headers,
                            refresh_faces=state.satellite_view,
                        )
                        live_topology["face_headers"] = face_headers
                else:
                    stats["topology_live_index_invalidations"] = (
                        stats.get("topology_live_index_invalidations", 0) + 1
                    )
        if topology is None:
            topology = _read_face_topology(
                gamemem,
                face_base,
                face_headers,
                nfaces,
                nvert,
                stable_cache=(
                    cache_stable_topology_for_node
                    if cache_topology_for_node is None
                    else None
                ),
                stable_key=stable_topology_key,
                validate_stable_topology=(
                    is_target_tree
                    or (
                        cache_live_topology_for_node is not None
                        and not lod_resource_identity
                    )
                ),
                volatility_record=topology_volatility,
            )
            if cache_topology_for_node is not None:
                _topology_cache_put(cache_topology_for_node, topology_key, topology)
            elif (
                cache_live_topology_for_node is not None
                and not topology.get("topology_volatile", False)
            ):
                _topology_cache_put(
                    cache_live_topology_for_node,
                    live_topology_key,
                    {
                        "face_headers": face_headers,
                        "topology": topology,
                    },
                )
    owner_addrs_override = (
        None
        if owner_addr_override is None
        else (int(owner_addr_override),)
    )
    qualified_rotor_faces = _qualified_rotor_disc_faces(
        topology,
        nvert,
        nfaces,
    )
    rotor_source_faces = (
        qualified_rotor_faces
        if state.camera["enhanced_heli_rotors"]
        else ()
    )
    rotor_face_ids = frozenset(int(face[0]) for face in rotor_source_faces)
    qualified_rotor_face_ids = frozenset(
        int(face[0]) for face in qualified_rotor_faces
    )
    aero_lift_fan_faces = (
        _aero_lift_fan_faces(topology, qualified_rotor_face_ids)
        if (
            not state.satellite_view
            and not state.build_wireframe
            and state.camera["enhanced_aero_lift_fans"]
        )
        else ()
    )
    aero_lift_fan_face_ids = frozenset(
        int(face[0]) for face in aero_lift_fan_faces
    )
    enhanced_face_ids = rotor_face_ids | aero_lift_fan_face_ids
    processing_topology = topology
    if state.flat_textured_faces:
        processing_topology = _target_flat_presentation_topology(
            gamemem,
            topology,
            state,
            owner_addrs_override,
        )
    faces_for_node = processing_topology["faces"]
    topology_vertex_requirements = processing_topology["vertex_requirements"]
    has_mode3 = topology_vertex_requirements["control_uvs_legacy"]
    satellite_topology = None
    if satellite_face_classification:
        satellite_topology = _satellite_classified_topology(
            gamemem,
            topology,
            state,
            owner_addrs_override,
        )
        faces_for_node = satellite_topology["faces"]
        topology_vertex_requirements = satellite_topology[
            "vertex_requirements"
        ]
        has_mode3 = topology_vertex_requirements["control_uvs_legacy"]
    rotor_view_faces = tuple(
        face for face in faces_for_node if int(face[0]) in rotor_face_ids
    )
    if enhanced_face_ids:
        processing_topology = _topology_without_face_ids(
            processing_topology,
            enhanced_face_ids,
        )
        faces_for_node = tuple(
            face
            for face in faces_for_node
            if int(face[0]) not in enhanced_face_ids
        )
        if satellite_topology is not None:
            satellite_topology = _topology_without_face_ids(
                satellite_topology,
                enhanced_face_ids,
            )
    effect_topology = None
    ordinary_topology = processing_topology
    if state.preserve_enhanced_imaging_effects:
        (
            processing_topology,
            effect_topology,
            effect_texture_requests,
        ) = _enhanced_imaging_effect_topologies(
            processing_topology,
            state.enhanced_imaging_effect_descriptors,
        )
        faces_for_node = processing_topology["faces"]
        state.texture_requests.update(effect_texture_requests)
    force_cached_world = source == GEOMETRY_SOURCE_MODEL_TREE
    use_entity_transform = not is_terrain and not force_cached_world
    is_static = is_terrain and state_static and not has_mode3 and not is_target_tree

    if is_static:
        state.static_block_ids.add(int(block_data))
        build_partition = state.static_build
    else:
        if source == GEOMETRY_SOURCE_RENDERER_LOD:
            node_partition = DYNAMIC_PARTITION_ENTITY
        build_partition = state.dynamic_partitions[node_partition]
    target_buffers = build_partition.geometry
    if is_static and state.cache_static_final:
        if state.static_cache_hit:
            return True

    vertex_requirements = (
        {
            "mode57": False,
            "legacy": True,
            "c_in": False,
            "control_uvs_legacy": False,
        }
        if state.wireframe_only or state.enhanced_wireframe_only
        else topology_vertex_requirements
    )
    if effect_topology is not None and state.enhanced_wireframe_only:
        effect_requirements = effect_topology["vertex_requirements"]
        vertex_requirements = dict(vertex_requirements)
        vertex_requirements["mode57"] = effect_requirements["mode57"]
        vertex_requirements["control_uvs_legacy"] = bool(
            effect_requirements["control_uvs_legacy"]
            or (is_static and effect_requirements["mode57"])
        )
    matrix = _read_entity_matrix_for_vertices(
        gamemem,
        use_entity_transform,
        entity_ref,
        state.entity_vertex_mode,
        matrix_override=entity_matrix_override,
        smooth_owner_matrices=state.camera.get("_smooth_owner_matrices"),
    )
    indexed_flat_pending = build_partition.indexed_flat
    mode4_pending = build_partition.mode4
    mode57_pending = build_partition.mode57
    wireframe_pending = build_partition.wireframe
    satellite_mode4_pending = build_partition.satellite_mode4
    legacy_required = bool(
        vertex_requirements["legacy"]
        or state.build_wireframe
        or state.flat_textured_faces
        or mode57_pending is None
    )
    if legacy_required:
        legacy_requirements = {
            "positions": True,
            "c_in": vertex_requirements["c_in"],
            "control_uvs": vertex_requirements["control_uvs_legacy"],
        }
    else:
        legacy_requirements = {
            "positions": False,
            "c_in": False,
            "control_uvs": False,
        }
    decode_requirements = {
        "positions": bool(
            legacy_requirements["positions"]
            or vertex_requirements["mode57"]
            or rotor_source_faces
            or aero_lift_fan_faces
        ),
        "c_in": legacy_requirements["c_in"],
        "control_uvs": bool(
            legacy_requirements["control_uvs"]
            or (mode57_pending is not None and vertex_requirements["mode57"])
            or aero_lift_fan_faces
        ),
    }
    world_vertices, c_in_values, control_uvs = _read_vertices_np(
        gamemem,
        topology,
        block_data,
        block_header,
        nvert,
        matrix,
        decode_requirements,
    )
    if node_partition == DYNAMIC_PARTITION_COCKPIT:
        far_depth, radius = cockpit_bounds_fixed(world_vertices, state.camera)
        state.cockpit_far_depth_fixed = max(
            state.cockpit_far_depth_fixed,
            far_depth,
        )
        state.cockpit_radius_fixed = max(
            state.cockpit_radius_fixed,
            radius,
        )
    elif state.cockpit_effect_tracker is not None:
        world_vertices, relocated, latched = relocate_cockpit_effect_vertices(
            world_vertices,
            node_addr,
            state.camera,
            state.cockpit_effect_tracker,
            state.previous_cockpit_far_depth_fixed,
        )
        if relocated:
            state.stats["cockpit_effect_nodes_relocated"] = int(
                state.stats.get("cockpit_effect_nodes_relocated", 0)
            ) + 1
        if latched:
            state.stats["cockpit_effect_transforms_latched"] = int(
                state.stats.get("cockpit_effect_transforms_latched", 0)
            ) + 1
    if (
        is_terrain
        and state.terrain_block_deltas is not None
        and world_vertices.size
    ):
        terrain_block_id = (
            int(nvert),
            int(nfaces),
            int(world_vertices[:, 0].sum(dtype=np.int64)),
            int(world_vertices[:, 2].sum(dtype=np.int64)),
        )
        terrain_delta = state.terrain_block_deltas.get(terrain_block_id)
        if terrain_delta is not None:
            world_vertices = world_vertices.astype(np.float64, copy=True)
            world_vertices[:, 0] += terrain_delta[0] * FIXED_16_16_SCALE
            world_vertices[:, 2] += terrain_delta[1] * FIXED_16_16_SCALE
    mode57_vertices = None
    mode57_control_uvs = ()
    if (
        mode57_pending is not None and vertex_requirements["mode57"]
    ) or aero_lift_fan_faces:
        mode57_vertices = world_vertices
        mode57_control_uvs = control_uvs
    # Mode 5/6/7 UVs use the separate handoff above; the legacy view is only
    # populated for billboard faces that explicitly request control UVs.
    if not legacy_requirements["control_uvs"]:
        control_uvs = ()
    node_lighting = state.lighting
    if state.satellite_view and is_terrain:
        node_lighting = dict(state.lighting)
        node_lighting["fog_distance"] = SATELLITE_NO_FOG_DISTANCE
    if aero_lift_fan_faces:
        _emit_aero_lift_fan_batch(
            aero_lift_fan_faces,
            world_vertices,
            mode57_control_uvs,
            node_lighting,
            target_buffers,
            state.texture_requests,
            _component_policies_for_faces(
                gamemem,
                state,
                topology,
                aero_lift_fan_faces,
                owner_addr_override,
            ),
        )
    if rotor_source_faces:
        outline_palette = None
        if state.build_wireframe and not state.satellite_view:
            owner_addr = int(owner_addr_override or 0)
            if owner_addr_override is None:
                owner_addr = int(
                    topology.get("wireframe_owner_addrs", {}).get(
                        int(rotor_source_faces[0][0]),
                        0,
                    )
                )
            outline_palette = _wireframe_palette_index(
                gamemem,
                owner_addr,
            )
        _emit_rotor_disc(
            rotor_source_faces,
            rotor_view_faces,
            world_vertices,
            node_lighting,
            target_buffers,
            state.texture_requests,
            _component_policies_for_faces(
                gamemem,
                state,
                topology,
                rotor_source_faces,
                owner_addr_override,
            ),
            outline_only=bool(
                state.satellite_view
                or state.wireframe_only
                or state.enhanced_wireframe_only
            ),
            outline_palette=outline_palette,
            emit_outline=state.cached_enhanced_wireframe,
        )
    used_cached_enhanced_wireframe = False
    if state.cached_enhanced_wireframe:
        used_cached_enhanced_wireframe = _collect_cached_enhanced_wireframe(
            gamemem,
            processing_topology,
            world_vertices,
            target_buffers,
            wireframe_pending,
            state.wireframe_owner_palette_cache,
            state.enhanced_wireframe_only,
            owner_addrs_override,
        )
    used_cached_satellite_faces = False
    if satellite_topology is not None:
        faces_for_node = _collect_cached_satellite_faces(
            satellite_topology,
            world_vertices,
            c_in_values,
            control_uvs,
            target_buffers,
            indexed_flat_pending,
            mode4_pending,
            mode57_pending,
            mode57_vertices,
            mode57_control_uvs,
            state.texture_requests,
            wireframe_pending,
            satellite_mode4_pending,
        )
        used_cached_satellite_faces = True
    emit_ordinary_faces = not (
        used_cached_enhanced_wireframe
        and (state.enhanced_wireframe_only or state.wireframe_only)
    )
    build_uncached_wireframe = bool(
        state.build_wireframe and not used_cached_enhanced_wireframe
    )
    if effect_topology is not None and not emit_ordinary_faces:
        _collect_cached_deferred_faces(
            effect_topology,
            world_vertices,
            c_in_values,
            control_uvs,
            target_buffers,
            indexed_flat_pending,
            mode4_pending,
            mode57_pending,
            mode57_vertices,
            mode57_control_uvs,
            state.texture_requests,
            False,
            False,
            False,
        )
    if emit_ordinary_faces and not used_cached_satellite_faces:
        ordinary_faces_for_node = (
            ordinary_topology["faces"]
            if effect_topology is not None
            else faces_for_node
        )
        deferred_other_faces = _collect_cached_deferred_faces(
            ordinary_topology,
            world_vertices,
            c_in_values,
            control_uvs,
            target_buffers,
            indexed_flat_pending,
            mode4_pending,
            mode57_pending,
            mode57_vertices,
            mode57_control_uvs,
            state.texture_requests,
            build_uncached_wireframe,
            state.wireframe_only,
            state.satellite_view,
            gamemem=gamemem,
            state=state,
            owner_addrs_override=owner_addrs_override,
        )
        if deferred_other_faces is not None:
            ordinary_faces_for_node = deferred_other_faces
    elif emit_ordinary_faces:
        # The satellite batch emits its wireframe and solid faces above, then
        # returns preserved source modes such as animated explosion billboards.
        ordinary_faces_for_node = faces_for_node
    else:
        ordinary_faces_for_node = ()
    if emit_ordinary_faces and len(ordinary_faces_for_node) > 0:
        _emit_remaining_faces(
            ordinary_faces_for_node,
            world_vertices,
            control_uvs,
            target_buffers,
            state.texture_requests,
            satellite_view=state.satellite_view,
            satellite_primitive_gates=state.camera.get(
                "satellite_primitive_gates",
                (True, True),
            ),
        )
    return True


def _read_vertices_np(
    gamemem,
    topology,
    block_data,
    block_header,
    nvert,
    matrix,
    vertex_requirements,
):
    nvert = int(nvert)
    decode_positions = bool(vertex_requirements.get("positions", True))
    decode_c_in = bool(vertex_requirements["c_in"])
    decode_control_uvs = bool(vertex_requirements["control_uvs"])
    use_matrix = matrix is not None
    # Matrix-owned instances consume immutable local coordinates and authored
    # attributes, so their typed asset can follow the validated topology/header
    # cache. Cached-world fallback paths remain live guest-memory reads because
    # the game may refresh those coordinates independently.
    # Matrix-owned local vertex assets depend on the state/count/layout prefix.
    # The native renderer mutates block +0x10 as live scratch/state, but this
    # extractor does not consume it when decoding the immutable local stream.
    asset_key = bytes(block_header[:BLOCK_VERTEX_LAYOUT_SIZE])
    vertex_asset = topology.get("vertex_asset") if use_matrix else None
    if vertex_asset is None or vertex_asset["key"] != asset_key:
        vertex_data = gamemem.read_runtime_bytes(
            block_data + HEADER_SIZE,
            nvert * VERTEX_STRIDE,
        )
        local_vertices = (
            np.empty((nvert, 3), dtype=np.int64)
            if use_matrix
            else np.empty((0, 3), dtype=np.int64)
        )
        cached_world_vertices = (
            np.empty((0, 3), dtype=np.int64)
            if use_matrix
            else np.empty((nvert, 3), dtype=np.int64)
        )
        asset_c_in_values = np.empty(nvert, dtype=np.uint8)
        asset_control_uvs = np.empty((nvert, 2), dtype=np.int32)
        vertex_bytes = np.frombuffer(
            vertex_data,
            dtype=np.uint8,
            count=nvert * VERTEX_STRIDE,
        )
        decode_geometry_vertex_asset(
            local_vertices,
            cached_world_vertices,
            asset_c_in_values,
            asset_control_uvs,
            vertex_bytes,
            nvert,
            VERTEX_STRIDE,
            use_matrix,
            not use_matrix,
        )
        vertex_asset = {
            "key": asset_key,
            "local_vertices": local_vertices,
            "cached_world_vertices": cached_world_vertices,
            "c_in_values": asset_c_in_values,
            "control_uvs": asset_control_uvs,
            "world_matrix": None,
            "world_vertices": None,
        }
        if use_matrix:
            topology["vertex_asset"] = vertex_asset

    if not decode_positions:
        world_vertices = np.empty((0, 3), dtype=np.int64)
    elif use_matrix:
        world_vertices = vertex_asset.get("world_vertices")
        if (
            world_vertices is None
            or vertex_asset.get("world_matrix") != matrix
        ):
            if isinstance(matrix, dict):
                rotation = np.asarray(
                    matrix["rotation"],
                    dtype=np.float64,
                ).reshape((3, 3))
                translation = np.asarray(
                    matrix["translation"],
                    dtype=np.float64,
                )
                world_vertices = (
                    vertex_asset["local_vertices"].astype(np.float64)
                    @ rotation.T
                ) + translation
            else:
                matrix_bytes = np.frombuffer(
                    matrix,
                    dtype=np.uint8,
                    count=ENTITY_MATRIX_SIZE,
                )
                world_vertices = np.empty((nvert, 3), dtype=np.int64)
                transform_geometry_vertex_asset(
                    world_vertices,
                    vertex_asset["local_vertices"],
                    matrix_bytes,
                    ENTITY_TRANSLATION_OFFSET,
                )
            vertex_asset["world_matrix"] = matrix
            vertex_asset["world_vertices"] = world_vertices
    else:
        world_vertices = vertex_asset["cached_world_vertices"]
    c_in_values = (
        vertex_asset["c_in_values"]
        if decode_c_in
        else np.empty(0, dtype=np.uint8)
    )
    control_uvs = (
        vertex_asset["control_uvs"]
        if decode_control_uvs
        else np.empty((0, 2), dtype=np.int32)
    )
    return world_vertices, c_in_values, control_uvs


def _read_entity_matrix_for_vertices(
    gamemem,
    use_entity_transform,
    entity_ref,
    entity_vertex_mode,
    matrix_override=None,
    smooth_owner_matrices=None,
):
    if use_entity_transform and entity_vertex_mode == ENTITY_VERTEX_MODE_CACHED:
        return None
    if use_entity_transform and entity_ref:
        smooth_matrix = (
            smooth_owner_matrices.get(int(entity_ref))
            if smooth_owner_matrices is not None
            else None
        )
        if smooth_matrix is not None:
            return smooth_matrix
        if matrix_override is not None:
            return matrix_override
        return gamemem.read_runtime_bytes(
            entity_ref + ENTITY_MATRIX_OFFSET,
            ENTITY_MATRIX_SIZE,
        )
    return None


def _read_face_topology(
    gamemem,
    face_base,
    face_headers,
    nfaces,
    nvert,
    stable_cache=None,
    stable_key=None,
    validate_stable_topology=False,
    volatility_record=None,
):
    header_topology = _read_face_header_topology(
        face_base,
        face_headers,
        nfaces,
    )
    face_info = header_topology["face_info"]
    stable_topology = None
    index_span_base = None
    index_span = None
    index_span_already_read = False
    volatile_topology = False
    probe_volatile_topology = False
    if volatility_record is not None and volatility_record.get(
        "volatile",
        False,
    ):
        volatile_topology = True
        volatility_record["bypasses"] = int(
            volatility_record.get("bypasses", 0)
        ) + 1
    if (
        stable_cache is not None
        and stable_key is not None
        and not volatile_topology
    ):
        volatile_key = ("stable_volatile", stable_key)
        volatile_marker = stable_cache.get(volatile_key)
        if volatile_marker is not None:
            bypasses = int(volatile_marker.get("bypasses", 0)) + 1
            volatile_marker["bypasses"] = bypasses
            probe_volatile_topology = (
                bypasses % STABLE_TOPOLOGY_VOLATILE_PROBE_INTERVAL == 0
            )
            volatile_topology = not probe_volatile_topology
        if not volatile_topology:
            stable_topology = stable_cache.get(stable_key)
        if stable_topology is not None:
            topology_matches = True
            if validate_stable_topology:
                index_span_base, index_span = _read_index_span(
                    gamemem,
                    face_info,
                )
                index_span_already_read = True
                topology_matches = _stable_topology_matches_index_span(
                    stable_topology,
                    index_span_base,
                    index_span,
                )
            if topology_matches:
                faces = _compose_faces_from_stable_topology(
                    face_info,
                    stable_topology,
                )
                if faces is not None:
                    stable_cache.pop(("stable_failures", stable_key), None)
                    stable_cache.pop(("stable_volatile", stable_key), None)
                    return {
                        "faces": faces,
                        "wireframe_owner_addrs": header_topology[
                            "wireframe_owner_addrs"
                        ],
                        "source_face_flags": header_topology[
                            "source_face_flags"
                        ],
                        "vertex_requirements": header_topology[
                            "vertex_requirements"
                        ],
                        "index_span_base": int(
                            stable_topology.get("index_span_base", 0)
                        ),
                        "index_span": stable_topology.get("index_span"),
                        "topology_volatile": False,
                    }
            if volatility_record is not None:
                invalidations = int(
                    volatility_record.get("invalidations", 0)
                ) + 1
                volatility_record["invalidations"] = invalidations
                if (
                    invalidations >= STABLE_TOPOLOGY_VOLATILE_THRESHOLD
                    and not volatility_record.get("volatile", False)
                ):
                    volatility_record["volatile"] = True
                    volatile_topology = True
            failure_key = ("stable_failures", stable_key)
            if not volatile_topology:
                failure_count = int(stable_cache.get(failure_key, 0)) + 1
                _topology_cache_put(stable_cache, failure_key, failure_count)
                if failure_count >= STABLE_TOPOLOGY_VOLATILE_THRESHOLD:
                    volatile_topology = True
                    marker = stable_cache.get(("stable_volatile", stable_key))
                    if marker is None:
                        _topology_cache_put(
                            stable_cache,
                            ("stable_volatile", stable_key),
                            {"bypasses": 0},
                        )

    stable_topology = _read_stable_face_topology(
        gamemem,
        face_info,
        nvert,
        index_span_base=index_span_base,
        index_span=index_span,
        index_span_already_read=index_span_already_read,
    )
    if (
        stable_cache is not None
        and stable_key is not None
        and (not volatile_topology or probe_volatile_topology)
    ):
        _topology_cache_put(stable_cache, stable_key, stable_topology)

    faces = _compose_faces_from_stable_topology(
        face_info,
        stable_topology,
    )
    return {
        "faces": faces or (),
        "wireframe_owner_addrs": header_topology["wireframe_owner_addrs"],
        "source_face_flags": header_topology["source_face_flags"],
        "vertex_requirements": header_topology["vertex_requirements"],
        "index_span_base": int(stable_topology.get("index_span_base", 0)),
        "index_span": stable_topology.get("index_span"),
        "topology_volatile": bool(volatile_topology),
    }


def _read_face_header_topology(
    face_base,
    face_headers,
    nfaces,
):
    face_info = []
    wireframe_owner_addrs = {}
    source_face_flags = {}
    has_mode57 = False
    needs_legacy_vertices = False
    needs_c_in = False
    needs_control_uvs = False
    for face_index in range(nfaces):
        face_offset = face_index * FACE_STRIDE
        face_flags = _u16(face_headers, face_offset + 0x00)
        face_vertex_count = _u16(face_headers, face_offset + 0x02) & 0xFF
        index_offset = _u32(face_headers, face_offset + 0x04)
        owner_addr = _u32(face_headers, face_offset + 0x20)
        wireframe_palette_index = WIREFRAME_DEFAULT_PALETTE_INDEX
        mode = (face_flags >> 12) & 0xF
        if mode not in SUPPORTED_FACE_MODES:
            continue
        min_vertices = 1 if face_vertex_count <= 2 else (2 if mode == MODE_POLYLINE else 3)
        if face_vertex_count < min_vertices or face_vertex_count > MAX_FACE_VERTICES:
            continue

        is_mode57 = mode in TEXTURED_FACE_MODES
        has_mode57 = has_mode57 or is_mode57
        needs_legacy_vertices = (
            needs_legacy_vertices
            or face_vertex_count <= 2
            or not is_mode57
        )
        needs_c_in = needs_c_in or mode == MODE_ILLUMINATE
        needs_control_uvs = needs_control_uvs or mode == MODE_BILLBOARD

        index_addr = face_base + face_offset + index_offset
        normal = (
            _i32(face_headers, face_offset + 0x14),
            _i32(face_headers, face_offset + 0x18),
            _i32(face_headers, face_offset + 0x1C),
        )
        face_info.append(
            (
                face_index,
                face_flags,
                mode,
                face_vertex_count,
                wireframe_palette_index,
                index_addr,
                normal,
                0,
            )
        )
        wireframe_owner_addrs[int(face_index)] = int(owner_addr)
        source_face_flags[int(face_index)] = int(face_flags)

    return {
        "face_info": tuple(face_info),
        "wireframe_owner_addrs": wireframe_owner_addrs,
        "source_face_flags": source_face_flags,
        "vertex_requirements": {
            "mode57": has_mode57,
            "legacy": needs_legacy_vertices,
            "c_in": needs_c_in,
            "control_uvs_legacy": needs_control_uvs,
        },
    }


def _read_stable_face_topology(
    gamemem,
    face_info,
    nvert,
    index_span_base=None,
    index_span=None,
    index_span_already_read=False,
):
    if not index_span_already_read:
        index_span_base, index_span = _read_index_span(gamemem, face_info)
    entries = []
    entries_by_face_index = {}
    invalid_face_indices = []

    for (
        face_index,
        _face_flags,
        mode,
        face_vertex_count,
        _wireframe_palette_index,
        index_addr,
        _normal,
        _owner_class,
    ) in face_info:
        indices = _read_face_indices(
            gamemem,
            index_addr,
            face_vertex_count,
            index_span_base,
            index_span,
        )
        if indices is None or any(index >= nvert for index in indices):
            invalid_face_indices.append(face_index)
            continue
        entry = (
            face_index,
            mode,
            face_vertex_count,
            index_addr,
            tuple(indices),
        )
        entries.append(entry)
        entries_by_face_index[face_index] = entry

    return {
        "entries": tuple(entries),
        "entries_by_face_index": entries_by_face_index,
        "invalid_face_indices": frozenset(invalid_face_indices),
        "index_span_base": int(index_span_base),
        "index_span": None if index_span is None else bytes(index_span),
    }


def _stable_topology_matches_index_span(
    stable_topology,
    index_span_base,
    index_span,
):
    if index_span is None:
        return False
    return (
        int(stable_topology.get("index_span_base", -1)) == int(index_span_base)
        and stable_topology.get("index_span") == bytes(index_span)
    )


def _live_topology_index_span_matches(gamemem, topology):
    if not topology:
        return False
    expected = topology.get("index_span")
    if expected is None:
        return False
    if not expected:
        return True
    index_span_base = int(topology.get("index_span_base", 0))
    if index_span_base <= 0:
        return False
    current = gamemem.read_runtime_bytes(
        index_span_base,
        len(expected),
    )
    return current == expected


def _topology_volatility_record(
    state,
    source,
    node_addr,
    entity_ref,
    stable_topology_key,
):
    records = state.topology_volatility
    if records is None:
        return None
    if int(entity_ref or 0):
        owner_key = ("entity", int(source), int(entity_ref))
    else:
        owner_key = ("node", int(source), int(node_addr))
    record = records.get(owner_key)
    if record is None:
        record = {
            "last_stable_key": stable_topology_key,
            "key_changes": 0,
            "invalidations": 0,
            "bypasses": 0,
            "volatile": False,
        }
        records[owner_key] = record
        return record

    if record.get("last_stable_key") != stable_topology_key:
        record["last_stable_key"] = stable_topology_key
        key_changes = int(record.get("key_changes", 0)) + 1
        record["key_changes"] = key_changes
        if (
            key_changes >= STABLE_TOPOLOGY_VOLATILE_THRESHOLD
            and not record.get("volatile", False)
        ):
            record["volatile"] = True
    return record


def _live_topology_headers_match(live_topology, face_headers, nfaces):
    previous = live_topology.get("face_headers")
    if previous == face_headers:
        return True
    if previous is None or len(previous) != len(face_headers):
        return False
    return bool(
        face_source_headers_equal(
            np.frombuffer(previous, dtype=np.uint8),
            np.frombuffer(face_headers, dtype=np.uint8),
            int(nfaces),
            FACE_STRIDE,
        )
    )


def _refresh_live_topology_normals(
    topology,
    face_headers,
    *,
    refresh_faces=False,
):
    if refresh_faces:
        faces = topology.get("faces", ())
        refreshed_faces = []
        for face in faces:
            face_offset = int(face[0]) * FACE_STRIDE + 0x14
            normal = (
                _i32(face_headers, face_offset),
                _i32(face_headers, face_offset + 4),
                _i32(face_headers, face_offset + 8),
            )
            if normal == face[4]:
                refreshed_faces.append(face)
            else:
                refreshed_faces.append((*face[:4], normal, *face[5:]))
        topology["faces"] = tuple(refreshed_faces)
        # Presentation adapters share one compiled plan. Rebuild it after a
        # source-normal refresh so every view observes the same geometry.
        topology.pop("mesh_plan", None)

    plan = _compiled_mesh_plan(topology)
    batch = _compiled_deferred_adapter(topology, plan)
    indexed = batch["indexed"]
    mode4 = batch["mode4"]
    mode57 = batch["mode57"]
    refresh_deferred_face_normals(
        np.frombuffer(face_headers, dtype=np.uint8),
        batch["indexed_face_indices"],
        indexed[4],
        batch["mode4_face_indices"],
        mode4[3],
        batch["mode57_face_indices"],
        mode57[3],
        FACE_STRIDE,
    )
    batch["normal_generation"] = int(batch.get("normal_generation", 0)) + 1


def _topology_cache_put(cache, key, value):
    cache[key] = value
    if len(cache) <= MAX_TOPOLOGY_CACHE_ENTRIES:
        return
    remove_count = MAX_TOPOLOGY_CACHE_ENTRIES // 4
    for old_key in tuple(cache)[:remove_count]:
        cache.pop(old_key, None)


def _compose_faces_from_stable_topology(
    face_info,
    stable_topology,
):
    entries = stable_topology.get("entries_by_face_index")
    if entries is None:
        entries = {
            int(entry[0]): entry
            for entry in stable_topology.get("entries", ())
        }
    invalid_face_indices = stable_topology.get("invalid_face_indices", ())
    faces = []
    for (
        face_index,
        face_flags,
        mode,
        face_vertex_count,
        wireframe_palette_index,
        index_addr,
        normal,
        owner_class,
    ) in face_info:
        entry = entries.get(face_index)
        if entry is None:
            if face_index in invalid_face_indices:
                continue
            return None

        (
            _entry_face_index,
            entry_mode,
            entry_face_vertex_count,
            entry_index_addr,
            indices,
        ) = entry
        if (
            mode != entry_mode
            or face_vertex_count != entry_face_vertex_count
            or index_addr != entry_index_addr
        ):
            return None
        faces.append(
            (
                face_index,
                face_flags,
                mode,
                indices,
                normal,
                wireframe_palette_index,
                owner_class,
            )
        )

    return tuple(faces)


def _append_numpy_values(target, values):
    values = np.ascontiguousarray(values)
    if values.size <= 0:
        return
    target.frombytes(values.tobytes())


def _vertices_numeric_array(world_vertices):
    if isinstance(world_vertices, np.ndarray):
        if (
            world_vertices.dtype in (np.int64, np.float64)
            and world_vertices.flags.c_contiguous
        ):
            return world_vertices
        dtype = (
            np.float64
            if np.issubdtype(world_vertices.dtype, np.floating)
            else np.int64
        )
        return np.ascontiguousarray(world_vertices, dtype=dtype)
    if not world_vertices:
        return np.empty((0, 3), dtype=np.int64)
    values = np.asarray(world_vertices)
    dtype = (
        np.float64 if np.issubdtype(values.dtype, np.floating) else np.int64
    )
    return np.ascontiguousarray(values, dtype=dtype)


def _control_uvs_i32_array(control_uvs, vertex_count):
    if isinstance(control_uvs, np.ndarray):
        if control_uvs.shape[0] >= vertex_count:
            if control_uvs.dtype == np.int32 and control_uvs.flags.c_contiguous:
                if control_uvs.shape[0] == vertex_count:
                    return control_uvs
                return control_uvs[:vertex_count]
            return np.ascontiguousarray(control_uvs[:vertex_count], dtype=np.int32)
    if control_uvs and len(control_uvs) >= vertex_count:
        return np.ascontiguousarray(
            np.asarray(control_uvs[:vertex_count], dtype=np.int32)
        )
    return np.zeros((int(vertex_count), 2), dtype=np.int32)


def _c_in_u8_array(c_in_values, vertex_count):
    if isinstance(c_in_values, np.ndarray):
        if c_in_values.shape[0] >= vertex_count:
            if c_in_values.dtype == np.uint8 and c_in_values.flags.c_contiguous:
                if c_in_values.shape[0] == vertex_count:
                    return c_in_values
                return c_in_values[:vertex_count]
            return np.ascontiguousarray(c_in_values[:vertex_count], dtype=np.uint8)
        return np.zeros(int(vertex_count), dtype=np.uint8)
    if len(c_in_values) >= vertex_count:
        return np.ascontiguousarray(np.asarray(c_in_values[:vertex_count], dtype=np.uint8))
    return np.zeros(int(vertex_count), dtype=np.uint8)


def _face_vertex_requirements(faces):
    modes = tuple(int(face[2]) for face in faces)
    has_mode3 = MODE_BILLBOARD in modes
    has_mode57 = any(mode in TEXTURED_FACE_MODES for mode in modes)
    return {
        "mode57": has_mode57,
        "legacy": any(
            len(face[3]) <= 2 or mode not in TEXTURED_FACE_MODES
            for face, mode in zip(faces, modes)
        ),
        "c_in": MODE_ILLUMINATE in modes,
        "control_uvs_legacy": has_mode3,
    }


def _compiled_mesh_plan(topology):
    plan = topology.get("mesh_plan")
    if plan is not None:
        return plan

    faces = topology.get("faces", ())
    face_count = len(faces)
    owner_addrs_by_face = topology.get("wireframe_owner_addrs", {})
    source_flags_by_face = topology.get("source_face_flags", {})

    face_ids = np.empty(face_count, dtype=np.int32)
    face_flags = np.empty(face_count, dtype=np.int64)
    source_face_flags = np.empty(face_count, dtype=np.int64)
    face_modes = np.empty(face_count, dtype=np.uint8)
    face_counts = np.empty(face_count, dtype=np.uint8)
    face_indices = np.empty((face_count, MAX_FACE_VERTICES), dtype=np.uint16)
    face_normals = np.empty((face_count, 3), dtype=np.int64)
    face_owner_slots = np.empty(face_count, dtype=np.uint16)

    owner_addrs = []
    owner_slots = {}

    for row, face in enumerate(faces):
        face_index, flags, mode, indices, normal, _palette, _owner = face
        face_index = int(face_index)
        flags = int(flags)
        mode = int(mode)
        count = len(indices)
        owner_addr = int(owner_addrs_by_face.get(face_index, 0))
        owner_slot = owner_slots.get(owner_addr)
        if owner_slot is None:
            owner_slot = len(owner_addrs)
            owner_slots[owner_addr] = owner_slot
            owner_addrs.append(owner_addr)

        face_ids[row] = face_index
        source_flags = int(source_flags_by_face.get(face_index, flags))
        face_flags[row] = flags
        source_face_flags[row] = source_flags
        face_modes[row] = mode
        face_counts[row] = count
        face_indices[row, :count] = indices
        face_normals[row] = normal
        face_owner_slots[row] = owner_slot
    plan = MeshPlan(
        face_ids=face_ids,
        face_flags=face_flags,
        source_face_flags=source_face_flags,
        face_modes=face_modes,
        face_counts=face_counts,
        face_indices=face_indices,
        face_normals=face_normals,
        face_owner_slots=face_owner_slots,
        owner_addrs=tuple(owner_addrs),
    )
    topology["mesh_plan"] = plan
    return plan


def _owner_class(gamemem, owner_addr, cache):
    owner_addr = int(owner_addr)
    owner_class = cache.get(owner_addr)
    if owner_class is None:
        owner_class = (
            int(gamemem.read_runtime_u16(owner_addr + 0x02))
            if owner_addr else 0
        )
        cache[owner_addr] = owner_class
    return int(owner_class)


def _component_lighting_policy(gamemem, state, owner_addr):
    """Return 0, damage 1..15 toward shade 0, or 16..30 toward shade 15."""
    owner_addr = int(owner_addr)
    cached = state.component_lighting_cache.get(owner_addr)
    if cached is not None:
        return int(cached)
    if owner_addr == 0:
        policy = 0
    else:
        owner_head = gamemem.read_runtime_bytes(owner_addr, 4)
        component_state = _u16(owner_head, 0x00)
        owner_class_flags = _u16(owner_head, 0x02)
        component_damage = (component_state & 0x00F0) >> 4
        eligible = (
            (owner_class_flags & 0x0100) != 0
            or (owner_class_flags & 0x00F0) == 0x0050
        )
        policy = component_damage if eligible else 0
        if policy and int(state.lighting["component_lighting_mode"]) != 0:
            policy += 15
    state.component_lighting_cache[owner_addr] = int(policy)
    return int(policy)


def _component_policies_for_plan(
    gamemem,
    state,
    plan,
    owner_addrs_override=None,
):
    owner_addrs = owner_addrs_override or plan.owner_addrs
    policies = tuple(
        _component_lighting_policy(gamemem, state, owner_addr)
        for owner_addr in owner_addrs
    )
    return policies or (0,)


def _component_policies_for_faces(
    gamemem,
    state,
    topology,
    faces,
    owner_addr_override=None,
):
    owner_addrs = topology.get("wireframe_owner_addrs", {})
    return tuple(
        _component_lighting_policy(
            gamemem,
            state,
            (
                int(owner_addr_override)
                if owner_addr_override is not None
                else int(owner_addrs.get(int(face[0]), 0))
            ),
        )
        for face in faces
    )


def _target_flat_presentation_topology(
    gamemem,
    topology,
    state,
    owner_addrs_override=None,
):
    plan = _compiled_mesh_plan(topology)
    owner_addrs = owner_addrs_override or plan.owner_addrs
    owner_classes = tuple(
        _owner_class(
            gamemem,
            owner_addr,
            state.target_owner_class_cache,
        )
        for owner_addr in owner_addrs
    )
    cached = plan.target_flat
    if cached is not None and cached[0] == owner_classes:
        return cached[1]

    faces = topology.get("faces", ())
    adapted_faces = [None] * len(faces)
    for row, face in enumerate(faces):
        face_index, flags, mode, indices, normal, palette, _source_owner = face
        owner_class = owner_classes[int(plan.face_owner_slots[row])]
        effective_flags = int(flags)
        effective_mode = int(mode)
        if effective_mode in TEXTURED_FACE_MODES:
            converted_flags = _target_flat_face_flags(
                effective_flags,
                owner_class,
            )
            if converted_flags is not None:
                effective_flags = converted_flags
                effective_mode = MODE_FLAT_LIT

        adapted_faces[row] = (
            face_index,
            effective_flags,
            effective_mode,
            indices,
            normal,
            palette,
            owner_class,
        )

    adapted_faces = tuple(adapted_faces)
    vertex_requirements = _face_vertex_requirements(adapted_faces)
    adapted = {
        "faces": adapted_faces,
        "wireframe_owner_addrs": topology.get("wireframe_owner_addrs", {}),
        "source_face_flags": topology.get("source_face_flags", {}),
        "vertex_requirements": vertex_requirements,
        "index_span_base": topology.get("index_span_base", 0),
        "index_span": topology.get("index_span"),
        "topology_volatile": topology.get("topology_volatile", False),
    }
    plan.target_flat = (owner_classes, adapted)
    return adapted


def _compiled_deferred_adapter(topology, plan):
    cached = plan.deferred
    if cached is not None:
        return cached

    indexed_count = 0
    mode4_count = 0
    mode3_count = 0
    mode57_count = 0
    other_count = 0
    for row in range(len(plan.face_ids)):
        mode = int(plan.face_modes[row])
        count = int(plan.face_counts[row])
        if count < 3 or mode == MODE_POLYLINE:
            other_count += 1
            continue
        if mode in (MODE_FLAT_UNLIT, MODE_FLAT_LIT):
            indexed_count += 1
        elif mode == MODE_BILLBOARD:
            mode3_count += 1
        elif mode in TEXTURED_FACE_MODES:
            mode57_count += 1
        else:
            # The native polygon dispatcher and the legacy extractor both
            # route otherwise-unhandled polygon modes through mode 4. Terrain
            # assets depend on this fallback for their illuminated faces.
            mode4_count += 1

    indexed_modes = np.empty(indexed_count, dtype=np.uint8)
    indexed_flags = np.empty(indexed_count, dtype=np.int64)
    indexed_counts = np.empty(indexed_count, dtype=np.uint8)
    indexed_indices = np.empty(
        (indexed_count, MAX_FACE_VERTICES),
        dtype=np.uint16,
    )
    indexed_normals = np.empty((indexed_count, 3), dtype=np.int64)
    indexed_owner_slots = np.empty(indexed_count, dtype=np.uint16)
    indexed_face_indices = np.empty(indexed_count, dtype=np.int32)
    mode4_flags = np.empty(mode4_count, dtype=np.int64)
    mode4_counts = np.empty(mode4_count, dtype=np.uint8)
    mode4_indices = np.empty(
        (mode4_count, MAX_FACE_VERTICES),
        dtype=np.uint16,
    )
    mode4_normals = np.empty((mode4_count, 3), dtype=np.int64)
    mode4_owner_slots = np.empty(mode4_count, dtype=np.uint16)
    mode4_face_indices = np.empty(mode4_count, dtype=np.int32)
    mode3_descs = np.empty(mode3_count, dtype=np.int16)
    mode3_counts = np.empty(mode3_count, dtype=np.uint8)
    mode3_indices = np.empty(
        (mode3_count, MAX_FACE_VERTICES),
        dtype=np.uint16,
    )
    mode57_descs = np.empty(mode57_count, dtype=np.int16)
    mode57_counts = np.empty(mode57_count, dtype=np.uint8)
    mode57_indices = np.empty(
        (mode57_count, MAX_FACE_VERTICES),
        dtype=np.uint16,
    )
    mode57_normals = np.empty((mode57_count, 3), dtype=np.int64)
    mode57_owner_slots = np.empty(mode57_count, dtype=np.uint16)
    mode57_face_indices = np.empty(mode57_count, dtype=np.int32)
    other_faces = [None] * other_count

    indexed_pos = 0
    mode4_pos = 0
    mode3_pos = 0
    mode57_pos = 0
    other_pos = 0
    texture_requests = set()
    faces = topology.get("faces", ())
    for row in range(len(plan.face_ids)):
        face_index = int(plan.face_ids[row])
        flags = int(plan.face_flags[row])
        mode = int(plan.face_modes[row])
        count = int(plan.face_counts[row])
        indices = plan.face_indices[row]
        normal = plan.face_normals[row]
        owner_slot = plan.face_owner_slots[row]
        if count < 3 or mode == MODE_POLYLINE:
            other_faces[other_pos] = faces[row]
            other_pos += 1
            continue
        if mode in (MODE_FLAT_UNLIT, MODE_FLAT_LIT):
            indexed_modes[indexed_pos] = mode
            indexed_flags[indexed_pos] = flags
            indexed_counts[indexed_pos] = count
            indexed_indices[indexed_pos, :count] = indices[:count]
            indexed_normals[indexed_pos] = normal
            indexed_owner_slots[indexed_pos] = owner_slot
            indexed_face_indices[indexed_pos] = face_index
            indexed_pos += 1
        elif mode == MODE_BILLBOARD:
            desc_idx = _mode3_texture_desc_index(flags)
            mode3_descs[mode3_pos] = desc_idx
            mode3_counts[mode3_pos] = count
            mode3_indices[mode3_pos, :count] = indices[:count]
            texture_requests.add(desc_idx)
            mode3_pos += 1
        elif mode in TEXTURED_FACE_MODES:
            desc_idx = _mode57_texture_desc_index(flags)
            mode57_descs[mode57_pos] = desc_idx
            mode57_counts[mode57_pos] = count
            mode57_indices[mode57_pos, :count] = indices[:count]
            mode57_normals[mode57_pos] = normal
            mode57_owner_slots[mode57_pos] = owner_slot
            mode57_face_indices[mode57_pos] = face_index
            texture_requests.add(desc_idx)
            mode57_pos += 1
        else:
            mode4_flags[mode4_pos] = flags
            mode4_counts[mode4_pos] = count
            mode4_indices[mode4_pos, :count] = indices[:count]
            mode4_normals[mode4_pos] = normal
            mode4_owner_slots[mode4_pos] = owner_slot
            mode4_face_indices[mode4_pos] = face_index
            mode4_pos += 1

    cached = {
        "indexed": (
            indexed_modes,
            indexed_flags,
            indexed_counts,
            indexed_indices,
            indexed_normals,
        ),
        "indexed_face_indices": indexed_face_indices,
        "indexed_owner_slots": indexed_owner_slots,
        "mode4": (
            mode4_flags,
            mode4_counts,
            mode4_indices,
            mode4_normals,
        ),
        "mode4_face_indices": mode4_face_indices,
        "mode4_owner_slots": mode4_owner_slots,
        "mode3": (mode3_descs, mode3_counts, mode3_indices),
        "mode3_groups": _build_mode3_groups(mode3_descs),
        "mode57": (
            mode57_descs,
            mode57_counts,
            mode57_indices,
            mode57_normals,
        ),
        "mode57_face_indices": mode57_face_indices,
        "mode57_owner_slots": mode57_owner_slots,
        "texture_requests": tuple(sorted(texture_requests)),
        "other_faces": tuple(other_faces),
        "normal_generation": 0,
    }
    plan.deferred = cached
    return cached


def _enhanced_imaging_effect_topologies(
    topology,
    selected_descriptors,
):
    plan = _compiled_mesh_plan(topology)
    deferred = _compiled_deferred_adapter(topology, plan)
    selected_descriptors = frozenset(selected_descriptors)
    if plan.enhanced_effects is None:
        plan.enhanced_effects = (
            frozenset(int(value) for value in deferred["mode57"][0]),
            {},
        )
    candidate_descriptors, cached_views = plan.enhanced_effects
    relevant_descriptors = candidate_descriptors & selected_descriptors
    cached = cached_views.get(relevant_descriptors)
    if cached is not None:
        cached_wireframe_topology = cached[0]
        return (
            topology
            if cached_wireframe_topology is None
            else cached_wireframe_topology,
            *cached[1:],
        )

    effect_faces = []
    effect_face_ids = []
    for row, face in enumerate(topology.get("faces", ())):
        mode = int(plan.face_modes[row])
        flags = int(plan.face_flags[row])
        selected = mode == MODE_BILLBOARD or (
            mode in TEXTURED_FACE_MODES
            and _mode57_texture_desc_index(flags) in relevant_descriptors
        )
        if not selected:
            continue
        effect_faces.append(face)
        effect_face_ids.append(int(plan.face_ids[row]))

    effect_faces = tuple(effect_faces)
    requirements = _face_vertex_requirements(effect_faces)
    effect_topology = None
    wireframe_topology = topology
    if effect_faces:
        effect_topology = dict(topology)
        effect_topology["faces"] = effect_faces
        effect_topology["vertex_requirements"] = requirements
        effect_topology.pop("mesh_plan", None)
        wireframe_topology = _topology_without_face_ids(
            topology,
            effect_face_ids,
        )
    # A None sentinel means "use the caller's topology". Caching the topology
    # itself here would create topology -> plan -> view -> topology cycles and
    # defer their cleanup to expensive cyclic-GC passes.
    cached_wireframe_topology = (
        None if wireframe_topology is topology else wireframe_topology
    )
    cached = (
        cached_wireframe_topology,
        effect_topology,
        deferred["texture_requests"],
    )
    cached_views[relevant_descriptors] = cached
    return (
        wireframe_topology,
        effect_topology,
        deferred["texture_requests"],
    )


def _compiled_enhanced_wireframe_adapter(topology, plan):
    cached = plan.enhanced_wireframe
    if cached is not None:
        return cached

    polygon_count = 0
    for row in range(len(plan.face_ids)):
        count = int(plan.face_counts[row])
        mode = int(plan.face_modes[row])
        if count < 3:
            continue
        polygon_count += 1

    face_counts = np.empty(polygon_count, dtype=np.uint8)
    face_indices = np.empty(
        (polygon_count, MAX_FACE_VERTICES),
        dtype=np.uint16,
    )
    face_owner_slots = np.empty(polygon_count, dtype=np.uint16)
    face_emit_occluder = np.empty(polygon_count, dtype=np.uint8)
    owner_addrs = []
    owner_slots = {}
    explicit_lines = []
    polygon_pos = 0
    for row in range(len(plan.face_ids)):
        flags = int(plan.face_flags[row])
        mode = int(plan.face_modes[row])
        count = int(plan.face_counts[row])
        source_indices = plan.face_indices[row, :count]
        if count == 2:
            explicit_lines.append(
                (source_indices, _mode0_palette_index(flags), False)
            )
            continue
        if count < 3:
            continue
        owner_addr = plan.owner_addrs[int(plan.face_owner_slots[row])]
        owner_slot = owner_slots.get(owner_addr)
        if owner_slot is None:
            owner_slot = len(owner_addrs)
            owner_slots[owner_addr] = owner_slot
            owner_addrs.append(owner_addr)
        face_counts[polygon_pos] = count
        face_indices[polygon_pos, :count] = plan.face_indices[row, :count]
        face_owner_slots[polygon_pos] = owner_slot
        face_emit_occluder[polygon_pos] = int(mode != MODE_POLYLINE)
        if mode == MODE_POLYLINE:
            explicit_lines.append(
                (source_indices, _mode2_palette_index(flags), True)
            )
        polygon_pos += 1

    cached = {
        "face_counts": face_counts,
        "face_indices": face_indices,
        "face_owner_slots": face_owner_slots,
        "face_emit_occluder": face_emit_occluder,
        "owner_addrs": tuple(owner_addrs),
        "explicit_lines": tuple(explicit_lines),
    }
    plan.enhanced_wireframe = cached
    return cached


def _collect_cached_enhanced_wireframe(
    gamemem,
    topology,
    world_vertices,
    buffers,
    wireframe_pending,
    owner_palette_cache,
    exclusive_wireframe,
    owner_addrs_override=None,
):
    plan = _compiled_mesh_plan(topology)
    batch = _compiled_enhanced_wireframe_adapter(topology, plan)
    face_counts = batch["face_counts"]
    if face_counts.size > 0:
        owner_addrs = owner_addrs_override or batch["owner_addrs"]
        owner_palettes = np.empty(len(owner_addrs), dtype=np.uint8)
        for owner_pos, owner_addr in enumerate(owner_addrs):
            palette_index = owner_palette_cache.get(owner_addr)
            if palette_index is None:
                palette_index = _wireframe_palette_index(
                    gamemem,
                    owner_addr,
                )
                owner_palette_cache[owner_addr] = palette_index
            owner_palettes[owner_pos] = palette_index
        face_palettes = np.empty(len(face_counts), dtype=np.uint8)
        fill_wireframe_face_palettes(
            face_palettes,
            batch["face_owner_slots"],
            owner_palettes,
        )
        target_pending = wireframe_pending
        emit_immediately = target_pending is None
        if emit_immediately:
            target_pending = []
        _collect_deferred_wireframe(
            target_pending,
            face_counts,
            batch["face_indices"],
            face_palettes,
            batch["face_emit_occluder"],
            len(face_counts),
            world_vertices,
        )
        if emit_immediately:
            _emit_pending_wireframes(
                target_pending,
                buffers,
                False,
            )

    if exclusive_wireframe:
        # In wireframe-only snapshots these primitives have no ordinary face
        # pass to emit them. Dual snapshots leave both jobs to that normal pass
        # so counts and visible line primitives are not duplicated.
        for indices, palette_index, is_polyline in batch["explicit_lines"]:
            if is_polyline:
                _append_polyline_segments(
                    buffers.line_vertices,
                    world_vertices,
                    indices,
                    palette_index,
                )
            else:
                _append_line_segment(
                    buffers.line_vertices,
                    world_vertices,
                    indices[0],
                    indices[1],
                    palette_index,
                )
    return True


def _satellite_owner_state(gamemem, owner_addr, state):
    owner_addr = int(owner_addr)
    owner_class = _owner_class(
        gamemem,
        owner_addr,
        state.target_owner_class_cache,
    )

    primary = int(owner_class) & 0x0F00
    iff_index = -1
    if primary == 0x0100:
        iff_index = _satellite_primary_iff_index(
            gamemem,
            owner_addr,
            state.satellite_iff_cache,
        )
    elif primary == 0x0200:
        iff_index = _satellite_secondary_iff_index(
            gamemem,
            owner_addr,
            state.satellite_iff_cache,
        )
    return int(owner_class), int(iff_index)


def _satellite_flags_from_owner_state(
    source_flags,
    owner_class,
    iff_index,
    colors,
):
    primary = int(owner_class) & 0x0F00
    secondary = int(owner_class) & 0x00F0
    if primary == 0x0100:
        return int(colors[max(0, min(2, int(iff_index)))])
    if primary == 0x0200:
        return int(colors[3 + max(0, min(2, int(iff_index)))])
    if primary == 0x0400:
        return int(colors[6])
    if primary == 0x0800:
        return _satellite_force_mode4(
            source_flags,
            colors[10],
        )
    if secondary == 0x0040:
        return int(colors[7])
    if secondary == 0x0050:
        return int(colors[8])
    if secondary == 0x0080:
        return int(colors[9])
    if secondary in (0x0010, 0x0020, 0x0060):
        return int(source_flags)
    return _satellite_force_mode4(source_flags, colors[10])


def _build_satellite_emission_batch(faces):
    wireframe_count = 0
    solid_count = 0
    deferred_count = 0
    other_count = 0
    for face in faces:
        indices = face[3]
        mode = int(face[2])
        if len(indices) >= 3 and mode == MODE_SATELLITE_WIREFRAME:
            wireframe_count += 1
        elif len(indices) >= 3 and mode == MODE_SATELLITE_SOLID:
            solid_count += 1
        elif len(indices) >= 3 and mode in DEFERRED_PACKAGED_FACE_MODES - {
            MODE_BILLBOARD,
        }:
            deferred_count += 1
        else:
            other_count += 1

    wireframe_counts = np.empty(wireframe_count, dtype=np.uint8)
    wireframe_indices = np.empty(
        (wireframe_count, MAX_FACE_VERTICES),
        dtype=np.uint16,
    )
    wireframe_palettes = np.empty(wireframe_count, dtype=np.uint8)
    wireframe_occluders = np.empty(wireframe_count, dtype=np.uint8)
    solid_counts = np.empty(solid_count, dtype=np.uint8)
    solid_indices = np.empty(
        (solid_count, MAX_FACE_VERTICES),
        dtype=np.uint16,
    )
    solid_palette_families = np.empty(solid_count, dtype=np.uint8)
    deferred_faces = [None] * deferred_count
    other_faces = [None] * other_count

    wireframe_pos = 0
    solid_pos = 0
    deferred_pos = 0
    other_pos = 0
    for face in faces:
        face_flags = int(face[1])
        mode = int(face[2])
        indices = face[3]
        count = len(indices)
        if count >= 3 and mode == MODE_SATELLITE_WIREFRAME:
            wireframe_counts[wireframe_pos] = count
            for index_pos, vertex_index in enumerate(indices):
                wireframe_indices[wireframe_pos, index_pos] = int(vertex_index)
            wireframe_palettes[wireframe_pos] = int(face[5])
            wireframe_occluders[wireframe_pos] = 1
            wireframe_pos += 1
        elif count >= 3 and mode == MODE_SATELLITE_SOLID:
            solid_counts[solid_pos] = count
            for index_pos, vertex_index in enumerate(indices):
                solid_indices[solid_pos, index_pos] = int(vertex_index)
            solid_palette_families[solid_pos] = face_flags & 0xF0
            solid_pos += 1
        elif count >= 3 and mode in DEFERRED_PACKAGED_FACE_MODES - {
            MODE_BILLBOARD,
        }:
            # Satellite mode 1 uses its low byte as a final, unlit palette
            # index. Normalize it into the ordinary indexed-flat plan.
            deferred_faces[deferred_pos] = (
                face[0],
                int(face_flags) & 0xFF,
                MODE_FLAT_UNLIT,
                *face[3:],
            ) if mode == MODE_FLAT_LIT else face
            deferred_pos += 1
        else:
            other_faces[other_pos] = face
            other_pos += 1

    deferred_topology = None
    if deferred_faces:
        deferred_faces = tuple(deferred_faces)
        vertex_requirements = _face_vertex_requirements(deferred_faces)
        deferred_topology = {
            "faces": deferred_faces,
            "wireframe_owner_addrs": {},
            "source_face_flags": {},
            "vertex_requirements": vertex_requirements,
        }
    return {
        "wireframe_counts": wireframe_counts,
        "wireframe_indices": wireframe_indices,
        "wireframe_palettes": wireframe_palettes,
        "wireframe_occluders": wireframe_occluders,
        "solid_counts": solid_counts,
        "solid_indices": solid_indices,
        "solid_palette_families": solid_palette_families,
        "deferred_topology": deferred_topology,
        "other_faces": tuple(other_faces),
        "normal_generation": 0,
    }


def _satellite_classified_topology(
    gamemem,
    topology,
    state,
    owner_addrs_override=None,
):
    plan = _compiled_mesh_plan(topology)
    owner_addrs = owner_addrs_override or plan.owner_addrs
    colors = tuple(int(value) for value in state.camera.get("satellite_colors", ()))
    if len(colors) < 11:
        colors = SATELLITE_DEFAULT_COLORS
    owner_states = tuple(
        _satellite_owner_state(gamemem, owner_addr, state)
        for owner_addr in owner_addrs
    )
    signature = (tuple(colors), owner_states)
    cached = plan.satellite_source
    if cached is not None and cached.get("signature") == signature:
        return cached

    faces = []
    for row in range(len(plan.face_ids)):
        face_index = int(plan.face_ids[row])
        source_flags = int(plan.source_face_flags[row])
        count = int(plan.face_counts[row])
        indices = plan.face_indices[row, :count]
        normal = plan.face_normals[row]
        owner_slot = int(plan.face_owner_slots[row])
        owner_class, iff_index = owner_states[owner_slot]
        face_flags = _satellite_flags_from_owner_state(
            source_flags,
            owner_class,
            iff_index,
            colors,
        )
        computed_mode = (int(face_flags) & 0x7000) >> 12
        wireframe_palette_index = WIREFRAME_DEFAULT_PALETTE_INDEX
        if computed_mode == 0:
            mode = MODE_SATELLITE_WIREFRAME
            wireframe_palette_index = int(face_flags) & 0xFF
        elif computed_mode == MODE_ILLUMINATE:
            mode = MODE_SATELLITE_SOLID
        else:
            mode = computed_mode
        faces.append(
            (
                face_index,
                face_flags,
                mode,
                indices,
                normal,
                wireframe_palette_index,
                owner_class,
            )
        )

    faces = tuple(faces)
    vertex_requirements = _face_vertex_requirements(faces)
    cached = {
        "signature": signature,
        "faces": faces,
        "emission_batch": _build_satellite_emission_batch(faces),
        "vertex_requirements": vertex_requirements,
    }
    plan.satellite_source = cached
    return cached


def _collect_cached_satellite_faces(
    satellite_topology,
    world_vertices,
    c_in_values,
    control_uvs,
    buffers,
    indexed_flat_pending,
    mode4_pending,
    mode57_pending,
    mode57_world_vertices,
    mode57_control_uvs,
    texture_requests,
    wireframe_pending,
    satellite_mode4_pending,
):
    batch = satellite_topology.get("emission_batch")
    if batch is None:
        return satellite_topology.get("faces", ())

    wireframe_count = len(batch["wireframe_counts"])
    if wireframe_count > 0:
        _collect_deferred_wireframe(
            wireframe_pending,
            batch["wireframe_counts"],
            batch["wireframe_indices"],
            batch["wireframe_palettes"],
            batch["wireframe_occluders"],
            wireframe_count,
            world_vertices,
        )

    solid_count = len(batch["solid_counts"])
    if solid_count > 0:
        satellite_mode4_pending.append(
            (
                _vertices_numeric_array(world_vertices),
                batch["solid_palette_families"],
                batch["solid_counts"],
                batch["solid_indices"],
            )
        )
    deferred_topology = batch.get("deferred_topology")
    if deferred_topology is not None:
        _collect_cached_deferred_faces(
            deferred_topology,
            world_vertices,
            c_in_values,
            control_uvs,
            buffers,
            indexed_flat_pending,
            mode4_pending,
            mode57_pending,
            mode57_world_vertices,
            mode57_control_uvs,
            texture_requests,
            False,
            False,
            False,
        )
    return batch["other_faces"]


def _collect_cached_deferred_faces(
    topology,
    world_vertices,
    c_in_values,
    control_uvs,
    buffers,
    indexed_flat_pending,
    mode4_pending,
    mode57_pending,
    mode57_world_vertices,
    mode57_control_uvs,
    texture_requests,
    build_wireframe,
    wireframe_only,
    satellite_view,
    *,
    gamemem=None,
    state=None,
    owner_addrs_override=None,
):
    if (
        wireframe_only
        or indexed_flat_pending is None
        or mode4_pending is None
        or mode57_pending is None
    ):
        return None
    plan = _compiled_mesh_plan(topology)
    batch = _compiled_deferred_adapter(topology, plan)
    component_policies = (
        _component_policies_for_plan(
            gamemem,
            state,
            plan,
            owner_addrs_override,
        )
        if gamemem is not None and state is not None
        else (0,)
    )
    force_owner_slot_zero = bool(owner_addrs_override)

    texture_requests.update(batch["texture_requests"])

    indexed = batch["indexed"]
    if satellite_view and indexed[0].size > 0:
        satellite_indexed = batch.get("satellite_indexed")
        if satellite_indexed is None:
            modes = indexed[0].copy()
            flags = indexed[1].copy()
            for row in range(len(modes)):
                if int(modes[row]) != MODE_FLAT_LIT:
                    continue
                modes[row] = MODE_FLAT_UNLIT
                flags[row] = (int(flags[row]) & 0xFF) << 4
            satellite_indexed = (modes, flags, *indexed[2:])
            batch["satellite_indexed"] = satellite_indexed
        indexed = satellite_indexed
    mode4 = batch["mode4"]
    mode3 = batch["mode3"]
    mode57 = batch["mode57"]
    needs_world = (
        indexed[0].size > 0
        or mode4[0].size > 0
        or mode3[0].size > 0
    )
    world_vertices_np = (
        _vertices_numeric_array(world_vertices) if needs_world else None
    )
    if indexed[0].size > 0:
        indexed_flat_pending.append(
            (
                world_vertices_np,
                *indexed,
                batch["indexed_owner_slots"],
                component_policies,
                force_owner_slot_zero,
                int(batch.get("normal_generation", 0)),
            )
        )
    if mode4[0].size > 0:
        mode4_pending.append(
            (
                world_vertices_np,
                _c_in_u8_array(c_in_values, len(world_vertices_np)),
                *mode4,
                batch["mode4_owner_slots"],
                component_policies,
                force_owner_slot_zero,
                int(batch.get("normal_generation", 0)),
            )
        )
    if mode3[0].size > 0:
        control_uvs_np = _control_uvs_i32_array(
            control_uvs,
            len(world_vertices_np),
        )
        anchors, others, mirror_u, flip_winding = _cached_mode3_roles(
            batch,
            mode3[1],
            mode3[2],
            control_uvs_np,
        )
        face_order, groups = batch["mode3_groups"]
        instance_values = np.empty(
            len(anchors) * BILLBOARD_INSTANCE_FLOATS,
            dtype=np.float32,
        )
        fill_mode3_billboard_instances(
            instance_values,
            world_vertices_np,
            anchors,
            others,
            mirror_u,
            flip_winding,
            face_order,
        )
        for desc_idx, face_start, face_count in groups:
            value_start = face_start * BILLBOARD_INSTANCE_FLOATS
            value_end = value_start + face_count * BILLBOARD_INSTANCE_FLOATS
            _append_numpy_values(
                _grouped_vertices_for(buffers.billboard_instances, desc_idx),
                instance_values[value_start:value_end],
            )
    if mode57[0].size > 0:
        mode57_pending.append(
            (
                _vertices_numeric_array(mode57_world_vertices),
                _control_uvs_i32_array(
                    mode57_control_uvs,
                    len(mode57_world_vertices),
                ),
                *mode57,
                batch["mode57_owner_slots"],
                component_policies,
                force_owner_slot_zero,
                int(batch.get("normal_generation", 0)),
            )
        )
    return batch["other_faces"]


def _build_mode3_groups(descs):
    face_order_values = sorted(
        range(len(descs)),
        key=lambda face_pos: int(descs[face_pos]),
    )
    face_order = np.ascontiguousarray(
        np.asarray(face_order_values, dtype=np.int32)
    )
    groups = []
    group_start = 0
    while group_start < len(face_order_values):
        desc_idx = int(descs[face_order_values[group_start]])
        group_end = group_start + 1
        while (
            group_end < len(face_order_values)
            and int(descs[face_order_values[group_end]]) == desc_idx
        ):
            group_end += 1
        groups.append((desc_idx, group_start, group_end - group_start))
        group_start = group_end
    return face_order, tuple(groups)


def _cached_mode3_roles(batch, face_counts, face_indices, control_uvs):
    cached = batch.get("mode3_roles")
    if cached is not None and cached[0] is control_uvs:
        return cached[1:]

    face_count = len(face_counts)
    anchors = np.empty(face_count, dtype=np.int16)
    others = np.empty(face_count, dtype=np.int16)
    mirror_u = np.empty(face_count, dtype=np.uint8)
    flip_winding = np.empty(face_count, dtype=np.uint8)
    for face_pos in range(face_count):
        count = int(face_counts[face_pos])
        indices = face_indices[face_pos, :count]
        anchor, other, _third, mirrored, flipped = _analyze_mode3_face(
            indices,
            control_uvs,
        )
        if (anchor < 0 or other < 0) and count >= 3:
            anchor = int(indices[0])
            other = int(indices[2])
        anchors[face_pos] = anchor
        others[face_pos] = other
        mirror_u[face_pos] = int(mirrored)
        flip_winding[face_pos] = int(flipped)

    batch["mode3_roles"] = (
        control_uvs,
        anchors,
        others,
        mirror_u,
        flip_winding,
    )
    return anchors, others, mirror_u, flip_winding


def _collect_deferred_wireframe(
    pending,
    face_counts,
    face_indices,
    face_palettes,
    face_emit_occluder,
    face_count,
    world_vertices,
):
    face_count = int(face_count)
    if face_count <= 0:
        return
    pending.append(
        (
            _vertices_numeric_array(world_vertices),
            np.ascontiguousarray(face_counts[:face_count]),
            np.ascontiguousarray(face_indices[:face_count]),
            np.ascontiguousarray(face_palettes[:face_count]),
            np.ascontiguousarray(face_emit_occluder[:face_count]),
        )
    )


def _pending_block_layout(pending, face_slot):
    block_count = len(pending)
    block_vertex_counts = np.empty(block_count, dtype=np.int64)
    block_world_offsets = np.empty(block_count, dtype=np.int64)
    total_vertices = 0
    total_faces = 0
    for block_pos, package in enumerate(pending):
        vertex_count = len(package[0])
        block_vertex_counts[block_pos] = vertex_count
        block_world_offsets[block_pos] = total_vertices
        total_vertices += vertex_count
        total_faces += len(package[face_slot])
    return (
        block_vertex_counts,
        block_world_offsets,
        total_vertices,
        total_faces,
    )


def _fill_pending_arrays(
    pending,
    count_slot,
    block_vertex_counts,
    block_world_offsets,
    all_world,
    face_block_indices,
    vertex_fields=(),
    face_fields=(),
    block_face_offsets=None,
    normal_generations=None,
):
    face_write_pos = 0
    if block_face_offsets is not None:
        block_face_offsets[0] = 0
    for block_pos, record in enumerate(pending):
        vertex_start = int(block_world_offsets[block_pos])
        vertex_end = vertex_start + int(block_vertex_counts[block_pos])
        all_world[vertex_start:vertex_end] = record[0]
        for target, slot in vertex_fields:
            target[vertex_start:vertex_end] = record[slot]

        face_end = face_write_pos + len(record[count_slot])
        face_block_indices[face_write_pos:face_end] = block_pos
        for target, slot in face_fields:
            target[face_write_pos:face_end] = record[slot]
        face_write_pos = face_end
        if block_face_offsets is not None:
            block_face_offsets[block_pos + 1] = face_write_pos
        if normal_generations is not None:
            normal_generations[block_pos] = int(record[-1])


def _emit_pending_wireframes(
    pending,
    buffers,
    replace_buffers,
):
    block_count = len(pending)
    if block_count <= 0:
        return

    (
        block_vertex_counts,
        block_world_offsets,
        total_vertices,
        total_faces,
    ) = _pending_block_layout(pending, 1)

    if total_vertices <= 0 or total_faces <= 0:
        return

    world_dtype = np.result_type(*(record[0].dtype for record in pending))
    all_world = np.empty((total_vertices, 3), dtype=world_dtype)
    face_block_indices = np.empty(total_faces, dtype=np.int32)
    face_counts = np.empty(total_faces, dtype=np.uint8)
    face_indices = np.empty((total_faces, MAX_FACE_VERTICES), dtype=np.uint16)
    face_palettes = np.empty(total_faces, dtype=np.uint8)
    face_emit_occluder = np.empty(total_faces, dtype=np.uint8)

    _fill_pending_arrays(
        pending,
        1,
        block_vertex_counts,
        block_world_offsets,
        all_world,
        face_block_indices,
        face_fields=(
            (face_counts, 1),
            (face_indices, 2),
            (face_palettes, 3),
            (face_emit_occluder, 4),
        ),
    )

    face_triangle_offsets = np.empty(total_faces, dtype=np.int64)
    face_line_offsets = np.empty(total_faces, dtype=np.int64)
    totals = np.empty(2, dtype=np.int64)
    build_wireframe_offsets(
        face_counts,
        face_emit_occluder,
        face_triangle_offsets,
        face_line_offsets,
        totals,
    )
    total_triangles = int(totals[0])
    total_lines = int(totals[1])
    vertex_values = np.empty(
        total_vertices * INDEXED_GEOMETRY_VERTEX_FLOATS,
        dtype=np.float32,
    )
    occluder_indices = np.empty(total_triangles * 3, dtype=np.uint32)
    line_indices = np.empty(total_lines * 2, dtype=np.uint32)
    line_palettes = np.empty(total_lines, dtype=np.float32)
    fill_indexed_wireframe_buffers(
        vertex_values,
        occluder_indices,
        line_indices,
        line_palettes,
        face_block_indices,
        face_counts,
        face_indices,
        face_palettes,
        face_emit_occluder,
        face_triangle_offsets,
        face_line_offsets,
        block_world_offsets,
        all_world,
    )
    if replace_buffers:
        buffers.wireframe_indexed_vertices = vertex_values
        buffers.wireframe_occluder_indices = occluder_indices
        buffers.wireframe_line_indices = line_indices
        buffers.wireframe_line_palette = line_palettes
    else:
        vertex_base = (
            len(buffers.wireframe_indexed_vertices)
            // INDEXED_GEOMETRY_VERTEX_FLOATS
        )
        _append_numpy_values(buffers.wireframe_indexed_vertices, vertex_values)
        if vertex_base:
            occluder_indices = occluder_indices + np.uint32(vertex_base)
            line_indices = line_indices + np.uint32(vertex_base)
        _append_numpy_values(
            buffers.wireframe_occluder_indices,
            occluder_indices,
        )
        _append_numpy_values(buffers.wireframe_line_indices, line_indices)
        _append_numpy_values(buffers.wireframe_line_palette, line_palettes)


def _finalize_geometry_partitions(state):
    for partition in (state.static_build, *state.dynamic_partitions.values()):
        if partition.wireframe:
            _emit_pending_wireframes(
                partition.wireframe,
                partition.geometry,
                True,
            )
        _process_pending_indexed_flat_partition(
            state,
            partition.indexed_flat,
            partition.geometry,
            partition.batch_cache,
        )
        _process_pending_mode4_partition(
            state,
            partition.mode4,
            partition.geometry,
            partition.batch_cache,
        )
        _process_pending_satellite_mode4_partition(
            state,
            partition.satellite_mode4,
            partition.geometry,
        )
        if partition.mode57:
            grouped = _build_mode57_grouped_buffers(
                partition.mode57,
                state.lighting,
                partition.batch_cache,
                shared_vertices=True,
            )
            if grouped is not None:
                _replace_mode57_grouped_buffers(
                    partition.geometry,
                    grouped,
                )


def _process_pending_indexed_flat_partition(
    state,
    pending,
    buffers,
    batch_cache,
):
    if not pending:
        return

    lighting = state.lighting
    layout_signature = _deferred_layout_signature(pending, 1)
    cached = None
    if batch_cache is not None:
        cached = batch_cache.get("indexed_flat")
    if cached is not None and cached.get("layout_signature") == layout_signature:
        _update_cached_indexed_flat(cached, pending, lighting)
        buffers.indexed_vertices = cached["vertex_values"]
        buffers.indexed_indices = cached["index_values"]
        buffers.indexed_primitive_palette = cached["primitive_palette"]
        return

    scene_light = np.ascontiguousarray(
        np.asarray(lighting["scene_light"], dtype=np.int64)
    )
    camera_position = np.ascontiguousarray(
        np.asarray(lighting["camera_position"], dtype=np.int64)
    )
    camera_forward = np.ascontiguousarray(
        np.asarray(lighting["camera_forward"], dtype=np.int64)
    )
    scene_light_is_directional = int(lighting["scene_light_is_directional"])
    ambient = int(lighting["ambient"])
    fog_distance = int(lighting["fog_distance"])

    (
        block_vertex_counts,
        block_world_offsets,
        total_vertices,
        total_faces,
    ) = _pending_block_layout(pending, 1)

    if total_vertices <= 0 or total_faces <= 0:
        return

    world_dtype = np.result_type(*(record[0].dtype for record in pending))
    all_world = np.empty((total_vertices, 3), dtype=world_dtype)
    face_block_indices = np.empty(total_faces, dtype=np.int32)
    face_modes = np.empty(total_faces, dtype=np.uint8)
    face_flags = np.empty(total_faces, dtype=np.int64)
    face_counts = np.empty(total_faces, dtype=np.uint8)
    face_indices = np.empty((total_faces, MAX_FACE_VERTICES), dtype=np.uint16)
    face_normals = np.empty((total_faces, 3), dtype=np.int64)
    block_face_offsets = np.empty(len(pending) + 1, dtype=np.int64)
    normal_generations = np.empty(len(pending), dtype=np.int64)
    block_has_mode1 = np.empty(len(pending), dtype=np.uint8)

    _fill_pending_arrays(
        pending,
        1,
        block_vertex_counts,
        block_world_offsets,
        all_world,
        face_block_indices,
        face_fields=(
            (face_modes, 1),
            (face_flags, 2),
            (face_counts, 3),
            (face_indices, 4),
            (face_normals, 5),
        ),
        block_face_offsets=block_face_offsets,
        normal_generations=normal_generations,
    )
    (
        face_owner_slots,
        block_owner_offsets,
        owner_component_policies,
        component_policy_signatures,
    ) = _pending_component_layout(pending, 1)
    for block_pos, record in enumerate(pending):
        block_has_mode1[block_pos] = int(
            any(int(mode) == MODE_FLAT_LIT for mode in record[1])
        )

    face_triangle_offsets = np.empty(total_faces, dtype=np.int64)
    total_triangles = int(
        build_face_triangle_offsets(face_counts, face_triangle_offsets)
    )
    if total_triangles <= 0:
        return

    vertex_values = np.empty(
        total_vertices * INDEXED_GEOMETRY_VERTEX_FLOATS,
        dtype=np.float32,
    )
    fill_indexed_flat_vertices(vertex_values, all_world)

    index_values = np.empty(total_triangles * 3, dtype=np.uint16)
    primitive_palette = np.empty(total_triangles, dtype=np.float32)
    fill_indexed_flat_indices_and_palettes(
        index_values,
        primitive_palette,
        face_block_indices,
        face_modes,
        face_flags,
        face_counts,
        face_indices,
        face_normals,
        face_owner_slots,
        face_triangle_offsets,
        block_world_offsets,
        block_world_offsets,
        block_owner_offsets,
        owner_component_policies,
        all_world,
        scene_light,
        scene_light_is_directional,
        ambient,
        fog_distance,
        camera_position,
        camera_forward,
    )
    buffers.indexed_vertices = vertex_values
    buffers.indexed_indices = index_values
    buffers.indexed_primitive_palette = primitive_palette
    if batch_cache is not None:
        batch_cache["indexed_flat"] = {
            "layout_signature": layout_signature,
            "world_sources": [record[0] for record in pending],
            "world_changed": np.empty(len(pending), dtype=np.uint8),
            "palette_changed": np.empty(len(pending), dtype=np.uint8),
            "normal_generations": normal_generations,
            "block_face_offsets": block_face_offsets,
            "block_has_mode1": block_has_mode1,
            "block_vertex_counts": block_vertex_counts,
            "block_world_offsets": block_world_offsets,
            "all_world": all_world,
            "face_block_indices": face_block_indices,
            "face_modes": face_modes,
            "face_flags": face_flags,
            "face_counts": face_counts,
            "face_indices": face_indices,
            "face_normals": face_normals,
            "face_owner_slots": face_owner_slots,
            "face_triangle_offsets": face_triangle_offsets,
            "block_owner_offsets": block_owner_offsets,
            "owner_component_policies": owner_component_policies,
            "component_policy_signatures": component_policy_signatures,
            "vertex_values": vertex_values,
            "index_values": index_values,
            "primitive_palette": primitive_palette,
            "shading_key": _indexed_flat_shading_key(lighting),
        }


def _deferred_layout_signature(pending, face_slot):
    return tuple(
        (
            record[0].dtype.str,
            *(id(record[slot]) for slot in range(face_slot, 6)),
            id(record[6]),
            len(record[7]),
            bool(record[8]),
            len(record[0]),
            len(record[face_slot]),
        )
        for record in pending
    )


def _pending_component_layout(pending, count_slot):
    block_owner_offsets = np.empty(len(pending), dtype=np.int64)
    total_faces = sum(len(record[count_slot]) for record in pending)
    total_owners = sum(len(record[7]) for record in pending)
    face_owner_slots = np.empty(total_faces, dtype=np.uint16)
    owner_component_policies = np.empty(total_owners, dtype=np.uint8)
    policy_signatures = []
    face_pos = 0
    owner_pos = 0
    for block_pos, record in enumerate(pending):
        face_end = face_pos + len(record[count_slot])
        if bool(record[8]):
            face_owner_slots[face_pos:face_end] = 0
        else:
            face_owner_slots[face_pos:face_end] = record[6]
        face_pos = face_end

        policies = tuple(int(value) for value in record[7])
        owner_end = owner_pos + len(policies)
        block_owner_offsets[block_pos] = owner_pos
        owner_component_policies[owner_pos:owner_end] = policies
        owner_pos = owner_end
        policy_signatures.append(policies)
    return (
        face_owner_slots,
        block_owner_offsets,
        owner_component_policies,
        policy_signatures,
    )


def _refresh_cached_component_policies(cached, pending, changed_blocks):
    changed_count = 0
    signatures = cached["component_policy_signatures"]
    owner_offsets = cached["block_owner_offsets"]
    owner_policies = cached["owner_component_policies"]
    for block_pos, record in enumerate(pending):
        current = tuple(int(value) for value in record[7])
        if current == signatures[block_pos]:
            continue
        owner_start = int(owner_offsets[block_pos])
        owner_end = owner_start + len(current)
        owner_policies[owner_start:owner_end] = current
        signatures[block_pos] = current
        if changed_blocks[block_pos] == 0:
            changed_blocks[block_pos] = 1
            changed_count += 1
    return changed_count


def _indexed_flat_shading_key(lighting):
    return (
        tuple(int(value) for value in lighting["scene_light"]),
        int(lighting["scene_light_is_directional"]),
        int(lighting["ambient"]),
        int(lighting["fog_distance"]),
        tuple(int(value) for value in lighting["camera_position"]),
        tuple(int(value) for value in lighting["camera_forward"]),
    )


def _update_cached_indexed_flat(cached, pending, lighting):
    world_changed = cached["world_changed"]
    palette_changed = cached["palette_changed"]
    world_changed.fill(0)
    palette_changed.fill(0)
    world_sources = cached["world_sources"]
    normal_generations = cached["normal_generations"]
    block_world_offsets = cached["block_world_offsets"]
    block_vertex_counts = cached["block_vertex_counts"]
    block_face_offsets = cached["block_face_offsets"]
    block_has_mode1 = cached["block_has_mode1"]
    all_world = cached["all_world"]
    face_normals = cached["face_normals"]
    world_changed_count = 0
    palette_changed_count = 0

    for block_pos, record in enumerate(pending):
        world_vertices = record[0]
        if world_sources[block_pos] is not world_vertices:
            vertex_start = int(block_world_offsets[block_pos])
            vertex_end = vertex_start + int(block_vertex_counts[block_pos])
            all_world[vertex_start:vertex_end] = world_vertices
            world_sources[block_pos] = world_vertices
            world_changed[block_pos] = 1
            world_changed_count += 1
            if block_has_mode1[block_pos]:
                palette_changed[block_pos] = 1
                palette_changed_count += 1

        normal_generation = int(record[-1])
        if int(normal_generations[block_pos]) != normal_generation:
            face_start = int(block_face_offsets[block_pos])
            face_end = int(block_face_offsets[block_pos + 1])
            face_normals[face_start:face_end] = record[5]
            normal_generations[block_pos] = normal_generation
            if block_has_mode1[block_pos] and not palette_changed[block_pos]:
                palette_changed[block_pos] = 1
                palette_changed_count += 1

    palette_changed_count += _refresh_cached_component_policies(
        cached,
        pending,
        palette_changed,
    )

    if world_changed_count:
        update_indexed_flat_vertices(
            cached["vertex_values"],
            all_world,
            block_world_offsets,
            block_vertex_counts,
            world_changed,
        )

    shading_key = _indexed_flat_shading_key(lighting)
    if shading_key != cached["shading_key"]:
        palette_changed[:] = block_has_mode1
        palette_changed_count = int(sum(int(value) for value in block_has_mode1))
        cached["shading_key"] = shading_key

    if palette_changed_count:
        update_indexed_flat_palettes(
            cached["primitive_palette"],
            cached["face_block_indices"],
            cached["face_modes"],
            cached["face_flags"],
            cached["face_counts"],
            cached["face_indices"],
            face_normals,
            cached["face_owner_slots"],
            cached["face_triangle_offsets"],
            block_world_offsets,
            cached["block_owner_offsets"],
            cached["owner_component_policies"],
            all_world,
            np.ascontiguousarray(
                np.asarray(lighting["scene_light"], dtype=np.int64)
            ),
            int(lighting["scene_light_is_directional"]),
            int(lighting["ambient"]),
            int(lighting["fog_distance"]),
            np.ascontiguousarray(
                np.asarray(lighting["camera_position"], dtype=np.int64)
            ),
            np.ascontiguousarray(
                np.asarray(lighting["camera_forward"], dtype=np.int64)
            ),
            palette_changed,
        )

    cached["last_world_changed"] = world_changed_count
    cached["last_palette_changed"] = palette_changed_count


def _process_pending_mode4_partition(
    state,
    pending,
    buffers,
    batch_cache,
):
    if not pending:
        return

    lighting = state.lighting
    layout_signature = _deferred_layout_signature(pending, 2)
    cached = None
    if batch_cache is not None:
        cached = batch_cache.get("mode4")
    if cached is not None and cached.get("layout_signature") == layout_signature:
        buffers.mode4_vertices = _update_cached_mode4_vertices(
            cached,
            pending,
            lighting,
        )
        return

    scene_light = np.ascontiguousarray(
        np.asarray(lighting["scene_light"], dtype=np.int64)
    )
    scene_light_is_directional = int(lighting["scene_light_is_directional"])
    ambient = int(lighting["ambient"])

    (
        block_vertex_counts,
        block_world_offsets,
        total_vertices,
        total_faces,
    ) = _pending_block_layout(pending, 2)

    if total_vertices <= 0 or total_faces <= 0:
        return

    world_dtype = np.result_type(*(record[0].dtype for record in pending))
    all_world = np.empty((total_vertices, 3), dtype=world_dtype)
    all_c_in = np.empty(total_vertices, dtype=np.uint8)
    face_block_indices = np.empty(total_faces, dtype=np.int32)
    face_flags = np.empty(total_faces, dtype=np.int64)
    face_counts = np.empty(total_faces, dtype=np.uint8)
    face_indices = np.empty((total_faces, MAX_FACE_VERTICES), dtype=np.uint16)
    face_normals = np.empty((total_faces, 3), dtype=np.int64)
    block_face_offsets = np.empty(len(pending) + 1, dtype=np.int64)
    normal_generations = np.empty(len(pending), dtype=np.int64)

    _fill_pending_arrays(
        pending,
        2,
        block_vertex_counts,
        block_world_offsets,
        all_world,
        face_block_indices,
        vertex_fields=((all_c_in, 1),),
        face_fields=(
            (face_flags, 2),
            (face_counts, 3),
            (face_indices, 4),
            (face_normals, 5),
        ),
        block_face_offsets=block_face_offsets,
        normal_generations=normal_generations,
    )
    (
        face_owner_slots,
        block_owner_offsets,
        owner_component_policies,
        component_policy_signatures,
    ) = _pending_component_layout(pending, 2)

    face_triangle_offsets = np.empty(total_faces, dtype=np.int64)
    total_triangles = int(
        build_face_triangle_offsets(face_counts, face_triangle_offsets)
    )
    if total_triangles <= 0:
        return

    vertex_values = np.empty(
        total_triangles * 3 * MODE4_VERTEX_FLOATS,
        dtype=np.float32,
    )
    fill_mode4_vertices(
        vertex_values,
        face_block_indices,
        face_flags,
        face_counts,
        face_indices,
        face_normals,
        face_owner_slots,
        face_triangle_offsets,
        block_world_offsets,
        block_owner_offsets,
        owner_component_policies,
        all_world,
        all_c_in,
        scene_light,
        scene_light_is_directional,
        ambient,
    )

    buffers.mode4_vertices = vertex_values
    if batch_cache is not None:
        batch_cache["mode4"] = {
            "layout_signature": layout_signature,
            "world_sources": [record[0] for record in pending],
            "changed_blocks": np.empty(len(pending), dtype=np.uint8),
            "normal_generations": normal_generations,
            "block_face_offsets": block_face_offsets,
            "block_vertex_counts": block_vertex_counts,
            "block_world_offsets": block_world_offsets,
            "all_world": all_world,
            "all_c_in": all_c_in,
            "face_block_indices": face_block_indices,
            "face_flags": face_flags,
            "face_counts": face_counts,
            "face_indices": face_indices,
            "face_normals": face_normals,
            "face_owner_slots": face_owner_slots,
            "face_triangle_offsets": face_triangle_offsets,
            "block_owner_offsets": block_owner_offsets,
            "owner_component_policies": owner_component_policies,
            "component_policy_signatures": component_policy_signatures,
            "vertex_values": vertex_values,
            "lighting_key": _mode57_lighting_key(lighting),
        }


def _update_cached_mode4_vertices(cached, pending, lighting):
    block_count = len(pending)
    changed_blocks = cached["changed_blocks"]
    changed_blocks.fill(0)
    world_sources = cached["world_sources"]
    normal_generations = cached["normal_generations"]
    block_world_offsets = cached["block_world_offsets"]
    block_vertex_counts = cached["block_vertex_counts"]
    block_face_offsets = cached["block_face_offsets"]
    all_world = cached["all_world"]
    face_normals = cached["face_normals"]
    changed_count = 0

    for block_pos, record in enumerate(pending):
        world_vertices = record[0]
        if world_sources[block_pos] is not world_vertices:
            vertex_start = int(block_world_offsets[block_pos])
            vertex_end = vertex_start + int(block_vertex_counts[block_pos])
            all_world[vertex_start:vertex_end] = world_vertices
            world_sources[block_pos] = world_vertices
            changed_blocks[block_pos] = 1
            changed_count += 1

        normal_generation = int(record[-1])
        if int(normal_generations[block_pos]) != normal_generation:
            face_start = int(block_face_offsets[block_pos])
            face_end = int(block_face_offsets[block_pos + 1])
            face_normals[face_start:face_end] = record[5]
            normal_generations[block_pos] = normal_generation
            if not changed_blocks[block_pos]:
                changed_blocks[block_pos] = 1
                changed_count += 1

    changed_count += _refresh_cached_component_policies(
        cached,
        pending,
        changed_blocks,
    )

    lighting_key = _mode57_lighting_key(lighting)
    if lighting_key != cached["lighting_key"]:
        changed_blocks[:] = 1
        changed_count = block_count
        cached["lighting_key"] = lighting_key

    if changed_count:
        update_mode4_vertices(
            cached["vertex_values"],
            cached["face_block_indices"],
            cached["face_flags"],
            cached["face_counts"],
            cached["face_indices"],
            face_normals,
            cached["face_owner_slots"],
            cached["face_triangle_offsets"],
            block_world_offsets,
            cached["block_owner_offsets"],
            cached["owner_component_policies"],
            all_world,
            cached["all_c_in"],
            np.ascontiguousarray(
                np.asarray(lighting["scene_light"], dtype=np.int64)
            ),
            int(lighting["scene_light_is_directional"]),
            int(lighting["ambient"]),
            changed_blocks,
        )

    return cached["vertex_values"]


def _process_pending_satellite_mode4_partition(state, pending, buffers):
    if not pending:
        return

    lighting = state.lighting
    camera_position = np.ascontiguousarray(
        np.asarray(lighting["camera_position"], dtype=np.int64)
    )
    camera_forward = np.ascontiguousarray(
        np.asarray(lighting["camera_forward"], dtype=np.int64)
    )

    (
        block_vertex_counts,
        block_world_offsets,
        total_vertices,
        total_faces,
    ) = _pending_block_layout(pending, 2)

    if total_vertices <= 0 or total_faces <= 0:
        return

    all_world = np.empty((total_vertices, 3), dtype=np.int64)
    face_block_indices = np.empty(total_faces, dtype=np.int32)
    face_palette_families = np.empty(total_faces, dtype=np.uint8)
    face_counts = np.empty(total_faces, dtype=np.uint8)
    face_indices = np.empty((total_faces, MAX_FACE_VERTICES), dtype=np.uint16)

    _fill_pending_arrays(
        pending,
        2,
        block_vertex_counts,
        block_world_offsets,
        all_world,
        face_block_indices,
        face_fields=(
            (face_palette_families, 1),
            (face_counts, 2),
            (face_indices, 3),
        ),
    )

    face_triangle_offsets = np.empty(total_faces, dtype=np.int64)
    total_triangles = int(
        build_face_triangle_offsets(face_counts, face_triangle_offsets)
    )
    if total_triangles <= 0:
        return

    vertex_values = np.empty(
        total_triangles * 3 * MODE4_VERTEX_FLOATS,
        dtype=np.float32,
    )
    fill_satellite_mode4_vertices_batched(
        vertex_values,
        face_block_indices,
        face_palette_families,
        face_counts,
        face_indices,
        face_triangle_offsets,
        block_world_offsets,
        all_world,
        camera_position,
        camera_forward,
        int(lighting.get("satellite_width_fixed", 0)),
        int(lighting.get("satellite_shade_bias", 0)),
        int(lighting.get("satellite_shade_divisor", 1)),
    )

    existing = buffers.mode4_vertices
    if len(existing) <= 0:
        buffers.mode4_vertices = vertex_values
    elif isinstance(existing, np.ndarray):
        combined = np.empty(existing.size + vertex_values.size, dtype=np.float32)
        combined[:existing.size] = existing
        combined[existing.size:] = vertex_values
        buffers.mode4_vertices = combined
    else:
        _append_numpy_values(existing, vertex_values)


def _build_mode57_grouped_buffers(
    pending,
    lighting,
    dynamic_batch_cache=None,
    shared_vertices=False,
):
    block_count = len(pending)
    layout_signature = _deferred_layout_signature(pending, 2)
    cached = None
    if dynamic_batch_cache is not None:
        cached = dynamic_batch_cache.get("mode57")
    if (
        cached is not None
        and cached.get("layout_signature") == layout_signature
        and bool(cached.get("shared_vertices")) == bool(shared_vertices)
    ):
        return _update_cached_mode57_grouped_buffers(
            cached,
            pending,
            lighting,
        )

    (
        block_vertex_counts,
        block_world_offsets,
        total_vertices,
        total_faces,
    ) = _pending_block_layout(pending, 2)
    if total_vertices <= 0 or total_faces <= 0:
        return None

    world_dtype = np.result_type(*(record[0].dtype for record in pending))
    all_world = np.empty((total_vertices, 3), dtype=world_dtype)
    all_control_uvs = np.empty((total_vertices, 2), dtype=np.int32)
    face_block_indices = np.empty(total_faces, dtype=np.int32)
    face_descs = np.empty(total_faces, dtype=np.int16)
    face_counts = np.empty(total_faces, dtype=np.uint8)
    face_indices = np.empty((total_faces, MAX_FACE_VERTICES), dtype=np.uint16)
    face_normals = np.empty((total_faces, 3), dtype=np.int64)
    block_face_offsets = np.empty(block_count + 1, dtype=np.int64)
    normal_generations = np.empty(block_count, dtype=np.int64)

    _fill_pending_arrays(
        pending,
        2,
        block_vertex_counts,
        block_world_offsets,
        all_world,
        face_block_indices,
        vertex_fields=((all_control_uvs, 1),),
        face_fields=(
            (face_descs, 2),
            (face_counts, 3),
            (face_indices, 4),
            (face_normals, 5),
        ),
        block_face_offsets=block_face_offsets,
        normal_generations=normal_generations,
    )

    (
        face_owner_slots,
        block_owner_offsets,
        owner_component_policies,
        component_policy_signatures,
    ) = _pending_component_layout(pending, 2)

    block_desc_present = np.empty(
        (block_count, MODE57_DESC_TABLE_SIZE),
        dtype=np.uint8,
    )
    desc_triangle_counts = np.empty(MODE57_DESC_TABLE_SIZE, dtype=np.int64)
    face_primitive_offsets = np.empty(total_faces, dtype=np.int64)
    analyze_mode57_faces(
        face_block_indices,
        face_descs,
        face_counts,
        block_desc_present,
        desc_triangle_counts,
        face_primitive_offsets,
    )

    desc_primitive_offsets = np.empty(MODE57_DESC_TABLE_SIZE, dtype=np.int64)
    total_triangles = int(
        build_mode57_desc_offsets(desc_triangle_counts, desc_primitive_offsets)
    )
    if total_triangles <= 0:
        return None

    entry_count = int(count_mode57_block_desc_entries(block_desc_present))
    block_desc_vertex_offsets = None
    desc_vertex_offsets = None
    entry_block_indices = None
    entry_descs = None
    grouped_vertices = {}
    if shared_vertices:
        emitted_vertex_count = total_vertices
        vertex_values = np.empty(
            total_vertices * INDEXED_TEXMAP_VERTEX_FLOATS,
            dtype=np.float32,
        )
        fill_mode57_shared_vertices(
            vertex_values,
            all_world,
            all_control_uvs,
        )
        emitted_indices = np.empty(total_triangles * 3, dtype=np.uint32)
    else:
        block_desc_vertex_offsets = np.empty(
            (block_count, MODE57_DESC_TABLE_SIZE),
            dtype=np.int64,
        )
        desc_vertex_counts = np.empty(MODE57_DESC_TABLE_SIZE, dtype=np.int64)
        assign_mode57_vertex_offsets(
            block_vertex_counts,
            block_desc_present,
            block_desc_vertex_offsets,
            desc_vertex_counts,
        )
        desc_vertex_offsets = np.empty(MODE57_DESC_TABLE_SIZE, dtype=np.int64)
        emitted_vertex_count = int(
            build_mode57_desc_offsets(desc_vertex_counts, desc_vertex_offsets)
        )
        if emitted_vertex_count <= 0:
            return None
        entry_block_indices = np.empty(entry_count, dtype=np.int32)
        entry_descs = np.empty(entry_count, dtype=np.int16)
        fill_mode57_block_desc_entries(
            block_desc_present,
            entry_block_indices,
            entry_descs,
        )
        vertex_values = np.empty(
            emitted_vertex_count * INDEXED_TEXMAP_VERTEX_FLOATS,
            dtype=np.float32,
        )
        fill_mode57_grouped_vertices(
            vertex_values,
            all_world,
            all_control_uvs,
            block_world_offsets,
            block_vertex_counts,
            block_desc_vertex_offsets,
            desc_vertex_offsets,
            entry_block_indices,
            entry_descs,
        )
        emitted_indices = np.empty(total_triangles * 3, dtype=np.uint16)

    emitted_lighting = np.empty(total_triangles, dtype=np.float32)
    scene_light = np.ascontiguousarray(
        np.asarray(lighting["scene_light"], dtype=np.int64)
    )
    if shared_vertices:
        fill_mode57_shared_indices_and_lighting(
            emitted_indices,
            emitted_lighting,
            face_block_indices,
            face_descs,
            face_counts,
            face_indices,
            face_normals,
            face_owner_slots,
            face_primitive_offsets,
            block_world_offsets,
            block_owner_offsets,
            desc_primitive_offsets,
            owner_component_policies,
            all_world,
            scene_light,
            int(lighting["scene_light_is_directional"]),
            int(lighting["ambient"]),
        )
    else:
        fill_mode57_grouped_indices_and_lighting(
            emitted_indices,
            emitted_lighting,
            face_block_indices,
            face_descs,
            face_counts,
            face_indices,
            face_normals,
            face_owner_slots,
            face_primitive_offsets,
            block_world_offsets,
            block_desc_vertex_offsets,
            block_owner_offsets,
            desc_primitive_offsets,
            owner_component_policies,
            all_world,
            scene_light,
            int(lighting["scene_light_is_directional"]),
            int(lighting["ambient"]),
        )

    grouped_indices = {}
    grouped_lighting = {}
    for desc_idx in range(MODE57_DESC_TABLE_SIZE):
        triangle_count = int(desc_triangle_counts[desc_idx])
        if triangle_count <= 0:
            continue

        if not shared_vertices:
            vertex_count = int(desc_vertex_counts[desc_idx])
            vertex_start = (
                int(desc_vertex_offsets[desc_idx]) * INDEXED_TEXMAP_VERTEX_FLOATS
            )
            vertex_end = vertex_start + vertex_count * INDEXED_TEXMAP_VERTEX_FLOATS
            grouped_vertices[desc_idx] = vertex_values[vertex_start:vertex_end]
        primitive_start = int(desc_primitive_offsets[desc_idx])
        primitive_end = primitive_start + triangle_count
        grouped_indices[desc_idx] = emitted_indices[
            primitive_start * 3:primitive_end * 3
        ]
        grouped_lighting[desc_idx] = emitted_lighting[
            primitive_start:primitive_end
        ]
    grouped = (
        vertex_values if shared_vertices else grouped_vertices,
        grouped_indices,
        grouped_lighting,
        bool(shared_vertices),
    )
    if dynamic_batch_cache is not None:
        dynamic_batch_cache["mode57"] = {
            "layout_signature": layout_signature,
            "shared_vertices": bool(shared_vertices),
            "world_sources": [record[0] for record in pending],
            "world_changed": np.empty(len(pending), dtype=np.uint8),
            "normal_changed": np.empty(len(pending), dtype=np.uint8),
            "normal_generations": normal_generations,
            "block_face_offsets": block_face_offsets,
            "block_vertex_counts": block_vertex_counts,
            "block_world_offsets": block_world_offsets,
            "all_world": all_world,
            "all_control_uvs": all_control_uvs,
            "face_block_indices": face_block_indices,
            "face_descs": face_descs,
            "face_counts": face_counts,
            "face_indices": face_indices,
            "face_normals": face_normals,
            "face_owner_slots": face_owner_slots,
            "face_primitive_offsets": face_primitive_offsets,
            "block_desc_vertex_offsets": block_desc_vertex_offsets,
            "desc_vertex_offsets": desc_vertex_offsets,
            "desc_primitive_offsets": desc_primitive_offsets,
            "block_owner_offsets": block_owner_offsets,
            "owner_component_policies": owner_component_policies,
            "component_policy_signatures": component_policy_signatures,
            "entry_block_indices": entry_block_indices,
            "entry_descs": entry_descs,
            "vertex_values": vertex_values,
            "emitted_lighting": emitted_lighting,
            "lighting_key": _mode57_lighting_key(lighting),
            "grouped": grouped,
        }
    return grouped


def _mode57_lighting_key(lighting):
    return (
        tuple(int(value) for value in lighting["scene_light"]),
        int(lighting["scene_light_is_directional"]),
        int(lighting["ambient"]),
    )


def _update_cached_mode57_grouped_buffers(cached, pending, lighting):
    block_count = len(pending)
    world_changed = cached["world_changed"]
    normal_changed = cached["normal_changed"]
    world_changed.fill(0)
    normal_changed.fill(0)
    world_sources = cached["world_sources"]
    normal_generations = cached["normal_generations"]
    block_world_offsets = cached["block_world_offsets"]
    block_vertex_counts = cached["block_vertex_counts"]
    block_face_offsets = cached["block_face_offsets"]
    all_world = cached["all_world"]
    face_normals = cached["face_normals"]
    world_changed_count = 0
    normal_changed_count = 0

    for block_pos, record in enumerate(pending):
        world_vertices = record[0]
        if world_sources[block_pos] is not world_vertices:
            vertex_start = int(block_world_offsets[block_pos])
            vertex_end = vertex_start + int(block_vertex_counts[block_pos])
            all_world[vertex_start:vertex_end] = world_vertices
            world_sources[block_pos] = world_vertices
            world_changed[block_pos] = 1
            world_changed_count += 1

        normal_generation = int(record[-1])
        if int(normal_generations[block_pos]) != normal_generation:
            face_start = int(block_face_offsets[block_pos])
            face_end = int(block_face_offsets[block_pos + 1])
            face_normals[face_start:face_end] = record[5]
            normal_generations[block_pos] = normal_generation
            normal_changed[block_pos] = 1
            normal_changed_count += 1

    if world_changed_count:
        if cached["shared_vertices"]:
            update_mode57_shared_vertices(
                cached["vertex_values"],
                all_world,
                cached["all_control_uvs"],
                block_world_offsets,
                block_vertex_counts,
                world_changed,
            )
        else:
            update_mode57_grouped_vertices(
                cached["vertex_values"],
                all_world,
                cached["all_control_uvs"],
                block_world_offsets,
                block_vertex_counts,
                cached["block_desc_vertex_offsets"],
                cached["desc_vertex_offsets"],
                cached["entry_block_indices"],
                cached["entry_descs"],
                world_changed,
            )

    lighting_key = _mode57_lighting_key(lighting)
    lighting_changed = normal_changed
    lighting_changed_count = normal_changed_count
    lighting_changed_count += _refresh_cached_component_policies(
        cached,
        pending,
        lighting_changed,
    )
    if lighting_key != cached["lighting_key"]:
        lighting_changed[:] = 1
        lighting_changed_count = block_count
        cached["lighting_key"] = lighting_key
    elif int(lighting["scene_light_is_directional"]) == 0 and world_changed_count:
        for block_pos in range(block_count):
            if world_changed[block_pos] and not lighting_changed[block_pos]:
                lighting_changed[block_pos] = 1
                lighting_changed_count += 1

    if lighting_changed_count:
        lighting_args = (
            cached["emitted_lighting"],
            cached["face_block_indices"],
            cached["face_descs"],
            cached["face_counts"],
            cached["face_indices"],
            face_normals,
            cached["face_owner_slots"],
            cached["face_primitive_offsets"],
            block_world_offsets,
        )
        lighting_tail = (
            cached["block_owner_offsets"],
            cached["desc_primitive_offsets"],
            cached["owner_component_policies"],
            all_world,
            np.ascontiguousarray(
                np.asarray(lighting["scene_light"], dtype=np.int64)
            ),
            int(lighting["scene_light_is_directional"]),
            int(lighting["ambient"]),
            lighting_changed,
        )
        if cached["shared_vertices"]:
            update_mode57_shared_lighting(
                *lighting_args,
                *lighting_tail,
            )
        else:
            update_mode57_grouped_lighting(
                *lighting_args,
                cached["block_desc_vertex_offsets"],
                *lighting_tail,
            )

    return cached["grouped"]


def _replace_mode57_grouped_buffers(buffers, grouped):
    if grouped[3]:
        buffers.indexed_texmap_shared_vertices = grouped[0]
        buffers.indexed_texmap_vertices = {}
    else:
        buffers.indexed_texmap_shared_vertices = array("f")
        buffers.indexed_texmap_vertices = grouped[0]
    buffers.indexed_texmap_indices = grouped[1]
    buffers.indexed_texmap_primitive_lighting = grouped[2]


def _emit_remaining_faces(
    faces,
    world_vertices,
    control_uvs,
    buffers,
    texture_requests,
    satellite_view=False,
    satellite_primitive_gates=(True, True),
):
    satellite_points_enabled, satellite_lines_enabled = (
        bool(satellite_primitive_gates[0]),
        bool(satellite_primitive_gates[1]),
    )
    for (
        _face_index,
        face_flags,
        mode,
        indices,
        _normal,
        _wireframe_palette_index,
        _owner_class,
    ) in faces:
        # Some blocks encode point/line primitives with short index lists; keep
        # that geometry alive before applying polygon mode-specific handling.
        if len(indices) == 1:
            if satellite_view and (
                not satellite_points_enabled or int(face_flags) == 0x1000
            ):
                continue
            _append_point_sprite(
                buffers.point_vertices,
                world_vertices,
                indices[0],
                (
                    int(face_flags) & 0xFF
                    if satellite_view
                    else _mode0_palette_index(face_flags)
                ),
            )
            continue
        if len(indices) == 2:
            if satellite_view and not satellite_lines_enabled:
                continue
            _append_line_segment(
                buffers.line_vertices,
                world_vertices,
                indices[0],
                indices[1],
                (
                    int(face_flags) & 0xFF
                    if satellite_view
                    else _mode0_palette_index(face_flags)
                ),
            )
            continue
        if mode == MODE_POLYLINE:
            _append_polyline_segments(
                buffers.line_vertices,
                world_vertices,
                indices,
                (
                    int(face_flags) & 0xFF
                    if satellite_view
                    else _mode2_palette_index(face_flags)
                ),
            )
            continue

        if mode == MODE_BILLBOARD:
            desc_idx = _mode3_texture_desc_index(face_flags)
            first, second, third, mirror_u, flip_winding = (
                _analyze_mode3_face(indices, control_uvs)
            )
            if satellite_view:
                # Native satellite expansion centers on V=(0,max_v) and
                # derives size from U=(max_u,0), unlike ordinary O/V.
                anchor_index, other_index = second, third
            else:
                anchor_index, other_index = first, second
            if (anchor_index < 0 or other_index < 0) and len(indices) >= 3:
                anchor_index, other_index = int(indices[0]), int(indices[2])
            if anchor_index < 0 or other_index < 0:
                continue

            texture_requests.add(desc_idx)
            _append_mode3_billboard_instance(
                _grouped_vertices_for(buffers.billboard_instances, desc_idx),
                world_vertices[anchor_index],
                world_vertices[other_index],
                mirror_u,
                flip_winding,
            )
            continue



def _analyze_mode3_face(indices, control_uvs):
    indices = () if indices is None else tuple(int(index) for index in indices)
    indexed_uvs = []
    for index in indices:
        uv = control_uvs[index] if 0 <= index < len(control_uvs) else (0, 0)
        indexed_uvs.append((index, (int(uv[0]), int(uv[1]))))
    if not indexed_uvs:
        return -1, -1, -1, False, False

    max_u = max(uv[0] for _index, uv in indexed_uvs)
    max_v = max(uv[1] for _index, uv in indexed_uvs)

    a_index = b_index = c_index = -1
    for vertex_index, uv in indexed_uvs:
        if uv == (0, 0):
            a_index = int(vertex_index)
        if uv == (0, max_v):
            b_index = int(vertex_index)
        if uv == (max_u, 0):
            c_index = int(vertex_index)
    flip_winding = False
    if len(indexed_uvs) >= 3:
        a_uv = indexed_uvs[0][1]
        b_uv = indexed_uvs[1][1]
        c_uv = indexed_uvs[2][1]
        ax, ay = int(a_uv[0]), int(a_uv[1])
        bx, by = int(b_uv[0]), int(b_uv[1])
        cx, cy = int(c_uv[0]), int(c_uv[1])
        area2 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        flip_winding = area2 < 0

    return (
        a_index,
        b_index,
        c_index,
        any(int(uv[0]) < 0 for _index, uv in indexed_uvs),
        flip_winding,
    )


def _read_index_span(gamemem, face_info):
    if not face_info:
        return 0, None

    span_begin = min(
        index_addr
        for _face_index, _flags, _mode, _count, _color, index_addr, _normal, _owner in face_info
    )
    span_end = max(
        index_addr + count
        for _face_index, _flags, _mode, count, _color, index_addr, _normal, _owner in face_info
    )
    if span_end < span_begin or span_end - span_begin > MAX_FACE_INDEX_SPAN:
        return 0, None

    return span_begin, gamemem.read_runtime_bytes(
        span_begin,
        span_end - span_begin,
    )


def _read_face_indices(
    gamemem,
    index_addr,
    face_vertex_count,
    index_span_base,
    index_span,
):
    if index_span is not None:
        span_offset = index_addr - index_span_base
        span_end = span_offset + face_vertex_count
        if 0 <= span_offset and span_end <= len(index_span):
            return list(index_span[span_offset:span_end])

    return list(
        gamemem.read_runtime_bytes(
            index_addr,
            face_vertex_count,
        )
    )


def _read_lighting_state(gamemem, camera):
    return {
        "scene_light": (
            gamemem.read_reloc_i32(ADDR_SCENE_LIGHT_X),
            gamemem.read_reloc_i32(ADDR_SCENE_LIGHT_Y),
            gamemem.read_reloc_i32(ADDR_SCENE_LIGHT_Z),
        ),
        "scene_light_is_directional": (
            gamemem.read_reloc_i32(ADDR_SCENE_LIGHT_IS_DIRECTIONAL) != 0
        ),
        "ambient": gamemem.read_reloc_i32(ADDR_AMBIENT),
        "fog_distance": gamemem.read_reloc_i32(ADDR_FOG_DISTANCE),
        "component_lighting_mode": gamemem.read_reloc_u32(
            ADDR_COMPONENT_LIGHTING_MODE
        ),
        "camera_position": _camera_fixed_tuple(
            camera,
            "position_fixed",
            "position",
            FIXED_16_16_SCALE,
        ),
        "camera_forward": _camera_fixed_tuple(
            camera,
            "forward_fixed",
            "forward",
            ROTATION_FIXED_SCALE,
        ),
        "satellite_width_fixed": int(camera.get("satellite_width_fixed", 0)),
        "satellite_shade_bias": int(camera.get("satellite_shade_bias", 0)),
        "satellite_shade_divisor": int(
            camera.get("satellite_shade_divisor", 1)
        ),
    }


def _camera_fixed_tuple(camera, fixed_key, float_key, scale):
    if fixed_key in camera:
        return tuple(int(value) for value in camera[fixed_key])
    return tuple(int(round(float(value) * scale)) for value in camera[float_key])


def _compute_face_light_level(
    normal,
    world_vertices,
    indices,
    lighting,
    material_light_scale,
    include_fog,
    continuous,
):
    nx, ny, nz = int(normal[0]), int(normal[1]), int(normal[2])
    if lighting["scene_light_is_directional"]:
        lx = int(lighting["scene_light"][0])
        ly = int(lighting["scene_light"][1])
        lz = int(lighting["scene_light"][2])
    else:
        v0 = world_vertices[indices[0]]
        lx = int(lighting["scene_light"][0]) - int(v0[0])
        ly = int(lighting["scene_light"][1]) - int(v0[1])
        lz = int(lighting["scene_light"][2]) - int(v0[2])

    dot64 = lx * nx + ly * ny + lz * nz
    dot_shifted = dot64 >> 16

    ax, ay, az = abs(lx), abs(ly), abs(lz)
    light_or = ax | ay | az
    if light_or == 0:
        dot_norm = 1
        table_val = 1
    else:
        norm_shift = max(0, light_or.bit_length() - 1 - 7)
        lx8 = (ax >> norm_shift) & 0xFF
        ly8 = (ay >> norm_shift) & 0xFF
        lz8 = (az >> norm_shift) & 0xFF
        dot_norm = dot_shifted >> norm_shift

        mag_sq = lx8 * lx8 + ly8 * ly8 + lz8 * lz8
        table_idx = (mag_sq >> 7) & 0xFFFE
        entry_idx = max(1, table_idx // 2)
        table_val = SQRT_TABLE[min(entry_idx, len(SQRT_TABLE) - 1)]

    ambient_span = 128 - lighting["ambient"]
    if continuous:
        normalized_light = float(dot_norm) / float(table_val)
        ambient_adjusted_light = (
            normalized_light * float(ambient_span) / 128.0
        ) + float(lighting["ambient"])
        lit_shade_before_fog = (
            float(int(material_light_scale) >> 1)
            * ambient_adjusted_light
        ) / 1088.0
        fog_shade_loss = (
            _compute_fog_shade_loss_float(world_vertices, indices, lighting)
            if include_fog
            else 0.0
        )
        return max(0.0, min(15.0, lit_shade_before_fog - fog_shade_loss))

    normalized_light = _trunc_div(dot_norm, table_val)
    ambient_adjusted_light = (
        (normalized_light * ambient_span) >> 7
    ) + lighting["ambient"]
    material_gain = int(material_light_scale) >> 1
    lit_shade_before_fog = _trunc_div(
        material_gain * ambient_adjusted_light,
        1088,
    )
    fog_shade_loss = (
        _compute_fog_shade_loss(world_vertices, indices, lighting)
        if include_fog
        else 0
    )
    return max(0, min(15, lit_shade_before_fog - fog_shade_loss))


def _compute_fog_shade_loss(world_vertices, indices, lighting):
    fog_distance = int(lighting["fog_distance"])
    if fog_distance == 0:
        return 0
    face_depth_x4 = 0
    camera_position = lighting["camera_position"]
    camera_forward = lighting["camera_forward"]
    for index in indices:
        vertex = world_vertices[index]
        depth_dot = (
            (int(vertex[0]) - camera_position[0]) * camera_forward[0]
            + (int(vertex[1]) - camera_position[1]) * camera_forward[1]
            + (int(vertex[2]) - camera_position[2]) * camera_forward[2]
        )
        face_depth_x4 = max(face_depth_x4, (depth_dot + (1 << 26)) >> 27)
    return ((face_depth_x4 << 4) // fog_distance) >> 4


def _compute_fog_shade_loss_float(world_vertices, indices, lighting):
    fog_distance = int(lighting["fog_distance"])
    if fog_distance == 0:
        return 0.0
    camera_position = lighting["camera_position"]
    face_distance_x4 = max(
        math.sqrt(
            (float(world_vertices[index][0]) - float(camera_position[0])) ** 2
            + (float(world_vertices[index][1]) - float(camera_position[1])) ** 2
            + (float(world_vertices[index][2]) - float(camera_position[2])) ** 2
        ) * 4.0
        for index in indices
    )
    return face_distance_x4 / float(fog_distance)


def _append_mode3_billboard_instance(
    instances,
    a_world_vertex,
    b_world_vertex,
    mirror_u,
    flip_winding,
):
    instances.extend(
        (
            float(a_world_vertex[0]) / FIXED_16_16_SCALE,
            float(a_world_vertex[1]) / FIXED_16_16_SCALE,
            float(a_world_vertex[2]) / FIXED_16_16_SCALE,
            float(b_world_vertex[0]) / FIXED_16_16_SCALE,
            float(b_world_vertex[1]) / FIXED_16_16_SCALE,
            float(b_world_vertex[2]) / FIXED_16_16_SCALE,
            float(int(bool(mirror_u)) | (int(bool(flip_winding)) << 1)),
        )
    )


def _append_point_sprite(vertices, world_vertices, vertex_index, palette_index):
    _append_geometry_vertex(
        vertices,
        world_vertices[vertex_index],
        palette_index,
    )


def _append_line_segment(
    vertices,
    world_vertices,
    first_vertex_index,
    second_vertex_index,
    palette_index,
):
    _append_geometry_vertex(
        vertices,
        world_vertices[first_vertex_index],
        palette_index,
    )
    _append_geometry_vertex(
        vertices,
        world_vertices[second_vertex_index],
        palette_index,
    )


def _triangle_fan_indices(indices):
    anchor = indices[0]
    for triangle_index in range(len(indices) - 2):
        yield (anchor, indices[triangle_index + 1], indices[triangle_index + 2])


def _append_polyline_segments(vertices, world_vertices, indices, palette_index):
    for index_pos, vertex_index in enumerate(indices):
        next_vertex_index = indices[(index_pos + 1) % len(indices)]
        _append_geometry_vertex(
            vertices,
            world_vertices[vertex_index],
            palette_index,
        )
        _append_geometry_vertex(
            vertices,
            world_vertices[next_vertex_index],
            palette_index,
        )


def _append_geometry_vertex(vertices, world_vertex, palette_index):
    x = float(world_vertex[0]) / FIXED_16_16_SCALE
    y = float(world_vertex[1]) / FIXED_16_16_SCALE
    z = float(world_vertex[2]) / FIXED_16_16_SCALE
    vertices.extend(
        (
            x,
            y,
            z,
            float(palette_index),
        )
    )


def _grouped_vertices_for(grouped_vertices, desc_idx):
    desc_idx = int(desc_idx)
    vertices = grouped_vertices.get(desc_idx)
    if vertices is None:
        vertices = array("f")
        grouped_vertices[desc_idx] = vertices
    return vertices


def _grouped_vertex_count(grouped_vertices, vertex_floats):
    return sum(
        len(vertices) // int(vertex_floats)
        for vertices in grouped_vertices.values()
    )


def _mode0_palette_index(face_flags):
    return (int(face_flags) >> 4) & 0xFF


def _mode2_palette_index(face_flags):
    return (int(face_flags) & 0xFF0) >> 4


def _satellite_force_mode4(face_flags, billboard_color):
    face_flags = int(face_flags)
    mode_bits = face_flags & 0x7000
    base = int(billboard_color) if mode_bits == 0x3000 else (
        (face_flags & 0x0F00) >> 4
    )
    return 0x4000 | (base & 0xFF)


def _satellite_primary_iff_index(gamemem, owner_addr, cache):
    key = (0x0100, int(owner_addr or 0))
    cached = cache.get(key)
    if cached is not None:
        return cached
    index = 2
    entity_index = int(gamemem.read_runtime_u16(owner_addr + 0x14))
    entity_count = int(gamemem.read_reloc_i32(ADDR_ENTITY_COUNT))
    if 0 <= entity_index < min(PRIMARY_ENTITY_LIMIT, max(0, entity_count)):
        body = int(
            gamemem.read_reloc_u32(ADDR_ENTITY_BODY_TABLE + entity_index * 4)
        )
        if body:
            slot = int(gamemem.read_runtime_u32(body + 0x08))
            if 0 <= slot < PRIMARY_CLASSIFICATION_SLOTS:
                index = int(
                    gamemem.read_reloc_u8(
                        ADDR_PRIMARY_CLASSIFICATION
                        + slot * PRIMARY_CLASSIFICATION_STRIDE
                    )
                )
    index = max(0, min(2, int(index)))
    cache[key] = index
    return index


def _satellite_secondary_iff_index(gamemem, owner_addr, cache):
    key = (0x0200, int(owner_addr or 0))
    cached = cache.get(key)
    if cached is not None:
        return cached
    index = 2
    record_index = int(gamemem.read_runtime_u16(owner_addr + 0x14))
    if 0 <= record_index <= 0xFF:
        reference = int(
            gamemem.read_reloc_i32(
                ADDR_SECONDARY_IFF_REFERENCES + record_index * 0x40
            )
        )
        if reference >= 0:
            index = int(
                gamemem.read_reloc_u8(
                    ADDR_SECONDARY_CLASSIFICATION + reference
                )
            )
    index = max(0, min(2, int(index)))
    cache[key] = index
    return index


def _wireframe_palette_index(gamemem, owner_addr):
    if not owner_addr:
        return WIREFRAME_DEFAULT_PALETTE_INDEX
    owner_head = gamemem.read_runtime_bytes(owner_addr, 4)
    owner_type = _u16(owner_head, 0x00)
    owner_flags = _u16(owner_head, 0x02)

    ah = (int(owner_flags) >> 8) & 0xFF
    if ah & 0x02:
        return 0x07
    if ah & 0x04:
        return 0x0B
    if ah & 0x01:
        type_nibble = (int(owner_type) & 0x00F0) >> 4
        if type_nibble < 1:
            return 0x07
        return 0x0B if type_nibble >= 12 else 0x03
    return WIREFRAME_DEFAULT_PALETTE_INDEX


def _mode57_texture_desc_index(face_flags):
    return (int(face_flags) & 0xFF) + 0x100


def _target_flat_face_flags(face_flags, owner_class):
    owner_class = int(owner_class)
    lighting_scale = None
    if (TARGET_FLAT_MATERIAL_CLASS_MASK & 0x0100) and (
        (owner_class & 0x0100)
        or (owner_class & 0x00F0) == 0x0050
    ):
        lighting_scale = TARGET_MECH_LIGHTING_SCALE
    elif TARGET_FLAT_MATERIAL_CLASS_MASK & owner_class:
        lighting_scale = TARGET_OTHER_LIGHTING_SCALE
    if lighting_scale is None:
        return None
    # Keep the source S-field as the fallback palette family while discarding
    # only the texture index and replacing it with the target lighting scale.
    return (int(face_flags) & 0x0F00) | lighting_scale


def _mode3_texture_desc_index(face_flags):
    return (int(face_flags) & 0xFF0) >> 4


def _trunc_div(numerator, denominator):
    if denominator == 0:
        raise ZeroDivisionError("integer division by zero")
    sign = -1 if (numerator < 0) != (denominator < 0) else 1
    return sign * (abs(numerator) // abs(denominator))
