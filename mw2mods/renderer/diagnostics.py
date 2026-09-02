import gc
import os
import time


DEBUG_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "renderer_debug.log",
)
DEBUG_LOG_INTERVAL_FRAMES = max(
    60, int(os.environ.get("MW2_RENDERER_DEBUG_LOG_INTERVAL_FRAMES", "600"))
)
PERF_LOG_INTERVAL_FRAMES = max(
    60, int(os.environ.get("MW2_RENDERER_PERF_LOG_INTERVAL_FRAMES", "600"))
)
PERF_SPIKE_THRESHOLD_MS = max(
    1.0, float(os.environ.get("MW2_RENDERER_SPIKE_THRESHOLD_MS", "12.0"))
)
PERF_SPIKE_COOLDOWN_FRAMES = 10
PERF_SPIKE_WARMUP_FRAMES = 180

_debug_output = None
_gc_timing_installed = False
_gc_start_ns = [0, 0, 0]
_gc_elapsed_ns = [0, 0, 0]
_gc_collections = [0, 0, 0]
_gc_collected = 0
_gc_uncollectable = 0


def logging_enabled(modstate):
    conf = getattr(modstate, "conf", None)
    return bool(
        conf is not None
        and getattr(conf, "enable_diagnostic_logging", False)
    )


def _gc_timing_callback(phase, info):
    global _gc_collected, _gc_uncollectable
    generation = int(info.get("generation", 0))
    if generation < 0 or generation >= len(_gc_start_ns):
        return
    if phase == "start":
        _gc_start_ns[generation] = time.perf_counter_ns()
        return
    if phase != "stop":
        return

    started_ns = _gc_start_ns[generation]
    _gc_start_ns[generation] = 0
    if started_ns:
        _gc_elapsed_ns[generation] += max(0, time.perf_counter_ns() - started_ns)
    _gc_collections[generation] += 1
    _gc_collected += int(info.get("collected", 0))
    _gc_uncollectable += int(info.get("uncollectable", 0))


def install_gc_timing():
    global _gc_timing_installed
    if _gc_timing_installed:
        return
    gc.callbacks.append(_gc_timing_callback)
    _gc_timing_installed = True


def gc_timing_snapshot():
    return (
        sum(_gc_elapsed_ns),
        _gc_collections[0],
        _gc_collections[1],
        _gc_collections[2],
        _gc_collected,
        _gc_uncollectable,
    )


def gc_timing_delta(started):
    current = gc_timing_snapshot()
    return {
        "gc_ms": max(0, current[0] - started[0]) / 1_000_000.0,
        "gc_gen0": max(0, current[1] - started[1]),
        "gc_gen1": max(0, current[2] - started[2]),
        "gc_gen2": max(0, current[3] - started[3]),
        "gc_collected": max(0, current[4] - started[4]),
        "gc_uncollectable": max(0, current[5] - started[5]),
    }


def _format_gc_timing(gc_stats):
    return (
        f"gc_ms={float(gc_stats.get('gc_ms') or 0.0):.3f} "
        f"gc_gen={int(gc_stats.get('gc_gen0') or 0)}/"
        f"{int(gc_stats.get('gc_gen1') or 0)}/"
        f"{int(gc_stats.get('gc_gen2') or 0)} "
        f"gc_collected={int(gc_stats.get('gc_collected') or 0)} "
        f"gc_uncollectable={int(gc_stats.get('gc_uncollectable') or 0)}"
    )


def format_controls(controls):
    return ",".join(
        f"{key}={int(value) if isinstance(value, bool) else value}"
        for key, value in controls.items()
    )

def debug_log(modstate, message, *, flush=False):
    global _debug_output
    if not logging_enabled(modstate):
        return
    install_gc_timing()
    try:
        frame = int(getattr(modstate, "frame", 0))
        time_value = float(getattr(modstate, "time", 0.0))
        if _debug_output is None or _debug_output.closed:
            _debug_output = open(
                DEBUG_LOG_PATH,
                "a",
                encoding="utf-8",
                buffering=2 * 1024 * 1024,
            )
        _debug_output.write(f"frame={frame} time={time_value:.3f} {message}\n")
        if flush:
            _debug_output.flush()
    except Exception:
        pass


def _log_entity_lod_decisions(
    modstate,
    view,
    columns,
    rows,
    *,
    flush=False,
):
    if not rows:
        return
    debug_log(
        modstate,
        "entity_lod_decisions "
        f"view={view} columns={columns} rows={rows}",
        flush=flush,
    )


def maybe_log_geometry_snapshot(modstate, geometry, mfd_geometry=None):
    if not logging_enabled(modstate):
        return
    frame = int(getattr(modstate, "frame", 0))
    last_log_frame = int(getattr(modstate, "mw2_renderer_geometry_log_frame", -1000000))
    if frame - last_log_frame < DEBUG_LOG_INTERVAL_FRAMES:
        return

    modstate.mw2_renderer_geometry_log_frame = frame
    stats = geometry["stats"]
    debug_log(modstate, format_geometry_stats(stats))
    _log_entity_lod_decisions(
        modstate,
        "main",
        stats.get("renderer_entity_lod_decision_columns", ()),
        stats.get("renderer_entity_lod_decisions", ()),
    )
    if mfd_geometry is not None:
        mfd_stats = mfd_geometry["stats"]
        _log_entity_lod_decisions(
            modstate,
            "mfd",
            mfd_stats.get("renderer_entity_lod_decision_columns", ()),
            mfd_stats.get("renderer_entity_lod_decisions", ()),
            flush=True,
        )


def maybe_log_texture_remap_changes(modstate, geometry):
    if not logging_enabled(modstate):
        return
    textures = geometry.get("textures", {})
    previous = getattr(modstate, "mw2_renderer_texture_remap_state", None)
    if previous is None:
        previous = {}
        modstate.mw2_renderer_texture_remap_state = previous

    current_descs = set()
    for desc_idx, texture in sorted(textures.items()):
        desc_idx = int(desc_idx)
        if desc_idx < 0x100:
            continue
        current_descs.add(desc_idx)
        metadata = texture_remap_metadata(texture)
        if previous.get(desc_idx) == metadata:
            continue

        old = previous.get(desc_idx)
        previous[desc_idx] = metadata
        debug_log(
            modstate,
            "texture_remap_change "
            f"desc={hex_value(desc_idx)} "
            f"old={format_remap_metadata(old)} "
            f"new={format_remap_metadata(metadata)}",
        )

    for desc_idx in list(previous.keys()):
        if desc_idx in current_descs:
            continue
        old = previous.pop(desc_idx)
        debug_log(
            modstate,
            "texture_remap_change "
            f"desc={hex_value(desc_idx)} "
            f"old={format_remap_metadata(old)} "
            "new=missing",
        )


def texture_remap_metadata(texture):
    return (
        str(texture.get("remap_kind", "identity")),
        int(texture.get("remap_kind_id", 0)),
        rounded_tuple(texture.get("dark_ratio", (0.0, 0.0, 0.0))),
        rounded_tuple(texture.get("fog_terminal_color", (0.0, 0.0, 0.0))),
        rounded_tuple(texture.get("s8_ratio", (0.0, 0.0, 0.0))),
        int(texture.get("page", -1)),
        int(texture.get("selector", -1)),
        str(texture.get("selection_mode", "")),
        tuple(texture.get("signature", ())),
        int(texture.get("width", 0)),
        int(texture.get("height", 0)),
        int(texture.get("remap_bright_idx", -1)),
        int(texture.get("remap_s0_idx", -1)),
        int(texture.get("remap_s8_idx", -1)),
        int(texture.get("remap_s15_idx", -1)),
        int(texture.get("remap_unique_colors", 0)),
        int(texture.get("remap_opaque_pixels", 0)),
        round(float(texture.get("remap_max_lum", 0.0)), 6),
        int(texture.get("remap_rgb_spread", 0)),
        round(float(texture.get("remap_contrast_ratio", 0.0)), 6),
    )


def rounded_tuple(values):
    return tuple(round(float(value), 6) for value in values)


def format_remap_metadata(metadata):
    return "none" if metadata is None else repr(metadata)

def _format_geometry_stat(key, value):
    if value is None:
        return "None"
    if key == "delta" or key.endswith(("_addr", "_pointer")):
        return hex_value(value)
    if key.endswith("_ms"):
        return f"{float(value or 0.0):.3f}"
    return str(value)


def _format_values(values):
    return " ".join(
        f"{key}={_format_geometry_stat(key, value)}"
        for key, value in values.items()
        if not key.startswith("_")
    )


def format_geometry_stats(stats):
    return "geometry " + _format_values(stats)


def hex_value(value):
    value = int(value or 0)
    if value == 0:
        return "0"
    return f"0x{value:08X}"


def maybe_log_target_perf(modstate, target_view, stats, timings, gc_stats=None):
    if not logging_enabled(modstate):
        return
    frame = int(getattr(modstate, "frame", 0))
    target_signature = tuple(getattr(target_view, "target_id", ()) or ())
    previous_target = getattr(modstate, "mw2_renderer_target_perf_target", None)
    last_frame = int(
        getattr(modstate, "mw2_renderer_target_perf_log_frame", -1000000)
    )
    if (
        target_signature == previous_target
        and frame - last_frame < PERF_LOG_INTERVAL_FRAMES
    ):
        return
    modstate.mw2_renderer_target_perf_target = target_signature
    modstate.mw2_renderer_target_perf_log_frame = frame
    values = {
        "target": getattr(target_view, "target_id", None),
        "mode": int(getattr(target_view, "display_mode", 0)),
        **stats,
        **timings,
    }
    debug_log(
        modstate,
        "target_perf " + _format_values(values) + " "
        + _format_gc_timing(gc_stats or {}),
        flush=True,
    )
    _log_entity_lod_decisions(
        modstate,
        "target",
        stats.get("renderer_entity_lod_decision_columns", ()),
        stats.get("renderer_entity_lod_decisions", ()),
        flush=True,
    )

def maybe_log_menu_pages(modstate, menu_pages, stage):
    if not logging_enabled(modstate):
        return
    signature = tuple(
        (
            page.title,
            tuple(
                (
                    item.text,
                    item.item_type,
                    item.callback_reloc,
                    item.callback_parameter,
                )
                for item in page.items
            ),
        )
        for page in menu_pages
    )
    signature_attribute = f"mw2_renderer_menu_signature_{stage}"
    if signature == getattr(modstate, signature_attribute, None):
        return
    setattr(modstate, signature_attribute, signature)
    if not signature:
        return
    for page_index, page in enumerate(menu_pages):
        item_summary = "; ".join(
            f"{item_index}:type={item.item_type},"
            f"callback=0x{item.callback_reloc:08X},"
            f"param=0x{item.callback_parameter:08X},"
            f"value={item.value_text!r},"
            f"slider="
            f"{'' if item.slider is None else ''.join(('L' if item.slider.left_sprite else '-', 'T' if item.slider.track_sprite else '-', 'R' if item.slider.right_sprite else '-', 'M' if item.slider.thumb_sprite else '-'))},"
            f"text={item.text!r}"
            for item_index, item in enumerate(page.items)
        )
        debug_log(
            modstate,
            f"menu_page stage={stage} index={page_index} "
            f"handler_id={page.handler_id} title={page.title!r} "
            f"items=[{item_summary}]",
        )


def maybe_log_perf(
    modstate,
    snapshot,
    hook_count,
    snapshot_ms,
    render_timings,
    gc_stats=None,
):
    if not logging_enabled(modstate):
        return
    gc_stats = gc_stats or {}
    frame = int(getattr(modstate, "frame", 0))
    render_ms = float(render_timings.get("render_total_ms") or 0.0)
    hook_ms = snapshot_ms + render_ms
    last_spike_frame = int(
        getattr(modstate, "mw2_renderer_perf_spike_log_frame", -1000000)
    )
    if (
        frame > PERF_SPIKE_WARMUP_FRAMES
        and hook_ms >= PERF_SPIKE_THRESHOLD_MS
        and frame - last_spike_frame >= PERF_SPIKE_COOLDOWN_FRAMES
    ):
        modstate.mw2_renderer_perf_spike_log_frame = frame
        stats = snapshot.get("geometry", {}).get("stats", {})
        spike_values = {"hook_ms": hook_ms, "snapshot_ms": snapshot_ms}
        for source, keys in (
            (
                stats,
                (
                    "extract_ms", "topology_cache_misses",
                    "texture_cache_misses", "vertices_emitted",
                ),
            ),
            (
                render_timings,
                (
                    "palette_ms", "hud_texture_preload_ms", "clear_ms", "sky_ms",
                    "render_total_ms", "geometry_upload_ms", "geometry_draw_ms",
                    "hud_camera_ms", "hud_overlay_ms", "present_ms",
                ),
            ),
        ):
            spike_values.update((key, source.get(key)) for key in keys)
        debug_log(
            modstate,
            "render_spike " + _format_values(spike_values) + " "
            + _format_gc_timing(gc_stats),
            flush=True,
        )

    last_log_frame = int(
        getattr(modstate, "mw2_renderer_perf_log_frame", -1000000)
    )
    if frame - last_log_frame < PERF_LOG_INTERVAL_FRAMES:
        return
    modstate.mw2_renderer_perf_log_frame = frame

    stats = snapshot.get("geometry", {}).get("stats", {})
    camera = snapshot.get("camera", {})
    imaging_active = int(snapshot.get("imaging_active", 0) or 0)
    render_mode = (
        "satellite"
        if camera.get("satellite_view", False)
        else {1: "enhanced_imaging", 2: "xray"}.get(imaging_active, "normal")
    )
    values = {
        "mission": snapshot.get("mission_name", ""),
        "render_mode": render_mode,
        "perf_variant": os.environ.get("MW2_PERF_VARIANT", "working"),
        "imaging_active": imaging_active,
        "imaging_sub_mode": int(snapshot.get("imaging_sub_mode", 0) or 0),
        "wireframe_fade": snapshot.get("enhanced_imaging_wireframe_fade") or {},
        "hook_count_this_frame": hook_count,
        "snapshot_ms": snapshot_ms,
        **stats,
        **render_timings,
    }
    debug_log(
        modstate,
        "perf controls=" + format_controls(snapshot.get("render_controls", {}))
        + " " + _format_values(values) + " " + _format_gc_timing(gc_stats),
        flush=True,
    )

def maybe_log_composite_perf(modstate, composite_ms, viewport):
    if not logging_enabled(modstate):
        return
    frame = int(getattr(modstate, "frame", 0))
    last_log_frame = int(
        getattr(modstate, "mw2_renderer_composite_perf_log_frame", -1000000)
    )
    if frame - last_log_frame < PERF_LOG_INTERVAL_FRAMES:
        return

    modstate.mw2_renderer_composite_perf_log_frame = frame
    x, y, width, height = [int(value) for value in viewport]
    debug_log(
        modstate,
        "composite_perf "
        f"composite_ms={composite_ms:.3f} "
        f"viewport={x},{y},{width},{height}",
        flush=True,
    )
