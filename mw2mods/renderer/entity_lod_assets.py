import struct

ADDR_RESOURCE_ACQUIRE = 0x0004B080
ADDR_RESOURCE_RELEASE = 0x0004B020
ADDR_RESOURCE_CONTEXT = 0x000AEA8C
ADDR_POLY_TYPE_POINTER = 0x000AE9E8

POLY_MAGIC = b"WTBO"
DUMMY_RESOURCE_ID = 0x07B1
WTBO_HEADER_SIZE = 0x20
WTBO_VERTEX_STRIDE = 0x10
MAX_POLY_VERTICES = 4096
MAX_POLY_FACES = 4096
MAX_POLY_FACE_VERTICES = 16
MAX_POLY_PAYLOAD_BYTES = 4 * 1024 * 1024

def wtbo_face_stride(vertex_count):
    raw_size = 4 + int(vertex_count) * 2
    return max(12, ((raw_size + 5) // 6) * 6)


def _validated_counts(header):
    if len(header) < WTBO_HEADER_SIZE or header[:4] != POLY_MAGIC:
        raise ValueError("POLY resource does not begin with WTBO")
    vertex_count = int(struct.unpack_from("<H", header, 0x18)[0])
    face_count = int(struct.unpack_from("<H", header, 0x1A)[0])
    if vertex_count < 3 or vertex_count > MAX_POLY_VERTICES:
        raise ValueError(f"invalid WTBO vertex count {vertex_count}")
    if face_count < 1 or face_count > MAX_POLY_FACES:
        raise ValueError(f"invalid WTBO face count {face_count}")
    return vertex_count, face_count


def measure_wtbo_payload(gamemem, payload_ptr):
    payload_ptr = int(payload_ptr)
    header = bytes(
        gamemem.read_runtime_bytes(payload_ptr, WTBO_HEADER_SIZE)
    )
    vertex_count, face_count = _validated_counts(header)
    offset = WTBO_HEADER_SIZE + vertex_count * WTBO_VERTEX_STRIDE
    for _ in range(face_count):
        face_header = bytes(
            gamemem.read_runtime_bytes(payload_ptr + offset, 4)
        )
        if len(face_header) != 4:
            raise ValueError("short WTBO face header")
        face_vertices = int(struct.unpack_from("<H", face_header, 2)[0])
        if face_vertices < 3 or face_vertices > MAX_POLY_FACE_VERTICES:
            raise ValueError(
                f"invalid WTBO face vertex count {face_vertices}"
            )
        indices = bytes(
            gamemem.read_runtime_bytes(
                payload_ptr + offset + 4,
                face_vertices * 2,
            )
        )
        if len(indices) != face_vertices * 2 or any(
            index >= vertex_count
            for index in struct.unpack(f"<{face_vertices}H", indices)
        ):
            raise ValueError("invalid WTBO face indices")
        offset += wtbo_face_stride(face_vertices)
        if offset > MAX_POLY_PAYLOAD_BYTES:
            raise ValueError("WTBO payload exceeds safety limit")
    return offset


def resolve_poly_resource_context(gamemem):
    resource_context = int(gamemem.read_reloc_u32(ADDR_RESOURCE_CONTEXT))
    pointer = int(gamemem.read_reloc_u32(ADDR_POLY_TYPE_POINTER))
    if pointer:
        type_name = bytes(gamemem.read_runtime_bytes(pointer, 5))
        if type_name[:4] == b"POLY":
            return resource_context, pointer
    raise LookupError(
        "POLY resource lookup unavailable "
        f"context=0x{resource_context:08X} "
        f"type=0x{pointer:08X}"
    )


def acquire_poly_asset(
    gamemem,
    resource_id,
    resource_context,
    poly_type_pointer,
):
    resource_id = int(resource_id)
    if resource_id == DUMMY_RESOURCE_ID:
        return None
    payload_ptr = int(
        gamemem.call_reloc_u32(
            ADDR_RESOURCE_ACQUIRE,
            eax=int(resource_context),
            ebx=int(poly_type_pointer),
            ecx=0,
            edx=resource_id,
        )
    )
    if payload_ptr == 0:
        return None
    try:
        payload_size = measure_wtbo_payload(gamemem, payload_ptr)
        blob = bytes(gamemem.read_runtime_bytes(payload_ptr, payload_size))
        if len(blob) != payload_size:
            raise ValueError("short WTBO payload read")
        return blob
    finally:
        gamemem.call_reloc_u32(
            ADDR_RESOURCE_RELEASE,
            eax=resource_id,
            ebx=0,
            ecx=0,
            edx=int(poly_type_pointer),
        )


def process_entity_lod_asset_safe_point(
    gamemem,
    resource_store,
):
    if resource_store.state == "READY":
        return
    if resource_store.state != "QUEUED":
        return

    try:
        resource_context, poly_type_pointer = resolve_poly_resource_context(
            gamemem
        )
        resource_store.state = "LOADING"
        resource_store.error = ""
        for resource_id in resource_store.resource_ids:
            resource_id = int(resource_id)
            try:
                asset = acquire_poly_asset(
                    gamemem,
                    resource_id,
                    resource_context,
                    poly_type_pointer,
                )
                if asset is not None:
                    resource_store.assets[resource_id] = asset
            except Exception as exc:
                resource_store.error = f"{type(exc).__name__}: {exc}"

        resource_store.state = "READY"
    except LookupError as exc:
        resource_store.state = "WAITING"
        resource_store.error = str(exc)
    except Exception as exc:
        resource_store.state = "FAILED"
        resource_store.error = f"{type(exc).__name__}: {exc}"
