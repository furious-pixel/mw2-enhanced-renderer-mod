from .geometry_numba import (
    cockpit_bounds_fixed_kernel,
    relocate_cockpit_effect_vertices_kernel,
)


ADDR_COLLISION_EFFECT_POOL = 0x00327100
COLLISION_EFFECT_SLOT_SIZE = 0x24
COLLISION_EFFECT_SLOT_COUNT = 256
COLLISION_EFFECT_POOL_SIZE = (
    COLLISION_EFFECT_SLOT_SIZE * COLLISION_EFFECT_SLOT_COUNT
)
MAX_RELOCATED_EFFECT_ID = 22
MODEL_TREE_NODE_SIZE = 0x70
MAX_MODEL_TREE_NODES = 1000
COCKPIT_EFFECT_DEPTH_MARGIN = 1.10


class CockpitEffectTracker:
    """Retain render data only for fixed-pool effects near the cockpit."""

    __slots__ = (
        "node_slots",
        "model_roots",
        "effect_ids",
        "raw_x",
        "raw_y",
        "raw_z",
        "render_nodes",
        "transforms",
    )

    def __init__(self):
        self.node_slots = {}
        self.model_roots = [0] * COLLISION_EFFECT_SLOT_COUNT
        self.effect_ids = [-1] * COLLISION_EFFECT_SLOT_COUNT
        self.raw_x = [0] * COLLISION_EFFECT_SLOT_COUNT
        self.raw_y = [0] * COLLISION_EFFECT_SLOT_COUNT
        self.raw_z = [0] * COLLISION_EFFECT_SLOT_COUNT
        self.render_nodes = [None] * COLLISION_EFFECT_SLOT_COUNT
        self.transforms = [None] * COLLISION_EFFECT_SLOT_COUNT

    def clear(self):
        self.node_slots.clear()
        for slot_index in range(COLLISION_EFFECT_SLOT_COUNT):
            self.model_roots[slot_index] = 0
            self.effect_ids[slot_index] = -1
            self.render_nodes[slot_index] = None
            self.transforms[slot_index] = None

    def update(self, gamemem, camera, cockpit_radius_fixed):
        if int(camera.get("camera_mode", -1)) != 0:
            return
        try:
            pool = gamemem.read_reloc_bytes(
                ADDR_COLLISION_EFFECT_POOL,
                COLLISION_EFFECT_POOL_SIZE,
            )
        except Exception:
            self.clear()
            return

        head = camera.get("_smooth_position_fixed", camera["position_fixed"])
        head_x = float(head[0])
        head_y = float(head[1])
        head_z = float(head[2])
        radius = max(0.0, float(cockpit_radius_fixed))
        radius_sq = (radius * COCKPIT_EFFECT_DEPTH_MARGIN) ** 2
        for slot_index in range(COLLISION_EFFECT_SLOT_COUNT):
            offset = slot_index * COLLISION_EFFECT_SLOT_SIZE
            active = _u32(pool, offset + 0x14) != 0
            effect_id = _i32(pool, offset + 0x20) if active else -1
            model_root = _u32(pool, offset) if active else 0
            if (
                not active
                or not 0 <= effect_id <= MAX_RELOCATED_EFFECT_ID
                or model_root == 0
                or radius_sq <= 0.0
            ):
                self._release_slot(slot_index)
                continue

            raw_x = _i32(pool, offset + 0x04)
            raw_y = _i32(pool, offset + 0x08)
            raw_z = _i32(pool, offset + 0x0C)
            dx = float(raw_y) - head_x
            dy = float(raw_x) - head_y
            dz = float(raw_z) - head_z
            if dx * dx + dy * dy + dz * dz > radius_sq:
                self._release_slot(slot_index)
                continue

            changed = (
                self.model_roots[slot_index] != model_root
                or self.effect_ids[slot_index] != effect_id
                or self.raw_x[slot_index] != raw_x
                or self.raw_y[slot_index] != raw_y
                or self.raw_z[slot_index] != raw_z
            )
            if changed:
                self._release_slot(slot_index)
                self.model_roots[slot_index] = model_root
                self.effect_ids[slot_index] = effect_id
                self.raw_x[slot_index] = raw_x
                self.raw_y[slot_index] = raw_y
                self.raw_z[slot_index] = raw_z
                nodes = tuple(_render_nodes_for_model(gamemem, model_root))
                self.render_nodes[slot_index] = nodes
                for node_addr in nodes:
                    self.node_slots[int(node_addr)] = slot_index

    def _release_slot(self, slot_index):
        nodes = self.render_nodes[slot_index]
        if (
            self.model_roots[slot_index] == 0
            and self.effect_ids[slot_index] == -1
            and nodes is None
            and self.transforms[slot_index] is None
        ):
            return
        if nodes is not None:
            for node_addr in nodes:
                node_addr = int(node_addr)
                if self.node_slots.get(node_addr) == slot_index:
                    del self.node_slots[node_addr]
        self.model_roots[slot_index] = 0
        self.effect_ids[slot_index] = -1
        self.render_nodes[slot_index] = None
        self.transforms[slot_index] = None


def cockpit_bounds_fixed(world_vertices, camera):
    if not world_vertices.size:
        return 0.0, 0.0
    head = camera.get("_smooth_position_fixed", camera["position_fixed"])
    forward = camera["forward"]
    return cockpit_bounds_fixed_kernel(
        world_vertices,
        float(head[0]),
        float(head[1]),
        float(head[2]),
        float(forward[0]),
        float(forward[1]),
        float(forward[2]),
    )


def relocate_cockpit_effect_vertices(
    world_vertices,
    node_addr,
    camera,
    tracker,
    previous_cockpit_far_depth_fixed,
):
    """Apply one head-centered, spawn-latched relocation to an effect node."""
    slot_index = tracker.node_slots.get(int(node_addr))
    if slot_index is None or not world_vertices.size:
        return world_vertices, False, False

    transform = tracker.transforms[slot_index]
    latched = False
    if transform is None:
        if int(camera.get("camera_mode", -1)) != 0:
            return world_vertices, False, False
        far_depth = float(previous_cockpit_far_depth_fixed or 0.0)
        if far_depth <= 0.0:
            return world_vertices, False, False
        head = camera.get("_smooth_position_fixed", camera["position_fixed"])
        forward = camera["forward"]
        head_x = float(head[0])
        head_y = float(head[1])
        head_z = float(head[2])
        anchor_depth = (
            (float(tracker.raw_y[slot_index]) - head_x) * float(forward[0])
            + (float(tracker.raw_x[slot_index]) - head_y)
            * float(forward[1])
            + (float(tracker.raw_z[slot_index]) - head_z)
            * float(forward[2])
        )
        target_depth = far_depth * COCKPIT_EFFECT_DEPTH_MARGIN
        scale = 1.0
        forward_push = 0.0
        if anchor_depth < target_depth:
            if anchor_depth > 1.0e-6:
                scale = target_depth / anchor_depth
            else:
                forward_push = target_depth - anchor_depth
        transform = (
            head_x,
            head_y,
            head_z,
            float(scale),
            float(forward[0] * forward_push),
            float(forward[1] * forward_push),
            float(forward[2] * forward_push),
        )
        tracker.transforms[slot_index] = transform
        latched = True

    scale = float(transform[3])
    if (
        scale == 1.0
        and transform[4] == 0.0
        and transform[5] == 0.0
        and transform[6] == 0.0
    ):
        return world_vertices, False, latched
    relocated = relocate_cockpit_effect_vertices_kernel(
        world_vertices,
        float(transform[0]),
        float(transform[1]),
        float(transform[2]),
        scale,
        float(transform[4]),
        float(transform[5]),
        float(transform[6]),
    )
    return relocated, True, latched


def _render_nodes_for_model(gamemem, root):
    root = int(root)
    render_nodes = []
    seen = set()
    stack = [root]
    while stack and len(seen) < MAX_MODEL_TREE_NODES:
        node_addr = int(stack.pop())
        if node_addr == 0 or node_addr in seen:
            continue
        seen.add(node_addr)
        try:
            data = bytes(
                gamemem.read_runtime_bytes(node_addr, MODEL_TREE_NODE_SIZE)
            )
        except Exception:
            continue
        child = _u32(data, 0x04)
        sibling = _u32(data, 0x08)
        geometry = _u32(data, 0x6C)
        if geometry:
            render_nodes.append(int(geometry))
        if sibling and node_addr != root:
            stack.append(int(sibling))
        if child:
            stack.append(int(child))
    return render_nodes


def _u32(data, offset):
    return (
        int(data[offset])
        | int(data[offset + 1]) << 8
        | int(data[offset + 2]) << 16
        | int(data[offset + 3]) << 24
    )


def _i32(data, offset):
    value = _u32(data, offset)
    return value - 0x100000000 if value & 0x80000000 else value
