from .numba_cache import configure_numba_cache

configure_numba_cache(__package__)

import numpy as np
from numba import njit


FIXED_16_16_SCALE_F32 = np.float32(1.0 / 65536.0)
SQRT_TABLE_NP = np.array(
    [max(1, int(1024 * np.sqrt(max(1, index)))) for index in range(1024)],
    dtype=np.int64,
)


@njit(cache=True)
def cockpit_bounds_fixed_kernel(
    world_vertices,
    head_x,
    head_y,
    head_z,
    forward_x,
    forward_y,
    forward_z,
):
    far_depth = 0.0
    max_radius_sq = 0.0
    for index in range(world_vertices.shape[0]):
        dx = float(world_vertices[index, 0]) - head_x
        dy = float(world_vertices[index, 1]) - head_y
        dz = float(world_vertices[index, 2]) - head_z
        depth = dx * forward_x + dy * forward_y + dz * forward_z
        radius_sq = dx * dx + dy * dy + dz * dz
        far_depth = max(far_depth, depth)
        max_radius_sq = max(max_radius_sq, radius_sq)
    return far_depth, np.sqrt(max_radius_sq)


@njit(cache=True)
def relocate_cockpit_effect_vertices_kernel(
    world_vertices,
    head_x,
    head_y,
    head_z,
    scale,
    push_x,
    push_y,
    push_z,
):
    relocated = np.empty((world_vertices.shape[0], 3), dtype=np.float64)
    for index in range(world_vertices.shape[0]):
        relocated[index, 0] = (
            head_x
            + (float(world_vertices[index, 0]) - head_x) * scale
            + push_x
        )
        relocated[index, 1] = (
            head_y
            + (float(world_vertices[index, 1]) - head_y) * scale
            + push_y
        )
        relocated[index, 2] = (
            head_z
            + (float(world_vertices[index, 2]) - head_z) * scale
            + push_z
        )
    return relocated


@njit(cache=True)
def fill_mode3_billboard_instances(
    out_instances,
    world_vertices,
    anchors,
    others,
    mirror_u,
    flip_winding,
    face_order,
):
    for out_face_pos in range(face_order.shape[0]):
        face_pos = int(face_order[out_face_pos])
        anchor = int(anchors[face_pos])
        other = int(others[face_pos])
        out_base = out_face_pos * 7
        out_instances[out_base + 0] = (
            np.float32(world_vertices[anchor, 0]) * FIXED_16_16_SCALE_F32
        )
        out_instances[out_base + 1] = (
            np.float32(world_vertices[anchor, 1]) * FIXED_16_16_SCALE_F32
        )
        out_instances[out_base + 2] = (
            np.float32(world_vertices[anchor, 2]) * FIXED_16_16_SCALE_F32
        )
        out_instances[out_base + 3] = (
            np.float32(world_vertices[other, 0]) * FIXED_16_16_SCALE_F32
        )
        out_instances[out_base + 4] = (
            np.float32(world_vertices[other, 1]) * FIXED_16_16_SCALE_F32
        )
        out_instances[out_base + 5] = (
            np.float32(world_vertices[other, 2]) * FIXED_16_16_SCALE_F32
        )
        out_instances[out_base + 6] = np.float32(
            int(mirror_u[face_pos]) | (int(flip_winding[face_pos]) << 1)
        )


@njit(cache=True, inline="always")
def _decode_i32_le(data, offset):
    value = (
        np.uint32(data[offset])
        | (np.uint32(data[offset + 1]) << np.uint32(8))
        | (np.uint32(data[offset + 2]) << np.uint32(16))
        | (np.uint32(data[offset + 3]) << np.uint32(24))
    )
    return np.int32(value)


@njit(cache=True)
def decode_geometry_vertex_asset(
    local_vertices,
    cached_world_vertices,
    c_in_values,
    control_uvs,
    vertex_bytes,
    vertex_count,
    vertex_stride,
    decode_local,
    decode_cached_world,
):
    for index in range(vertex_count):
        vertex_offset = index * vertex_stride
        for axis in range(3):
            if decode_local:
                local_vertices[index, axis] = np.int64(
                    _decode_i32_le(vertex_bytes, vertex_offset + axis * 4)
                )
            if decode_cached_world:
                cached_world_vertices[index, axis] = np.int64(
                    _decode_i32_le(vertex_bytes, vertex_offset + 0x0C + axis * 4)
                )
        c_in_values[index] = np.uint8(vertex_bytes[vertex_offset + 0x18])
        control_uvs[index, 0] = _decode_i32_le(
            vertex_bytes,
            vertex_offset + 0x18,
        )
        control_uvs[index, 1] = _decode_i32_le(
            vertex_bytes,
            vertex_offset + 0x1C,
        )


@njit(cache=True)
def transform_geometry_vertex_asset(
    world_vertices,
    local_vertices,
    matrix_bytes,
    translation_offset,
):
    r0 = np.int64(_decode_i32_le(matrix_bytes, 0x00))
    r1 = np.int64(_decode_i32_le(matrix_bytes, 0x04))
    r2 = np.int64(_decode_i32_le(matrix_bytes, 0x08))
    r3 = np.int64(_decode_i32_le(matrix_bytes, 0x0C))
    r4 = np.int64(_decode_i32_le(matrix_bytes, 0x10))
    r5 = np.int64(_decode_i32_le(matrix_bytes, 0x14))
    r6 = np.int64(_decode_i32_le(matrix_bytes, 0x18))
    r7 = np.int64(_decode_i32_le(matrix_bytes, 0x1C))
    r8 = np.int64(_decode_i32_le(matrix_bytes, 0x20))
    tx = np.int64(_decode_i32_le(matrix_bytes, translation_offset + 0x00))
    ty = np.int64(_decode_i32_le(matrix_bytes, translation_offset + 0x04))
    tz = np.int64(_decode_i32_le(matrix_bytes, translation_offset + 0x08))

    for index in range(local_vertices.shape[0]):
        x = np.int64(local_vertices[index, 0])
        y = np.int64(local_vertices[index, 1])
        z = np.int64(local_vertices[index, 2])
        world_vertices[index, 0] = (
            (r0 * x + r1 * y + r2 * z) >> np.int64(29)
        ) + tx
        world_vertices[index, 1] = (
            (r3 * x + r4 * y + r5 * z) >> np.int64(29)
        ) + ty
        world_vertices[index, 2] = (
            (r6 * x + r7 * y + r8 * z) >> np.int64(29)
        ) + tz


@njit(cache=True)
def transform_geometry_face_normals(
    out_normals,
    face_edges_a,
    face_edges_b,
    matrix_bytes,
):
    r0 = np.float64(_decode_i32_le(matrix_bytes, 0x00))
    r1 = np.float64(_decode_i32_le(matrix_bytes, 0x04))
    r2 = np.float64(_decode_i32_le(matrix_bytes, 0x08))
    r3 = np.float64(_decode_i32_le(matrix_bytes, 0x0C))
    r4 = np.float64(_decode_i32_le(matrix_bytes, 0x10))
    r5 = np.float64(_decode_i32_le(matrix_bytes, 0x14))
    r6 = np.float64(_decode_i32_le(matrix_bytes, 0x18))
    r7 = np.float64(_decode_i32_le(matrix_bytes, 0x1C))
    r8 = np.float64(_decode_i32_le(matrix_bytes, 0x20))

    for face_index in range(face_edges_a.shape[0]):
        ax = np.float64(face_edges_a[face_index, 0])
        ay = np.float64(face_edges_a[face_index, 1])
        az = np.float64(face_edges_a[face_index, 2])
        bx = np.float64(face_edges_b[face_index, 0])
        by = np.float64(face_edges_b[face_index, 1])
        bz = np.float64(face_edges_b[face_index, 2])
        tax = r0 * ax + r1 * ay + r2 * az
        tay = r3 * ax + r4 * ay + r5 * az
        taz = r6 * ax + r7 * ay + r8 * az
        tbx = r0 * bx + r1 * by + r2 * bz
        tby = r3 * bx + r4 * by + r5 * bz
        tbz = r6 * bx + r7 * by + r8 * bz
        # WTBO face winding follows the game's left-handed component-space
        # convention. Reverse the ordinary right-handed edge cross so the
        # reconstructed N2 normal matches installed face+0x14..+0x1F.
        nx = taz * tby - tay * tbz
        ny = tax * tbz - taz * tbx
        nz = tay * tbx - tax * tby
        length = np.sqrt(nx * nx + ny * ny + nz * nz)
        if length > 0.0:
            scale = np.float64(536870912.0) / length
            out_normals[face_index, 0] = np.int64(np.rint(nx * scale))
            out_normals[face_index, 1] = np.int64(np.rint(ny * scale))
            out_normals[face_index, 2] = np.int64(np.rint(nz * scale))
        else:
            out_normals[face_index, 0] = 0
            out_normals[face_index, 1] = 0
            out_normals[face_index, 2] = 0


@njit(cache=True)
def face_source_headers_equal(previous, current, face_count, face_stride):
    """Compare authored/topology fields while ignoring live world normals."""
    for face_index in range(face_count):
        face_offset = face_index * face_stride
        for byte_offset in range(0x14):
            if (
                previous[face_offset + byte_offset]
                != current[face_offset + byte_offset]
            ):
                return False
        for byte_offset in range(0x20, face_stride):
            if (
                previous[face_offset + byte_offset]
                != current[face_offset + byte_offset]
            ):
                return False
    return True


@njit(cache=True)
def refresh_deferred_face_normals(
    face_headers,
    indexed_face_indices,
    indexed_normals,
    mode4_face_indices,
    mode4_normals,
    mode57_face_indices,
    mode57_normals,
    face_stride,
):
    """Decode current world normals into persistent deferred face packages."""
    for row in range(indexed_face_indices.shape[0]):
        face_offset = int(indexed_face_indices[row]) * face_stride + 0x14
        indexed_normals[row, 0] = _decode_i32_le(face_headers, face_offset)
        indexed_normals[row, 1] = _decode_i32_le(face_headers, face_offset + 4)
        indexed_normals[row, 2] = _decode_i32_le(face_headers, face_offset + 8)

    for row in range(mode4_face_indices.shape[0]):
        face_offset = int(mode4_face_indices[row]) * face_stride + 0x14
        mode4_normals[row, 0] = _decode_i32_le(face_headers, face_offset)
        mode4_normals[row, 1] = _decode_i32_le(face_headers, face_offset + 4)
        mode4_normals[row, 2] = _decode_i32_le(face_headers, face_offset + 8)

    for row in range(mode57_face_indices.shape[0]):
        face_offset = int(mode57_face_indices[row]) * face_stride + 0x14
        mode57_normals[row, 0] = _decode_i32_le(face_headers, face_offset)
        mode57_normals[row, 1] = _decode_i32_le(face_headers, face_offset + 4)
        mode57_normals[row, 2] = _decode_i32_le(face_headers, face_offset + 8)


@njit(cache=True)
def _bit_length_u64(value):
    value = np.uint64(value)
    result = 0
    while value != 0:
        result += 1
        value = value >> np.uint64(1)
    return result


@njit(cache=True, inline="always")
def _mode57_contribution_from_light(lx, ly, lz, nx, ny, nz, fog_min):
    dot64 = lx * nx + ly * ny + lz * nz
    dot_shifted = dot64 >> np.int64(16)

    ax = abs(lx)
    ay = abs(ly)
    az = abs(lz)
    light_or = np.uint64(ax | ay | az)
    if light_or == 0:
        return np.float32(1.0)

    norm_shift = _bit_length_u64(light_or) - 1 - 7
    if norm_shift < 0:
        norm_shift = 0
    lx8 = (ax >> norm_shift) & np.int64(0xFF)
    ly8 = (ay >> norm_shift) & np.int64(0xFF)
    lz8 = (az >> norm_shift) & np.int64(0xFF)
    dot_norm = dot_shifted >> norm_shift
    mag_sq = lx8 * lx8 + ly8 * ly8 + lz8 * lz8
    table_idx = (mag_sq >> np.int64(7)) & np.int64(0xFFFE)
    entry_idx = table_idx // np.int64(2)
    if entry_idx < 1:
        entry_idx = np.int64(1)
    if entry_idx >= SQRT_TABLE_NP.shape[0]:
        entry_idx = np.int64(SQRT_TABLE_NP.shape[0] - 1)
    table_val = SQRT_TABLE_NP[entry_idx]
    brightness = np.float32(dot_norm) / np.float32(table_val)
    remapped = (
        brightness * np.float32(np.int64(128 - fog_min)) / np.float32(128.0)
    ) + np.float32(fog_min)
    contribution = np.float32(127.5) * remapped / np.float32(1088.0)
    if contribution < np.float32(0.0):
        contribution = np.float32(0.0)
    elif contribution > np.float32(15.0):
        contribution = np.float32(15.0)
    return contribution


@njit(cache=True, inline="always")
def _trunc_div_i64(numerator, denominator):
    sign = np.int64(1)
    if (numerator < 0) != (denominator < 0):
        sign = np.int64(-1)
    return sign * (abs(numerator) // abs(denominator))


@njit(cache=True, inline="always")
def _light_table_value(lx, ly, lz, dot_shifted):
    ax = abs(lx)
    ay = abs(ly)
    az = abs(lz)
    light_or = np.uint64(ax | ay | az)
    if light_or == 0:
        return np.int64(0), np.int64(0)

    norm_shift = _bit_length_u64(light_or) - 1 - 7
    if norm_shift < 0:
        norm_shift = 0
    lx8 = (ax >> norm_shift) & np.int64(0xFF)
    ly8 = (ay >> norm_shift) & np.int64(0xFF)
    lz8 = (az >> norm_shift) & np.int64(0xFF)
    dot_norm = dot_shifted >> norm_shift
    mag_sq = lx8 * lx8 + ly8 * ly8 + lz8 * lz8
    table_idx = (mag_sq >> np.int64(7)) & np.int64(0xFFFE)
    entry_idx = table_idx // np.int64(2)
    if entry_idx < 1:
        entry_idx = np.int64(1)
    if entry_idx >= SQRT_TABLE_NP.shape[0]:
        entry_idx = np.int64(SQRT_TABLE_NP.shape[0] - 1)
    return dot_norm, SQRT_TABLE_NP[entry_idx]


@njit(cache=True, inline="always")
def _mode1_fog_offset(
    face_flags,
    count,
    indices,
    normal,
    world_base,
    world_vertices,
    light,
    light_directional,
    fog_min,
    fog_distance,
    camera_position,
    camera_forward,
):
    first_index = int(indices[0])
    first_world_index = world_base + first_index
    if light_directional != 0:
        lx = np.int64(light[0])
        ly = np.int64(light[1])
        lz = np.int64(light[2])
    else:
        lx = np.int64(light[0]) - np.int64(world_vertices[first_world_index, 0])
        ly = np.int64(light[1]) - np.int64(world_vertices[first_world_index, 1])
        lz = np.int64(light[2]) - np.int64(world_vertices[first_world_index, 2])

    nx = np.int64(normal[0])
    ny = np.int64(normal[1])
    nz = np.int64(normal[2])
    dot_shifted = (lx * nx + ly * ny + lz * nz) >> np.int64(16)
    dot_norm, table_val = _light_table_value(lx, ly, lz, dot_shifted)
    if table_val == 0:
        return np.int64(1)

    brightness = _trunc_div_i64(dot_norm, table_val)
    remapped = ((brightness * np.int64(128 - fog_min)) >> np.int64(7)) + np.int64(
        fog_min
    )
    half_base = np.int64(face_flags & np.int64(0xFF)) >> np.int64(1)
    contribution = _trunc_div_i64(half_base * remapped, np.int64(1088))

    sx = np.int64(0)
    sy = np.int64(0)
    sz = np.int64(0)
    for index_pos in range(count):
        vertex_index = world_base + int(indices[index_pos])
        sx += np.int64(world_vertices[vertex_index, 0])
        sy += np.int64(world_vertices[vertex_index, 1])
        sz += np.int64(world_vertices[vertex_index, 2])

    centroid_x = _trunc_div_i64(sx, np.int64(count))
    centroid_y = _trunc_div_i64(sy, np.int64(count))
    centroid_z = _trunc_div_i64(sz, np.int64(count))
    delta_x = centroid_x - np.int64(camera_position[0])
    delta_y = centroid_y - np.int64(camera_position[1])
    delta_z = centroid_z - np.int64(camera_position[2])
    depth_dot = (
        delta_x * np.int64(camera_forward[0])
        + delta_y * np.int64(camera_forward[1])
        + delta_z * np.int64(camera_forward[2])
    )
    view_depth = (depth_dot >> np.int64(29)) * np.int64(4)
    if view_depth < 0:
        view_depth = np.int64(0)
    fog_atten = ((view_depth << np.int64(4)) // np.int64(fog_distance)) >> np.int64(4)
    contribution -= fog_atten
    if contribution < 0:
        contribution = np.int64(0)
    elif contribution > 15:
        contribution = np.int64(15)
    return contribution


@njit(cache=True, inline="always")
def _mode4_contribution(
    face_flags,
    first_world_index,
    normal,
    world_vertices,
    light,
    light_directional,
    fog_min,
):
    if light_directional != 0:
        lx = np.int64(light[0])
        ly = np.int64(light[1])
        lz = np.int64(light[2])
    else:
        lx = np.int64(light[0]) - np.int64(world_vertices[first_world_index, 0])
        ly = np.int64(light[1]) - np.int64(world_vertices[first_world_index, 1])
        lz = np.int64(light[2]) - np.int64(world_vertices[first_world_index, 2])

    nx = np.int64(normal[0])
    ny = np.int64(normal[1])
    nz = np.int64(normal[2])
    dot_shifted = (lx * nx + ly * ny + lz * nz) >> np.int64(16)
    dot_norm, table_val = _light_table_value(lx, ly, lz, dot_shifted)
    if table_val == 0:
        return np.float32(1.0)

    brightness = np.float64(dot_norm) / np.float64(table_val)
    remapped = (
        brightness * np.float64(np.int64(128 - fog_min)) / np.float64(128.0)
    ) + np.float64(fog_min)
    contribution = (
        (np.float64(face_flags & np.int64(0xFF)) * np.float64(0.5))
        * remapped
    ) / np.float64(1088.0)
    if contribution < np.float64(0.0):
        contribution = np.float64(0.0)
    elif contribution > np.float64(15.0):
        contribution = np.float64(15.0)
    return np.float32(contribution)


@njit(cache=True)
def build_face_triangle_offsets(face_counts, face_triangle_offsets):
    running = np.int64(0)
    for face_pos in range(face_counts.shape[0]):
        face_triangle_offsets[face_pos] = running
        count = int(face_counts[face_pos])
        if count >= 3:
            running += np.int64(count - 2)
    return running


@njit(cache=True)
def build_wireframe_offsets(
    face_counts,
    face_emit_occluder,
    face_triangle_offsets,
    face_line_offsets,
    totals,
):
    triangle_running = np.int64(0)
    line_running = np.int64(0)
    for face_pos in range(face_counts.shape[0]):
        face_triangle_offsets[face_pos] = triangle_running
        face_line_offsets[face_pos] = line_running
        count = int(face_counts[face_pos])
        if face_emit_occluder[face_pos] != 0 and count >= 3:
            triangle_running += np.int64(count - 2)
        if count >= 2:
            line_running += np.int64(count)
    totals[0] = triangle_running
    totals[1] = line_running


@njit(cache=True)
def fill_indexed_wireframe_buffers(
    out_vertices,
    out_occluder_indices,
    out_line_indices,
    out_line_palettes,
    face_block_indices,
    face_counts,
    face_indices,
    face_palettes,
    face_emit_occluder,
    face_triangle_offsets,
    face_line_offsets,
    block_world_offsets,
    world_vertices,
):
    for vertex_pos in range(world_vertices.shape[0]):
        out_base = vertex_pos * 3
        out_vertices[out_base + 0] = (
            np.float32(world_vertices[vertex_pos, 0]) * FIXED_16_16_SCALE_F32
        )
        out_vertices[out_base + 1] = (
            np.float32(world_vertices[vertex_pos, 1]) * FIXED_16_16_SCALE_F32
        )
        out_vertices[out_base + 2] = (
            np.float32(world_vertices[vertex_pos, 2]) * FIXED_16_16_SCALE_F32
        )

    for face_pos in range(face_counts.shape[0]):
        count = int(face_counts[face_pos])
        if count < 2:
            continue

        block_pos = int(face_block_indices[face_pos])
        world_base = int(block_world_offsets[block_pos])
        line_index_base = int(face_line_offsets[face_pos]) * 2
        line_primitive_base = int(face_line_offsets[face_pos])
        palette = np.float32(face_palettes[face_pos])
        for edge_pos in range(count):
            out_line_indices[line_index_base + edge_pos * 2] = np.uint32(
                world_base + int(face_indices[face_pos, edge_pos])
            )
            out_line_indices[line_index_base + edge_pos * 2 + 1] = np.uint32(
                world_base + int(face_indices[face_pos, (edge_pos + 1) % count])
            )
            out_line_palettes[line_primitive_base + edge_pos] = palette

        if face_emit_occluder[face_pos] == 0 or count < 3:
            continue
        anchor = world_base + int(face_indices[face_pos, 0])
        triangle_index_base = int(face_triangle_offsets[face_pos]) * 3
        for triangle_pos in range(count - 2):
            out_base = triangle_index_base + triangle_pos * 3
            out_occluder_indices[out_base] = np.uint32(anchor)
            out_occluder_indices[out_base + 1] = np.uint32(
                world_base + int(face_indices[face_pos, triangle_pos + 1])
            )
            out_occluder_indices[out_base + 2] = np.uint32(
                world_base + int(face_indices[face_pos, triangle_pos + 2])
            )


@njit(cache=True)
def fill_wireframe_face_palettes(
    out_face_palettes,
    face_owner_slots,
    owner_palettes,
):
    for face_pos in range(face_owner_slots.shape[0]):
        out_face_palettes[face_pos] = owner_palettes[
            int(face_owner_slots[face_pos])
        ]


@njit(cache=True)
def fill_indexed_flat_vertices(out_vertices, world_vertices):
    for index in range(world_vertices.shape[0]):
        base = index * 3
        out_vertices[base + 0] = (
            np.float32(world_vertices[index, 0]) * FIXED_16_16_SCALE_F32
        )
        out_vertices[base + 1] = (
            np.float32(world_vertices[index, 1]) * FIXED_16_16_SCALE_F32
        )
        out_vertices[base + 2] = (
            np.float32(world_vertices[index, 2]) * FIXED_16_16_SCALE_F32
        )


@njit(cache=True)
def update_indexed_flat_vertices(
    out_vertices,
    world_vertices,
    block_world_offsets,
    block_vertex_counts,
    changed_blocks,
):
    for block_pos in range(block_vertex_counts.shape[0]):
        if changed_blocks[block_pos] == 0:
            continue
        vertex_start = int(block_world_offsets[block_pos])
        vertex_end = vertex_start + int(block_vertex_counts[block_pos])
        for index in range(vertex_start, vertex_end):
            base = index * 3
            out_vertices[base + 0] = (
                np.float32(world_vertices[index, 0]) * FIXED_16_16_SCALE_F32
            )
            out_vertices[base + 1] = (
                np.float32(world_vertices[index, 1]) * FIXED_16_16_SCALE_F32
            )
            out_vertices[base + 2] = (
                np.float32(world_vertices[index, 2]) * FIXED_16_16_SCALE_F32
            )


@njit(cache=True)
def fill_indexed_flat_indices_and_palettes(
    out_indices,
    out_palette,
    face_block_indices,
    face_modes,
    face_flags,
    face_counts,
    face_indices,
    face_normals,
    face_triangle_offsets,
    block_world_offsets,
    block_vertex_offsets,
    world_vertices,
    light,
    light_directional,
    fog_min,
    fog_distance,
    camera_position,
    camera_forward,
):
    for face_pos in range(face_counts.shape[0]):
        count = int(face_counts[face_pos])
        if count < 3:
            continue

        block_pos = int(face_block_indices[face_pos])
        world_base = int(block_world_offsets[block_pos])
        vertex_base = int(block_vertex_offsets[block_pos])
        flags = np.int64(face_flags[face_pos])
        if int(face_modes[face_pos]) == 0:
            palette_value = np.float32((flags >> np.int64(4)) & np.int64(0xFF))
        else:
            fog_offset = _mode1_fog_offset(
                flags,
                count,
                face_indices[face_pos],
                face_normals[face_pos],
                world_base,
                world_vertices,
                light,
                light_directional,
                fog_min,
                fog_distance,
                camera_position,
                camera_forward,
            )
            palette_value = np.float32(
                (((flags >> np.int64(8)) & np.int64(0xF)) << np.int64(4))
                | (fog_offset & np.int64(0xF))
            )

        anchor = vertex_base + int(face_indices[face_pos, 0])
        primitive_base = int(face_triangle_offsets[face_pos])
        for triangle_pos in range(count - 2):
            primitive_pos = primitive_base + triangle_pos
            index_pos = primitive_pos * 3
            out_indices[index_pos + 0] = np.uint16(anchor)
            out_indices[index_pos + 1] = np.uint16(
                vertex_base + int(face_indices[face_pos, triangle_pos + 1])
            )
            out_indices[index_pos + 2] = np.uint16(
                vertex_base + int(face_indices[face_pos, triangle_pos + 2])
            )
            out_palette[primitive_pos] = palette_value


@njit(cache=True)
def update_indexed_flat_palettes(
    out_palette,
    face_block_indices,
    face_modes,
    face_flags,
    face_counts,
    face_indices,
    face_normals,
    face_triangle_offsets,
    block_world_offsets,
    world_vertices,
    light,
    light_directional,
    fog_min,
    fog_distance,
    camera_position,
    camera_forward,
    changed_blocks,
):
    for face_pos in range(face_counts.shape[0]):
        block_pos = int(face_block_indices[face_pos])
        if changed_blocks[block_pos] == 0:
            continue
        count = int(face_counts[face_pos])
        if count < 3:
            continue

        flags = np.int64(face_flags[face_pos])
        if int(face_modes[face_pos]) == 0:
            palette_value = np.float32((flags >> np.int64(4)) & np.int64(0xFF))
        else:
            fog_offset = _mode1_fog_offset(
                flags,
                count,
                face_indices[face_pos],
                face_normals[face_pos],
                int(block_world_offsets[block_pos]),
                world_vertices,
                light,
                light_directional,
                fog_min,
                fog_distance,
                camera_position,
                camera_forward,
            )
            palette_value = np.float32(
                (((flags >> np.int64(8)) & np.int64(0xF)) << np.int64(4))
                | (fog_offset & np.int64(0xF))
            )

        primitive_base = int(face_triangle_offsets[face_pos])
        for triangle_pos in range(count - 2):
            out_palette[primitive_base + triangle_pos] = palette_value


@njit(cache=True)
def fill_mode4_vertices(
    out_vertices,
    face_block_indices,
    face_flags,
    face_counts,
    face_indices,
    face_normals,
    face_triangle_offsets,
    block_world_offsets,
    world_vertices,
    c_in_values,
    light,
    light_directional,
    fog_min,
):
    for face_pos in range(face_counts.shape[0]):
        count = int(face_counts[face_pos])
        if count < 3:
            continue

        block_pos = int(face_block_indices[face_pos])
        world_base = int(block_world_offsets[block_pos])
        first_world_index = world_base + int(face_indices[face_pos, 0])
        contribution = _mode4_contribution(
            np.int64(face_flags[face_pos]),
            first_world_index,
            face_normals[face_pos],
            world_vertices,
            light,
            light_directional,
            fog_min,
        )

        output_vertex = int(face_triangle_offsets[face_pos]) * 3
        anchor = int(face_indices[face_pos, 0])
        for triangle_pos in range(count - 2):
            for corner_pos in range(3):
                if corner_pos == 0:
                    local_index = anchor
                else:
                    local_index = int(face_indices[face_pos, triangle_pos + corner_pos])
                world_index = world_base + local_index
                out_base = (output_vertex + triangle_pos * 3 + corner_pos) * 5
                out_vertices[out_base + 0] = (
                    np.float32(world_vertices[world_index, 0])
                    * FIXED_16_16_SCALE_F32
                )
                out_vertices[out_base + 1] = (
                    np.float32(world_vertices[world_index, 1])
                    * FIXED_16_16_SCALE_F32
                )
                out_vertices[out_base + 2] = (
                    np.float32(world_vertices[world_index, 2])
                    * FIXED_16_16_SCALE_F32
                )
                out_vertices[out_base + 3] = np.float32(c_in_values[world_index])
                out_vertices[out_base + 4] = contribution


@njit(cache=True)
def update_mode4_vertices(
    out_vertices,
    face_block_indices,
    face_flags,
    face_counts,
    face_indices,
    face_normals,
    face_triangle_offsets,
    block_world_offsets,
    world_vertices,
    c_in_values,
    light,
    light_directional,
    fog_min,
    changed_blocks,
):
    for face_pos in range(face_counts.shape[0]):
        block_pos = int(face_block_indices[face_pos])
        if changed_blocks[block_pos] == 0:
            continue
        count = int(face_counts[face_pos])
        if count < 3:
            continue

        world_base = int(block_world_offsets[block_pos])
        first_world_index = world_base + int(face_indices[face_pos, 0])
        contribution = _mode4_contribution(
            np.int64(face_flags[face_pos]),
            first_world_index,
            face_normals[face_pos],
            world_vertices,
            light,
            light_directional,
            fog_min,
        )

        output_vertex = int(face_triangle_offsets[face_pos]) * 3
        anchor = int(face_indices[face_pos, 0])
        for triangle_pos in range(count - 2):
            for corner_pos in range(3):
                if corner_pos == 0:
                    local_index = anchor
                else:
                    local_index = int(face_indices[face_pos, triangle_pos + corner_pos])
                world_index = world_base + local_index
                out_base = (output_vertex + triangle_pos * 3 + corner_pos) * 5
                out_vertices[out_base + 0] = (
                    np.float32(world_vertices[world_index, 0])
                    * FIXED_16_16_SCALE_F32
                )
                out_vertices[out_base + 1] = (
                    np.float32(world_vertices[world_index, 1])
                    * FIXED_16_16_SCALE_F32
                )
                out_vertices[out_base + 2] = (
                    np.float32(world_vertices[world_index, 2])
                    * FIXED_16_16_SCALE_F32
                )
                out_vertices[out_base + 3] = np.float32(c_in_values[world_index])
                out_vertices[out_base + 4] = contribution


@njit(cache=True, inline="always")
def _round_shift_27_satellite_batch(value):
    half = np.int64(1 << 26)
    if value >= 0:
        return (value + half) >> np.int64(27)
    return -(((-value) + half) >> np.int64(27))


@njit(cache=True)
def fill_satellite_mode4_vertices_batched(
    out_vertices,
    face_block_indices,
    face_palette_families,
    face_counts,
    face_indices,
    face_triangle_offsets,
    block_world_offsets,
    world_vertices,
    camera_position,
    camera_forward,
    satellite_width_fixed,
    satellite_shade_bias,
    satellite_shade_divisor,
):
    divisor = np.int64(satellite_shade_divisor)
    if divisor == 0:
        divisor = np.int64(1)
    for face_pos in range(face_counts.shape[0]):
        count = int(face_counts[face_pos])
        if count < 3:
            continue

        block_pos = int(face_block_indices[face_pos])
        world_base = int(block_world_offsets[block_pos])
        palette_family = int(face_palette_families[face_pos]) & 0xF0
        output_vertex = int(face_triangle_offsets[face_pos]) * 3
        anchor = int(face_indices[face_pos, 0])
        for triangle_pos in range(count - 2):
            for corner_pos in range(3):
                if corner_pos == 0:
                    local_index = anchor
                else:
                    local_index = int(
                        face_indices[face_pos, triangle_pos + corner_pos]
                    )
                world_index = world_base + local_index
                delta_x = (
                    np.int64(world_vertices[world_index, 0])
                    - np.int64(camera_position[0])
                )
                delta_y = (
                    np.int64(world_vertices[world_index, 1])
                    - np.int64(camera_position[1])
                )
                delta_z = (
                    np.int64(world_vertices[world_index, 2])
                    - np.int64(camera_position[2])
                )
                depth = _round_shift_27_satellite_batch(
                    delta_x * np.int64(camera_forward[0])
                    + delta_y * np.int64(camera_forward[1])
                    + delta_z * np.int64(camera_forward[2])
                )
                numerator = (
                    np.int64(satellite_width_fixed)
                    - (depth >> np.int64(2))
                    - np.int64(satellite_shade_bias)
                )
                ratio_q16 = _trunc_div_i64(
                    numerator << np.int64(16),
                    divisor,
                )
                offset = (ratio_q16 * np.int64(16) + np.int64(0x8000)) >> np.int64(16)
                if offset < 0:
                    offset = np.int64(0)
                elif offset > 15:
                    offset = np.int64(15)
                palette_index = palette_family | int(offset)

                out_base = (output_vertex + triangle_pos * 3 + corner_pos) * 5
                out_vertices[out_base + 0] = (
                    np.float32(world_vertices[world_index, 0])
                    * FIXED_16_16_SCALE_F32
                )
                out_vertices[out_base + 1] = (
                    np.float32(world_vertices[world_index, 1])
                    * FIXED_16_16_SCALE_F32
                )
                out_vertices[out_base + 2] = (
                    np.float32(world_vertices[world_index, 2])
                    * FIXED_16_16_SCALE_F32
                )
                out_vertices[out_base + 3] = np.float32(palette_index)
                out_vertices[out_base + 4] = np.float32(-1.0)


@njit(cache=True)
def analyze_mode57_faces(
    face_block_indices,
    face_descs,
    face_counts,
    block_desc_present,
    desc_triangle_counts,
    face_primitive_offsets,
):
    for block_pos in range(block_desc_present.shape[0]):
        for desc_idx in range(block_desc_present.shape[1]):
            block_desc_present[block_pos, desc_idx] = np.uint8(0)
    for desc_idx in range(desc_triangle_counts.shape[0]):
        desc_triangle_counts[desc_idx] = np.int64(0)

    for face_pos in range(face_counts.shape[0]):
        desc_idx = int(face_descs[face_pos])
        if desc_idx < 0 or desc_idx >= desc_triangle_counts.shape[0]:
            face_primitive_offsets[face_pos] = np.int64(0)
            continue

        block_pos = int(face_block_indices[face_pos])
        if 0 <= block_pos < block_desc_present.shape[0]:
            block_desc_present[block_pos, desc_idx] = np.uint8(1)

        count = int(face_counts[face_pos])
        triangle_count = count - 2
        if triangle_count < 0:
            triangle_count = 0
        face_primitive_offsets[face_pos] = desc_triangle_counts[desc_idx]
        desc_triangle_counts[desc_idx] += np.int64(triangle_count)


@njit(cache=True)
def assign_mode57_vertex_offsets(
    block_vertex_counts,
    block_desc_present,
    block_desc_vertex_offsets,
    desc_vertex_counts,
):
    for desc_idx in range(desc_vertex_counts.shape[0]):
        desc_vertex_counts[desc_idx] = np.int64(0)

    for block_pos in range(block_desc_present.shape[0]):
        vertex_count = np.int64(block_vertex_counts[block_pos])
        for desc_idx in range(block_desc_present.shape[1]):
            if block_desc_present[block_pos, desc_idx] != 0:
                block_desc_vertex_offsets[block_pos, desc_idx] = (
                    desc_vertex_counts[desc_idx]
                )
                desc_vertex_counts[desc_idx] += vertex_count
            else:
                block_desc_vertex_offsets[block_pos, desc_idx] = np.int64(-1)


@njit(cache=True)
def build_mode57_desc_offsets(counts, offsets):
    running = np.int64(0)
    for desc_idx in range(counts.shape[0]):
        offsets[desc_idx] = running
        running += np.int64(counts[desc_idx])
    return running


@njit(cache=True)
def count_mode57_block_desc_entries(block_desc_present):
    count = 0
    for block_pos in range(block_desc_present.shape[0]):
        for desc_idx in range(block_desc_present.shape[1]):
            if block_desc_present[block_pos, desc_idx] != 0:
                count += 1
    return count


@njit(cache=True)
def fill_mode57_block_desc_entries(
    block_desc_present,
    entry_block_indices,
    entry_descs,
):
    write_pos = 0
    for block_pos in range(block_desc_present.shape[0]):
        for desc_idx in range(block_desc_present.shape[1]):
            if block_desc_present[block_pos, desc_idx] != 0:
                entry_block_indices[write_pos] = np.int32(block_pos)
                entry_descs[write_pos] = np.int16(desc_idx)
                write_pos += 1


@njit(cache=True)
def fill_mode57_grouped_vertices(
    out_vertices,
    all_world_vertices,
    all_control_uvs,
    block_world_offsets,
    block_vertex_counts,
    block_desc_vertex_offsets,
    desc_vertex_offsets,
    entry_block_indices,
    entry_descs,
):
    for entry_pos in range(entry_descs.shape[0]):
        block_pos = int(entry_block_indices[entry_pos])
        desc_idx = int(entry_descs[entry_pos])
        source_vertex = int(block_world_offsets[block_pos])
        vertex_count = int(block_vertex_counts[block_pos])
        output_vertex = int(desc_vertex_offsets[desc_idx]) + int(
            block_desc_vertex_offsets[block_pos, desc_idx]
        )
        output_base = output_vertex * 5

        for vertex_pos in range(vertex_count):
            source_pos = source_vertex + vertex_pos
            target_base = output_base + vertex_pos * 5
            out_vertices[target_base + 0] = (
                np.float32(all_world_vertices[source_pos, 0])
                * FIXED_16_16_SCALE_F32
            )
            out_vertices[target_base + 1] = (
                np.float32(all_world_vertices[source_pos, 1])
                * FIXED_16_16_SCALE_F32
            )
            out_vertices[target_base + 2] = (
                np.float32(all_world_vertices[source_pos, 2])
                * FIXED_16_16_SCALE_F32
            )
            out_vertices[target_base + 3] = np.float32(all_control_uvs[source_pos, 0])
            out_vertices[target_base + 4] = np.float32(all_control_uvs[source_pos, 1])


@njit(cache=True)
def update_mode57_grouped_vertices(
    out_vertices,
    all_world_vertices,
    all_control_uvs,
    block_world_offsets,
    block_vertex_counts,
    block_desc_vertex_offsets,
    desc_vertex_offsets,
    entry_block_indices,
    entry_descs,
    changed_blocks,
):
    for entry_pos in range(entry_descs.shape[0]):
        block_pos = int(entry_block_indices[entry_pos])
        if changed_blocks[block_pos] == 0:
            continue
        desc_idx = int(entry_descs[entry_pos])
        source_vertex = int(block_world_offsets[block_pos])
        vertex_count = int(block_vertex_counts[block_pos])
        output_vertex = int(desc_vertex_offsets[desc_idx]) + int(
            block_desc_vertex_offsets[block_pos, desc_idx]
        )
        output_base = output_vertex * 5

        for vertex_pos in range(vertex_count):
            source_pos = source_vertex + vertex_pos
            target_base = output_base + vertex_pos * 5
            out_vertices[target_base + 0] = (
                np.float32(all_world_vertices[source_pos, 0])
                * FIXED_16_16_SCALE_F32
            )
            out_vertices[target_base + 1] = (
                np.float32(all_world_vertices[source_pos, 1])
                * FIXED_16_16_SCALE_F32
            )
            out_vertices[target_base + 2] = (
                np.float32(all_world_vertices[source_pos, 2])
                * FIXED_16_16_SCALE_F32
            )
            out_vertices[target_base + 3] = np.float32(all_control_uvs[source_pos, 0])
            out_vertices[target_base + 4] = np.float32(all_control_uvs[source_pos, 1])


@njit(cache=True)
def fill_mode57_shared_vertices(
    out_vertices,
    all_world_vertices,
    all_control_uvs,
):
    for vertex_pos in range(all_world_vertices.shape[0]):
        target_base = vertex_pos * 5
        out_vertices[target_base + 0] = (
            np.float32(all_world_vertices[vertex_pos, 0])
            * FIXED_16_16_SCALE_F32
        )
        out_vertices[target_base + 1] = (
            np.float32(all_world_vertices[vertex_pos, 1])
            * FIXED_16_16_SCALE_F32
        )
        out_vertices[target_base + 2] = (
            np.float32(all_world_vertices[vertex_pos, 2])
            * FIXED_16_16_SCALE_F32
        )
        out_vertices[target_base + 3] = np.float32(all_control_uvs[vertex_pos, 0])
        out_vertices[target_base + 4] = np.float32(all_control_uvs[vertex_pos, 1])


@njit(cache=True)
def update_mode57_shared_vertices(
    out_vertices,
    all_world_vertices,
    all_control_uvs,
    block_world_offsets,
    block_vertex_counts,
    changed_blocks,
):
    for block_pos in range(changed_blocks.shape[0]):
        if changed_blocks[block_pos] == 0:
            continue
        source_vertex = int(block_world_offsets[block_pos])
        vertex_count = int(block_vertex_counts[block_pos])
        for vertex_offset in range(vertex_count):
            vertex_pos = source_vertex + vertex_offset
            target_base = vertex_pos * 5
            out_vertices[target_base + 0] = (
                np.float32(all_world_vertices[vertex_pos, 0])
                * FIXED_16_16_SCALE_F32
            )
            out_vertices[target_base + 1] = (
                np.float32(all_world_vertices[vertex_pos, 1])
                * FIXED_16_16_SCALE_F32
            )
            out_vertices[target_base + 2] = (
                np.float32(all_world_vertices[vertex_pos, 2])
                * FIXED_16_16_SCALE_F32
            )
            out_vertices[target_base + 3] = np.float32(
                all_control_uvs[vertex_pos, 0]
            )
            out_vertices[target_base + 4] = np.float32(
                all_control_uvs[vertex_pos, 1]
            )


@njit(cache=True)
def fill_mode57_grouped_indices_and_contribution(
    out_indices,
    out_contribution,
    face_block_indices,
    face_descs,
    face_counts,
    face_indices,
    face_normals,
    face_primitive_offsets,
    block_world_offsets,
    block_desc_vertex_offsets,
    desc_primitive_offsets,
    world_vertices,
    light,
    light_directional,
    fog_min,
):
    light_x = np.int64(light[0])
    light_y = np.int64(light[1])
    light_z = np.int64(light[2])

    for face_pos in range(face_counts.shape[0]):
        count = int(face_counts[face_pos])
        if count < 3:
            continue

        desc_idx = int(face_descs[face_pos])
        block_pos = int(face_block_indices[face_pos])
        vertex_offset = int(block_desc_vertex_offsets[block_pos, desc_idx])
        if vertex_offset < 0:
            continue

        first_index = int(face_indices[face_pos, 0])
        if light_directional != 0:
            lx = light_x
            ly = light_y
            lz = light_z
        else:
            first_world_index = int(block_world_offsets[block_pos]) + first_index
            lx = light_x - np.int64(world_vertices[first_world_index, 0])
            ly = light_y - np.int64(world_vertices[first_world_index, 1])
            lz = light_z - np.int64(world_vertices[first_world_index, 2])

        contribution = _mode57_contribution_from_light(
            lx,
            ly,
            lz,
            np.int64(face_normals[face_pos, 0]),
            np.int64(face_normals[face_pos, 1]),
            np.int64(face_normals[face_pos, 2]),
            fog_min,
        )

        anchor = vertex_offset + first_index
        primitive_base = int(desc_primitive_offsets[desc_idx]) + int(
            face_primitive_offsets[face_pos]
        )
        for triangle_pos in range(count - 2):
            primitive_pos = primitive_base + triangle_pos
            index_pos = primitive_pos * 3
            out_indices[index_pos + 0] = np.uint16(anchor)
            out_indices[index_pos + 1] = np.uint16(
                vertex_offset + int(face_indices[face_pos, triangle_pos + 1])
            )
            out_indices[index_pos + 2] = np.uint16(
                vertex_offset + int(face_indices[face_pos, triangle_pos + 2])
            )
            out_contribution[primitive_pos] = contribution


@njit(cache=True)
def fill_mode57_shared_indices_and_contribution(
    out_indices,
    out_contribution,
    face_block_indices,
    face_descs,
    face_counts,
    face_indices,
    face_normals,
    face_primitive_offsets,
    block_world_offsets,
    desc_primitive_offsets,
    world_vertices,
    light,
    light_directional,
    fog_min,
):
    light_x = np.int64(light[0])
    light_y = np.int64(light[1])
    light_z = np.int64(light[2])

    for face_pos in range(face_counts.shape[0]):
        count = int(face_counts[face_pos])
        if count < 3:
            continue

        desc_idx = int(face_descs[face_pos])
        if desc_idx < 0 or desc_idx >= desc_primitive_offsets.shape[0]:
            continue
        block_pos = int(face_block_indices[face_pos])
        vertex_offset = int(block_world_offsets[block_pos])
        first_index = int(face_indices[face_pos, 0])
        if light_directional != 0:
            lx = light_x
            ly = light_y
            lz = light_z
        else:
            first_world_index = vertex_offset + first_index
            lx = light_x - np.int64(world_vertices[first_world_index, 0])
            ly = light_y - np.int64(world_vertices[first_world_index, 1])
            lz = light_z - np.int64(world_vertices[first_world_index, 2])

        contribution = _mode57_contribution_from_light(
            lx,
            ly,
            lz,
            np.int64(face_normals[face_pos, 0]),
            np.int64(face_normals[face_pos, 1]),
            np.int64(face_normals[face_pos, 2]),
            fog_min,
        )

        anchor = vertex_offset + first_index
        primitive_base = int(desc_primitive_offsets[desc_idx]) + int(
            face_primitive_offsets[face_pos]
        )
        for triangle_pos in range(count - 2):
            primitive_pos = primitive_base + triangle_pos
            index_pos = primitive_pos * 3
            out_indices[index_pos + 0] = np.uint32(anchor)
            out_indices[index_pos + 1] = np.uint32(
                vertex_offset + int(face_indices[face_pos, triangle_pos + 1])
            )
            out_indices[index_pos + 2] = np.uint32(
                vertex_offset + int(face_indices[face_pos, triangle_pos + 2])
            )
            out_contribution[primitive_pos] = contribution


@njit(cache=True)
def update_mode57_grouped_contribution(
    out_contribution,
    face_block_indices,
    face_descs,
    face_counts,
    face_indices,
    face_normals,
    face_primitive_offsets,
    block_world_offsets,
    block_desc_vertex_offsets,
    desc_primitive_offsets,
    world_vertices,
    light,
    light_directional,
    fog_min,
    changed_blocks,
):
    light_x = np.int64(light[0])
    light_y = np.int64(light[1])
    light_z = np.int64(light[2])

    for face_pos in range(face_counts.shape[0]):
        block_pos = int(face_block_indices[face_pos])
        if changed_blocks[block_pos] == 0:
            continue
        count = int(face_counts[face_pos])
        if count < 3:
            continue

        desc_idx = int(face_descs[face_pos])
        vertex_offset = int(block_desc_vertex_offsets[block_pos, desc_idx])
        if vertex_offset < 0:
            continue

        first_index = int(face_indices[face_pos, 0])
        if light_directional != 0:
            lx = light_x
            ly = light_y
            lz = light_z
        else:
            first_world_index = int(block_world_offsets[block_pos]) + first_index
            lx = light_x - np.int64(world_vertices[first_world_index, 0])
            ly = light_y - np.int64(world_vertices[first_world_index, 1])
            lz = light_z - np.int64(world_vertices[first_world_index, 2])

        contribution = _mode57_contribution_from_light(
            lx,
            ly,
            lz,
            np.int64(face_normals[face_pos, 0]),
            np.int64(face_normals[face_pos, 1]),
            np.int64(face_normals[face_pos, 2]),
            fog_min,
        )
        primitive_base = int(desc_primitive_offsets[desc_idx]) + int(
            face_primitive_offsets[face_pos]
        )
        for triangle_pos in range(count - 2):
            out_contribution[primitive_base + triangle_pos] = contribution


@njit(cache=True)
def update_mode57_shared_contribution(
    out_contribution,
    face_block_indices,
    face_descs,
    face_counts,
    face_indices,
    face_normals,
    face_primitive_offsets,
    block_world_offsets,
    desc_primitive_offsets,
    world_vertices,
    light,
    light_directional,
    fog_min,
    changed_blocks,
):
    light_x = np.int64(light[0])
    light_y = np.int64(light[1])
    light_z = np.int64(light[2])

    for face_pos in range(face_counts.shape[0]):
        block_pos = int(face_block_indices[face_pos])
        if changed_blocks[block_pos] == 0:
            continue
        count = int(face_counts[face_pos])
        if count < 3:
            continue

        desc_idx = int(face_descs[face_pos])
        if desc_idx < 0 or desc_idx >= desc_primitive_offsets.shape[0]:
            continue
        first_index = int(face_indices[face_pos, 0])
        vertex_offset = int(block_world_offsets[block_pos])
        if light_directional != 0:
            lx = light_x
            ly = light_y
            lz = light_z
        else:
            first_world_index = vertex_offset + first_index
            lx = light_x - np.int64(world_vertices[first_world_index, 0])
            ly = light_y - np.int64(world_vertices[first_world_index, 1])
            lz = light_z - np.int64(world_vertices[first_world_index, 2])

        contribution = _mode57_contribution_from_light(
            lx,
            ly,
            lz,
            np.int64(face_normals[face_pos, 0]),
            np.int64(face_normals[face_pos, 1]),
            np.int64(face_normals[face_pos, 2]),
            fog_min,
        )
        primitive_base = int(desc_primitive_offsets[desc_idx]) + int(
            face_primitive_offsets[face_pos]
        )
        for triangle_pos in range(count - 2):
            out_contribution[primitive_base + triangle_pos] = contribution
