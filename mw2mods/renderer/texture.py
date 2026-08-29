import struct

import numpy as np

from .texture_catalog import (
    cel_name_for_resource_id,
    enhanced_texture_role,
    is_enhanced_imaging_effect_cel,
)


ADDR_TEXTURE_DESCRIPTOR_TABLE = 0x0013DFE0
ADDR_TEXTURE_CELL_TABLE = 0x0013FBE0
ADDR_TEXTURE_REMAP_TABLE_PTR = 0x000A6DCC
ADDR_TEXTURE_TABLE_INIT_STATE = 0x000A6DB8
TEXTURE_REMAP_TABLE_COUNT = 16
TEXTURE_REMAP_TABLE_SIZE = 256
TEXTURE_DESCRIPTOR_STRIDE = 14
TEXTURE_DESCRIPTOR_COUNT = 512
TEXTURE_CELL_PAGE_STRIDE = 256
TEXTURE_CELL_SUB_ENTRY_STRIDE = 8
TEXTURE_CELL_SUB_ENTRY_COUNT = 32
CEL_HEADER_SIZE = 0x14
CEL_CELL_POINTER_OFFSET = 0x10
CEL_MAGIC = b"CEL\0"
MAX_TEXTURE_DIMENSION = 1024
MAX_TEXTURE_BYTES = 1024 * 1024
TEXTURE_REMAP_STATE_UNRESOLVED = "unresolved"
TEXTURE_REMAP_STATE_READY = "ready"
TEXTURE_REMAP_STATE_INVALID = "invalid"


def observe_texture_table_state(gamemem, resource_store):
    try:
        first_cell = gamemem.read_reloc_bytes(ADDR_TEXTURE_CELL_TABLE, 2)
        cell_sentinel = _i16(first_cell, 0)
        if resource_store.texture_tables_initialized and cell_sentinel != 0:
            descriptor_sentinel = resource_store.texture_descriptor_sentinel
            init_state = resource_store.texture_init_state
            initialized = True
        else:
            descriptor_active = gamemem.read_reloc_bytes(
                ADDR_TEXTURE_DESCRIPTOR_TABLE + 8,
                2,
            )
            descriptor_sentinel = _i16(descriptor_active, 0)
            init_state = int(gamemem.read_reloc_i32(ADDR_TEXTURE_TABLE_INIT_STATE))
            # Immediately after initialization the first cell and descriptor
            # retain -1/-2. Mission script population can then replace either
            # sentinel with a live binding. Teardown instead clears the cell
            # and restores the descriptor sentinel, while pre-init storage is
            # all zero with the lazy-init global still at -2.
            initialized = init_state != -2 and (
                cell_sentinel != 0
                or descriptor_sentinel not in (0, -2)
            )
        resource_store.observe_texture_tables(
            int(getattr(gamemem, "delta")),
            initialized,
            descriptor_sentinel,
            init_state,
        )
        return True
    except Exception:
        # A transient failed sample must not invalidate renderer-owned copies.
        # The next post-scene hook retries the capability observation.
        return False


def _u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def _i16(data, offset):
    return struct.unpack_from("<h", data, offset)[0]


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _read_indexed_textures(
    gamemem,
    desc_indices,
    palette_rgb,
    texture_cache,
    stats,
    *,
    enhancement_options,
    enhanced_imaging_effect_descriptors=None,
):
    textures = {}
    page_cache = {}
    texture_remap_tables = None
    remap_context = None
    if any(int(desc_idx) >= 0x100 for desc_idx in desc_indices):
        texture_remap_tables = _read_texture_remap_tables(gamemem, stats)
        remap_context = _texture_remap_context(palette_rgb, texture_remap_tables)

    for desc_idx in sorted(desc_indices):
        desc_idx = int(desc_idx)
        try:
            descriptor = _read_texture_descriptor(gamemem, desc_idx)
            if descriptor is None:
                continue
            page = int(descriptor["page"])
            if enhanced_imaging_effect_descriptors is not None:
                if page not in page_cache:
                    page_cache[page] = _read_texture_page_entries(
                        gamemem,
                        page,
                        texture_cache,
                        stats,
                    )
                page_entries = page_cache[page]
                if page_entries and all(
                    is_enhanced_imaging_effect_cel(entry["resource_id"])
                    for entry in page_entries.values()
                ):
                    enhanced_imaging_effect_descriptors.add(desc_idx)
            # `texture_descriptor_lookup` suppresses texturing at draw time when a
            # descriptor is completed/stopped or explicitly disabled. We mirror that
            # here so the GPU never sees stale/final-frame billboards cycling back.
            if int(descriptor.get("state", 0)) == 0:
                continue
            if int(descriptor.get("active_flag", 0)) < 0:
                continue

            if page not in page_cache:
                page_cache[page] = _read_texture_page_entries(
                    gamemem,
                    page,
                    texture_cache,
                    stats,
                )
            page_entries = page_cache[page]

            if desc_idx < 0x100:
                texture, texture_class = _read_mode3_texture_for_descriptor(
                    gamemem,
                    descriptor,
                    page_entries,
                    texture_cache,
                    stats,
                )
            else:
                texture, texture_class = _read_mode5_texture_for_descriptor(
                    gamemem,
                    descriptor,
                    page_entries,
                    remap_context,
                    texture_cache,
                    stats,
                    enhancement_options,
                )
            if texture is None:
                continue

            texture = dict(texture)
            texture["desc_idx"] = int(desc_idx)
            texture["page"] = int(page)
            texture["animation_class"] = texture_class
            textures[desc_idx] = texture
        except Exception as exc:
            stats["last_texture_error"] = str(exc)

    return textures


def _apply_known_texture_enhancement(texture, options, atlas_cache=None):
    role = enhanced_texture_role(
        texture.get("resource_id", -1),
        texture.get("cel_name"),
    )
    enabled = (
        role == "camo"
        and bool(options["enhanced_mech_textures"])
    ) or (
        role == "cruise"
        and bool(options["enhanced_dropship_textures"])
    )
    if not enabled:
        return texture

    width = int(texture.get("width") or 0)
    height = int(texture.get("height") or 0)
    pixels = texture.get("pixels")
    if not pixels or width <= 0 or height <= 0:
        return texture

    atlas_pixels = (
        None
        if atlas_cache is None
        else atlas_cache.get("mirrored_atlas_pixels")
    )
    if atlas_pixels is None:
        source = np.frombuffer(pixels, dtype=np.uint8)
        if source.size != width * height:
            return texture
        source = source.reshape((height, width))
        atlas = np.empty((height * 2, width * 2), dtype=np.uint8)
        atlas[:height, :width] = source
        atlas[:height, width:] = source[:, ::-1]
        atlas[height:, :width] = source[::-1, :]
        atlas[height:, width:] = source[::-1, ::-1]
        atlas_pixels = atlas.tobytes()
        if atlas_cache is not None:
            atlas_cache["mirrored_atlas_pixels"] = atlas_pixels

    enhanced = dict(texture)
    enhanced["pixels"] = atlas_pixels
    enhanced["source_width"] = width
    enhanced["source_height"] = height
    enhanced["width"] = width * 2
    enhanced["height"] = height * 2
    enhanced["repeat"] = True
    enhanced["enhancement_role"] = role
    enhanced["enhancement_role_id"] = 1 if role == "camo" else 2
    scale_option = (
        "enhanced_mech_texture_uv_scale"
        if role == "camo"
        else "enhanced_dropship_texture_uv_scale"
    )
    enhanced["enhanced_uv_scale"] = float(options[scale_option])
    enhanced["signature"] = (
        "mirrored_atlas",
        role,
        texture.get("signature"),
    )
    return enhanced


def _read_texture_descriptor(gamemem, desc_idx):
    desc_idx = int(desc_idx)
    if desc_idx < 0 or desc_idx >= TEXTURE_DESCRIPTOR_COUNT:
        return None

    data = gamemem.read_reloc_bytes(
        ADDR_TEXTURE_DESCRIPTOR_TABLE + desc_idx * TEXTURE_DESCRIPTOR_STRIDE,
        TEXTURE_DESCRIPTOR_STRIDE,
    )
    page = _i16(data, 0)
    if page < 0 or page >= 512:
        return None
    selector = int(_u16(data, 2))
    animation_interval = int(_u16(data, 4))
    state = int(_u16(data, 6))
    active_flag = int(_i16(data, 8))
    last_update_tick = int(_u16(data, 10))
    unknown_0C = int(_u16(data, 12))
    return {
        "desc_idx": int(desc_idx),
        "page": int(page),
        "selector": selector,
        "animation_interval": animation_interval,
        "state": state,
        "active_flag": active_flag,
        "last_update_tick": last_update_tick,
        "unknown_0C": unknown_0C,
        "bytes": bytes(data),
    }


def _read_mode3_texture_for_descriptor(
    gamemem,
    descriptor,
    page_entries,
    texture_cache,
    stats,
):
    page_entry, selection_mode = _select_texture_entry_by_selector(descriptor, page_entries)
    if page_entry is None:
        return None, "missing"

    texture, _asset = _read_cel_texture_from_page_entry(
        gamemem,
        descriptor,
        page_entry,
        texture_cache,
        stats,
    )
    if texture is None:
        return None, "missing"

    mode3_class = _classify_mode3_descriptor(descriptor, page_entries)
    texture["selection_mode"] = selection_mode
    texture["mode3_class"] = mode3_class
    return texture, mode3_class


def _read_mode5_texture_for_descriptor(
    gamemem,
    descriptor,
    page_entries,
    remap_context,
    texture_cache,
    stats,
    enhancement_options,
):
    page_entry, selection_mode = _select_texture_entry_by_selector(descriptor, page_entries)
    if page_entry is None:
        return None, "missing"

    texture, asset = _read_cel_texture_from_page_entry(
        gamemem,
        descriptor,
        page_entry,
        texture_cache,
        stats,
    )
    if texture is None:
        return None, "missing"

    texture["selection_mode"] = selection_mode
    texture["texture_kind"] = "texmap"
    texture["repeat"] = True
    remap_info = _cached_texture_remap_classification(
        asset,
        remap_context,
    )
    texture.update(remap_info)
    texture = _apply_known_texture_enhancement(
        texture,
        enhancement_options,
        asset,
    )
    mode57_class = "single_frame"
    if int(descriptor.get("animation_interval", 0)) != 0 or int(descriptor.get("selector", 0)) != 0:
        mode57_class = "animated"
    elif len(page_entries) > 1:
        mode57_class = "multi_frame_static"
    return texture, mode57_class


def _read_texture_remap_tables(gamemem, stats):
    try:
        table_runtime = int(gamemem.read_reloc_u32(ADDR_TEXTURE_REMAP_TABLE_PTR))
    except Exception as exc:
        stats["last_texture_error"] = str(exc)
        return None
    if table_runtime <= 0:
        return None
    table_data = gamemem.read_runtime_bytes(
        table_runtime,
        TEXTURE_REMAP_TABLE_COUNT * TEXTURE_REMAP_TABLE_SIZE,
    )
    if len(table_data) < TEXTURE_REMAP_TABLE_COUNT * TEXTURE_REMAP_TABLE_SIZE:
        return None
    return tuple(
        bytes(table_data[
            table_index * TEXTURE_REMAP_TABLE_SIZE:
            (table_index + 1) * TEXTURE_REMAP_TABLE_SIZE
        ])
        for table_index in range(TEXTURE_REMAP_TABLE_COUNT)
    )


def _read_texture_page_entries(gamemem, page, texture_cache, stats):
    cell_data = gamemem.read_reloc_bytes(
        ADDR_TEXTURE_CELL_TABLE + int(page) * TEXTURE_CELL_PAGE_STRIDE,
        TEXTURE_CELL_PAGE_STRIDE,
    )
    entries = {}

    for sub_entry in range(TEXTURE_CELL_SUB_ENTRY_COUNT):
        cell_offset = sub_entry * TEXTURE_CELL_SUB_ENTRY_STRIDE
        resource_id = int(_i16(cell_data, cell_offset))
        if resource_id < 1:
            continue
        data_ptr = _u32(cell_data, cell_offset + 4)
        asset_key = (int(page), int(sub_entry), resource_id)
        asset = None if texture_cache is None else texture_cache.get(asset_key)
        if asset is not None:
            entries[int(sub_entry)] = _texture_page_entry_from_asset(asset)
            continue
        if data_ptr == 0:
            continue

        entry = _probe_cel_texture_from_cell_pointer(
            gamemem,
            page,
            sub_entry,
            data_ptr,
            stats,
        )
        if entry is None:
            continue
        entry["resource_id"] = resource_id
        if texture_cache is not None:
            asset = _capture_resident_texture_asset(
                gamemem,
                page,
                sub_entry,
                resource_id,
                entry,
                stats,
            )
            if asset is None:
                continue
            texture_cache[asset_key] = asset
            entry = _texture_page_entry_from_asset(asset)
        entries[int(sub_entry)] = entry

    return entries


def _capture_resident_texture_asset(
    gamemem,
    page,
    sub_entry,
    resource_id,
    page_entry,
    stats,
):
    pixel_count = int(page_entry["width"]) * int(page_entry["height"])
    try:
        pixels = gamemem.read_runtime_bytes(
            int(page_entry["header_addr"]) + CEL_HEADER_SIZE,
            pixel_count,
        )
    except Exception as exc:
        stats["last_texture_error"] = str(exc)
        return None
    if len(pixels) != pixel_count:
        stats["last_texture_error"] = (
            f"short CEL pixel read {len(pixels)}/{pixel_count} "
            f"for page={int(page)} sub={int(sub_entry)}"
        )
        return None

    width = int(page_entry["width"])
    height = int(page_entry["height"])
    return texture_asset_from_pixels(
        page,
        sub_entry,
        resource_id,
        width,
        height,
        pixels,
    )


def texture_asset_from_pixels(
    page,
    sub_entry,
    resource_id,
    width,
    height,
    pixels,
):
    pixels = bytes(pixels)
    histogram, included_sample_count = _texture_pixel_histogram(pixels)
    width = int(width)
    height = int(height)
    cel_name = cel_name_for_resource_id(resource_id)
    return {
        "page": int(page),
        "sub_entry": int(sub_entry),
        "resource_id": int(resource_id),
        "cel_name": cel_name,
        "width": width,
        "height": height,
        "pixels": pixels,
        "raw_index_histogram": histogram,
        "included_sample_count": included_sample_count,
        "classification_signature": None,
        "classification": None,
        "signature": (
            "CEL",
            int(resource_id),
            width,
            height,
            pixels,
        ),
    }


def _texture_page_entry_from_asset(asset):
    return {
        "page": int(asset["page"]),
        "sub_entry": int(asset["sub_entry"]),
        "resource_id": int(asset["resource_id"]),
        "cel_name": str(asset.get("cel_name") or ""),
        "width": int(asset["width"]),
        "height": int(asset["height"]),
        "asset": asset,
    }


def _select_texture_entry_by_selector(descriptor, page_entries):
    if not page_entries:
        return None, "missing"

    selector = int(descriptor.get("selector", 0))
    if selector in page_entries:
        return page_entries[selector], "exact"

    valid_sub_entries = sorted(int(sub_entry) for sub_entry in page_entries.keys())
    selected_sub_entry = None
    for sub_entry in valid_sub_entries:
        if sub_entry > selector:
            break
        selected_sub_entry = sub_entry

    if selected_sub_entry is not None:
        return page_entries[selected_sub_entry], "clamp_down"
    return page_entries[valid_sub_entries[0]], "clamp_up"


def _classify_mode3_descriptor(descriptor, page_entries):
    if int(descriptor.get("animation_interval", 0)) != 0 or int(descriptor.get("selector", 0)) != 0:
        return "animated"
    if len(page_entries) > 1:
        return "multi_frame_static"
    return "single_frame"


def _default_remap_info(state=TEXTURE_REMAP_STATE_UNRESOLVED):
    return {
        "remap_state": state,
        "remap_kind": "identity",
        "remap_kind_id": 0,
        "dark_ratio": (0.0, 0.0, 0.0),
        "fog_color": (0.0, 0.0, 0.0),
        "s8_ratio": (0.0, 0.0, 0.0),
    }


def _texture_remap_context(palette_rgb, remap_tables):
    try:
        if len(palette_rgb) < 256 * 3 or len(remap_tables) < 16:
            return None
        palette_bytes = bytes(palette_rgb[:256 * 3])
        table_bytes = tuple(bytes(remap_tables[index][:256]) for index in range(16))
    except (TypeError, ValueError, IndexError):
        return None
    if any(len(table) < 256 for table in table_bytes):
        return None
    return {
        "palette_rgb": palette_bytes,
        "remap_tables": table_bytes,
        "signature": (palette_bytes, table_bytes),
    }


def _cached_texture_remap_classification(asset, context):
    if context is None:
        return _default_remap_info(TEXTURE_REMAP_STATE_UNRESOLVED)

    signature = context["signature"]
    if (
        asset.get("classification_signature") == signature
        and asset.get("classification") is not None
    ):
        info = dict(asset["classification"])
        return info

    info = _classify_texture_remap(
        asset["raw_index_histogram"],
        asset["included_sample_count"],
        context["palette_rgb"],
        context["remap_tables"],
    )
    if info["remap_state"] != TEXTURE_REMAP_STATE_UNRESOLVED:
        asset["classification_signature"] = signature
        asset["classification"] = dict(info)
    return info


def _classify_texture_remap(hist, n_opaque, palette_rgb, remap_tables):
    if n_opaque <= 0:
        return _default_remap_info(TEXTURE_REMAP_STATE_INVALID)

    info = _default_remap_info(TEXTURE_REMAP_STATE_READY)

    s0_table = remap_tables[0]
    s8_table = remap_tables[8]
    s15_table = remap_tables[15]
    used = tuple(index for index in range(255) if int(hist[index]) != 0)
    bright_idx = max(used, key=lambda index: _palette_luminance(palette_rgb, index))

    total_dist = 0
    for index in used:
        count = int(hist[index])
        m0 = int(s0_table[index]) & 0xFF
        m15 = int(s15_table[index]) & 0xFF
        if m0 != m15:
            total_dist += int(count) * _palette_manhattan_rgb(palette_rgb, m0, m15)
    if (float(total_dist) / float(n_opaque)) <= 2.0:
        return info

    s0_colors = set()
    s0_lums_weighted = []
    s0_luminances = []
    s15_luminances = []
    for index in used:
        count = int(hist[index])
        s0_index = int(s0_table[index]) & 0xFF
        s15_index = int(s15_table[index]) & 0xFF
        s0_color = _palette_rgb_tuple(palette_rgb, s0_index)
        s0_lum = _rgb_luminance(s0_color)
        s15_lum = _palette_luminance(palette_rgb, s15_index)
        s0_colors.add(s0_color)
        s0_lums_weighted.append((s0_lum, int(count)))
        s0_luminances.append(s0_lum)
        s15_luminances.append(s15_lum)

    max_lum = _weighted_percentile_luminance(s0_lums_weighted, n_opaque, 0.99)
    rgb_spread = _rgb_color_spread(s0_colors)
    s15_range = max(s15_luminances) - min(s15_luminances)
    s0_range = max(s0_luminances) - min(s0_luminances)
    contrast_ratio = float("inf") if s15_range <= 0.0 else s0_range / s15_range
    fog_color_u8 = _palette_rgb_tuple(palette_rgb, int(s0_table[bright_idx]) & 0xFF)

    category = "unclassified"
    if len(s0_colors) >= 2 and max_lum <= 35.0:
        category = "darkening_2color"
    elif len(s0_colors) == 1 and max_lum <= 15.0 and _rgb_chroma(fog_color_u8) <= 8:
        category = "darkening"
    elif max_lum <= 15.0 and _rgb_chroma(fog_color_u8) > 8:
        category = "fog"
    elif contrast_ratio < 0.15:
        category = "fog"
    elif rgb_spread <= 15:
        category = "fog"

    if category == "unclassified":
        category = "fog"

    dark_ratio = _palette_ratio(palette_rgb, int(s0_table[bright_idx]) & 0xFF, bright_idx)
    fog_color = tuple(float(channel) / 255.0 for channel in fog_color_u8)
    s8_ratio = _palette_ratio(palette_rgb, int(s8_table[bright_idx]) & 0xFF, bright_idx)
    s0_index = int(s0_table[bright_idx]) & 0xFF
    s8_index = int(s8_table[bright_idx]) & 0xFF
    s15_index = int(s15_table[bright_idx]) & 0xFF

    if category == "fog":
        category = _select_fog_category(
            bright_idx,
            palette_rgb,
            remap_tables,
            fog_color_u8,
            s8_ratio,
        )

    kind_ids = {
        "identity": 0,
        "darkening": 1,
        "darkening_2color": 2,
        "fog": 3,
        "split_fog": 4,
    }
    info.update(
        {
            "remap_kind": category,
            "remap_kind_id": kind_ids[category],
            "dark_ratio": dark_ratio,
            "fog_color": fog_color,
            "s8_ratio": s8_ratio,
            "remap_bright_idx": int(bright_idx),
            "remap_s0_idx": s0_index,
            "remap_s8_idx": s8_index,
            "remap_s15_idx": s15_index,
            "remap_unique_colors": len(used),
            "remap_opaque_pixels": int(n_opaque),
            "remap_max_lum": float(max_lum),
            "remap_rgb_spread": int(rgb_spread),
            "remap_contrast_ratio": float(contrast_ratio),
        }
    )
    return info


def _texture_pixel_histogram(pixels):
    pixel_view = np.frombuffer(pixels, dtype=np.uint8)
    hist = np.bincount(pixel_view, minlength=256).astype(np.uint32, copy=False)
    hist[0xFF] = 0
    return hist, int(hist.sum(dtype=np.uint64))


def _select_fog_category(bright_idx, palette_rgb, remap_tables, fog_color_u8, s8_ratio):
    if any(channel >= 1.0 for channel in s8_ratio):
        return "fog"

    bright_s15 = _palette_rgb_tuple(palette_rgb, bright_idx)
    mid = tuple(float(bright_s15[channel]) * s8_ratio[channel] for channel in range(3))
    mse_linear = 0.0
    mse_split = 0.0
    for shade in range(1, 15):
        game_color = _palette_rgb_tuple(
            palette_rgb,
            int(remap_tables[shade][bright_idx]) & 0xFF,
        )
        t = float(shade) / 15.0
        linear_pred = tuple(
            float(fog_color_u8[channel])
            + (float(bright_s15[channel]) - float(fog_color_u8[channel])) * t
            for channel in range(3)
        )
        if t >= (8.0 / 15.0):
            u = (t - (8.0 / 15.0)) / (7.0 / 15.0)
            split_pred = tuple(
                mid[channel] + (float(bright_s15[channel]) - mid[channel]) * u
                for channel in range(3)
            )
        else:
            u = t / (8.0 / 15.0)
            split_pred = tuple(
                float(fog_color_u8[channel])
                + (mid[channel] - float(fog_color_u8[channel])) * u
                for channel in range(3)
            )
        mse_linear += _rgb_squared_error(game_color, linear_pred)
        mse_split += _rgb_squared_error(game_color, split_pred)
    return "split_fog" if mse_split < mse_linear else "fog"


def _palette_rgb_tuple(palette_rgb, index):
    offset = (int(index) & 0xFF) * 3
    return (
        int(palette_rgb[offset]),
        int(palette_rgb[offset + 1]),
        int(palette_rgb[offset + 2]),
    )


def _rgb_luminance(rgb):
    return 0.299 * float(rgb[0]) + 0.587 * float(rgb[1]) + 0.114 * float(rgb[2])


def _palette_luminance(palette_rgb, index):
    return _rgb_luminance(_palette_rgb_tuple(palette_rgb, index))


def _palette_manhattan_rgb(palette_rgb, lhs_index, rhs_index):
    lhs = _palette_rgb_tuple(palette_rgb, lhs_index)
    rhs = _palette_rgb_tuple(palette_rgb, rhs_index)
    return sum(abs(lhs[channel] - rhs[channel]) for channel in range(3))


def _weighted_percentile_luminance(lums_weighted, total_count, percentile):
    target = float(total_count) * float(percentile)
    cumulative = 0
    for luminance, count in sorted(lums_weighted, key=lambda item: item[0]):
        cumulative += int(count)
        if float(cumulative) >= target:
            return float(luminance)
    return float(lums_weighted[-1][0])


def _rgb_color_spread(colors):
    if not colors:
        return 0
    return max(
        max(color[channel] for color in colors) - min(color[channel] for color in colors)
        for channel in range(3)
    )


def _rgb_chroma(rgb):
    return max(rgb) - min(rgb)


def _palette_ratio(palette_rgb, remapped_index, base_index):
    remapped = _palette_rgb_tuple(palette_rgb, remapped_index)
    base = _palette_rgb_tuple(palette_rgb, base_index)
    ratios = []
    for channel in range(3):
        if base[channel] <= 0:
            ratios.append(0.0)
        else:
            ratios.append(max(0.0, float(remapped[channel]) / float(base[channel])))
    return tuple(ratios)


def _rgb_squared_error(lhs, rhs):
    return sum((float(lhs[channel]) - float(rhs[channel])) ** 2.0 for channel in range(3))


def _read_cel_texture_from_page_entry(
    gamemem,
    descriptor,
    page_entry,
    texture_cache,
    stats,
):
    page_entry = dict(page_entry)
    asset = page_entry.pop("asset", None)
    if asset is None:
        stats["texture_cache_misses"] += int(texture_cache is not None)
        pixels = gamemem.read_runtime_bytes(
            int(page_entry["header_addr"]) + CEL_HEADER_SIZE,
            int(page_entry["width"]) * int(page_entry["height"]),
        )
        histogram, included_sample_count = _texture_pixel_histogram(pixels)
        asset = {
            "pixels": pixels,
            "raw_index_histogram": histogram,
            "included_sample_count": included_sample_count,
            "classification_signature": None,
            "classification": None,
        }
        signature = (
            int(page_entry["sub_entry"]),
            int(page_entry["header_addr"]),
            int(page_entry["width"]),
            int(page_entry["height"]),
            asset["pixels"],
        )
    else:
        signature = asset["signature"]
    page_entry["pixels"] = asset["pixels"]
    resource_id = int(
        page_entry.get("resource_id", asset.get("resource_id", -1))
    )
    page_entry["resource_id"] = resource_id
    page_entry["cel_name"] = str(
        page_entry.get("cel_name")
        or asset.get("cel_name")
        or cel_name_for_resource_id(resource_id)
    )
    selector = int(descriptor.get("selector", 0))
    page_entry["selector"] = selector
    page_entry["signature"] = signature
    return page_entry, asset


def _probe_cel_texture_from_cell_pointer(
    gamemem,
    page,
    sub_entry,
    data_ptr,
    stats,
):
    header_addr = int(data_ptr) - CEL_CELL_POINTER_OFFSET
    if header_addr <= 0:
        return None

    try:
        header = gamemem.read_runtime_bytes(
            header_addr,
            CEL_HEADER_SIZE,
        )
    except Exception as exc:
        stats["last_texture_error"] = str(exc)
        return None

    if header[0:4] != CEL_MAGIC:
        if not stats["last_texture_probe"]:
            stats["last_texture_probe"] = (
                f"page={int(page)} sub={int(sub_entry)} "
                f"ptr=0x{int(data_ptr):08X} "
                f"header=0x{int(header_addr):08X} "
                f"magic={bytes(header[0:4]).hex()}"
            )
        return None

    width = _u16(header, 0x10)
    height = _u16(header, 0x12)
    pixel_count = int(width) * int(height)
    if (
        width <= 0
        or height <= 0
        or width > MAX_TEXTURE_DIMENSION
        or height > MAX_TEXTURE_DIMENSION
        or pixel_count <= 0
        or pixel_count > MAX_TEXTURE_BYTES
    ):
        stats["last_texture_error"] = (
            f"invalid CEL dimensions {int(width)}x{int(height)} "
            f"at 0x{int(header_addr):08X}"
        )
        return None

    return {
        "sub_entry": int(sub_entry),
        "data_ptr": int(data_ptr),
        "header_addr": int(header_addr),
        "width": int(width),
        "height": int(height),
    }
