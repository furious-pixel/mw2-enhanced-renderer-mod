import math
import struct

import numpy as np

from .entity_lod_assets import (
    DUMMY_RESOURCE_ID,
    WTBO_HEADER_SIZE,
    WTBO_VERTEX_STRIDE,
    wtbo_face_stride,
)
from .object_lod import snapshot_component_descriptors
from .projection import perspective_projection_info


ADDR_ENTITY_BODY_TABLE = 0x00108B00
ADDR_ENTITY_COUNT = 0x000A6270
ADDR_PLAYER_SLOT = 0x000A5918
MAX_PRIMARY_ENTITIES = 4096
HIDDEN_DETAIL = 0xFF
ENTITY_MODEL_RADIUS_OFFSET = 0xE8
RUNTIME_BLOCK_HEADER_SIZE = 0x18
RUNTIME_FACE_STRIDE = 0x24
DETAIL_MODES = ("detail0", "detail1", "detail2", "detail3")
EMBLEM_TEXTURE_SOURCE_INDEX = 0x14

_ENTITY_FRAME_DTYPE = np.dtype(
    {
        "names": (
            "owner_id", "flags", "native_detail", "model_pointer", "position",
            "pointer", "camo", "emblem", "selected_detail",
        ),
        "formats": (
            "<i4", "<u2", "<i4", "<u4", ("<i4", (3,)),
            "<u4", "u1", "u1", "u1",
        ),
        "offsets": (
            0x04, 0x14, 0x1C, 0x20, 0x50, 0x5C, 0x60, 0x61, 0x62,
        ),
        "itemsize": 0x64,
    }
)
class EntityComponentFrame:
    """Reusable fixed-dtype live entity and component bindings."""

    __slots__ = (
        "entity_count",
        "entities",
        "descriptor_snapshot",
        "entity_indices",
        "node_flags",
        "node_seen",
    )

    def __init__(self, descriptor_count):
        self.entity_count = 0
        self.entities = np.empty(MAX_PRIMARY_ENTITIES, dtype=_ENTITY_FRAME_DTYPE)
        self.descriptor_snapshot = None
        self.entity_indices = np.empty(int(descriptor_count), dtype=np.int32)
        self.node_flags = np.empty(int(descriptor_count), dtype=np.uint32)
        self.node_seen = np.empty(int(descriptor_count), dtype=np.uint8)

def prepare_entity_lod_catalog(gamemem, resource_store):
    """Build immutable mission catalog and queue its exterior POLY assets."""
    if resource_store.state not in ("IDLE", "WAITING"):
        return
    try:
        descriptor_snapshot = snapshot_component_descriptors(gamemem)
        if descriptor_snapshot.count <= 0:
            resource_store.state = "WAITING"
            resource_store.error = "component descriptor table is empty"
            return
        resource_store.clear()
        resource_store.frame = EntityComponentFrame(descriptor_snapshot.count)
        resource_store.resource_ids = tuple(sorted({
            int(resource_id)
            for resource_ids in descriptor_snapshot.records["resource_ids"]
            for resource_id in resource_ids[:4]
            if int(resource_id) not in (0, DUMMY_RESOURCE_ID)
        }))
        resource_store.state = (
            "QUEUED" if resource_store.resource_ids else "READY"
        )
    except Exception as exc:
        resource_store.state = "WAITING"
        resource_store.error = f"{type(exc).__name__}: {exc}"


def _snapshot_entities(gamemem, frame, entity_count):
    pointer_bytes = bytes(
        gamemem.read_reloc_bytes(
            ADDR_ENTITY_BODY_TABLE,
            entity_count * 4,
        )
    )
    pointers = np.frombuffer(pointer_bytes, dtype="<u4", count=entity_count)
    entities = frame.entities
    entities["pointer"][:entity_count] = pointers
    entity_bytes = entities.view(np.uint8).reshape(
        len(entities),
        _ENTITY_FRAME_DTYPE.itemsize,
    )
    for entity_index in range(entity_count):
        entity = int(pointers[entity_index])
        if entity == 0:
            entity_bytes[entity_index, :0x5C].fill(0)
            entities["owner_id"][entity_index] = -1
            entities["native_detail"][entity_index] = -1
            continue
        entity_data = bytes(gamemem.read_runtime_bytes(entity, 0x5C))
        if len(entity_data) != 0x5C:
            raise ValueError("short primary entity record")
        entity_bytes[entity_index, :0x5C] = np.frombuffer(
            entity_data,
            dtype=np.uint8,
        )


def _bind_descriptors_to_entities(frame):
    bindings = frame.entity_indices
    bindings.fill(-1)
    owner_entities = {}
    for entity_index in range(frame.entity_count):
        if int(frame.entities["pointer"][entity_index]) == 0:
            continue
        owner_id = int(frame.entities["owner_id"][entity_index])
        owner_entities[owner_id] = (
            entity_index if owner_id not in owner_entities else -2
        )
    owner_ids = frame.descriptor_snapshot.records["owner_id"]
    for descriptor_index in range(len(bindings)):
        binding = int(
            owner_entities.get(int(owner_ids[descriptor_index]), -1)
        )
        bindings[descriptor_index] = binding


def snapshot_entity_component_frame(
    gamemem,
    descriptor_snapshot,
    frame,
):
    entity_count = int(gamemem.read_reloc_i32(ADDR_ENTITY_COUNT))
    if entity_count < 0 or entity_count > MAX_PRIMARY_ENTITIES:
        raise ValueError(f"invalid primary entity count {entity_count}")
    descriptor_count = int(descriptor_snapshot.count)
    if descriptor_count != len(frame.entity_indices):
        raise ValueError(
            "component descriptor count changed within mission generation"
        )
    frame.entity_count = entity_count
    _snapshot_entities(gamemem, frame, entity_count)
    frame.descriptor_snapshot = descriptor_snapshot
    _bind_descriptors_to_entities(frame)
    frame.node_flags.fill(0)
    frame.node_seen.fill(0)
    return frame


def _scan_entity_materials(
    gamemem,
    frame,
    entity_index,
    resource_store,
):
    counts = [0] * 8
    emblem_counts = {}
    records = frame.descriptor_snapshot.records
    for descriptor_index in range(len(frame.entity_indices)):
        if int(frame.entity_indices[descriptor_index]) != entity_index:
            continue
        node = int(records["installed_node"][descriptor_index])
        if node == 0:
            continue
        try:
            block = int(gamemem.read_runtime_u32(node + 0x1C))
            if block == 0:
                continue
            header = bytes(
                gamemem.read_runtime_bytes(block, RUNTIME_BLOCK_HEADER_SIZE)
            )
            face_count = int(struct.unpack_from("<H", header, 0x06)[0])
            face_offset = int(struct.unpack_from("<I", header, 0x08)[0])
            if face_count < 1 or face_count > 4096:
                continue
            faces = bytes(
                gamemem.read_runtime_bytes(
                    block + face_offset,
                    face_count * RUNTIME_FACE_STRIDE,
                )
            )
            asset = None
            authored_face_offset = 0
            installed_detail = int(
                records["installed_detail"][descriptor_index]
            )
            if 0 <= installed_detail < 5:
                resource_id = int(
                    records["resource_ids"][
                        descriptor_index,
                        installed_detail,
                    ]
                )
                asset = resource_store.assets.get(resource_id)
            if asset is not None:
                vertex_count, asset_face_count = struct.unpack_from(
                    "<HH",
                    asset,
                    0x18,
                )
                if int(asset_face_count) != face_count:
                    asset = None
                else:
                    authored_face_offset = (
                        WTBO_HEADER_SIZE
                        + int(vertex_count) * WTBO_VERTEX_STRIDE
                    )
            for face_index in range(face_count):
                flags = int(
                    struct.unpack_from(
                        "<H",
                        faces,
                        face_index * RUNTIME_FACE_STRIDE,
                    )[0]
                )
                mode = (flags >> 12) & 0xF
                texture = flags & 0xFF
                if mode in (5, 6, 7) and texture < 8:
                    counts[texture] += 1
                if asset is not None:
                    authored, count = struct.unpack_from(
                        "<HH",
                        asset,
                        authored_face_offset,
                    )
                    authored_face_offset += wtbo_face_stride(count)
                    if (
                        ((int(authored) >> 12) & 0xF) == mode
                        and mode in (5, 6, 7)
                        and (int(authored) & 0xFF)
                        == EMBLEM_TEXTURE_SOURCE_INDEX
                    ):
                        emblem_counts[texture] = (
                            int(emblem_counts.get(texture, 0)) + 1
                        )
        except Exception:
            continue
    camo = max(range(8), key=counts.__getitem__)
    emblem = (
        max(emblem_counts, key=emblem_counts.__getitem__)
        if emblem_counts
        else EMBLEM_TEXTURE_SOURCE_INDEX
    )
    return camo, emblem


def _projected_detail(projected_height, thresholds, previous, hysteresis):
    if previous is None or previous < 0 or previous > 3:
        for detail, threshold in enumerate(thresholds):
            if projected_height >= threshold:
                return detail
        return 3
    detail = int(previous)
    while detail > 0 and projected_height >= thresholds[detail - 1] * (
        1.0 + hysteresis
    ):
        detail -= 1
    while detail < 3 and projected_height < thresholds[detail] * (
        1.0 - hysteresis
    ):
        detail += 1
    return detail


def _output_projection_metric(camera, output_width, output_height):
    if camera.get("projection_type") == "orthographic":
        half_width = max(
            1.0 / 65536.0,
            float(camera.get("orthographic_half_width", 1.0)),
        )
        # Keep this identical to scene_renderer._camera_projection(). The
        # game's pane height is not necessarily the final renderer aspect.
        half_height = half_width * output_height / output_width
        return True, half_height

    explicit_output_focal = camera.get("entity_lod_output_focal_pixels")
    if explicit_output_focal is not None:
        output_focal = max(1.0, float(explicit_output_focal))
    else:
        projection = perspective_projection_info(
            output_width,
            output_height,
            focal_length_pixels=camera.get("focal_length_pixels", 512.0),
            max_horizontal_fov_degrees=camera.get(
                "max_horizontal_fov_degrees"
            ),
        )
        output_focal = projection.output_focal_length_pixels
    return False, output_focal


def select_entity_lod_details(gamemem, frame, resource_store, camera, stats):
    selection = str(camera["entity_lod_selection"])
    if selection == "native":
        return False
    forced_detail = (
        DETAIL_MODES.index(selection) if selection in DETAIL_MODES else None
    )
    thresholds = camera["entity_lod_detail_pixels"]
    threshold0, threshold1, threshold2 = map(float, thresholds)
    thresholds = (
        threshold0,
        min(threshold0, threshold1),
        min(threshold0, threshold1, threshold2),
    )
    hysteresis = float(camera["entity_lod_hysteresis"])
    view_key = str(camera.get("entity_lod_view_key", "main"))
    output_width = max(1, int(camera.get("output_viewport_width", 1024)))
    output_height = max(1, int(camera.get("output_viewport_height", 768)))
    debug_decisions = bool(camera["entity_lod_debug_decisions"])
    needs_projection = forced_detail is None or debug_decisions
    selected_entity = camera.get("entity_lod_entity_index")
    if needs_projection:
        orthographic, projection_metric = _output_projection_metric(
            camera,
            output_width,
            output_height,
        )
    player_slot = int(gamemem.read_reloc_i32(ADDR_PLAYER_SLOT))
    hide_player = camera.get("entity_lod_hide_player")
    if hide_player is None:
        hide_player = (
            int(camera.get("camera_mode", 0)) == 0
            and not camera.get("satellite_view", False)
        )
    camera_position = camera.get("position_fixed", (0, 0, 0))
    history = resource_store.selection_history
    detail_counts = [0, 0, 0, 0]
    decision_rows = [] if debug_decisions else None
    entities = frame.entities
    if selected_entity is None:
        entity_indices = range(frame.entity_count)
    else:
        entities["selected_detail"][:frame.entity_count].fill(HIDDEN_DETAIL)
        entity_indices = (int(selected_entity),)
    for entity_index in entity_indices:
        if entity_index < 0 or entity_index >= frame.entity_count:
            continue
        entity_pointer = int(entities["pointer"][entity_index])
        if entity_pointer == 0:
            entities["selected_detail"][entity_index] = HIDDEN_DETAIL
            continue
        if entity_index == player_slot and bool(hide_player):
            entities["selected_detail"][entity_index] = HIDDEN_DETAIL
            continue
        materials = resource_store.material_indices.get(entity_pointer)
        if materials is None:
            materials = _scan_entity_materials(
                gamemem,
                frame,
                entity_index,
                resource_store,
            )
            resource_store.material_indices[entity_pointer] = materials
        camo, emblem = materials
        entities["camo"][entity_index] = int(camo)
        entities["emblem"][entity_index] = int(emblem)

        radial = -1.0
        radius = 0
        if needs_projection:
            model_pointer = int(entities["model_pointer"][entity_index])
            radius = resource_store.model_radii.get(model_pointer)
            if radius is None:
                try:
                    radius = (
                        abs(int(gamemem.read_runtime_i32(
                            model_pointer + ENTITY_MODEL_RADIUS_OFFSET
                        )))
                        if model_pointer else 0
                    )
                except Exception:
                    radius = 0
                resource_store.model_radii[model_pointer] = int(radius)
        projected_height = -1.0
        if needs_projection:
            position = entities["position"][entity_index]
            dx = int(position[0]) - int(camera_position[0])
            dy = int(position[1]) - int(camera_position[1])
            dz = int(position[2]) - int(camera_position[2])
            radial = math.sqrt(float(dx * dx + dy * dy + dz * dz))
            if radius > 0:
                if orthographic:
                    projected_height = (
                        (2.0 * radius / 65536.0)
                        * output_height
                        / projection_metric
                    )
                else:
                    projected_height = (
                        2.0
                        * radius
                        * projection_metric
                        / max(radial, 1.0)
                    )
        if forced_detail is not None:
            detail = forced_detail
        else:
            if radius <= 0:
                # Model +0xE8 is the authored bounding radius used by the
                # game's target camera. Unknown bounds must fail toward full
                # detail, not masquerade as a one-unit distant object.
                detail = 0
                projected_height = float("inf")
            else:
                history_key = (
                    view_key,
                    entity_pointer,
                    int(entities["owner_id"][entity_index]),
                )
                detail = _projected_detail(
                    projected_height,
                    thresholds,
                    history.get(history_key),
                    hysteresis,
                )
                history[history_key] = int(detail)
        entities["selected_detail"][entity_index] = int(detail)
        detail_counts[detail] += 1
        if decision_rows is not None:
            installed = frame.descriptor_snapshot.records["installed_detail"][
                frame.entity_indices == entity_index
            ]
            installed_min = int(installed.min()) if installed.size else -1
            installed_max = int(installed.max()) if installed.size else -1
            decision_rows.append(
                (
                    entity_index,
                    int(entities["owner_id"][entity_index]),
                    int(entities["native_detail"][entity_index]),
                    installed_min,
                    installed_max,
                    int(detail),
                    round(projected_height, 3),
                    round(radial / 65536.0, 3),
                    round(2.0 * radius / 65536.0, 3),
                )
            )

    stats["renderer_entity_lod_selection"] = selection
    stats["renderer_entity_lod_detail_counts"] = tuple(detail_counts)
    if decision_rows is not None:
        stats["renderer_entity_lod_decision_columns"] = (
            "entity",
            "owner",
            "native_requested",
            "installed_min",
            "installed_max",
            "renderer_selected",
            "projected_pixels",
            "radial_world",
            "bound_diameter_world",
        )
        stats["renderer_entity_lod_decisions"] = tuple(decision_rows)
    return True
