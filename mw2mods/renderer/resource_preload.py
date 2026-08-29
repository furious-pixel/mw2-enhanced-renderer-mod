import struct

from .texture import (
    ADDR_TEXTURE_CELL_TABLE,
    ADDR_TEXTURE_DESCRIPTOR_TABLE,
    MAX_TEXTURE_BYTES,
    MAX_TEXTURE_DIMENSION,
    TEXTURE_CELL_PAGE_STRIDE,
    TEXTURE_CELL_SUB_ENTRY_COUNT,
    TEXTURE_CELL_SUB_ENTRY_STRIDE,
    TEXTURE_DESCRIPTOR_COUNT,
    TEXTURE_DESCRIPTOR_STRIDE,
    texture_asset_from_pixels,
)


ADDR_RESOURCE_ACQUIRE = 0x0004B080
ADDR_RESOURCE_RELEASE = 0x0004B020
ADDR_RESOURCE_ACQUIRE_CONTEXT = 0x000AEA8C
ADDR_RESOURCE_TYPE_CONTEXT = 0x000AE9C4
MISSION_TEXTURE_MIN_DESCRIPTOR_COUNT = 8
PRELOAD_RESOURCES_PER_SAFE_POINT = 16
PRELOAD_RETRY_LIMIT = 3


class ResourcePreloadError(RuntimeError):
    pass


class ResourcePreloadRetry(ResourcePreloadError):
    pass


def preload_mission_texture_assets(gamemem, resource_store):
    if resource_store.texture_preload_state in (
        "READY",
        "FAILED",
        "UNAVAILABLE",
        "DISABLED",
    ):
        return False
    if (
        not callable(getattr(gamemem, "request_safe_point", None))
        or not callable(getattr(gamemem, "call_reloc_u32", None))
    ):
        resource_store.texture_preload_state = "UNAVAILABLE"
        return False
    if not resource_store.texture_tables_initialized:
        resource_store.texture_preload_state = "WAITING"
        return False

    if not resource_store.texture_preload_requests:
        try:
            descriptors = bytes(
                gamemem.read_reloc_bytes(
                    ADDR_TEXTURE_DESCRIPTOR_TABLE,
                    TEXTURE_DESCRIPTOR_COUNT * TEXTURE_DESCRIPTOR_STRIDE,
                )
            )
            pages, page_repeats = _descriptor_pages(descriptors)
            if len(pages) < MISSION_TEXTURE_MIN_DESCRIPTOR_COUNT:
                resource_store.texture_preload_state = "WAITING"
                return False

            cells = bytes(
                gamemem.read_reloc_bytes(
                    ADDR_TEXTURE_CELL_TABLE,
                    512 * TEXTURE_CELL_PAGE_STRIDE,
                )
            )
            requests = _mission_texture_requests(cells, pages, page_repeats)
            if not requests:
                resource_store.texture_preload_state = "WAITING"
                return False

            requests_by_resource = {}
            for page, sub_entry, resource_id, repeats in requests:
                requests_by_resource.setdefault(resource_id, []).append(
                    (page, sub_entry, repeats)
                )

            resource_store.texture_preload_requests = tuple(
                (resource_id, tuple(requests_by_resource[resource_id]))
                for resource_id in sorted(requests_by_resource)
            )
            resource_store.texture_preload_cursor = 0
            resource_store.texture_preload_state = "QUEUED"
            resource_store.texture_preload_error = ""
        except Exception as exc:
            resource_store.texture_preload_state = "FAILED"
            resource_store.texture_preload_error = type(exc).__name__
            return False

    try:
        gamemem.request_safe_point()
    except Exception as exc:
        resource_store.texture_preload_state = "FAILED"
        resource_store.texture_preload_error = type(exc).__name__
        return False
    return True


def process_texture_preload_safe_point(
    gamemem,
    resource_store,
    request_next_safe_point=True,
    resource_limit=PRELOAD_RESOURCES_PER_SAFE_POINT,
):
    """Process one preload attempt, optionally limiting its resource count."""
    if resource_store.texture_preload_state not in ("QUEUED", "LOADING"):
        return False

    if resource_limit is not None:
        resource_limit = max(1, int(resource_limit))

    resource_store.texture_preload_state = "LOADING"
    try:
        acquire_context = int(
            gamemem.read_reloc_u32(ADDR_RESOURCE_ACQUIRE_CONTEXT)
        )
        resource_type_context = int(
            gamemem.read_reloc_u32(ADDR_RESOURCE_TYPE_CONTEXT)
        )
        processed = 0
        requests = resource_store.texture_preload_requests
        while (
            resource_store.texture_preload_cursor < len(requests)
            and (resource_limit is None or processed < resource_limit)
        ):
            cursor = resource_store.texture_preload_cursor
            resource_id, bindings = requests[cursor]
            payload = _resident_resource_payload(
                resource_store,
                resource_id,
                bindings,
            )
            if payload is None:
                payload = _acquire_resource_payload(
                    gamemem,
                    resource_store,
                    resource_id,
                    acquire_context,
                    resource_type_context,
                )
            new_bindings = _store_resource_bindings(
                resource_store,
                resource_id,
                bindings,
                payload,
            )
            resource_store.texture_preload_bindings.extend(new_bindings)
            resource_store.texture_preload_cursor += 1
            resource_store.texture_preload_retry_counts.pop(resource_id, None)
            processed += 1

        if resource_store.texture_preload_cursor >= len(requests):
            resource_store.texture_preload_state = "READY"
            resource_store.texture_preload_error = ""
            return True

        if request_next_safe_point:
            gamemem.request_safe_point()
        return False
    except ResourcePreloadRetry as exc:
        cursor = resource_store.texture_preload_cursor
        requests = resource_store.texture_preload_requests
        resource_id = requests[cursor][0] if cursor < len(requests) else -1
        retry_count = resource_store.texture_preload_retry_counts.get(resource_id, 0) + 1
        resource_store.texture_preload_retry_counts[resource_id] = retry_count
        resource_store.texture_preload_error = str(exc)
        if retry_count < PRELOAD_RETRY_LIMIT:
            if request_next_safe_point:
                try:
                    gamemem.request_safe_point()
                except Exception as request_error:
                    resource_store.texture_preload_state = "FAILED"
                    resource_store.texture_preload_error = type(request_error).__name__
            return False
        resource_store.texture_preload_state = "FAILED"
        return False
    except ResourcePreloadError as exc:
        resource_store.texture_preload_state = "FAILED"
        resource_store.texture_preload_error = str(exc)
        return False
    except Exception as exc:
        resource_store.texture_preload_state = "FAILED"
        resource_store.texture_preload_error = type(exc).__name__
        return False


def drain_texture_preload_safe_point(gamemem, resource_store):
    """Finish all queued CPU preload work inside the current safe point."""
    while resource_store.texture_preload_state in ("QUEUED", "LOADING"):
        cursor_before = resource_store.texture_preload_cursor
        retry_counts_before = dict(resource_store.texture_preload_retry_counts)
        if process_texture_preload_safe_point(
            gamemem,
            resource_store,
            request_next_safe_point=False,
            resource_limit=None,
        ):
            return True
        if resource_store.texture_preload_state not in ("QUEUED", "LOADING"):
            return False
        if (
            resource_store.texture_preload_cursor == cursor_before
            and resource_store.texture_preload_retry_counts == retry_counts_before
        ):
            resource_store.texture_preload_state = "FAILED"
            resource_store.texture_preload_error = "drain_stalled"
            return False
    return resource_store.texture_preload_state == "READY"


def attach_texture_preload(geometry, resource_store):
    if resource_store.texture_preload_state == "READY":
        geometry["texture_preload_generation"] = (
            int(resource_store.mission_generation),
            int(resource_store.texture_generation),
        )
        geometry["texture_preloads"] = resource_store.texture_preload_bindings
    else:
        geometry["texture_preload_generation"] = None
        geometry["texture_preloads"] = ()
    geometry["hud_texture_preloads"] = resource_store.hud_texture_preloads


def _descriptor_pages(descriptors):
    pages = set()
    page_repeats = {}
    for descriptor_index in range(TEXTURE_DESCRIPTOR_COUNT):
        offset = descriptor_index * TEXTURE_DESCRIPTOR_STRIDE
        page = struct.unpack_from("<h", descriptors, offset)[0]
        if page < 0 or page >= 512:
            continue
        page = int(page)
        pages.add(page)
        page_repeats.setdefault(page, set()).add(descriptor_index >= 0x100)
    return pages, page_repeats


def _mission_texture_requests(cells, pages, page_repeats):
    requests = []
    for page in sorted(pages):
        page_offset = int(page) * TEXTURE_CELL_PAGE_STRIDE
        repeats = tuple(sorted(page_repeats.get(page, (False,))))
        for sub_entry in range(TEXTURE_CELL_SUB_ENTRY_COUNT):
            offset = page_offset + sub_entry * TEXTURE_CELL_SUB_ENTRY_STRIDE
            resource_id = struct.unpack_from("<h", cells, offset)[0]
            if resource_id < 1:
                continue
            requests.append(
                (int(page), int(sub_entry), int(resource_id), repeats)
            )
    return tuple(requests)


def _resident_resource_payload(resource_store, resource_id, bindings):
    payload = None
    for page, sub_entry, _repeats in bindings:
        asset = resource_store.texture_assets.get(
            (int(page), int(sub_entry), int(resource_id))
        )
        if asset is None:
            continue
        candidate = (
            int(asset.get("width", 0)),
            int(asset.get("height", 0)),
            bytes(asset.get("pixels", b"")),
        )
        _validate_payload(*candidate)
        if payload is not None and payload != candidate:
            raise ResourcePreloadError("resident resource payloads differ")
        payload = candidate
    return payload


def _acquire_resource_payload(
    gamemem,
    resource_store,
    resource_id,
    acquire_context,
    resource_type_context,
):
    try:
        payload_ptr = int(
            gamemem.call_reloc_u32(
                ADDR_RESOURCE_ACQUIRE,
                eax=acquire_context,
                ebx=resource_type_context,
                ecx=0,
                edx=int(resource_id) & 0xFFFFFFFF,
            )
        )
    except Exception as exc:
        raise ResourcePreloadRetry("acquire_call_failed") from exc
    if payload_ptr == 0:
        raise ResourcePreloadRetry("acquire_returned_null")

    try:
        header = bytes(gamemem.read_runtime_bytes(payload_ptr, 4))
        if len(header) != 4:
            raise ResourcePreloadError("resource header read was short")
        width, height = struct.unpack_from("<HH", header, 0)
        pixel_count = _validate_dimensions(width, height)
        pixels = bytes(gamemem.read_runtime_bytes(payload_ptr + 4, pixel_count))
        if len(pixels) != pixel_count:
            raise ResourcePreloadError("resource pixel read was short")
        return int(width), int(height), pixels
    finally:
        try:
            gamemem.call_reloc_u32(
                ADDR_RESOURCE_RELEASE,
                eax=int(resource_id) & 0xFFFFFFFF,
                ebx=0,
                ecx=0,
                edx=resource_type_context,
            )
        except Exception as exc:
            raise ResourcePreloadError("release_call_failed") from exc


def _store_resource_bindings(
    resource_store,
    resource_id,
    bindings,
    payload,
):
    width, height, pixels = payload
    _validate_payload(width, height, pixels)
    assets = []
    updates = []
    for page, sub_entry, repeats in bindings:
        asset_key = (int(page), int(sub_entry), int(resource_id))
        asset = resource_store.texture_assets.get(asset_key)
        if asset is not None:
            candidate = (
                int(asset.get("width", 0)),
                int(asset.get("height", 0)),
                bytes(asset.get("pixels", b"")),
            )
            _validate_payload(*candidate)
            if candidate != payload:
                raise ResourcePreloadError("resident texture asset differs")
        else:
            asset = texture_asset_from_pixels(
                page,
                sub_entry,
                resource_id,
                width,
                height,
                pixels,
            )
            updates.append((asset_key, asset))
        assets.append((asset, repeats))

    for asset_key, asset in updates:
        resource_store.texture_assets[asset_key] = asset

    preloads = []
    for asset, repeats in assets:
        for repeat in repeats:
            preloads.append((asset, bool(repeat)))
    return preloads


def _validate_payload(width, height, pixels):
    pixel_count = _validate_dimensions(width, height)
    if len(pixels) != pixel_count:
        raise ResourcePreloadError("invalid CEL payload")


def _validate_dimensions(width, height):
    width = int(width)
    height = int(height)
    pixel_count = width * height
    if (
        width <= 0
        or height <= 0
        or width > MAX_TEXTURE_DIMENSION
        or height > MAX_TEXTURE_DIMENSION
        or pixel_count <= 0
        or pixel_count > MAX_TEXTURE_BYTES
    ):
        raise ResourcePreloadError("invalid CEL payload")
    return pixel_count
