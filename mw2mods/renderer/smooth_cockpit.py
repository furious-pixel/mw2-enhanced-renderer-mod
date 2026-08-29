import struct
from typing import NamedTuple


ADDR_PLAYER_SLOT = 0x000A5918
ADDR_CAMERA_MODE = 0x000A6FB4
ADDR_ENTITY_BODY_TABLE = 0x00108B00

FP29_SCALE = float(1 << 29)
MODEL_TREE_NODE_SIZE = 0x70
MAX_MODEL_TREE_NODES = 1000


class TreeNode(NamedTuple):
    parent: int
    child: int
    sibling: int
    local: tuple
    native: tuple
    geometry: int


def prepare_smooth_cockpit(gamemem, camera):
    """Couple the cockpit camera and mesh to one float hierarchy solution."""
    try:
        if int(gamemem.read_reloc_i32(ADDR_CAMERA_MODE)) != 0:
            return camera
        player_slot = int(gamemem.read_reloc_u32(ADDR_PLAYER_SLOT))
        entity = int(
            gamemem.read_reloc_u32(ADDR_ENTITY_BODY_TABLE + player_slot * 4)
        )
        if entity == 0:
            return camera
        entity_data = bytes(gamemem.read_runtime_bytes(entity + 0x20, 0x28))
        mech = _u32(entity_data, 0x00)
        model_root = _u32(entity_data, 0x20)
        camera_attachment = _u32(entity_data, 0x24)
        if mech == 0 or model_root == 0:
            return camera
        cockpit_root = _u32(
            bytes(gamemem.read_runtime_bytes(mech + 0x60, 4)),
            0,
        )
        if cockpit_root == 0:
            return camera

        nodes = _snapshot_tree(gamemem, model_root)
        if cockpit_root not in nodes:
            return camera
        smooth_world = _solve_smooth_world(nodes)
        cockpit_nodes = _descendants(nodes, cockpit_root)
        owner_matrices = {
            node_addr: _matrix_record(*smooth_world[node_addr])
            for node_addr in cockpit_nodes
            if node_addr in smooth_world
        }
        render_nodes = tuple(
            sorted(
                {
                    int(nodes[node_addr].geometry)
                    for node_addr in cockpit_nodes
                    if int(nodes[node_addr].geometry) != 0
                }
            )
        )
        if not owner_matrices:
            return camera

        result = dict(camera)
        result["_smooth_owner_matrices"] = owner_matrices
        if render_nodes:
            result["_cockpit_render_node_addrs"] = render_nodes
        result["_smooth_cockpit_active"] = True
        if camera_attachment in smooth_world and camera_attachment in nodes:
            _couple_camera_to_smooth_attachment(
                result,
                smooth_world[camera_attachment],
                nodes[camera_attachment].native,
            )
        return result
    except Exception:
        # Mission transitions can expose incomplete player state. Preserve the
        # established native-matrix path until a coherent hierarchy is present.
        return camera


def _snapshot_tree(gamemem, root):
    nodes = {}
    stack = [int(root)]
    while stack and len(nodes) < MAX_MODEL_TREE_NODES:
        node_addr = stack.pop()
        if node_addr == 0 or node_addr in nodes:
            continue
        data = bytes(gamemem.read_runtime_bytes(node_addr, MODEL_TREE_NODE_SIZE))
        parent = _u32(data, 0x00)
        child = _u32(data, 0x04)
        sibling = _u32(data, 0x08)
        local_r = tuple(
            _i32(data, 0x0C + index * 4) / FP29_SCALE
            for index in range(9)
        )
        local_t = tuple(float(_i32(data, 0x30 + axis * 4)) for axis in range(3))
        native_r = tuple(
            _i32(data, 0x3C + index * 4) / FP29_SCALE
            for index in range(9)
        )
        native_t = tuple(float(_i32(data, 0x60 + axis * 4)) for axis in range(3))
        geometry = _u32(data, 0x6C)
        nodes[node_addr] = TreeNode(
            int(parent),
            int(child),
            int(sibling),
            (local_r, local_t),
            (native_r, native_t),
            int(geometry),
        )
        if sibling and node_addr != int(root):
            stack.append(int(sibling))
        if child:
            stack.append(int(child))
    return nodes


def _solve_smooth_world(nodes):
    solved = {}
    visiting = set()
    for node_addr in nodes:
        _solve_smooth_node(nodes, node_addr, solved, visiting)
    return solved


def _solve_smooth_node(nodes, node_addr, solved, visiting):
    existing = solved.get(node_addr)
    if existing is not None:
        return existing
    if node_addr in visiting:
        raise ValueError("model-tree parent cycle")
    node = nodes[node_addr]
    visiting.add(node_addr)
    try:
        local_r, local_t = node.local
        parent_addr = node.parent
        if parent_addr and parent_addr in nodes:
            parent_r, parent_t = _solve_smooth_node(
                nodes,
                parent_addr,
                solved,
                visiting,
            )
            world_r = _mat_mul(parent_r, local_r)
            rotated_t = _mat_vec(parent_r, local_t)
            world_t = tuple(
                rotated_t[axis] + parent_t[axis] for axis in range(3)
            )
        else:
            world_r = local_r
            world_t = local_t
        result = (world_r, world_t)
        solved[node_addr] = result
        return result
    finally:
        visiting.remove(node_addr)


def _descendants(nodes, root):
    result = set()
    stack = [int(root)]
    while stack:
        node_addr = stack.pop()
        if node_addr == 0 or node_addr in result or node_addr not in nodes:
            continue
        result.add(node_addr)
        child = nodes[node_addr].child
        while child and child in nodes:
            stack.append(child)
            child = nodes[child].sibling
    return result


def _couple_camera_to_smooth_attachment(camera, smooth, native):
    smooth_r, smooth_t = smooth
    native_r, native_t = native
    correction_r = _mat_mul(smooth_r, _mat_inverse(native_r))

    position_fixed = tuple(
        float(component) * 65536.0 for component in camera["position"]
    )
    relative_position = tuple(
        position_fixed[axis] - native_t[axis] for axis in range(3)
    )
    corrected_relative = _mat_vec(correction_r, relative_position)
    corrected_fixed = tuple(
        corrected_relative[axis] + smooth_t[axis] for axis in range(3)
    )
    camera["position"] = tuple(value / 65536.0 for value in corrected_fixed)
    camera["_smooth_position_fixed"] = corrected_fixed

    correction_transpose = _mat_transpose(correction_r)
    for basis_name in ("right", "up", "forward"):
        camera[basis_name] = _row_mat_mul(
            tuple(float(value) for value in camera[basis_name]),
            correction_transpose,
        )


def _matrix_record(rotation, translation):
    return {
        "rotation": rotation,
        "translation": translation,
    }


def _mat_mul(a, b):
    return tuple(
        sum(a[row * 3 + inner] * b[inner * 3 + col] for inner in range(3))
        for row in range(3)
        for col in range(3)
    )


def _mat_vec(matrix, vector):
    return tuple(
        sum(matrix[row * 3 + col] * vector[col] for col in range(3))
        for row in range(3)
    )


def _row_mat_mul(row, matrix):
    return tuple(
        sum(row[inner] * matrix[inner * 3 + col] for inner in range(3))
        for col in range(3)
    )


def _mat_transpose(matrix):
    return tuple(matrix[col * 3 + row] for row in range(3) for col in range(3))


def _mat_inverse(matrix):
    a, b, c, d, e, f, g, h, i = matrix
    determinant = (
        a * (e * i - f * h)
        - b * (d * i - f * g)
        + c * (d * h - e * g)
    )
    if abs(determinant) < 1.0e-12:
        raise ValueError("singular attachment matrix")
    reciprocal = 1.0 / determinant
    return (
        (e * i - f * h) * reciprocal,
        (c * h - b * i) * reciprocal,
        (b * f - c * e) * reciprocal,
        (f * g - d * i) * reciprocal,
        (a * i - c * g) * reciprocal,
        (c * d - a * f) * reciprocal,
        (d * h - e * g) * reciprocal,
        (b * g - a * h) * reciprocal,
        (a * e - b * d) * reciprocal,
    )


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _i32(data, offset):
    return struct.unpack_from("<i", data, offset)[0]
