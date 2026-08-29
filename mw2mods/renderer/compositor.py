from array import array

import moderngl

from . import scene_renderer


def create_targets(resources):
    _create_scene_target(resources)
    _create_overlay_target(resources)


def resize(resources, viewport_width, viewport_height):
    new_size = (max(1, int(viewport_width)), max(1, int(viewport_height)))
    if resources.size == new_size:
        return
    resources.size = new_size
    create_targets(resources)


def _create_scene_target(resources):
    _release_scene_target(resources)
    use_ssaa = (
        resources.requested_antialiasing == "ssaa_4x"
        and not resources.ssaa_creation_failed
    )
    if use_ssaa:
        _set_scene_sample_scale(resources, 2, "ssaa_4x")
        try:
            _allocate_scene_target(resources)
        except Exception as error:
            fallback_from_ssaa(resources, error)
            return
    else:
        _set_scene_sample_scale(resources, 1, "none")
        _allocate_scene_target(resources)

    _reset_scene_target_state(resources)


def fallback_from_ssaa(resources, error):
    if resources.scene_sample_scale <= 1:
        return False
    _release_scene_target(resources)
    resources.ssaa_creation_failed = True
    resources.antialiasing_fallback_reason = type(error).__name__
    _set_scene_sample_scale(resources, 1, "none")
    print(
        "MOD: MW2 renderer 4x SSAA target creation failed; "
        "falling back to none "
        f"error={type(error).__name__}:{error}",
        flush=True,
    )
    _allocate_scene_target(resources)
    _reset_scene_target_state(resources)
    return True


def _reset_scene_target_state(resources):
    resources.scene_frame = None
    resources.scene_render_serial = 0
    resources.published_scene_serial = 0
    resources.published_frame = None
    scene_renderer._clear_scene_to_color(resources, (0.0, 0.0, 0.0))
    scene_renderer._clear_scene_target(
        resources,
        resources.scene_present_fbo,
        (0.0, 0.0, 0.0),
        resources.scene_size,
    )


def _release_scene_target(resources):
    if resources.scene_fbo is not None:
        resources.scene_fbo.release()
        resources.scene_fbo = None
    if resources.scene_present_fbo is not None:
        resources.scene_present_fbo.release()
        resources.scene_present_fbo = None
    if resources.scene_depth is not None:
        resources.scene_depth.release()
        resources.scene_depth = None
    if resources.scene_texture is not None:
        resources.scene_texture.release()
        resources.scene_texture = None
    if resources.scene_present_texture is not None:
        resources.scene_present_texture.release()
        resources.scene_present_texture = None


def _set_scene_sample_scale(resources, sample_scale, antialiasing):
    resources.scene_sample_scale = max(1, int(sample_scale))
    resources.antialiasing = str(antialiasing)
    resources.scene_size = (
        resources.size[0] * resources.scene_sample_scale,
        resources.size[1] * resources.scene_sample_scale,
    )
    for target_name in (
        "target_view_target",
        "mfd_view_target",
        "satellite_damage_target",
    ):
        target = getattr(resources, target_name, None)
        if target is not None:
            target.set_sample_scale(resources.scene_sample_scale)


def _allocate_scene_target(resources):
    resources.scene_texture = resources.ctx.texture(
        resources.scene_size,
        components=4,
        dtype="f1",
    )
    texture_filter = (
        moderngl.LINEAR
        if resources.scene_sample_scale > 1
        else moderngl.NEAREST
    )
    resources.scene_texture.filter = (texture_filter, texture_filter)
    resources.scene_texture.repeat_x = False
    resources.scene_texture.repeat_y = False
    resources.scene_present_texture = resources.ctx.texture(
        resources.scene_size,
        components=4,
        dtype="f1",
    )
    resources.scene_present_texture.filter = (
        texture_filter,
        texture_filter,
    )
    resources.scene_present_texture.repeat_x = False
    resources.scene_present_texture.repeat_y = False
    resources.scene_depth = resources.ctx.depth_renderbuffer(
        resources.scene_size
    )
    resources.scene_fbo = resources.ctx.framebuffer(
        color_attachments=[resources.scene_texture],
        depth_attachment=resources.scene_depth,
    )
    resources.scene_present_fbo = resources.ctx.framebuffer(
        color_attachments=[resources.scene_present_texture],
        depth_attachment=resources.scene_depth,
    )


def _create_overlay_target(resources):
    if resources.overlay_fbo is not None:
        resources.overlay_fbo.release()
        resources.overlay_fbo = None
    if resources.overlay_texture is not None:
        resources.overlay_texture.release()
        resources.overlay_texture = None
    if resources.overlay_present_fbo is not None:
        resources.overlay_present_fbo.release()
        resources.overlay_present_fbo = None
    if resources.overlay_present_texture is not None:
        resources.overlay_present_texture.release()
        resources.overlay_present_texture = None
    resources.overlay_texture = resources.ctx.texture(
        resources.size,
        components=4,
        dtype="f1",
    )
    resources.overlay_texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
    resources.overlay_texture.repeat_x = False
    resources.overlay_texture.repeat_y = False
    resources.overlay_fbo = resources.ctx.framebuffer(
        color_attachments=[resources.overlay_texture],
    )
    resources.overlay_present_texture = resources.ctx.texture(
        resources.size,
        components=4,
        dtype="f1",
    )
    resources.overlay_present_texture.filter = (
        moderngl.LINEAR,
        moderngl.LINEAR,
    )
    resources.overlay_present_texture.repeat_x = False
    resources.overlay_present_texture.repeat_y = False
    resources.overlay_present_fbo = resources.ctx.framebuffer(
        color_attachments=[resources.overlay_present_texture],
    )
    clear_overlay_target(resources)
    _clear_overlay_target(resources, resources.overlay_present_fbo)


def clear_overlay_target(resources):
    _clear_overlay_target(resources, resources.overlay_fbo)


def clear_overlay(resources):
    clear_overlay_target(resources)
    resources.ctx.screen.use()
    resources.ctx.enable_only(moderngl.NOTHING)


def _clear_overlay_target(resources, framebuffer):
    framebuffer.use()
    framebuffer.viewport = (0, 0, resources.size[0], resources.size[1])
    resources.ctx.enable_only(moderngl.NOTHING)
    framebuffer.clear(0.0, 0.0, 0.0, 0.0)


def publish_frame(resources, frame):
    if resources.scene_render_serial == resources.published_scene_serial:
        return False

    resources.scene_texture, resources.scene_present_texture = (
        resources.scene_present_texture,
        resources.scene_texture,
    )
    resources.scene_fbo, resources.scene_present_fbo = (
        resources.scene_present_fbo,
        resources.scene_fbo,
    )
    resources.overlay_texture, resources.overlay_present_texture = (
        resources.overlay_present_texture,
        resources.overlay_texture,
    )
    resources.overlay_fbo, resources.overlay_present_fbo = (
        resources.overlay_present_fbo,
        resources.overlay_fbo,
    )
    resources.published_scene_serial = resources.scene_render_serial
    resources.published_frame = int(frame)
    return True


def _loading_sprite_texture(resources, sprite):
    texture = resources.loading_sprite_textures.get(sprite.cache_key)
    if texture is not None:
        return texture
    texture = resources.ctx.texture(
        (sprite.width, sprite.height),
        components=2,
        data=sprite.indexed_alpha,
        alignment=1,
    )
    texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
    texture.repeat_x = False
    texture.repeat_y = False
    resources.loading_sprite_textures[sprite.cache_key] = texture
    return texture


def _prepare_loading_visual(resources, visual):
    if resources.loading_visual_key == visual.cache_key:
        return
    for texture in resources.loading_sprite_textures.values():
        texture.release()
    resources.loading_sprite_textures = {}
    resources.loading_palette_texture.write(visual.palette_rgb)
    resources.loading_visual_key = visual.cache_key


def _draw_loading_sprite(resources, sprite, x, y):
    if sprite is None:
        return
    x0 = float(x + sprite.x_offset)
    y0 = float(y + sprite.y_offset)
    x1 = x0 + float(sprite.width)
    y1 = y0 + float(sprite.height)
    vertices = array(
        "f",
        (
            x0, y0, 0.0, 0.0,
            x1, y0, 1.0, 0.0,
            x0, y1, 0.0, 1.0,
            x0, y1, 0.0, 1.0,
            x1, y0, 1.0, 0.0,
            x1, y1, 1.0, 1.0,
        ),
    )
    resources.overlay_sprite_buffer.write(vertices.tobytes())
    _loading_sprite_texture(resources, sprite).use(location=2)
    resources.overlay_sprite_vao.render(mode=moderngl.TRIANGLES)


def composite_to_viewport(
    resources,
    viewport,
    fade_progress=0.0,
    loading_screen=None,
    monitor_brightness=None,
):
    x, y, width, height = [int(value) for value in viewport]
    if width <= 0 or height <= 0:
        return

    if loading_screen is not None:
        _composite_loading_screen(resources, viewport, loading_screen)
        return

    resources.ctx.screen.use()
    resources.ctx.screen.viewport = (x, y, width, height)
    resources.ctx.enable_only(moderngl.NOTHING)
    _update_monitor_brightness(resources, monitor_brightness)
    resources.blit_program["u_fade_progress"].value = min(
        1.0, max(0.0, float(fade_progress))
    )
    resources.scene_present_texture.use(location=0)
    resources.overlay_present_texture.use(location=1)
    resources.monitor_brightness_texture.use(location=4)
    resources.blit_vao.render(mode=moderngl.TRIANGLE_STRIP)


def _update_monitor_brightness(resources, monitor_brightness):
    table = (
        monitor_brightness.get("table")
        if isinstance(monitor_brightness, dict)
        else None
    )
    table = bytes(table) if table is not None else bytes(range(64))
    if len(table) != 64:
        table = bytes(range(64))
    if table == resources.monitor_brightness_table:
        return
    # Preserve the native 64-sample response while keeping enhanced output
    # continuous: the compositor shader interpolates these float samples per
    # channel instead of quantizing the composed image back to 6-bit DAC.
    samples = array("f", (value / 63.0 for value in table))
    resources.monitor_brightness_texture.write(samples.tobytes())
    resources.monitor_brightness_table = table


def _composite_loading_screen(resources, viewport, loading_screen):
    x, y, width, height = [int(value) for value in viewport]
    resources.ctx.screen.use()
    resources.ctx.screen.viewport = (x, y, width, height)
    resources.ctx.enable_only(moderngl.NOTHING)
    resources.ctx.scissor = (x, y, width, height)
    try:
        resources.ctx.clear(0.0, 0.0, 0.0, 1.0)
    finally:
        resources.ctx.scissor = None

    visual = loading_screen.get("visual")
    if visual is None:
        return
    _prepare_loading_visual(resources, visual)
    art_origin_x = (float(width) - 1024.0) * 0.5
    art_origin_y = (float(height) - 768.0) * 0.5
    resources.overlay_sprite_program["u_viewport_size"].value = (
        float(width),
        float(height),
    )
    resources.overlay_sprite_program["u_override_index"].value = -1
    resources.overlay_sprite_program["u_brightness"].value = min(
        1.0,
        max(0.0, float(loading_screen.get("brightness", 0.0))),
    )
    resources.loading_palette_texture.use(location=3)
    _draw_loading_sprite(
        resources,
        visual.background,
        art_origin_x + visual.clip_x,
        art_origin_y + visual.clip_y,
    )
    strip_index = loading_screen.get("strip_index")
    if visual.strips and strip_index is not None:
        _draw_loading_sprite(
            resources,
            visual.strips[int(strip_index) % len(visual.strips)],
            art_origin_x + visual.clip_x + visual.strip_x,
            art_origin_y + visual.clip_y + visual.strip_y,
        )
