import time
from array import array

import moderngl

from .gl_resources import (
    INDEXED_TEXMAP_VERTEX_FLOATS,
    INDEXED_TEXMAP_VERTEX_FORMAT,
    DynamicBillboardMesh,
    DynamicIndexedMesh,
    mesh_dict_has_vertices as _mesh_dict_has_vertices,
)
from .projection import (
    PROJECTION_FAR,
    PROJECTION_FOCAL_LENGTH_PIXELS,
    PROJECTION_NEAR,
    positive_z_orthographic_projection,
    positive_z_pane_projection,
    positive_z_projection,
    perspective_projection_info,
)
from .scene_meshes import SKY_RADIUS
from .scene_state import PALETTE_SIZE, palette_index_to_u
from .texture_catalog import is_aero_lift_fan_cel_name


# The stock damaged-satellite transition interpolates between reduced windows.
# At the renderer's 1024x768 reference output its largest endpoint is 320x240
# (320x200 in the 320x200 presentation). Higher-resolution outputs scale this
# reference with viewport height and adjust its width to the output aspect.
SATELLITE_DAMAGE_TARGET_SIZE = (320, 240)
NATIVE_RENDER_HEIGHT = 768.0
WORLD_PARTITIONS = ("scene", "view_excluded", "entity")


def _partition_resources(resources, name):
    return resources.geometry_resources[str(name)]


def upload_palette(resources, palette_rgb, frame):
    if len(palette_rgb) != PALETTE_SIZE:
        raise ValueError("expected 768 bytes of RGB palette data")
    if resources.palette_upload_frame == frame:
        return

    resources.palette_texture.write(palette_rgb)
    resources.palette_upload_frame = frame


def upload_geometry(
    resources,
    geometry,
    frame,
    freeze_after_first=False,
    build_wireframe=False,
):
    if resources.geometry_upload_frame == frame:
        return "same_frame"
    if (
        freeze_after_first
        and (
            resources.static_geometry_has_vertices
            or any(
                _partition_resources(resources, name).dynamic_geometry_has_vertices
                for name in geometry.get("partitions", {})
            )
        )
    ):
        return "frozen_after_first"

    sync_indexed_texture_preloads(
        resources,
        geometry.get("texture_preload_generation"),
        geometry.get("texture_preloads", ()),
    )
    _upload_indexed_textures(resources, geometry["textures"])
    active_texture_descs = frozenset(
        int(desc_idx) for desc_idx in geometry["textures"]
    )
    for name in ("static", *geometry.get("partitions", {})):
        _partition_resources(resources, name).active_texture_descs = (
            active_texture_descs
        )
    resources.enhanced_imaging_effect_descriptors = frozenset(
        int(desc_idx)
        for desc_idx in geometry.get(
            "enhanced_imaging_effect_descriptors",
            (),
        )
    )

    static = geometry["static"]
    static_signature = tuple(geometry["static_signature"] or ())
    build_wireframe = bool(build_wireframe)
    wireframe_build_changed = (
        build_wireframe != resources.geometry_wireframe_build
    )
    if (
        static_signature != resources.static_geometry_signature
        or wireframe_build_changed
    ):
        _upload_geometry_partition(_partition_resources(resources, "static"), static)
        resources.static_geometry_signature = static_signature
    resources.static_geometry_has_vertices = _partition_resources(
        resources,
        "static",
    ).dynamic_geometry_has_vertices

    for name, partition in geometry.get("partitions", {}).items():
        _upload_geometry_partition(
            _partition_resources(resources, name),
            partition,
        )
    resources.geometry_wireframe_build = build_wireframe
    resources.geometry_upload_frame = frame
    return "uploaded"


def _upload_geometry_partition(resources, partition):
    resources.dynamic_triangle_mesh.update(partition.vertices)
    resources.dynamic_indexed_triangle_mesh.update(
        partition.indexed_vertices,
        partition.indexed_indices,
        partition.indexed_primitive_palette,
    )
    resources.dynamic_mode4_mesh.update(partition.mode4_vertices)
    resources.dynamic_point_mesh.update(partition.point_vertices)
    resources.dynamic_indexed_wireframe_mesh.update(
        partition.wireframe_indexed_vertices,
        partition.wireframe_occluder_indices,
        partition.wireframe_line_indices,
        partition.wireframe_line_palette,
    )
    resources.dynamic_line_mesh.update(partition.line_vertices)
    _sync_billboard_meshes(
        resources,
        resources.dynamic_billboard_meshes,
        partition.billboard_instances,
    )
    if len(partition.indexed_texmap_shared_vertices):
        _sync_indexed_texmap_meshes(
            resources,
            resources.grouped_indexed_texmap_meshes,
            {},
            {},
            {},
        )
        resources.dynamic_indexed_texmap_mesh_set.update(
            partition.indexed_texmap_shared_vertices,
            partition.indexed_texmap_indices,
            partition.indexed_texmap_primitive_lighting,
        )
    else:
        resources.dynamic_indexed_texmap_mesh_set.update((), {}, {})
        _sync_indexed_texmap_meshes(
            resources,
            resources.grouped_indexed_texmap_meshes,
            partition.indexed_texmap_vertices,
            partition.indexed_texmap_indices,
            partition.indexed_texmap_primitive_lighting,
        )
    _sync_rotor_meshes(resources, partition.rotor_batches)
    resources.dynamic_geometry_has_vertices = _geometry_partition_has_vertices(resources)


def _geometry_partition_has_vertices(resources):
    return (
        resources.dynamic_triangle_mesh.vertex_count > 0
        or resources.dynamic_indexed_triangle_mesh.index_count > 0
        or resources.dynamic_mode4_mesh.vertex_count > 0
        or resources.dynamic_point_mesh.vertex_count > 0
        or resources.dynamic_indexed_wireframe_mesh.vertex_count > 0
        or resources.dynamic_line_mesh.vertex_count > 0
        or _mesh_dict_has_vertices(resources.dynamic_billboard_meshes)
        or _mesh_dict_has_vertices(resources.dynamic_indexed_texmap_meshes)
        or _mesh_dict_has_vertices(resources.grouped_indexed_texmap_meshes)
        or any(mesh.vertex_count > 0 for mesh in resources.dynamic_rotor_meshes)
    )


def upload_view_geometry(
    resources,
    target,
    geometry,
):
    _upload_indexed_textures(resources, geometry["textures"])
    target.active_texture_descs = frozenset(
        int(desc_idx) for desc_idx in geometry["textures"]
    )
    _upload_geometry_partition(
        target,
        geometry["partitions"][geometry["primary_partition"]],
    )


def _sync_billboard_meshes(resources, meshes, grouped_instances):
    active_descs = set()
    for desc_idx, instances in grouped_instances.items():
        desc_idx = int(desc_idx)
        active_descs.add(desc_idx)
        mesh = meshes.get(desc_idx)
        if mesh is None:
            mesh = DynamicBillboardMesh(resources.ctx, resources.textured_program)
            meshes[desc_idx] = mesh
        mesh.update(instances)

    for desc_idx in list(meshes.keys()):
        if desc_idx in active_descs:
            continue
        meshes[desc_idx].release()
        del meshes[desc_idx]


def _sync_indexed_texmap_meshes(
    resources,
    meshes,
    grouped_vertices,
    grouped_indices,
    grouped_primitive_lighting,
):
    active_descs = set()
    for desc_idx, indices in grouped_indices.items():
        desc_idx = int(desc_idx)
        active_descs.add(desc_idx)
        mesh = meshes.get(desc_idx)
        if mesh is None:
            mesh = DynamicIndexedMesh(
                resources.ctx,
                resources.indexed_texmap_program,
                "u_primitive_lighting",
                attributes=("in_uv",),
                vertex_format=INDEXED_TEXMAP_VERTEX_FORMAT,
                vertex_floats=INDEXED_TEXMAP_VERTEX_FLOATS,
            )
            meshes[desc_idx] = mesh
        mesh.update(
            grouped_vertices.get(desc_idx, array("f")),
            indices,
            grouped_primitive_lighting.get(desc_idx, array("f")),
        )

    for desc_idx in list(meshes.keys()):
        if desc_idx in active_descs:
            continue
        meshes[desc_idx].release()
        del meshes[desc_idx]


def _sync_rotor_meshes(resources, batches):
    meshes = resources.dynamic_rotor_meshes
    while len(meshes) < len(batches):
        meshes.append(
            DynamicIndexedMesh(
                resources.ctx,
                resources.rotor_program,
                "u_primitive_lighting",
                attributes=("in_uv",),
                vertex_format=INDEXED_TEXMAP_VERTEX_FORMAT,
                vertex_floats=INDEXED_TEXMAP_VERTEX_FLOATS,
            )
        )
    for batch_index, batch in enumerate(batches):
        mesh = meshes[batch_index]
        mesh.rotor_effect = str(batch.get("effect") or "heli_rotor")
        mesh.rotor_desc_idx = int(batch["desc_idx"])
        mesh.rotor_center = tuple(float(value) for value in batch["center"])
        mesh.rotor_normalized_uv = bool(batch.get("normalized_uv", False))
        mesh.update(
            batch["vertices"],
            batch["indices"],
            batch["lighting"],
        )
    while len(meshes) > len(batches):
        meshes.pop().release()


def _upload_indexed_textures(resources, textures):
    for desc_idx, texture_info in textures.items():
        _upload_indexed_texture(resources, int(desc_idx), texture_info)


def sync_indexed_texture_preloads(resources, generation, preloads):
    """Synchronously populate the mission texture cache for one generation."""
    if generation is None:
        return ""
    generation = tuple(generation)
    if resources.indexed_texture_preload_generation == generation:
        return ""

    _release_all_indexed_textures(resources)
    uploaded = 0
    for asset, repeat in preloads:
        texture_info = {
            "pixels": asset.get("pixels"),
            "width": asset.get("width"),
            "height": asset.get("height"),
            "signature": asset.get("signature"),
        }
        repeat = bool(repeat)
        cache_key = _indexed_texture_cache_key(
            -1,
            texture_info,
            repeat,
        )
        _cached, created = _get_or_create_indexed_texture(
            resources,
            cache_key,
            texture_info,
            repeat,
        )
        if created:
            uploaded += 1
        resources.indexed_texture_preload_keys.add(cache_key)
    resources.indexed_texture_preload_generation = generation
    if uploaded:
        return f"textures_preloaded={uploaded}"
    return ""


def _upload_indexed_texture(resources, desc_idx, texture_info):
    pixels = texture_info.get("pixels")
    width = int(texture_info.get("width") or 0)
    height = int(texture_info.get("height") or 0)
    repeat = bool(texture_info.get("repeat", False))
    if not pixels or width <= 0 or height <= 0:
        return

    old_entry = resources.indexed_textures.get(desc_idx)
    old_cache_key = None if old_entry is None else old_entry.get("texture_cache_key")
    cache_key = _indexed_texture_cache_key(desc_idx, texture_info, repeat)
    cached, _created = _get_or_create_indexed_texture(
        resources,
        cache_key,
        texture_info,
        repeat,
    )
    texture = cached["texture"]
    texture.repeat_x = repeat
    texture.repeat_y = repeat

    resources.indexed_textures[desc_idx] = {
        "texture": texture,
        "texture_cache_key": cache_key,
        "signature": texture_info.get("signature"),
        "resource_id": int(texture_info.get("resource_id", -1)),
        "cel_name": str(texture_info.get("cel_name") or ""),
        "width": width,
        "height": height,
        "source_width": int(texture_info.get("source_width", width)),
        "source_height": int(texture_info.get("source_height", height)),
        "enhancement_role": str(texture_info.get("enhancement_role") or ""),
        "enhancement_role_id": int(texture_info.get("enhancement_role_id", 0)),
        "enhanced_uv_scale": float(texture_info.get("enhanced_uv_scale", 1.0)),
        "remap_kind": texture_info.get("remap_kind", "identity"),
        "remap_kind_id": int(texture_info.get("remap_kind_id", 0)),
        "dark_ratio": tuple(texture_info.get("dark_ratio", (0.0, 0.0, 0.0))),
        "fog_terminal_color": tuple(
            texture_info.get("fog_terminal_color", (0.0, 0.0, 0.0))
        ),
        "s8_ratio": tuple(texture_info.get("s8_ratio", (0.0, 0.0, 0.0))),
        "texture_kind": texture_info.get("texture_kind", "billboard"),
        "animation_class": texture_info.get(
            "animation_class",
            "single_frame",
        ),
    }
    if old_cache_key != cache_key:
        _release_indexed_texture_if_unused(resources, old_cache_key)


def _get_or_create_indexed_texture(
    resources,
    cache_key,
    texture_info,
    repeat,
):
    cached = resources.indexed_texture_cache.get(cache_key)
    if cached is not None:
        return cached, False
    pixels = texture_info.get("pixels")
    width = int(texture_info.get("width") or 0)
    height = int(texture_info.get("height") or 0)
    if not pixels or width <= 0 or height <= 0:
        return None, False
    texture = resources.ctx.texture(
        (width, height),
        components=1,
        data=pixels,
        dtype="f1",
    )
    texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
    texture.repeat_x = bool(repeat)
    texture.repeat_y = bool(repeat)
    cached = {
        "texture": texture,
        "width": width,
        "height": height,
    }
    resources.indexed_texture_cache[cache_key] = cached
    return cached, True


def _indexed_texture_cache_key(desc_idx, texture_info, repeat):
    signature = texture_info.get("signature")
    if signature is None:
        signature = (
            int(desc_idx),
            int(texture_info.get("width") or 0),
            int(texture_info.get("height") or 0),
            texture_info.get("pixels"),
        )
    return (signature, bool(repeat))


def _release_indexed_texture_if_unused(resources, cache_key):
    if cache_key is None:
        return
    if cache_key in resources.indexed_texture_preload_keys:
        return
    for entry in resources.indexed_textures.values():
        if entry.get("texture_cache_key") == cache_key:
            return
    cached = resources.indexed_texture_cache.pop(cache_key, None)
    if cached is not None:
        texture = cached.get("texture")
        if texture is not None:
            texture.release()


def _release_all_indexed_textures(resources):
    for cached in resources.indexed_texture_cache.values():
        texture = cached.get("texture")
        if texture is not None:
            texture.release()
    resources.indexed_textures.clear()
    resources.indexed_texture_cache.clear()
    resources.indexed_texture_preload_keys.clear()


def _camera_projection(resources, camera, projection_size=None):
    if camera.get("projection_type") == "orthographic":
        projection_width, projection_height = (
            projection_size if projection_size is not None else resources.size
        )
        half_width = camera["orthographic_half_width"]
        return positive_z_orthographic_projection(
            half_width,
            half_width * float(projection_height) / float(projection_width),
            near_plane=camera.get("near_plane", PROJECTION_NEAR),
            far_plane=camera.get("far_plane", PROJECTION_FAR),
        )
    focal_length = camera.get(
        "focal_length_pixels",
        PROJECTION_FOCAL_LENGTH_PIXELS,
    )
    if projection_size is not None:
        aspect_scale = float(camera.get("projection_aspect_scale", 1.0))
        return positive_z_pane_projection(
            *projection_size,
            focal_length_pixels=focal_length,
            focal_length_pixels_y=focal_length * aspect_scale,
            flip_x=False,
            center_pixels=camera.get("projection_center_pixels"),
            near_plane=camera.get("near_plane", PROJECTION_NEAR),
            far_plane=camera.get("far_plane", PROJECTION_FAR),
        )
    maximum_hfov = resources.max_horizontal_fov_degrees
    projection_info = perspective_projection_info(
        *resources.size,
        focal_length_pixels=focal_length,
        max_horizontal_fov_degrees=maximum_hfov,
    )
    resources.main_perspective_projection_info = projection_info
    resources.effective_horizontal_fov_degrees = (
        projection_info.effective_horizontal_fov_degrees
    )
    return positive_z_projection(
        *resources.size,
        focal_length_pixels=focal_length,
        flip_x=False,
        max_horizontal_fov_degrees=maximum_hfov,
        near_plane=camera.get("near_plane", PROJECTION_NEAR),
        far_plane=camera.get("far_plane", PROJECTION_FAR),
        projection_info=projection_info,
    )


def _write_sky_uniforms(resources, camera, projection_size=None, projection=None):
    if projection is None:
        projection = _camera_projection(resources, camera, projection_size)
    resources.sky_program["u_projection"].write(projection.tobytes())
    resources.sky_program["u_camera_right"].value = camera["right"]
    resources.sky_program["u_camera_up"].value = camera["up"]
    resources.sky_program["u_camera_forward"].value = camera["forward"]


def _write_camera_uniforms(resources, camera, projection, *programs):
    projection_bytes = projection.tobytes()
    near_plane = max(0.0, float(camera.get("clip_near_plane", 0.0)))
    for program in programs:
        program["u_projection"].write(projection_bytes)
        program["u_camera_position"].value = camera["position"]
        program["u_camera_right"].value = camera["right"]
        program["u_camera_up"].value = camera["up"]
        program["u_camera_forward"].value = camera["forward"]
        program["u_near_clip_plane"].value = near_plane


def _write_fog_uniforms(resources, fog_distance_world):
    fog_distance = float(fog_distance_world)
    resources.mode4_program["u_fog_distance"].value = fog_distance
    for program in (
        resources.indexed_texmap_program,
        resources.camo_texmap_program,
        resources.rotor_program,
    ):
        program["u_fog_distance"].value = fog_distance


def render_scene(resources, snapshot, clear_color, timings):
    try:
        _render_scene(resources, snapshot, clear_color, timings)
    finally:
        resources.ctx.screen.use()
        resources.ctx.enable_only(moderngl.NOTHING)


def _clear_scene_to_color(resources, clear_color):
    _clear_scene_target(
        resources,
        resources.scene_fbo,
        clear_color,
        resources.scene_size,
    )


def _clear_scene_target(resources, framebuffer, clear_color, render_size=None):
    render_size = render_size or resources.scene_size
    framebuffer.use()
    framebuffer.viewport = (0, 0, render_size[0], render_size[1])
    resources.ctx.enable_only(moderngl.NOTHING)
    resources.ctx.depth_mask = True
    resources.ctx.polygon_offset = (0.0, 0.0)
    framebuffer.clear(
        float(clear_color[0]),
        float(clear_color[1]),
        float(clear_color[2]),
        1.0,
        depth=1.0,
    )


def _ensure_scene_render_target(resources, target, size):
    try:
        target.ensure_size(size)
    except Exception as error:
        from . import compositor

        if not compositor.fallback_from_ssaa(resources, error):
            raise
        target.ensure_size(size)


def _render_scene(resources, snapshot, clear_color, timings):
    started = time.perf_counter()
    damage_viewport = snapshot.get("camera", {}).get(
        "satellite_damage_viewport"
    )
    damage_render_size = None
    damage_projection_size = None
    render_framebuffer = resources.scene_fbo
    if damage_viewport is not None:
        damage_projection_size = _satellite_damage_render_size(resources,
            damage_viewport
        )
        _ensure_scene_render_target(
            resources,
            resources.satellite_damage_target,
            _satellite_damage_target_size(resources)
        )
        damage_render_size = (
            resources.satellite_damage_target.render_size_for(
                damage_projection_size
            )
        )
        render_framebuffer = resources.satellite_damage_target.fbo

    clear_started = time.perf_counter()
    _clear_scene_target(resources,
        render_framebuffer,
        clear_color,
        render_size=damage_render_size,
    )
    timings["clear_ms"] = (time.perf_counter() - clear_started) * 1000.0
    resources.scene_frame = None
    if not snapshot:
        timings["scene_total_ms"] = (time.perf_counter() - started) * 1000.0
        return

    controls = snapshot["render_controls"]
    imaging_active = int(snapshot.get("imaging_active", 0) or 0)
    if (
        snapshot.get("sky_visible")
        and imaging_active == 0
        and not snapshot["camera"].get("satellite_view", False)
    ):
        sky_started = time.perf_counter()
        _draw_sky_to_scene(resources,
            snapshot["camera"],
            snapshot["sky_palette_index"],
            snapshot["draw_gradient"],
            snapshot["ground_palette_index"],
            snapshot["gradient_height"],
        )
        timings["sky_ms"] = (time.perf_counter() - sky_started) * 1000.0

    if controls["disable_geometry_upload"]:
        timings["geometry_upload_action"] = "disabled"
    else:
        upload_started = time.perf_counter()
        timings["geometry_upload_action"] = upload_geometry(resources,
            snapshot["geometry"],
            snapshot["frame"],
            freeze_after_first=controls[
                "freeze_geometry_upload_after_first"
            ],
            build_wireframe=(
                imaging_active != 0
                or bool(snapshot["camera"].get("satellite_view", False))
            ),
        )
        timings["geometry_upload_ms"] = (
            time.perf_counter() - upload_started
        ) * 1000.0
    if controls["disable_geometry_draw"]:
        timings["geometry_draw_action"] = "disabled"
    else:
        draw_started = time.perf_counter()
        draw_args = {
            "imaging_active": imaging_active,
            "wireframe_fade": snapshot.get(
                "enhanced_imaging_wireframe_fade"
            ),
            "framebuffer": render_framebuffer,
            "render_size": damage_render_size,
            "projection_size": damage_projection_size,
        }
        drew_geometry = _draw_geometry_to_scene(
            resources,
            snapshot["camera"],
            snapshot.get("fog_distance_world", 1.0),
            dynamic_resources=tuple(
                _partition_resources(resources, name)
                for name in WORLD_PARTITIONS
            ),
            **draw_args,
        )
        cockpit_camera = {
            **snapshot["camera"],
            "near_plane": PROJECTION_NEAR,
            "clip_near_plane": 0.0,
        }
        drew_geometry = _draw_geometry_to_scene(
            resources,
            cockpit_camera,
            snapshot.get("fog_distance_world", 1.0),
            draw_static=False,
            dynamic_resources=(
                _partition_resources(resources, "cockpit"),
            ),
            **draw_args,
        ) or drew_geometry
        timings["geometry_draw_ms"] = (time.perf_counter() - draw_started) * 1000.0
        timings["geometry_draw_action"] = (
            "drawn" if drew_geometry else "empty"
        )

    resources.scene_frame = int(snapshot.get("frame", 0))
    resources.scene_render_serial += 1
    timings["scene_total_ms"] = (time.perf_counter() - started) * 1000.0


def _satellite_damage_target_size(resources):
    output_width = max(1, int(resources.size[0]))
    output_height = max(1, int(resources.size[1]))
    vertical_scale = float(output_height) / NATIVE_RENDER_HEIGHT
    reference_height = SATELLITE_DAMAGE_TARGET_SIZE[1]
    target_width = max(1, int(round(
        reference_height
        * vertical_scale
        * float(output_width)
        / float(output_height)
    )))
    target_height = max(1, int(round(reference_height * vertical_scale)))
    return target_width, target_height


def _satellite_damage_render_size(resources, damage_viewport):
    left, top, right, bottom = [int(value) for value in damage_viewport]
    target_width, target_height = _satellite_damage_target_size(resources)
    native_width = max(1, right - left + 1)
    native_height = max(1, bottom - top + 1)
    maximum_width = min(
        resources.size[0],
        target_width,
        max(1, int(round(
            target_width
            * float(native_width)
            / float(SATELLITE_DAMAGE_TARGET_SIZE[0])
        ))),
    )
    maximum_height = min(
        resources.size[1],
        target_height,
        max(1, int(round(
            target_height
            * float(native_height)
            / float(SATELLITE_DAMAGE_TARGET_SIZE[1])
        ))),
    )
    output_aspect = float(resources.size[0]) / float(resources.size[1])
    width = maximum_width
    height = max(1, int(round(width / output_aspect)))
    if height > maximum_height:
        height = maximum_height
        width = max(1, int(round(height * output_aspect)))
    return width, height


def render_satellite_damage_overlay(
    resources,
    cockpit_hud,
    palette_rgb,
    damage_viewport,
    layout_settings,
):
    from . import hud_renderer

    render_size = _satellite_damage_render_size(resources, damage_viewport)
    physical_render_size = (
        resources.satellite_damage_target.render_size_for(render_size)
    )
    try:
        resources.satellite_damage_target.fbo.use()
        resources.satellite_damage_target.fbo.viewport = (
            0,
            0,
            physical_render_size[0],
            physical_render_size[1],
        )
        resources.ctx.enable_only(moderngl.NOTHING)
        if cockpit_hud is not None:
            hud_renderer._draw_radar_overlay(
                resources,
                cockpit_hud.radar,
                palette_rgb,
                physical_render_size,
                layout_settings["radar_stroke_width"],
                draw_text=False,
                layout_settings=layout_settings,
            )
        _stretch_satellite_damage_scene(resources, render_size)
    finally:
        resources.ctx.screen.use()
        resources.ctx.enable_only(moderngl.NOTHING)


def _stretch_satellite_damage_scene(resources, render_size):
    width = min(resources.size[0], max(1, int(render_size[0])))
    height = min(resources.size[1], max(1, int(render_size[1])))
    max_u = width / resources.satellite_damage_target.size[0]
    max_v = height / resources.satellite_damage_target.size[1]
    vertices = array(
        "f",
        (
            0.0, 0.0, 0.0, max_v,
            0.0, resources.scene_size[1], 0.0, 0.0,
            resources.scene_size[0], 0.0, max_u, max_v,
            resources.scene_size[0], resources.scene_size[1], max_u, 0.0,
        ),
    )
    resources.scene_fbo.use()
    resources.scene_fbo.viewport = (
        0,
        0,
        resources.scene_size[0],
        resources.scene_size[1],
    )
    resources.ctx.enable_only(moderngl.NOTHING)
    resources.camera_view_blit_program["u_viewport_size"].value = (
        resources.scene_size
    )
    resolve_ssaa = resources.scene_sample_scale > 1
    resources.camera_view_blit_program[
        "u_resolve_satellite_damage"
    ].value = resolve_ssaa
    if resolve_ssaa:
        resources.camera_view_blit_program[
            "u_source_logical_size"
        ].value = (width, height)
        resources.camera_view_blit_program[
            "u_destination_logical_size"
        ].value = resources.size
    resources.camera_view_blit_buffer.write(vertices.tobytes())
    resources.satellite_damage_target.texture.use(location=3)
    resources.ctx.disable(moderngl.BLEND)
    resources.camera_view_blit_vao.render(
        mode=moderngl.TRIANGLE_STRIP,
        vertices=4,
    )


def _draw_sky_to_scene(
    resources,
    camera,
    sky_palette_index,
    draw_gradient,
    ground_palette_index,
    gradient_height_pixels,
    framebuffer=None,
    render_size=None,
    projection_size=None,
):
    framebuffer = framebuffer or resources.scene_fbo
    render_size = render_size or resources.scene_size
    framebuffer.use()
    framebuffer.viewport = (0, 0, render_size[0], render_size[1])
    resources.ctx.enable_only(moderngl.NOTHING)
    resources.palette_texture.use(location=0)
    _write_sky_uniforms(resources, camera, projection_size)

    sky_u = palette_index_to_u(sky_palette_index)
    resources.sky_program["u_y_scale"].value = 1.0
    resources.sky_program["u_palette_start"].value = sky_u
    resources.sky_program["u_palette_end"].value = sky_u
    resources.sky_mesh.render()

    if draw_gradient:
        gradient_world_height = (
            max(0.0, float(gradient_height_pixels))
            * SKY_RADIUS
            / PROJECTION_FOCAL_LENGTH_PIXELS
        )
        if gradient_world_height <= 0.0:
            return

        resources.sky_program["u_y_scale"].value = gradient_world_height
        resources.sky_program["u_palette_start"].value = sky_u
        resources.sky_program["u_palette_end"].value = palette_index_to_u(
            int(ground_palette_index) - 1
        )
        resources.gradient_mesh.render()


def _set_scene_raster_scale(resources):
    point_raster_scale = (
        max(1.0, float(resources.size[1]) / NATIVE_RENDER_HEIGHT)
        * float(resources.scene_sample_scale)
    )
    line_raster_scale = point_raster_scale
    if resources.scene_sample_scale > 1:
        line_raster_scale *= float(resources.ssaa_line_width)
    line_width_range = resources.ctx.info.get(
        "GL_ALIASED_LINE_WIDTH_RANGE",
        (1.0, 1.0),
    )
    resources.ctx.line_width = min(
        line_raster_scale,
        float(line_width_range[1]),
    )
    resources.ctx.point_size = point_raster_scale
    resources.geometry_program["u_point_size"].value = point_raster_scale


def _draw_partition_meshes(
    resources,
    partition,
    animated_effects=None,
    double_sided=False,
):
    partition.dynamic_triangle_mesh.render()
    partition.dynamic_indexed_triangle_mesh.render()
    partition.dynamic_point_mesh.render()
    partition.dynamic_line_mesh.render()
    partition.dynamic_mode4_mesh.render()
    _draw_billboard_mesh_dict(
        resources,
        partition.dynamic_billboard_meshes,
        partition.active_texture_descs,
        animated_effects=animated_effects,
        double_sided=double_sided,
    )
    _draw_partition_texmaps(
        resources,
        partition,
        partition.active_texture_descs,
        animated_effects=animated_effects,
    )


def _draw_geometry_to_scene(
    resources,
    camera,
    fog_distance_world,
    imaging_active=0,
    wireframe_fade=None,
    framebuffer=None,
    render_size=None,
    projection_size=None,
    draw_static=True,
    depth_func="<=",
    dynamic_resources=None,
):
    if dynamic_resources is None:
        dynamic_resources = (_partition_resources(resources, "scene"),)
    elif not isinstance(dynamic_resources, (tuple, list)):
        dynamic_resources = (dynamic_resources,)
    static_resources = _partition_resources(resources, "static")
    if not any(
        partition.dynamic_geometry_has_vertices
        for partition in dynamic_resources
    ) and (
        not draw_static or not resources.static_geometry_has_vertices
    ):
        return False

    framebuffer = framebuffer or resources.scene_fbo
    render_size = render_size or resources.scene_size
    framebuffer.use()
    framebuffer.viewport = (0, 0, render_size[0], render_size[1])
    resources.ctx.enable_only(moderngl.DEPTH_TEST)
    resources.ctx.depth_func = depth_func
    resources.ctx.depth_mask = True
    resources.ctx.polygon_offset = (0.0, 0.0)
    resources.ctx.enable(moderngl.CULL_FACE)
    resources.ctx.cull_face = "back"
    resources.ctx.front_face = "ccw"
    _set_scene_raster_scale(resources)
    resources.palette_texture.use(location=0)
    projection = _camera_projection(resources, camera, projection_size)
    satellite_view = bool(camera.get("satellite_view", False))
    _write_camera_uniforms(
        resources,
        camera,
        projection,
        resources.geometry_program,
        resources.indexed_geometry_program,
        resources.wireframe_occluder_program,
        resources.mode4_program,
        resources.textured_program,
        resources.indexed_texmap_program,
        resources.camo_texmap_program,
        resources.rotor_program,
    )
    fade_start = float((wireframe_fade or {}).get("start", 0.0))
    fade_end = float((wireframe_fade or {}).get("end", 0.0))
    for program in (resources.geometry_program, resources.indexed_geometry_program):
        program["u_wireframe_fade_start"].value = fade_start
        program["u_wireframe_fade_end"].value = fade_end
    _write_fog_uniforms(resources, fog_distance_world)
    resources.textured_program["u_viewport_size"].value = tuple(
        float(value) for value in (render_size or resources.size)
    )
    resources.textured_program["u_satellite_billboard"].value = int(satellite_view)
    imaging_active = int(imaging_active or 0)
    if imaging_active in (1, 2):
        partitions = (
            ((static_resources,) if draw_static else ())
            + tuple(dynamic_resources)
        )
        if imaging_active == 1:
            resources.ctx.polygon_offset = (1.0, 1.0)
            try:
                for partition in partitions:
                    partition.dynamic_indexed_wireframe_mesh.render_occluders()
            finally:
                resources.ctx.polygon_offset = (0.0, 0.0)
        else:
            resources.ctx.enable_only(moderngl.NOTHING)
            resources.palette_texture.use(location=0)
        for index, partition in enumerate(partitions):
            partition.dynamic_indexed_wireframe_mesh.render_lines()
            partition.dynamic_line_mesh.render()
            _draw_enhanced_imaging_effects(
                resources,
                partition,
                camera,
                fog_distance_world,
                render_size,
                projection,
                depth_func,
                draw_static and index == 0,
            )
        if imaging_active == 1:
            resources.ctx.disable(moderngl.CULL_FACE)
        return True

    if draw_static:
        if satellite_view:
            # Satellite terrain intentionally uses the normal cached scene
            # materials without cockpit distance fog.
            _write_fog_uniforms(resources, 1.0e30)
        _draw_partition_meshes(
            resources,
            static_resources,
            double_sided=satellite_view,
        )

    if satellite_view:
        # Restore normal effect/object fog uniforms after the static
        # terrain pass. Satellite-specific solid debris carries its final
        # palette value and is unaffected by this shader fog state.
        _write_fog_uniforms(resources, fog_distance_world)

    for partition in dynamic_resources:
        if satellite_view:
            resources.ctx.polygon_offset = (1.0, 1.0)
            try:
                partition.dynamic_indexed_wireframe_mesh.render_occluders()
            finally:
                resources.ctx.polygon_offset = (0.0, 0.0)
        _draw_partition_meshes(
            resources,
            partition,
            animated_effects=False if satellite_view else None,
            double_sided=satellite_view,
        )
        if satellite_view:
            _draw_satellite_animated_effects(resources, partition, depth_func)
            partition.dynamic_indexed_wireframe_mesh.render_lines()
        else:
            _draw_rotor_meshes(resources, partition, camera)
    resources.ctx.disable(moderngl.CULL_FACE)
    return True


def _active_indexed_texture(resources, active_texture_descs, desc_idx):
    desc_idx = int(desc_idx)
    if desc_idx not in active_texture_descs:
        return None
    return resources.indexed_textures.get(desc_idx)


def _draw_enhanced_imaging_effects(
    resources,
    dynamic_resources,
    camera,
    fog_distance_world,
    render_size,
    projection,
    depth_func,
    draw_static,
):
    static_resources = _partition_resources(resources, "static")
    static_billboards = draw_static and _mesh_dict_has_vertices(
        static_resources.dynamic_billboard_meshes
    )
    static_texmaps = draw_static and (
        _mesh_dict_has_vertices(static_resources.dynamic_indexed_texmap_meshes)
        or _mesh_dict_has_vertices(static_resources.grouped_indexed_texmap_meshes)
    )
    dynamic_billboards = _mesh_dict_has_vertices(
        dynamic_resources.dynamic_billboard_meshes
    )
    dynamic_texmaps = _mesh_dict_has_vertices(
        dynamic_resources.dynamic_indexed_texmap_meshes
    ) or _mesh_dict_has_vertices(
        dynamic_resources.grouped_indexed_texmap_meshes
    )
    if not any((
        static_billboards,
        static_texmaps,
        dynamic_billboards,
        dynamic_texmaps,
    )):
        return

    resources.ctx.enable(moderngl.DEPTH_TEST)
    resources.ctx.depth_func = depth_func
    resources.ctx.depth_mask = True
    resources.ctx.enable(moderngl.CULL_FACE)
    resources.ctx.polygon_offset = (1.0, 1.0)
    try:
        _write_camera_uniforms(
            resources,
            camera,
            projection,
            resources.textured_program,
            resources.indexed_texmap_program,
            resources.camo_texmap_program,
        )
        _write_fog_uniforms(resources, fog_distance_world)
        resources.textured_program["u_viewport_size"].value = tuple(
            float(value) for value in render_size
        )
        resources.textured_program["u_satellite_billboard"].value = int(
            bool(camera.get("satellite_view", False))
        )
        static_descs = static_resources.active_texture_descs
        dynamic_descs = dynamic_resources.active_texture_descs
        enhanced_descs = resources.enhanced_imaging_effect_descriptors
        static_effect_descs = static_descs & enhanced_descs
        dynamic_effect_descs = dynamic_descs & enhanced_descs
        if static_billboards:
            _draw_billboard_mesh_dict(
                resources,
                static_resources.dynamic_billboard_meshes,
                static_descs,
            )
        if static_texmaps:
            _draw_partition_texmaps(
                resources,
                static_resources,
                static_effect_descs,
            )
        if dynamic_billboards:
            _draw_billboard_mesh_dict(
                resources, dynamic_resources.dynamic_billboard_meshes, dynamic_descs
            )
        if dynamic_texmaps:
            _draw_partition_texmaps(
                resources,
                dynamic_resources,
                dynamic_effect_descs,
            )
    finally:
        resources.ctx.polygon_offset = (0.0, 0.0)


def _draw_billboard_mesh_dict(
    resources,
    meshes,
    active_texture_descs,
    animated_effects=None,
    double_sided=False,
):
    if double_sided:
        resources.ctx.disable(moderngl.CULL_FACE)
    try:
        for desc_idx, mesh in meshes.items():
            entry = _active_indexed_texture(
                resources,
                active_texture_descs,
                desc_idx,
            )
            if entry is None:
                continue
            is_animated_effect = entry.get("animation_class") in (
                "animated",
                "multi_frame_static",
            )
            if (
                animated_effects is not None
                and is_animated_effect != bool(animated_effects)
            ):
                continue
            texture = entry.get("texture")
            if texture is None:
                continue
            texture.use(location=1)
            mesh.render()
    finally:
        if double_sided:
            resources.ctx.enable(moderngl.CULL_FACE)


def _draw_satellite_animated_effects(resources, dynamic_resources, depth_func):
    # Native satellite effects are painter-sorted after opaque geometry.
    # Draw animated billboards and texture layers late and double-sided;
    # transparent texels are still discarded by their normal shaders.
    resources.ctx.disable(moderngl.CULL_FACE)
    resources.ctx.disable(moderngl.DEPTH_TEST)
    resources.ctx.depth_mask = False
    try:
        _draw_billboard_mesh_dict(resources,
            dynamic_resources.dynamic_billboard_meshes,
            dynamic_resources.active_texture_descs,
            animated_effects=True,
        )
        _draw_partition_texmaps(resources,
            dynamic_resources,
            dynamic_resources.active_texture_descs,
            animated_effects=True,
        )
    finally:
        resources.ctx.depth_mask = True
        resources.ctx.enable(moderngl.DEPTH_TEST)
        resources.ctx.depth_func = depth_func
        resources.ctx.enable(moderngl.CULL_FACE)


def _draw_indexed_texmap_mesh_dict(
    resources,
    meshes,
    active_texture_descs,
    animated_effects=None,
):
    for desc_idx, mesh in meshes.items():
        entry = _active_indexed_texture(
            resources,
            active_texture_descs,
            desc_idx,
        )
        if entry is None:
            continue
        is_animated_effect = entry.get("animation_class") in (
            "animated",
            "multi_frame_static",
        )
        if (
            animated_effects is not None
            and is_animated_effect != bool(animated_effects)
        ):
            continue
        texture = entry.get("texture")
        if texture is None:
            continue
        program = _indexed_texmap_program(resources, entry)
        mesh.set_program(program)
        _write_indexed_texmap_texture(
            program,
            entry,
        )
        _write_indexed_texmap_material(
            program,
            entry,
        )
        texture.use(location=1)
        mesh.render()


def _draw_partition_texmaps(
    resources,
    geometry_resources,
    active_texture_descs,
    animated_effects=None,
):
    for meshes in (
        geometry_resources.dynamic_indexed_texmap_meshes,
        geometry_resources.grouped_indexed_texmap_meshes,
    ):
        _draw_indexed_texmap_mesh_dict(
            resources,
            meshes,
            active_texture_descs,
            animated_effects,
        )


def _draw_rotor_meshes(resources, dynamic_resources, camera):
    meshes = dynamic_resources.dynamic_rotor_meshes
    if not meshes:
        return
    enhanced_meshes = []
    fallback_meshes = []
    for mesh in meshes:
        entry = _active_indexed_texture(
            resources,
            dynamic_resources.active_texture_descs,
            mesh.rotor_desc_idx,
        )
        if entry is None:
            continue
        if (
            mesh.rotor_effect == "aero_lift_fan"
            and not is_aero_lift_fan_cel_name(entry.get("cel_name"))
        ):
            fallback_meshes.append((mesh, entry))
        else:
            enhanced_meshes.append((mesh, entry))

    for mesh, entry in fallback_meshes:
        texture = entry.get("texture")
        width = int(entry.get("width") or 0)
        height = int(entry.get("height") or 0)
        if texture is None or width <= 0 or height <= 0:
            continue
        program = _indexed_texmap_program(resources, entry)
        mesh.set_program(program)
        _write_indexed_texmap_texture(program, entry)
        _write_indexed_texmap_material(program, entry)
        texture.use(location=1)
        mesh.render()

    if not enhanced_meshes:
        return
    camera_position = tuple(float(value) for value in camera["position"])
    sorted_meshes = sorted(
        enhanced_meshes,
        key=lambda item: -sum(
            (
                float(item[0].rotor_center[axis])
                - camera_position[axis]
            ) ** 2
            for axis in range(3)
        ),
    )
    resources.ctx.enable(moderngl.BLEND)
    resources.ctx.blend_func = (
        moderngl.SRC_ALPHA,
        moderngl.ONE_MINUS_SRC_ALPHA,
    )
    resources.ctx.depth_mask = False
    program = resources.rotor_program
    try:
        for mesh, entry in sorted_meshes:
            texture = entry.get("texture")
            width = int(entry.get("width") or 0)
            height = int(entry.get("height") or 0)
            if texture is None or width <= 0 or height <= 0:
                continue
            mesh.set_program(program)
            program["u_uv_scale"].value = (
                (1.0, 1.0)
                if mesh.rotor_normalized_uv
                else (1.0 / width, 1.0 / height)
            )
            program["u_texture_size"].value = (width, height)
            _write_indexed_texmap_material(program, entry)
            texture.use(location=1)
            mesh.render()
    finally:
        resources.ctx.depth_mask = True
        resources.ctx.disable(moderngl.BLEND)


def _write_indexed_texmap_material(program, entry):
    program["u_remap_kind"].value = int(entry.get("remap_kind_id", 0))
    program["u_dark_ratio"].value = tuple(
        entry.get("dark_ratio", (0.0, 0.0, 0.0))
    )
    program["u_fog_terminal_color"].value = tuple(
        entry.get("fog_terminal_color", (0.0, 0.0, 0.0))
    )
    program["u_s8_ratio"].value = tuple(
        entry.get("s8_ratio", (0.0, 0.0, 0.0))
    )


def _indexed_texmap_program(resources, entry):
    if int(entry.get("enhancement_role_id", 0)) == 1:
        return resources.camo_texmap_program
    return resources.indexed_texmap_program


def _write_indexed_texmap_texture(program, entry):
    width = max(1, int(entry.get("width") or 1))
    height = max(1, int(entry.get("height") or 1))
    role_id = int(entry.get("enhancement_role_id", 0))
    if role_id == 1:
        program["u_texture_size"].value = (width, height)
    if role_id:
        scale = float(entry.get("enhanced_uv_scale", 1.0))
        program["u_uv_scale"].value = (
            scale / float(width),
            scale / float(height),
        )
    else:
        program["u_uv_scale"].value = (
            1.0 / float(width),
            1.0 / float(height),
        )
