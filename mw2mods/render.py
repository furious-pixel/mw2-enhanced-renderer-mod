import os
import time
from dataclasses import replace

import mod as _mod_api
from mod import modrender, modrenderhook
from mod_init import (
    config_keys_for_sections,
    load_mod_config,
)
from renderer import compositor, diagnostics, hud_renderer, scene_renderer
from renderer.resource_store import MissionResourceStore
from renderer.entity_components import prepare_entity_lod_catalog
from renderer.entity_lod_assets import (
    process_entity_lod_asset_safe_point,
)
from renderer.hud_menus import (
    MENU_HANDLER_COMMAND,
    MENU_HANDLER_ESC,
    MENU_HANDLER_USER_SYSTEMS,
    snapshot_menu_hud_state,
    snapshot_objectives_hud,
)

from renderer.hud_messages import snapshot_short_messages
from renderer.hud_3d_views import (
    resolve_target_model_root,
    snapshot_satellite_camera,
    snapshot_satellite_damage_viewport,
)
from renderer.hud_cockpit import (
    resolve_hud_video_noise_sprites,
    snapshot_cockpit_hud,
)
from renderer.hud_sprites import begin_hud_sprite_generation
from renderer.loading_screen import (
    LOADING_STRIP_PERIOD_SECONDS,
    capture_loading_background,
    capture_loading_screen,
)

from renderer.geometry import (
    extract_geometry,
    extract_renderer_entity_lod_view,
    extract_target_geometry,
)
from renderer.numba_warmup import warmup_numba_kernels
from renderer.texture import observe_texture_table_state
from renderer.resource_preload import (
    attach_texture_preload,
    drain_texture_preload_safe_point,
    preload_mission_texture_assets,
    process_texture_preload_safe_point,
)
from renderer.texture_state_audit import (
    maybe_log_texture_state_audit,
    reset_texture_state_audit,
)
from renderer.backend import (
    RendererResources,
)
from renderer.hud_renderer import TARGET_DISPLAY_ENHANCED_RENDERING
from renderer.scene_state import (
    FIXED_16_16_SCALE,
    NATIVE_VIEW_DEPTH_MULTIPLIER,
    PALETTE_SIZE,
    palette_color_float,
    palette_dac_to_rgb,
    read_camera,
)
from renderer.smooth_cockpit import prepare_smooth_cockpit

MOD_RENDER_VIEW_MOD_ONLY = getattr(_mod_api, "MOD_RENDER_VIEW_MOD_ONLY", 1)
MOD_RENDER_VIEW_SIDE_BY_SIDE = getattr(
    _mod_api,
    "MOD_RENDER_VIEW_SIDE_BY_SIDE",
    2,
)
MOD_RENDER_VIEW_SIDE_BY_SIDE_SUPPRESSED = getattr(
    _mod_api,
    "MOD_RENDER_VIEW_SIDE_BY_SIDE_SUPPRESSED",
    3,
)
modpostload = getattr(_mod_api, "modpostload", lambda func: func)


@modpostload
def renderer_post_load():
    warmup_numba_kernels()


ADDR_RENDER_LATCH = 0x0002CEB7
ADDR_FRAME_SUBMIT_CALL = 0x0002CEC1
ADDR_TARGET_3D_HELPER_CALL = 0x00047887
ADDR_LOADING_SCREEN_BEGIN_CALL = 0x0002CD11
ADDR_LOADING_SCREEN_FADE_CALL = 0x0002C4E2
ADDR_LOADING_SCREEN_STRIP_CALL = 0x0002C5B9
ADDR_LOADING_SCREEN_END_CALL = 0x0002CE42
ADDR_OUTRO_FADE_START_CALL = 0x0003FC2E
ADDR_MONITOR_BRIGHTNESS_STEP = 0x000A582C
ADDR_MONITOR_BRIGHTNESS_TABLE = 0x000B4F90
ADDR_PALETTE = 0x000B5390
ADDR_SKY_PALETTE_INDEX = 0x000A6F74
ADDR_GROUND_PALETTE_INDEX = 0x000A6F78
ADDR_GRADIENT_ENABLE = 0x000A70F0
ADDR_SKY_VISIBLE = 0x000A7108
ADDR_GROUND_VISIBLE = 0x000A710C
ADDR_GRADIENT_BAND_ENABLE = 0x000A7110
ADDR_GRADIENT_HEIGHT = 0x000A7154
ADDR_FOG_DISTANCE = 0x000A7130
ADDR_IMAGING_ACTIVE = 0x000A7120
ADDR_IMAGING_SUB_MODE = 0x000A7124
ADDR_MISSION_NAME = 0x0016BB6C
MISSION_NAME_MAX_BYTES = 32
# Reference captures on the native loading path measured 0.938/0.985 seconds
# from fade start to strip start and 0.392/0.968 seconds from strip start to
# handoff. The enhanced policy spends less time fading and more time showing
# the mission artwork: 0.5 seconds in, then spend 1/4 second fading out as soon
# as the first complete enhanced frame is published. The native 330 ms strip
# cadence continues through fade-out.
LOADING_FADE_IN_DURATION_SECONDS = 0.5
LOADING_FADE_OUT_DURATION_SECONDS = 1.0 / 4.0
LOADING_FIRST_FRAME_WAIT_TIMEOUT_SECONDS = 2.0
OUTRO_FADE_DURATION_SECONDS = 1.284
MONITOR_BRIGHTNESS_TABLE_SIZE = 64
HUD_MENU_HANDLER_IDS = frozenset(
    (MENU_HANDLER_USER_SYSTEMS, MENU_HANDLER_COMMAND)
)
LATE_MENU_HANDLER_IDS = frozenset((MENU_HANDLER_ESC,))
# About 10,000 native coordinate units after 16.16 conversion. Keep this in
# world units so the transition band does not grow with a level's far plane.
ENHANCED_IMAGING_FADE_WIDTH_WORLD = 0.15
DIAGNOSTIC_CONTROL_OVERRIDES = (
    "freeze_geometry_upload_after_first",
    "reuse_geometry_after_first",
    "cache_static_geometry",
    "disable_geometry_upload",
    "disable_geometry_draw",
    "entity_vertex_mode",
)
RENDERER_CONTROL_KEYS = config_keys_for_sections("DEBUG", "HUD", "renderer")
ACTIVE_PRELOAD_STATES = ("QUEUED", "LOADING")


def _env_bool(name, default):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return bool(default)

    normalized = raw_value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return bool(default)


def _env_positive_int(name, default):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return max(1, int(default))
    try:
        return max(1, int(raw_value))
    except (TypeError, ValueError):
        return max(1, int(default))


DEBUG_LOG_TEXTURE_REMAP_CHANGES = _env_bool(
    "MW2_RENDERER_LOG_TEXTURE_REMAP_CHANGES",
    False,
)
DEBUG_TEXTURE_STATE_AUDIT = _env_bool(
    "MW2_RENDERER_TEXTURE_STATE_AUDIT",
    False,
)
DEBUG_TEXTURE_STATE_AUDIT_INTERVAL_FRAMES = _env_positive_int(
    "MW2_RENDERER_TEXTURE_STATE_AUDIT_INTERVAL_FRAMES",
    300,
)
def _identity_decorator(func):
    return func


modsafe = getattr(_mod_api, "modsafe", _identity_decorator)


def _set_safe_point_barrier(gamemem, active):
    setter = getattr(gamemem, "set_safe_point_barrier", None)
    if setter is None:
        return False
    try:
        return bool(setter(bool(active)))
    except Exception:
        return False


@modsafe
def renderer_texture_preload_safe_point(modstate, gamemem):
    resource_store = _mission_resource_store(modstate)
    entity_lod = resource_store.entity_lod
    if getattr(
        modstate,
        "mw2_renderer_loading_texture_preload_barrier",
        False,
    ):
        cooperative = bool(
            getattr(
                modstate,
                "mw2_renderer_loading_texture_preload_cooperative",
                False,
            )
        )
        cpu_started = time.perf_counter()
        preload_started_at = getattr(
            modstate,
            "mw2_renderer_loading_texture_preload_started_at",
            None,
        )
        started = (
            cpu_started
            if preload_started_at is None
            else float(preload_started_at)
        )
        if cooperative:
            if resource_store.texture_preload_state in ACTIVE_PRELOAD_STATES:
                texture_cpu_ready = process_texture_preload_safe_point(
                    gamemem,
                    resource_store,
                )
            else:
                texture_cpu_ready = resource_store.texture_preload_state == "READY"
            # Keep resource families serialized. Interleaving POLY acquire/release
            # calls with texture resources causes avoidable loader/cache churn.
            if (
                resource_store.texture_preload_state not in ACTIVE_PRELOAD_STATES
                and entity_lod.state in ACTIVE_PRELOAD_STATES
            ):
                process_entity_lod_asset_safe_point(gamemem, entity_lod)
        else:
            texture_cpu_ready = drain_texture_preload_safe_point(
                gamemem,
                resource_store,
            )
            process_entity_lod_asset_safe_point(gamemem, entity_lod)
        cpu_finished = time.perf_counter()
        if cooperative and (
            resource_store.texture_preload_state in ACTIVE_PRELOAD_STATES
            or entity_lod.state in ACTIVE_PRELOAD_STATES
        ):
            return
        gpu_ready = False
        upload_action = "none"
        upload_error = "none"
        gpu_started = cpu_finished
        if texture_cpu_ready:
            resources = getattr(modstate, "mw2_renderer_resources", None)
            if resources is None:
                upload_error = "renderer_resources_unavailable"
            else:
                generation = (
                    int(resource_store.mission_generation),
                    int(resource_store.texture_generation),
                )
                try:
                    upload_action = (
                        scene_renderer.sync_indexed_texture_preloads(
                            resources,
                            generation,
                            resource_store.texture_preload_bindings,
                        )
                        or "cache_current"
                    )
                    gpu_ready = (
                        resources.indexed_texture_preload_generation
                        == generation
                    )
                except Exception as error:
                    upload_error = f"{type(error).__name__}:{error}"

        # A cooperative host barrier stays raised until the outer DOSBox loop
        # performs one final complete loading-screen composition with no next
        # safe point pending. The host then releases the barrier before
        # resuming guest execution.
        finished_at = time.perf_counter()
        modstate.mw2_renderer_loading_texture_preload_barrier = False
        modstate.mw2_renderer_loading_texture_preload_cooperative = False
        modstate.mw2_renderer_loading_preload_completed_at = finished_at
        diagnostics.debug_log(
            modstate,
            "loading_texture_preload "
            f"cpu_ready={int(texture_cpu_ready)} gpu_ready={int(gpu_ready)} "
            f"cooperative={int(cooperative)} "
            f"state={resource_store.texture_preload_state} "
            f"completed={resource_store.texture_preload_cursor}/"
            f"{len(resource_store.texture_preload_requests)} "
            f"bindings={len(resource_store.texture_preload_bindings)} "
            f"cpu_error={resource_store.texture_preload_error or 'none'} "
            f"upload_action={upload_action} upload_error={upload_error} "
            f"final_batch_cpu_ms={(cpu_finished - cpu_started) * 1000.0:.3f} "
            f"gpu_ms={(finished_at - gpu_started) * 1000.0:.3f} "
            f"total_ms={(finished_at - started) * 1000.0:.3f}",
        )
        diagnostics.debug_log(
            modstate,
            "loading_entity_lod_preload "
            f"state={entity_lod.state} "
            f"assets={len(entity_lod.assets)}/"
            f"{len(entity_lod.resource_ids)} "
            f"error={entity_lod.error or 'none'}",
        )
        return

    process_texture_preload_safe_point(
        gamemem,
        resource_store,
    )
    process_entity_lod_asset_safe_point(gamemem, entity_lod)


@modrenderhook("MW2.EXE", ADDR_LOADING_SCREEN_BEGIN_CALL, "call")
def renderer_loading_screen_begin(modstate, gamemem, modgl):
    _set_native_scene_raster_suppression(modstate, gamemem, False)
    _set_safe_point_barrier(gamemem, False)
    if modgl.set_frame_pacing_suspended(True):
        modstate.mw2_renderer_frame_pacing_loading_suspended = True
    resource_store = _mission_resource_store(modstate)
    resource_store.begin_mission()
    begin_hud_sprite_generation()
    modstate.mw2_hud_initial_startup_state = "armed"
    modstate.mw2_target_panel_last_hud_mode = None
    if _renderer_controls(modstate)["disable_mission_texture_preload"]:
        resource_store.texture_preload_state = "DISABLED"
    modstate.mw2_renderer_mission_name = ""
    modstate.mw2_renderer_mission_frame = -1
    modstate.mw2_renderer_texture_remap_state = {}
    reset_texture_state_audit(modstate)
    modstate.mw2_renderer_loading_active = True
    modstate.mw2_renderer_loading_visual = None
    modstate.mw2_renderer_loading_fade_started_at = None
    modstate.mw2_renderer_loading_strip_started_at = None
    modstate.mw2_renderer_loading_strip_index = 0
    modstate.mw2_renderer_loading_strip_next_at = None
    modstate.mw2_renderer_loading_handoff_at = None
    modstate.mw2_renderer_loading_texture_preload_barrier = False
    modstate.mw2_renderer_loading_texture_preload_cooperative = False
    modstate.mw2_renderer_loading_texture_preload_started_at = None
    modstate.mw2_renderer_loading_preload_completed_at = None
    modstate.mw2_renderer_loading_first_frame_timeout_logged = False
    modstate.mw2_renderer_outro_fade_started_at = None


@modrenderhook("MW2.EXE", ADDR_LOADING_SCREEN_FADE_CALL, "call")
def renderer_loading_screen_fade(modstate, gamemem, _modgl):
    try:
        modstate.mw2_renderer_loading_visual = capture_loading_background(gamemem)
    except Exception as error:
        diagnostics.debug_log(
            modstate,
            f"loading_screen background_capture_error={type(error).__name__}:{error}",
        )
    modstate.mw2_renderer_loading_fade_started_at = time.perf_counter()


@modrenderhook("MW2.EXE", ADDR_LOADING_SCREEN_STRIP_CALL, "call")
def renderer_loading_screen_strip(modstate, gamemem, _modgl):
    try:
        modstate.mw2_renderer_loading_visual = capture_loading_screen(
            gamemem,
            getattr(modstate, "mw2_renderer_loading_visual", None),
        )
    except Exception as error:
        diagnostics.debug_log(
            modstate,
            f"loading_screen strip_capture_error={type(error).__name__}:{error}",
        )
    strip_started_at = time.perf_counter()
    modstate.mw2_renderer_loading_strip_started_at = strip_started_at
    modstate.mw2_renderer_loading_strip_index = 0
    modstate.mw2_renderer_loading_strip_next_at = (
        strip_started_at + LOADING_STRIP_PERIOD_SECONDS
    )


@modrenderhook("MW2.EXE", ADDR_LOADING_SCREEN_END_CALL, "call")
def renderer_loading_screen_end(modstate, gamemem, modgl):
    resource_store = _mission_resource_store(modstate)
    entity_lod = resource_store.entity_lod
    started_at = time.perf_counter()
    modstate.mw2_renderer_loading_texture_preload_cooperative = False
    modstate.mw2_renderer_loading_texture_preload_started_at = started_at
    modstate.mw2_renderer_loading_texture_preload_barrier = False
    prepare_entity_lod_catalog(gamemem, entity_lod)
    entity_lod_queued = entity_lod.state in ACTIVE_PRELOAD_STATES
    if _renderer_controls(modstate)["disable_mission_texture_preload"]:
        resource_store.texture_preload_state = "DISABLED"
        if entity_lod_queued:
            cooperative = _set_safe_point_barrier(gamemem, True)
            modstate.mw2_renderer_loading_texture_preload_cooperative = cooperative
            modstate.mw2_renderer_loading_texture_preload_barrier = True
            gamemem.request_safe_point()
            diagnostics.debug_log(
                modstate,
                "loading_entity_lod_preload armed "
                f"cooperative={int(cooperative)} "
                f"resources={len(entity_lod.resource_ids)}",
            )
            return
        diagnostics.debug_log(
            modstate,
            "loading_texture_preload skipped "
            "reason=disabled_by_config state=DISABLED",
        )
        modstate.mw2_renderer_loading_preload_completed_at = time.perf_counter()
        return

    try:
        resources = _get_hook_resources(modstate, modgl)
    except Exception as error:
        diagnostics.debug_log(
            modstate,
            "loading_texture_preload skipped "
            f"reason=renderer_resources_error "
            f"error={type(error).__name__}:{error}",
        )
        finished_at = time.perf_counter()
        modstate.mw2_renderer_loading_preload_completed_at = finished_at
        modstate.mw2_renderer_loading_handoff_at = finished_at
        return

    if resources is not None:
        observed = observe_texture_table_state(gamemem, resource_store)
        cooperative = False
        if observed or entity_lod_queued:
            cooperative = _set_safe_point_barrier(gamemem, True)
            modstate.mw2_renderer_loading_texture_preload_cooperative = cooperative
            modstate.mw2_renderer_loading_texture_preload_barrier = True
        texture_preload_armed = bool(
            observed
            and preload_mission_texture_assets(gamemem, resource_store)
        )
        if entity_lod_queued and not texture_preload_armed:
            gamemem.request_safe_point()
        if texture_preload_armed or entity_lod_queued:
            diagnostics.debug_log(
                modstate,
                "loading_texture_preload armed "
                f"cooperative={int(cooperative)} "
                f"state={resource_store.texture_preload_state} "
                f"resources={len(resource_store.texture_preload_requests)}",
            )
            diagnostics.debug_log(
                modstate,
                "loading_entity_lod_preload armed "
                f"cooperative={int(cooperative)} "
                f"state={entity_lod.state} "
                f"descriptors={0 if entity_lod.frame is None else len(entity_lod.frame.entity_indices)} "
                f"resources={len(entity_lod.resource_ids)} "
                f"error={entity_lod.error or 'none'}",
            )
            return
        if observed and resource_store.texture_preload_state == "READY":
            try:
                gamemem.request_safe_point()
                return
            except Exception as error:
                resource_store.texture_preload_error = type(error).__name__
        modstate.mw2_renderer_loading_texture_preload_barrier = False
        modstate.mw2_renderer_loading_texture_preload_cooperative = False
        _set_safe_point_barrier(gamemem, False)
        diagnostics.debug_log(
            modstate,
            "loading_texture_preload skipped "
            f"reason={'not_observed' if not observed else 'not_queued'} "
            f"state={resource_store.texture_preload_state} "
            f"error={resource_store.texture_preload_error or 'none'}",
        )
    else:
        diagnostics.debug_log(
            modstate,
            "loading_texture_preload skipped "
            "reason=renderer_resources_unavailable",
        )

    finished_at = time.perf_counter()
    modstate.mw2_renderer_loading_preload_completed_at = finished_at
    if resources is None:
        modstate.mw2_renderer_loading_handoff_at = finished_at


@modrenderhook("MW2.EXE", ADDR_OUTRO_FADE_START_CALL, "call")
def renderer_outro_fade_start(modstate, _gamemem, modgl):
    modstate.mw2_renderer_outro_fade_started_at = time.perf_counter()
    if modgl.set_continuous_presentation(True):
        modstate.mw2_renderer_outro_fade_continuous_presentation = True


def _context_generation(modgl):
    return int(getattr(modgl, "context_generation", 0))


def _mission_resource_store(modstate):
    resource_store = getattr(modstate, "mw2_renderer_resource_store", None)
    if resource_store is None:
        resource_store = MissionResourceStore()
        modstate.mw2_renderer_resource_store = resource_store
    return resource_store


def _build_resources(modstate, modgl, viewport_width, viewport_height):
    controls = _renderer_controls(modstate)
    generation = _context_generation(modgl)
    old_resources = getattr(modstate, "mw2_renderer_resources", None)
    if old_resources is not None:
        old_resources.release()

    resources = RendererResources(
        viewport_width,
        viewport_height,
        panel_scaling=controls["panel_scaling"],
        target_marker_scaling=controls["target_marker_scaling"],
        antialiasing=controls["antialiasing"],
        ssaa_line_width=controls["ssaa_line_width"],
        max_horizontal_fov_degrees=controls["max_horizontal_fov_degrees"],
    )
    modstate.mw2_renderer_resources = resources
    modstate.mw2_renderer_generation = generation
    print(
        "MOD: MW2 renderer attached to OpenGL context generation "
        f"{generation} for viewport "
        f"{int(viewport_width)}x{int(viewport_height)} "
        f"antialiasing={resources.antialiasing}",
        flush=True,
    )
    diagnostics.debug_log(
        modstate,
        "renderer attach "
        f"generation={generation} "
        f"viewport={int(viewport_width)}x{int(viewport_height)} "
        f"antialiasing={resources.antialiasing} "
        f"requested_antialiasing={resources.requested_antialiasing} "
        f"fallback={resources.antialiasing_fallback_reason or 'none'} "
        f"controls={diagnostics.format_controls(controls)}",
    )
    return resources


def _get_resources(modstate, modgl, viewport_width, viewport_height):
    resources = getattr(modstate, "mw2_renderer_resources", None)
    generation = getattr(modstate, "mw2_renderer_generation", None)
    if resources is None or generation != _context_generation(modgl):
        resources = _build_resources(modstate, modgl, viewport_width, viewport_height)

    previous_size = resources.size
    compositor.resize(resources, viewport_width, viewport_height)
    if resources.size != previous_size:
        resources.update_hud_atlas()
    return resources


def _get_hook_resources(modstate, modgl):
    generation = _context_generation(modgl)
    viewport_width = int(getattr(modgl, "mod_viewport_w", 0))
    viewport_height = int(getattr(modgl, "mod_viewport_h", 0))
    if generation <= 0 or viewport_width <= 0 or viewport_height <= 0:
        return None

    return _get_resources(modstate, modgl, viewport_width, viewport_height)


def _enhanced_imaging_wireframe_fade(
    modstate,
    camera,
    imaging_active,
    satellite_view,
    controls,
):
    active = int(imaging_active) == 1 and not satellite_view
    if not active:
        modstate.mw2_renderer_enhanced_imaging_started_at = None
        return None

    now = time.perf_counter()
    started_at = getattr(
        modstate,
        "mw2_renderer_enhanced_imaging_started_at",
        None,
    )
    just_started = started_at is None
    if just_started:
        started_at = now
        modstate.mw2_renderer_enhanced_imaging_started_at = started_at

    native_far_world = float(camera.get("far_plane", 1000.0))
    far_depth_fixed = int(camera.get("far_depth_fixed", 0) or 0)
    if far_depth_fixed > 0:
        native_far_world = far_depth_fixed / (
            FIXED_16_16_SCALE * NATIVE_VIEW_DEPTH_MULTIPLIER
        )
    ratio = float(controls["enhanced_imaging_distance_ratio"])
    target_start = max(0.0, native_far_world * ratio)
    initial_start = max(
        0.0,
        float(
            camera.get(
                "clip_near_plane",
                camera.get("near_plane", 0.0),
            )
        ),
    )
    reveal_time = float(controls["enhanced_imaging_reveal_time"])
    progress = (
        1.0
        if reveal_time <= 0.0
        else min(1.0, max(0.0, (now - started_at) / reveal_time))
    )
    fade_start = initial_start + (target_start - initial_start) * progress
    fade = {
        "start": fade_start,
        "end": fade_start + ENHANCED_IMAGING_FADE_WIDTH_WORLD,
        "target": target_start,
        "native_far": native_far_world,
        "progress": progress,
    }
    if just_started:
        diagnostics.debug_log(
            modstate,
            "enhanced_imaging_reveal "
            f"start={fade['start']:.4f}/{fade['end']:.4f} "
            f"target={fade['target']:.4f} "
            f"native_far={fade['native_far']:.4f} "
            f"duration={reveal_time:.3f}",
            flush=True,
        )
    return fade


def _snapshot_scene_state(modstate, gamemem, modgl):
    controls = _renderer_controls(modstate)
    viewport_size = (
        max(1, int(getattr(modgl, "mod_viewport_w", 0) or 1024)),
        max(1, int(getattr(modgl, "mod_viewport_h", 0) or 768)),
    )
    mission_name = _cached_mission_name(modstate, gamemem)
    raw_palette = gamemem.read_reloc_bytes(ADDR_PALETTE, PALETTE_SIZE)
    palette_rgb = palette_dac_to_rgb(raw_palette)
    sky_palette_index = gamemem.read_reloc_u8(ADDR_SKY_PALETTE_INDEX)
    ground_palette_index = gamemem.read_reloc_u8(ADDR_GROUND_PALETTE_INDEX)
    sky_visible = gamemem.read_reloc_u32(ADDR_SKY_VISIBLE) != 0
    ground_visible = gamemem.read_reloc_u32(ADDR_GROUND_VISIBLE) != 0
    gradient_enable = gamemem.read_reloc_u32(ADDR_GRADIENT_ENABLE) != 0
    gradient_band_enable = gamemem.read_reloc_u32(ADDR_GRADIENT_BAND_ENABLE) != 0
    gradient_height = gamemem.read_reloc_u32(ADDR_GRADIENT_HEIGHT)
    imaging_active = gamemem.read_reloc_u32(ADDR_IMAGING_ACTIVE)
    imaging_sub_mode = gamemem.read_reloc_u32(ADDR_IMAGING_SUB_MODE)
    fog_distance_world = (
        max(1, gamemem.read_reloc_i32(ADDR_FOG_DISTANCE)) / FIXED_16_16_SCALE
    )

    active_camera = prepare_smooth_cockpit(gamemem, read_camera(gamemem))
    satellite_camera = snapshot_satellite_camera(gamemem, active_camera)
    if satellite_camera is not None:
        camera = {
            **satellite_camera,
            "satellite_damage_viewport": getattr(
                modstate,
                "mw2_renderer_satellite_damage_viewport",
                None,
            ),
        }
    else:
        camera = active_camera
    camera = {
        **camera,
        "output_viewport_width": viewport_size[0],
        "output_viewport_height": viewport_size[1],
        "max_horizontal_fov_degrees": controls["max_horizontal_fov_degrees"],
    }
    resource_store = _mission_resource_store(modstate)
    cockpit_hud = snapshot_cockpit_hud(
        gamemem,
        camera,
        modstate,
        compass_altimeter=controls["compass_altimeter"],
        power_meters=controls["power_meters"],
        htal_meters=controls["htal_meters"],
        alt_htal_view=controls["alt_htal_view"],
        rear_camera_mirror=controls["rear_camera_mirror"],
        alt_throttle_indicator_position=(
            controls["alt_throttle_indicator_position"]
        ),
        panel_scaling=controls["panel_scaling"],
        viewport_size=viewport_size,
        max_horizontal_fov_degrees=controls["max_horizontal_fov_degrees"],
    )
    mfd_view = getattr(cockpit_hud, "mfd_view", None)
    view_excluded_nodes = tuple(
        int(node_addr)
        for node_addr in (
            getattr(mfd_view, "excluded_node_addrs", ()) or ()
        )
        if int(node_addr) != 0
    )
    cockpit_render_nodes = tuple(
        int(node_addr)
        for node_addr in camera.get("_cockpit_render_node_addrs", ())
        if int(node_addr) != 0
    )
    satellite_view = bool(camera.get("satellite_view", False))
    enhanced_imaging_wireframe_fade = _enhanced_imaging_wireframe_fade(
        modstate,
        camera,
        imaging_active,
        satellite_view,
        controls,
    )
    # MFD scene cameras intentionally stay in normal textured mode. Preserve
    # ordinary geometry alongside the main enhanced wireframe while one is active.
    hud_camera_requires_normal_geometry = (
        getattr(cockpit_hud, "mfd_view", None) is not None
    )
    enhanced_effects_enabled = bool(
        controls["enhanced_enhanced_imaging"]
        and int(imaging_active) != 0
        and not satellite_view
    )
    geometry = _snapshot_geometry(
        modstate,
        gamemem,
        camera,
        palette_rgb,
        controls,
        mission_name,
        # Native enhanced imaging turns satellite terrain into wireframe, but
        # that combination is intentionally ignored by the replacement view.
        build_wireframe=(int(imaging_active) != 0 and not satellite_view),
        enhanced_wireframe_only=(
            int(imaging_active) != 0
            and not satellite_view
            and not hud_camera_requires_normal_geometry
        ),
        preserve_enhanced_imaging_effects=enhanced_effects_enabled,
        view_excluded_node_addrs=view_excluded_nodes,
        cockpit_node_addrs=cockpit_render_nodes,
    )
    mfd_entity_geometry = None
    if (
        controls["entity_lod_selection"] != "native"
        and resource_store.entity_lod.state == "READY"
    ):
        mfd_entity_geometry = _extract_entity_view_geometry(
            gamemem,
            cockpit_hud,
            mfd_view,
            viewport_size,
            palette_rgb,
            controls,
            resource_store,
            f"mfd:{mfd_view.kind}" if mfd_view is not None else "mfd",
            entity_lod_hide_player=(
                mfd_view is not None and mfd_view.kind in ("rear", "down")
            ),
        )
    diagnostics.maybe_log_geometry_snapshot(
        modstate,
        geometry,
        mfd_entity_geometry,
    )
    if DEBUG_TEXTURE_STATE_AUDIT:
        maybe_log_texture_state_audit(
            modstate,
            gamemem,
            geometry,
            raw_palette,
            DEBUG_TEXTURE_STATE_AUDIT_INTERVAL_FRAMES,
        )
    if DEBUG_LOG_TEXTURE_REMAP_CHANGES:
        diagnostics.maybe_log_texture_remap_changes(modstate, geometry)
    menu_pages, pending_menu_pages, active_menu_handlers = (
        snapshot_menu_hud_state(gamemem)
    )
    monitor_brightness = _snapshot_monitor_brightness(gamemem)

    snapshot = {
        "frame": int(getattr(modstate, "frame", 0)),
        "mission_name": mission_name,
        "palette_rgb": palette_rgb,
        "camera": camera,
        "geometry": geometry,
        "mfd_entity_geometry": mfd_entity_geometry,
        "fog_distance_world": fog_distance_world,
        "imaging_active": 0 if satellite_view else int(imaging_active),
        "imaging_sub_mode": int(imaging_sub_mode),
        "enhanced_imaging_wireframe_fade": (
            enhanced_imaging_wireframe_fade
        ),
        "sky_palette_index": sky_palette_index,
        "ground_palette_index": ground_palette_index,
        "sky_visible": sky_visible,
        "ground_visible": ground_visible,
        "gradient_height": gradient_height,
        "draw_gradient": (
            sky_visible
            and gradient_enable
            and gradient_band_enable
            and gradient_height > 0
            and ground_palette_index > sky_palette_index
        ),
        "ground_color": palette_color_float(palette_rgb, ground_palette_index),
        "render_controls": controls,
        "cockpit_hud": cockpit_hud,
        "menu_hud_pages_before_update": menu_pages,
        "pending_menu_hud_pages": pending_menu_pages,
        "active_menu_handler_ids": active_menu_handlers,
        "objectives_hud_before_update": snapshot_objectives_hud(gamemem),
    }
    modstate.mw2_renderer_monitor_brightness = monitor_brightness
    modstate.mw2_renderer_frame = snapshot
    return snapshot


def _snapshot_monitor_brightness(gamemem):
    step = max(
        0,
        min(15, int(gamemem.read_reloc_i32(ADDR_MONITOR_BRIGHTNESS_STEP))),
    )
    table = bytes(
        gamemem.read_reloc_bytes(
            ADDR_MONITOR_BRIGHTNESS_TABLE
            + step * MONITOR_BRIGHTNESS_TABLE_SIZE,
            MONITOR_BRIGHTNESS_TABLE_SIZE,
        )
    )
    return {"step": step, "table": table}


def _cached_mission_name(modstate, gamemem):
    frame = int(getattr(modstate, "frame", 0))
    cached_frame = int(getattr(modstate, "mw2_renderer_mission_frame", -1))
    cached_name = getattr(modstate, "mw2_renderer_mission_name", "")
    if cached_name and cached_frame <= frame and frame > 1:
        return cached_name

    mission_name = _read_mission_name(gamemem)
    modstate.mw2_renderer_mission_name = mission_name
    modstate.mw2_renderer_mission_frame = frame
    return mission_name


def _read_mission_name(gamemem):
    raw_name = gamemem.read_reloc_bytes(
        ADDR_MISSION_NAME,
        MISSION_NAME_MAX_BYTES,
    )

    chars = []
    for value in raw_name:
        value = int(value)
        if value == 0 or value <= 0x20:
            break
        if 0x21 <= value <= 0x7E:
            chars.append(chr(value))
            continue
        break
    return "".join(chars)


def _renderer_controls(modstate):
    conf = getattr(modstate, "conf", None)
    if conf is None:
        conf = load_mod_config(modstate)
    if conf is getattr(modstate, "mw2_renderer_controls_conf", None):
        return modstate.mw2_renderer_controls
    conf_values = vars(conf)
    controls = {key: conf_values[key] for key in RENDERER_CONTROL_KEYS}
    for key in DIAGNOSTIC_CONTROL_OVERRIDES:
        env_name = f"MW2_RENDERER_{key.upper()}"
        if env_name not in os.environ:
            continue
        controls[key] = (
            _env_bool(env_name, controls[key])
            if isinstance(controls[key], bool)
            else (
                "cached"
                if os.environ[env_name].strip().lower() == "cached"
                else "matrix"
            )
        )
    _apply_renderer_control_changes(modstate, controls)
    modstate.mw2_renderer_controls_conf = conf
    modstate.mw2_renderer_controls = controls
    return controls


def _entity_mesh_options(controls, view_key=None):
    options = {
        key: controls[key]
        for key in (
            "enhanced_heli_rotors",
            "enhanced_aero_lift_fans",
            "enhanced_mech_textures",
            "enhanced_dropship_textures",
            "enhanced_mech_texture_uv_scale",
            "enhanced_dropship_texture_uv_scale",
        )
    }
    if view_key is not None:
        options.update(
            entity_lod_selection=controls["entity_lod_selection"],
            entity_lod_detail_pixels=tuple(
                controls[f"entity_lod_detail{detail}_pixels"]
                for detail in range(3)
            ),
            entity_lod_hysteresis=controls["entity_lod_hysteresis"],
            entity_lod_debug_decisions=controls["enable_diagnostic_logging"],
            entity_lod_view_key=view_key,
        )
    return options


def _apply_renderer_control_changes(modstate, controls):
    signature = tuple(sorted(controls.items()))
    previous = getattr(modstate, "mw2_renderer_control_signature", None)
    if previous == signature:
        return

    modstate.mw2_renderer_control_signature = signature
    _mission_resource_store(modstate).clear_assets()
    modstate.mw2_renderer_texture_remap_state = {}
    diagnostics.debug_log(
        modstate,
        f"renderer controls changed controls={diagnostics.format_controls(controls)} caches_cleared=1",
    )


def _snapshot_geometry(
    modstate,
    gamemem,
    camera,
    palette_rgb,
    controls,
    mission_name="",
    build_wireframe=False,
    enhanced_wireframe_only=False,
    preserve_enhanced_imaging_effects=False,
    excluded_node_addrs=(),
    included_node_addrs=(),
    view_excluded_node_addrs=(),
    cockpit_node_addrs=(),
    enable_entity_lod=True,
):
    started = time.perf_counter()
    resource_store = _mission_resource_store(modstate)
    observe_texture_table_state(gamemem, resource_store)
    preload_mission_texture_assets(gamemem, resource_store)
    resource_store.cockpit_effect_tracker.update(
        gamemem,
        camera,
        resource_store.cockpit_radius_fixed,
    )
    excluded_node_addrs = tuple(int(value) for value in excluded_node_addrs)
    included_node_addrs = tuple(int(value) for value in included_node_addrs)
    view_excluded_node_addrs = tuple(
        int(value) for value in view_excluded_node_addrs
    )
    filtered_nodes = bool(excluded_node_addrs or included_node_addrs)
    effect_descriptors = (
        resource_store.enhanced_imaging_effect_descriptors
        if controls["enhanced_enhanced_imaging"]
        else None
    )
    geometry_reuse_signature = (
        bool(build_wireframe),
        bool(preserve_enhanced_imaging_effects),
        tuple(sorted(effect_descriptors or ())),
    )
    if controls["reuse_geometry_after_first"] and not filtered_nodes:
        cached = resource_store.cached_geometry
        cached_signature = resource_store.cached_geometry_signature
        if (
            cached is not None
            and cached_signature == geometry_reuse_signature
            and _geometry_vertex_count(cached) > 0
        ):
            geometry = dict(cached)
            geometry["stats"] = dict(cached.get("stats", {}))
            geometry["stats"]["extract_ms"] = 0.0
            attach_texture_preload(geometry, resource_store)
            return geometry

    try:
        static_cache = (
            resource_store.static_geometry
            if controls["cache_static_geometry"]
            else None
        )
        topology_cache = (
            resource_store.mesh_assets
            if controls["cache_geometry_data"]
            else None
        )
        texture_cache = (
            resource_store.texture_assets
            if controls["cache_textures"]
            else None
        )
        geometry = extract_geometry(
            gamemem,
            {
                **camera,
                "palette_rgb": palette_rgb,
                "excluded_node_addrs": excluded_node_addrs,
                "included_node_addrs": included_node_addrs,
                "view_excluded_node_addrs": view_excluded_node_addrs,
                "cockpit_node_addrs": cockpit_node_addrs,
                "cockpit_effect_tracker": (
                    resource_store.cockpit_effect_tracker
                ),
                "previous_cockpit_far_depth_fixed": (
                    resource_store.cockpit_far_depth_fixed
                ),
                "enhanced_heli_rotors": controls["enhanced_heli_rotors"],
                "enhanced_aero_lift_fans": controls[
                    "enhanced_aero_lift_fans"
                ],
                "reduce_terrain_gaps": controls["reduce_terrain_gaps"],
                "mission_name": mission_name,
                **_entity_mesh_options(controls, "main"),
            },
            entity_vertex_mode=controls["entity_vertex_mode"],
            static_cache=static_cache,
            topology_cache=topology_cache,
            topology_volatility=resource_store.topology_volatility,
            texture_cache=texture_cache,
            cache_static_final=controls["cache_static_geometry"],
            build_wireframe=build_wireframe,
            enhanced_wireframe_only=enhanced_wireframe_only,
            preserve_enhanced_imaging_effects=(
                preserve_enhanced_imaging_effects
            ),
            enhanced_imaging_effect_descriptors=effect_descriptors,
            entity_lod_store=(
                resource_store.entity_lod if enable_entity_lod else None
            ),
            dynamic_batch_cache=(
                None if filtered_nodes else resource_store.dynamic_batches
            ),
        )
        geometry["stats"]["extract_ms"] = (time.perf_counter() - started) * 1000.0
        geometry["stats"]["mission_generation"] = (
            resource_store.mission_generation
        )
        current_cockpit_far_depth = float(
            geometry.get("cockpit_far_depth_fixed", 0.0)
        )
        if current_cockpit_far_depth > 0.0:
            resource_store.cockpit_far_depth_fixed = current_cockpit_far_depth
        current_cockpit_radius = float(
            geometry.get("cockpit_radius_fixed", 0.0)
        )
        if current_cockpit_radius > 0.0:
            resource_store.cockpit_radius_fixed = current_cockpit_radius
        if effect_descriptors is not None and not filtered_nodes:
            resource_store.enhanced_imaging_effect_descriptors = frozenset(
                geometry.get("enhanced_imaging_effect_descriptors", ())
            )
        attach_texture_preload(geometry, resource_store)
        live_index_invalidations = int(
            geometry["stats"].get("topology_live_index_invalidations", 0)
        )
        if live_index_invalidations:
            diagnostics.debug_log(
                modstate,
                "topology_cache live_index_invalidations="
                f"{live_index_invalidations}",
                flush=True,
            )
        if (
            controls["reuse_geometry_after_first"]
            and not filtered_nodes
            and _geometry_vertex_count(geometry) > 0
        ):
            resource_store.cached_geometry_signature = geometry_reuse_signature
            cached_geometry = dict(geometry)
            cached_geometry["stats"] = dict(geometry["stats"])
            resource_store.cached_geometry = cached_geometry
        return geometry
    except Exception as exc:
        frame = int(getattr(modstate, "frame", 0))
        message = (
            f"frame={frame} geometry extraction failed: "
            f"{type(exc).__name__}:{exc}"
        )
        print(f"MOD: MW2 {message}", flush=True)
        diagnostics.debug_log(modstate, message, flush=True)
        raise


def _extract_entity_view_geometry(
    gamemem,
    cockpit_hud,
    view,
    viewport_size,
    palette_rgb,
    controls,
    resource_store,
    view_key,
    **camera_options,
):
    if view is None:
        return None
    _pixel_rect, output_size, logical_size = (
        hud_renderer.hud_camera_view_metrics(
            cockpit_hud,
            view,
            viewport_size,
            controls,
        )
    )
    logical_height = max(1, int(logical_size[1]))
    output_height = max(1, int(output_size[1]))
    pane_focal_y = max(
        1.0,
        float(view.camera.get("focal_length_pixels", 512.0))
        * float(view.camera.get("projection_aspect_scale", 1.0)),
    )
    camera = {
        **view.camera,
        "palette_rgb": palette_rgb,
        "output_viewport_width": max(1, int(output_size[0])),
        "output_viewport_height": output_height,
        "entity_lod_output_focal_pixels": (
            pane_focal_y * output_height / logical_height
        ),
        **_entity_mesh_options(controls, view_key),
        **camera_options,
    }
    return extract_renderer_entity_lod_view(
        gamemem,
        camera,
        resource_store.entity_lod,
        texture_cache=(
            resource_store.texture_assets
            if controls["cache_textures"]
            else None
        ),
    )


def _geometry_vertex_count(geometry):
    return int(geometry.get("stats", {}).get("vertices_emitted", 0))


def _ground_clear_color(snapshot):
    if snapshot and int(snapshot.get("imaging_active", 0) or 0) != 0:
        return (0.0, 0.0, 0.0)
    if snapshot and snapshot.get("ground_visible"):
        return snapshot["ground_color"]
    return (0.0, 0.0, 0.0)


def _menu_pages_for_handlers(
    modstate,
    current_pages,
    pending_pages,
    active_handler_ids,
    handler_ids,
    cache_attribute,
):
    pages = tuple(
        page for page in current_pages if page.handler_id in handler_ids
    )
    if not pages:
        pages = tuple(
            page for page in pending_pages if page.handler_id in handler_ids
        )

    if pages:
        setattr(modstate, cache_attribute, pages)
    elif not active_handler_ids.isdisjoint(handler_ids):
        pages = getattr(modstate, cache_attribute, ())
    else:
        setattr(modstate, cache_attribute, ())
    return pages


def _begin_loading_handoff_after_publish(modstate, frame):
    if not getattr(modstate, "mw2_renderer_loading_active", False):
        return
    if getattr(modstate, "mw2_renderer_loading_handoff_at", None) is not None:
        return
    preload_completed_at = getattr(
        modstate,
        "mw2_renderer_loading_preload_completed_at",
        None,
    )
    if preload_completed_at is None:
        return

    handoff_at = time.perf_counter()
    modstate.mw2_renderer_loading_handoff_at = handoff_at
    diagnostics.debug_log(
        modstate,
        "loading_handoff "
        f"first_frame_ready=1 frame={int(frame)} "
        f"wait_ms={(handoff_at - float(preload_completed_at)) * 1000.0:.3f}",
    )


def _publish_frame(modstate, modgl, resources, frame):
    if not compositor.publish_frame(resources, frame):
        return False
    modgl.notify_frame_ready()
    _begin_loading_handoff_after_publish(modstate, frame)
    return True


def _set_native_scene_raster_suppression(modstate, gamemem, enabled):
    setter = getattr(gamemem, "set_scene_raster_suppression", None)
    if setter is None:
        return False

    accepted = bool(setter(bool(enabled)))
    stats_reader = getattr(gamemem, "scene_raster_suppression_stats", None)
    if stats_reader is None:
        return accepted

    raw_stats = stats_reader()
    if not isinstance(raw_stats, tuple) or len(raw_stats) != 7:
        return accepted

    configured, validated, requested, phase_active, request_frame, current_frame, sites = (
        raw_stats
    )
    modstate.mw2_renderer_scene_raster_suppression_stats = raw_stats
    frame = int(getattr(modstate, "frame", 0))
    last_log_frame = int(
        getattr(modstate, "mw2_renderer_scene_raster_suppression_log_frame", -1000000)
    )
    if frame - last_log_frame >= 120:
        site_text = ",".join(
            f"0x{int(site[0]):08X}:{int(site[1])}/{int(site[2])}"
            for site in sites
            if isinstance(site, tuple) and len(site) == 3
        )
        diagnostics.debug_log(
            modstate,
            "native_scene_raster_suppression "
            f"configured={int(configured)} validated={int(validated)} "
            f"requested={int(requested)} phase_active={int(phase_active)} "
            f"accepted={int(accepted)} request_frame={int(request_frame)} "
            f"current_frame={int(current_frame)} sites={site_text}",
        )
        modstate.mw2_renderer_scene_raster_suppression_log_frame = frame
    return accepted


def _presentation_requests_native_scene_suppression(modgl, render_controls):
    if not render_controls["suppress_native_scene_raster"]:
        return False
    view_mode = int(getattr(modgl, "view_mode", 0))
    return view_mode in (
        MOD_RENDER_VIEW_MOD_ONLY,
        MOD_RENDER_VIEW_SIDE_BY_SIDE_SUPPRESSED,
    )


@modrenderhook("MW2.EXE", ADDR_RENDER_LATCH, "call")
def renderer_hud_entry(modstate, gamemem, modgl):
    modstate.mw2_renderer_scene_suppression_ready_frame = -1
    hook_started = time.perf_counter()
    gc_started = diagnostics.gc_timing_snapshot()
    hook_count = _record_hook_call(modstate)
    snapshot = _snapshot_scene_state(modstate, gamemem, modgl)
    snapshot_ms = (time.perf_counter() - hook_started) * 1000.0

    resources = _get_hook_resources(modstate, modgl)
    render_timings = {}
    if resources is not None:
        render_started = time.perf_counter()
        palette_started = time.perf_counter()
        scene_renderer.upload_palette(
            resources,
            snapshot["palette_rgb"],
            snapshot["frame"],
        )
        render_timings["palette_ms"] = (time.perf_counter() - palette_started) * 1000.0
        hud_preload_started = time.perf_counter()
        render_timings["hud_textures_preloaded"] = hud_renderer.preload_hud_sprites(
            resources,
            snapshot.get("geometry", {}).get("texture_preload_generation"),
            snapshot.get("geometry", {}).get("hud_texture_preloads", ()),
            snapshot.get("cockpit_hud"),
            snapshot.get("menu_hud_pages_before_update", ()),
            snapshot.get("pending_menu_hud_pages", ()),
            snapshot.get("objectives_hud_before_update"),
        )
        render_timings["hud_texture_preload_ms"] = (
            time.perf_counter() - hud_preload_started
        ) * 1000.0
        try:
            scene_renderer.render_scene(
                resources,
                snapshot,
                _ground_clear_color(snapshot),
                render_timings,
            )
        except Exception as exc:
            frame = int(snapshot.get("frame", getattr(modstate, "frame", 0)))
            message = (
                f"frame={frame} scene render failed: "
                f"{type(exc).__name__}:{exc}"
            )
            print(f"MOD: MW2 {message}", flush=True)
            diagnostics.debug_log(modstate, message)
            raise
        satellite_damage_viewport = snapshot.get("camera", {}).get(
            "satellite_damage_viewport"
        )
        satellite_degraded = bool(satellite_damage_viewport)
        if satellite_degraded:
            scene_renderer.render_satellite_damage_overlay(
                resources,
                snapshot.get("cockpit_hud"),
                snapshot["palette_rgb"],
                satellite_damage_viewport,
                snapshot["render_controls"],
            )
        else:
            mfd_entity_geometry = snapshot.get("mfd_entity_geometry")
            if mfd_entity_geometry is not None:
                mfd_entity_upload_started = time.perf_counter()
                scene_renderer.upload_view_geometry(
                    resources,
                    resources.geometry_resources["entity"],
                    mfd_entity_geometry,
                )
                render_timings["mfd_entity_upload_ms"] = (
                    time.perf_counter() - mfd_entity_upload_started
                ) * 1000.0
            hud_camera_started = time.perf_counter()
            hud_renderer.render_hud_camera_views(
                resources,
                snapshot.get("cockpit_hud"),
                snapshot.get("fog_distance_world", 1.0),
                scene_background={
                    "clear_color": (
                        snapshot["ground_color"]
                        if snapshot.get("ground_visible")
                        else (0.0, 0.0, 0.0)
                    ),
                    "sky_visible": snapshot.get("sky_visible", False),
                    "sky_palette_index": snapshot.get("sky_palette_index", 0),
                    "draw_gradient": snapshot.get("draw_gradient", False),
                    "ground_palette_index": snapshot.get("ground_palette_index", 0),
                    "gradient_height": snapshot.get("gradient_height", 0),
                },
                layout_settings=snapshot["render_controls"],
            )
            render_timings["hud_camera_ms"] = (
                time.perf_counter() - hud_camera_started
            ) * 1000.0
            hud_renderer.render_hud_overlay(
                resources,
                snapshot.get("cockpit_hud"),
                snapshot["palette_rgb"],
                radar_stroke_width=snapshot["render_controls"][
                    "radar_stroke_width"
                ],
                meter_style=snapshot["render_controls"],
                camera=snapshot.get("camera"),
                layout_settings=snapshot["render_controls"],
            )
            hud_menu_pages = _menu_pages_for_handlers(
                modstate,
                snapshot.get("menu_hud_pages_before_update", ()),
                snapshot.get("pending_menu_hud_pages", ()),
                snapshot.get("active_menu_handler_ids", frozenset()),
                HUD_MENU_HANDLER_IDS,
                "mw2_renderer_last_hud_menu_pages",
            )
            hud_renderer.render_objectives_overlay(
                resources,
                snapshot.get("objectives_hud_before_update"),
                snapshot["palette_rgb"],
            )
            hud_renderer.render_menu_overlay(
                resources,
                hud_menu_pages,
                snapshot["palette_rgb"],
                snapshot["render_controls"],
            )
            diagnostics.maybe_log_menu_pages(modstate, hud_menu_pages, "hud")
        modstate.mw2_renderer_scene_frame = snapshot["frame"]
        if _geometry_vertex_count(snapshot.get("geometry", {})) > 0:
            modstate.mw2_renderer_scene_suppression_ready_frame = snapshot["frame"]
        render_timings["render_total_ms"] = (
            time.perf_counter() - render_started
        ) * 1000.0

    diagnostics.maybe_log_perf(
        modstate,
        snapshot,
        hook_count,
        snapshot_ms,
        render_timings,
        diagnostics.gc_timing_delta(gc_started),
    )


@modrenderhook("MW2.EXE", ADDR_TARGET_3D_HELPER_CALL, "call")
def renderer_target_detail_ready(modstate, gamemem, modgl):
    hook_started = time.perf_counter()
    gc_started = diagnostics.gc_timing_snapshot()
    snapshot = getattr(modstate, "mw2_renderer_frame", None)
    resources = _get_hook_resources(modstate, modgl)
    if snapshot is None or resources is None:
        return

    cockpit_hud = snapshot.get("cockpit_hud")
    if snapshot.get("camera", {}).get("satellite_damage_viewport"):
        return
    target_view = getattr(cockpit_hud, "target_view", None)
    target_id = getattr(target_view, "target_id", None)
    if target_view is None or not target_id or len(target_id) < 2:
        return

    target_kind = int(target_id[0])
    target_index = int(target_id[1])
    try:
        resolve_started = time.perf_counter()
        model_root = resolve_target_model_root(
            gamemem,
            target_kind,
            target_index,
        )
        if model_root == 0:
            return
        resolve_ms = (time.perf_counter() - resolve_started) * 1000.0
        target_view = replace(
            target_view,
            model_root=model_root,
            target_id=(*tuple(target_id[:3]), model_root),
        )

        controls = snapshot["render_controls"]
        target_cache_key = (
            target_kind,
            target_index,
            int(target_id[2]) if len(target_id) > 2 else 0,
            int(model_root),
        )
        resource_store = _mission_resource_store(modstate)
        topology_cache = None
        if controls["cache_geometry_data"]:
            topology_cache = resource_store.target_assets(target_cache_key)
        texture_cache = None
        if controls["cache_textures"]:
            texture_cache = resource_store.texture_assets
        build_wireframe = int(target_view.display_mode) == 1
        extract_started = time.perf_counter()
        flat_textured_faces = (
            int(target_view.display_mode) == 2
            and not TARGET_DISPLAY_ENHANCED_RENDERING
        )
        camera_options = {
            **target_view.camera,
            "palette_rgb": snapshot["palette_rgb"],
            **_entity_mesh_options(controls),
        }
        geometry = None
        renderer_owned_target = bool(
            target_kind == 0x0200
            and controls["entity_lod_selection"] != "native"
            and resource_store.entity_lod.state == "READY"
        )
        if renderer_owned_target:
            selected = _extract_entity_view_geometry(
                gamemem,
                cockpit_hud,
                target_view,
                resources.size,
                snapshot["palette_rgb"],
                controls,
                resource_store,
                "target",
                entity_lod_selection="detail0",
                entity_lod_hysteresis=0.0,
                entity_lod_hide_player=False,
                entity_lod_entity_index=target_index,
                entity_lod_live_component_nodes=True,
                entity_lod_build_wireframe=build_wireframe,
                entity_lod_wireframe_only=build_wireframe,
                entity_lod_flat_textured_faces=flat_textured_faces,
            )
            if _geometry_vertex_count(selected) > 0:
                geometry = selected
                geometry["stats"]["renderer_entity_lod_target_owned"] = 1
        if geometry is None:
            geometry = extract_target_geometry(
                gamemem,
                camera_options,
                model_root,
                entity_vertex_mode=controls["entity_vertex_mode"],
                topology_cache=topology_cache,
                texture_cache=texture_cache,
                build_wireframe=build_wireframe,
                wireframe_only=build_wireframe,
                flat_textured_faces=flat_textured_faces,
            )
            geometry["stats"]["renderer_entity_lod_target_owned"] = 0
        extract_ms = (time.perf_counter() - extract_started) * 1000.0
        upload_started = time.perf_counter()
        scene_renderer.upload_view_geometry(
            resources,
            resources.geometry_resources["target"],
            geometry,
        )
        upload_ms = (time.perf_counter() - upload_started) * 1000.0
        draw_started = time.perf_counter()
        hud_renderer.render_prepared_target_camera_view(
            resources,
            snapshot.get("cockpit_hud"),
            target_view,
            snapshot.get("fog_distance_world", 1.0),
            snapshot["render_controls"],
        )
        draw_ms = (time.perf_counter() - draw_started) * 1000.0
        overlay_started = time.perf_counter()
        hud_renderer.refresh_target_camera_overlay(
            resources,
            snapshot.get("cockpit_hud"),
            target_view,
            snapshot["palette_rgb"],
            snapshot["render_controls"],
        )
        overlay_ms = (time.perf_counter() - overlay_started) * 1000.0
        diagnostics.maybe_log_target_perf(
            modstate,
            target_view,
            geometry.get("stats", {}),
            {
                "resolve_ms": resolve_ms,
                "extract_ms": extract_ms,
                "upload_ms": upload_ms,
                "draw_ms": draw_ms,
                "overlay_ms": overlay_ms,
                "total_ms": (time.perf_counter() - hook_started) * 1000.0,
            },
            diagnostics.gc_timing_delta(gc_started),
        )
    except Exception as exc:
        message = (
            "target_detail_error "
            f"target={target_id} error={type(exc).__name__}:{exc}"
        )
        print(f"MOD: MW2 {message}", flush=True)
        diagnostics.debug_log(
            modstate,
            message,
            flush=True,
        )
        raise


@modrenderhook("MW2.EXE", ADDR_FRAME_SUBMIT_CALL, "call")
def renderer_menu_overlay(modstate, gamemem, modgl):
    # This hook runs after native menu updates, so the live shadow step reflects
    # slider dragging immediately rather than waiting for the next scene frame.
    modstate.mw2_renderer_monitor_brightness = _snapshot_monitor_brightness(
        gamemem
    )
    snapshot = getattr(modstate, "mw2_renderer_frame", None)
    resources = _get_hook_resources(modstate, modgl)
    if snapshot is None or resources is None:
        _set_native_scene_raster_suppression(modstate, gamemem, False)
        return
    # This hook runs after the native HUD/radar pass. Latch the exact degraded
    # window copied by the native satellite swapper for the next scene frame;
    # the earlier scene hook necessarily runs before that state is produced.
    modstate.mw2_renderer_satellite_damage_viewport = (
        snapshot_satellite_damage_viewport(gamemem)
    )
    if snapshot.get("camera", {}).get("satellite_damage_viewport"):
        compositor.clear_overlay(resources)
    else:
        cockpit_hud = snapshot.get("cockpit_hud")
        # Native HUD has now acquired the damaged-video RLE resource if this
        # was its first use. Draw requests were latched before that call so
        # their pane and current-frame timing remain faithful to the original.
        video_noise_sprites = resolve_hud_video_noise_sprites(
            gamemem,
            getattr(cockpit_hud, "panels", ()),
        )
        hud_renderer.render_hud_video_noise_overlay(
            resources,
            video_noise_sprites,
            snapshot["render_controls"],
        )
        current_menu_pages, _pending_menu_pages, active_menu_handlers = (
            snapshot_menu_hud_state(gamemem, include_pending=False)
        )
        menu_pages = _menu_pages_for_handlers(
            modstate,
            current_menu_pages,
            snapshot.get("pending_menu_hud_pages", ()),
            active_menu_handlers,
            LATE_MENU_HANDLER_IDS,
            "mw2_renderer_last_late_menu_pages",
        )
        diagnostics.maybe_log_menu_pages(modstate, menu_pages, "late")
        hud_renderer.render_menu_overlay(
            resources,
            menu_pages,
            snapshot["palette_rgb"],
            snapshot["render_controls"],
        )
        hud_renderer.render_short_messages_overlay(
            resources,
            snapshot_short_messages(gamemem),
            snapshot["palette_rgb"],
        )
    published = _publish_frame(modstate, modgl, resources, snapshot["frame"])
    ready = (
        published
        and int(
            getattr(
                modstate,
                "mw2_renderer_scene_suppression_ready_frame",
                -1,
            )
        )
        == int(snapshot["frame"])
        and _presentation_requests_native_scene_suppression(
            modgl,
            snapshot["render_controls"],
        )
    )
    _set_native_scene_raster_suppression(modstate, gamemem, ready)


def _record_hook_call(modstate):
    frame = int(getattr(modstate, "frame", 0))
    last_frame = int(getattr(modstate, "mw2_renderer_hook_frame", -1))
    if frame != last_frame:
        modstate.mw2_renderer_hook_frame = frame
        modstate.mw2_renderer_hook_count = 1
        return 1

    modstate.mw2_renderer_hook_count = int(
        getattr(modstate, "mw2_renderer_hook_count", 0)
    ) + 1
    return modstate.mw2_renderer_hook_count


@modrender("init")
def renderer_init(modstate, modgl, viewport_width, viewport_height):
    load_mod_config(modstate)
    if diagnostics.logging_enabled(modstate):
        diagnostics.install_gc_timing()
    _mission_resource_store(modstate)
    _get_resources(modstate, modgl, viewport_width, viewport_height)


@modrender("compositor")
def renderer_compositor(
    modstate,
    modgl,
    viewport_x,
    viewport_y,
    viewport_width,
    viewport_height,
    _backbuffer_fbo,
):
    composite_started = time.perf_counter()
    loading_screen = _loading_screen_presentation(
        modstate,
        composite_started,
        int(getattr(modgl, "view_mode", 0)),
    )
    if (
        getattr(modstate, "mw2_renderer_frame_pacing_loading_suspended", False)
        and getattr(modstate, "mw2_renderer_loading_handoff_at", None) is not None
        and loading_screen is None
    ):
        modstate.mw2_renderer_frame_pacing_loading_suspended = False
        modgl.set_frame_pacing_suspended(False)
    fade_progress = _outro_fade_progress(modstate, composite_started)
    monitor_brightness = getattr(
        modstate,
        "mw2_renderer_monitor_brightness",
        None,
    )
    resources = _get_resources(modstate, modgl, viewport_width, viewport_height)
    compositor.composite_to_viewport(
        resources,
        (viewport_x, viewport_y, viewport_width, viewport_height),
        fade_progress=fade_progress,
        loading_screen=loading_screen,
        monitor_brightness=monitor_brightness,
    )
    if (
        fade_progress >= 1.0
        and getattr(
            modstate,
            "mw2_renderer_outro_fade_continuous_presentation",
            False,
        )
    ):
        modstate.mw2_renderer_outro_fade_continuous_presentation = False
        modgl.set_continuous_presentation(False)
    diagnostics.maybe_log_composite_perf(
        modstate,
        (time.perf_counter() - composite_started) * 1000.0,
        (viewport_x, viewport_y, viewport_width, viewport_height),
    )


def _loading_screen_presentation(modstate, now, view_mode):
    if not getattr(modstate, "mw2_renderer_loading_active", False):
        return None

    visual = getattr(modstate, "mw2_renderer_loading_visual", None)
    fade_started_at = getattr(
        modstate,
        "mw2_renderer_loading_fade_started_at",
        None,
    )
    strip_started_at = getattr(
        modstate,
        "mw2_renderer_loading_strip_started_at",
        None,
    )
    handoff_at = getattr(
        modstate,
        "mw2_renderer_loading_handoff_at",
        None,
    )
    preload_completed_at = getattr(
        modstate,
        "mw2_renderer_loading_preload_completed_at",
        None,
    )
    if (
        handoff_at is None
        and preload_completed_at is not None
        and float(now) - float(preload_completed_at)
        >= LOADING_FIRST_FRAME_WAIT_TIMEOUT_SECONDS
    ):
        handoff_at = float(now)
        modstate.mw2_renderer_loading_handoff_at = handoff_at
        if not getattr(
            modstate,
            "mw2_renderer_loading_first_frame_timeout_logged",
            False,
        ):
            modstate.mw2_renderer_loading_first_frame_timeout_logged = True
            diagnostics.debug_log(
                modstate,
                "loading_handoff first_frame_ready=0 reason=timeout",
            )
    if strip_started_at is not None:
        brightness = 1.0
        strip_index = int(getattr(modstate, "mw2_renderer_loading_strip_index", 0))
        strip_next_at = getattr(
            modstate,
            "mw2_renderer_loading_strip_next_at",
            None,
        )
        if strip_next_at is None:
            strip_next_at = float(strip_started_at) + LOADING_STRIP_PERIOD_SECONDS
        if float(now) >= float(strip_next_at):
            # A stalled compositor may miss several native timer periods. Move
            # only one layer and discard the missed ticks so cumulative diamond
            # frames never jump several stages on one presentation.
            strip_index += 1
            modstate.mw2_renderer_loading_strip_index = strip_index
            modstate.mw2_renderer_loading_strip_next_at = (
                float(now) + LOADING_STRIP_PERIOD_SECONDS
            )
    elif fade_started_at is not None:
        brightness = min(
            1.0,
            max(
                0.0,
                (float(now) - float(fade_started_at))
                / LOADING_FADE_IN_DURATION_SECONDS,
            ),
        )
        strip_index = None
    else:
        brightness = 0.0
        strip_index = None

    if handoff_at is not None:
        if view_mode in (
            MOD_RENDER_VIEW_SIDE_BY_SIDE,
            MOD_RENDER_VIEW_SIDE_BY_SIDE_SUPPRESSED,
        ):
            # Native VGA presentation resumes as soon as the preload barrier
            # releases. Once the matching complete mod frame exists, keeping
            # only the mod side in the cosmetic hold/fade would make the
            # comparison views appear to advance at different times.
            return None
        fade_out_started_at = float(handoff_at)
        fade_out_progress = (
            float(now) - fade_out_started_at
        ) / LOADING_FADE_OUT_DURATION_SECONDS
        if fade_out_progress >= 1.0:
            return None
        if fade_out_progress > 0.0:
            brightness *= 1.0 - fade_out_progress
    return {
        "visual": visual,
        "brightness": brightness,
        "strip_index": strip_index,
    }


def _outro_fade_progress(modstate, now):
    started_at = getattr(modstate, "mw2_renderer_outro_fade_started_at", None)
    if started_at is None:
        return 0.0
    return min(
        1.0,
        max(0.0, (float(now) - float(started_at)) / OUTRO_FADE_DURATION_SECONDS),
    )
