from array import array
from pathlib import Path

import moderngl

from . import compositor, hud_renderer
from .assets import load_rgb_png as _load_rgb_png
from .font_rendering import FontRenderer, TextLayoutSlot
from .hud_atlas import hud_atlas
from .hud_layout import resolved_panel_scale, resolved_target_marker_scale
from .gl_resources import (
    SCREEN_VERTEX_FORMAT,
    DynamicGeometryResources,
    Mesh,
)
from .render_targets import RenderTarget as _HudCameraTarget
from .scene_meshes import (
    build_gradient_cylinder_vertices,
    build_screen_quad_vertices,
    build_sky_hemisphere_vertices,
)
from .scene_state import PALETTE_SIZE
from .shaders import load_program


HUD_PANEL_TEXT_SLOT_COUNT = hud_renderer.HUD_PANEL_TEXT_SLOT_COUNT
OBJECTIVE_TEXT_ROW_COUNT = hud_renderer.OBJECTIVE_TEXT_ROW_COUNT
TRANSPARENT_PALETTE_INDEX = 255.0
MODE4_EMISSIVE_C_IN_THRESHOLD = 48.0
MESSAGE_BAR_TEXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "textures"
    / "msg_bar_tex_dark.png"
)
_RENDER_RESOURCE_NAMES = (
    "target_view_target", "mfd_view_target", "satellite_damage_target",
    "radar_ellipse_vao", "radar_ellipse_buffer", "radar_ellipse_program",
    "radar_line_vao", "radar_line_buffer", "radar_line_program",
    "overlay_sprite_vao", "overlay_sprite_buffer", "overlay_sprite_program",
    "hud_atlas_vao", "hud_atlas_buffer", "hud_atlas_texture",
    "loading_palette_texture", "monitor_brightness_texture",
    "message_bar_vao", "message_bar_buffer", "message_bar_program",
    "camera_view_blit_vao", "camera_view_blit_buffer",
    "camera_view_blit_program", "message_bar_texture",
    "overlay_line_vao", "overlay_line_buffer", "overlay_line_program",
    "overlay_rect_vao", "overlay_rect_buffer", "overlay_rect_program",
    "hud_scale_vao", "hud_scale_buffer", "hud_scale_program",
    "blit_vao", "blit_buffer", "font_renderer",
    "gradient_mesh", "sky_mesh",
    "blit_program", "textured_program", "indexed_texmap_program",
    "mode4_program", "geometry_program", "indexed_geometry_program",
    "wireframe_occluder_program", "sky_program", "palette_texture",
    "scene_fbo", "scene_present_fbo", "scene_depth", "scene_texture",
    "scene_present_texture", "overlay_fbo", "overlay_texture",
    "overlay_present_fbo", "overlay_present_texture",
)

class RendererResources:
    def __init__(
        self,
        viewport_width,
        viewport_height,
        *,
        panel_scaling,
        target_marker_scaling,
        antialiasing,
        ssaa_line_width,
        max_horizontal_fov_degrees,
    ):
        self.ctx = None
        for name in _RENDER_RESOURCE_NAMES:
            setattr(self, name, None)
        self.scene_size = (0, 0)
        self.scene_sample_scale = 1
        self.requested_antialiasing = str(antialiasing)
        self.ssaa_line_width = float(ssaa_line_width)
        self.max_horizontal_fov_degrees = float(
            max_horizontal_fov_degrees
        )
        self.antialiasing = "none"
        self.ssaa_creation_failed = False
        self.antialiasing_fallback_reason = None
        self.target_view_ready = False
        self.target_view_key = None
        self.mfd_view_ready = False
        self.static_geometry_has_vertices = False
        self.hud_panel_text_slots = [
            TextLayoutSlot() for _ in range(HUD_PANEL_TEXT_SLOT_COUNT)
        ]
        self.hud_static_text_sizes = [None, None]
        self.objectives_title_text_slot = TextLayoutSlot()
        self.objectives_footer_text_slot = TextLayoutSlot()
        self.objective_row_text_slots = [
            hud_renderer._ObjectiveRowTextSlots()
            for _ in range(OBJECTIVE_TEXT_ROW_COUNT)
        ]
        self.menu_label_indent_text_slot = TextLayoutSlot()
        self.radar_range_text_slot = TextLayoutSlot()
        self.radar_bearing_text_slot = TextLayoutSlot()
        self.target_nav_text_slot = TextLayoutSlot()
        self.short_message_text_slots = [TextLayoutSlot() for _ in range(2)]
        self.menu_page_text_slots = {}
        self.radar_line_buffer_size = 0
        self.overlay_rect_buffer_size = 0
        self.overlay_rect_writer = hud_renderer._HudRectWriter()
        self.hud_atlas = None
        self.hud_panel_scaling = float(panel_scaling)
        self.hud_target_marker_scaling = float(target_marker_scaling)
        self.overlay_sprite_textures = {}
        self.hud_texture_preload_generation = None
        self.loading_sprite_textures = {}
        self.loading_visual_key = None
        self.monitor_brightness_table = None
        self.geometry_resources = {}
        self.enhanced_imaging_effect_descriptors = frozenset()
        self.indexed_textures = {}
        self.indexed_texture_cache = {}
        self.indexed_texture_preload_generation = None
        self.indexed_texture_preload_keys = set()
        self.scene_frame = None
        self.scene_render_serial = 0
        self.published_scene_serial = 0
        self.published_frame = None
        self.ctx = moderngl.create_context(require=330)
        self.size = (max(1, int(viewport_width)), max(1, int(viewport_height)))
        self.palette_texture = self.ctx.texture(
            (256, 1),
            components=3,
            data=bytes(PALETTE_SIZE),
            dtype="f1",
        )
        self.palette_texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.palette_texture.repeat_x = False
        self.palette_texture.repeat_y = False
        identity_brightness = bytes(range(64))
        self.monitor_brightness_texture = self.ctx.texture(
            (64, 1),
            components=1,
            data=array(
                "f",
                (value / 63.0 for value in identity_brightness),
            ).tobytes(),
            dtype="f4",
        )
        self.monitor_brightness_texture.filter = (
            moderngl.NEAREST,
            moderngl.NEAREST,
        )
        self.monitor_brightness_texture.repeat_x = False
        self.monitor_brightness_texture.repeat_y = False
        self.monitor_brightness_table = identity_brightness
        self.palette_upload_frame = None
        compositor.create_targets(self)
        self.target_view_target = _HudCameraTarget(
            self.ctx,
            self.scene_sample_scale,
        )
        self.mfd_view_target = _HudCameraTarget(
            self.ctx,
            self.scene_sample_scale,
        )
        self.satellite_damage_target = _HudCameraTarget(
            self.ctx,
            self.scene_sample_scale,
        )
        self.font_renderer = FontRenderer(self.ctx)
        self.overlay_line_program = load_program(self.ctx, "overlay_line")
        self.overlay_line_buffer = self.ctx.buffer(reserve=5 * 2 * 4)
        self.overlay_line_vao = self.ctx.vertex_array(
            self.overlay_line_program,
            [(self.overlay_line_buffer, "2f", "in_pos")],
        )
        self.radar_line_program = load_program(self.ctx, "radar_line")
        self.radar_line_buffer_size = 4096
        self.radar_line_buffer = self.ctx.buffer(reserve=self.radar_line_buffer_size)
        self.radar_line_vao = self.ctx.vertex_array(
            self.radar_line_program,
            [(
                self.radar_line_buffer,
                "2f 2f 2f 4f",
                "in_pos",
                "in_start",
                "in_end",
                "in_color",
            )],
        )
        self.radar_ellipse_program = load_program(self.ctx, "radar_ellipse")
        self.radar_ellipse_buffer = self.ctx.buffer(reserve=6 * 2 * 4)
        self.radar_ellipse_vao = self.ctx.vertex_array(
            self.radar_ellipse_program,
            [(self.radar_ellipse_buffer, "2f", "in_pos")],
        )
        self.overlay_rect_program = load_program(self.ctx, "overlay_rect")
        self.overlay_rect_buffer_size = 4096
        self.overlay_rect_buffer = self.ctx.buffer(
            reserve=self.overlay_rect_buffer_size
        )
        self.overlay_rect_vao = self.ctx.vertex_array(
            self.overlay_rect_program,
            [(self.overlay_rect_buffer, "2f 4f", "in_pos", "in_color")],
        )
        self.hud_scale_program = load_program(self.ctx, "hud_scale")
        self.hud_scale_program["u_palette"].value = 3
        self.hud_scale_buffer = self.ctx.buffer(
            hud_renderer.STATIC_HUD_SCALE_VERTEX_BYTES
        )
        self.hud_scale_vao = self.ctx.vertex_array(
            self.hud_scale_program,
            [(self.hud_scale_buffer, "2f 1f", "in_pos", "in_index")],
        )
        self.overlay_sprite_program = load_program(self.ctx, "overlay_sprite")
        self.overlay_sprite_program["u_sprite"].value = 2
        self.overlay_sprite_program["u_palette"].value = 3
        self.overlay_sprite_program["u_override_index"].value = -1
        self.overlay_sprite_program["u_brightness"].value = 1.0
        self.overlay_sprite_program["u_vertex_origin"].value = (0.0, 0.0)
        self.overlay_sprite_program["u_vertex_scale"].value = (1.0, 1.0)
        self.overlay_sprite_buffer = self.ctx.buffer(reserve=6 * 4 * 4)
        self.overlay_sprite_vao = self.ctx.vertex_array(
            self.overlay_sprite_program,
            [(self.overlay_sprite_buffer, "2f 2f", "in_pos", "in_uv")],
        )
        self.update_hud_atlas()
        self.loading_palette_texture = self.ctx.texture(
            (256, 1),
            components=3,
            data=bytes(PALETTE_SIZE),
            dtype="f1",
        )
        self.loading_palette_texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.loading_palette_texture.repeat_x = False
        self.loading_palette_texture.repeat_y = False
        bar_width, bar_height, bar_pixels = _load_rgb_png(MESSAGE_BAR_TEXTURE_PATH)
        self.message_bar_texture = self.ctx.texture(
            (bar_width, bar_height),
            components=3,
            data=bar_pixels,
            alignment=1,
        )
        self.message_bar_texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.message_bar_texture.repeat_x = False
        self.message_bar_texture.repeat_y = False
        self.message_bar_program = load_program(self.ctx, "message_bar")
        self.message_bar_program["u_bar"].value = 4
        self.message_bar_buffer = self.ctx.buffer(reserve=18 * 4 * 4)
        self.message_bar_vao = self.ctx.vertex_array(
            self.message_bar_program,
            [(self.message_bar_buffer, "2f 2f", "in_pos", "in_uv")],
        )
        self.camera_view_blit_program = load_program(self.ctx, "camera_view_blit")
        self.camera_view_blit_program["u_camera_view"].value = 3
        self.camera_view_blit_program["u_resolve_satellite_damage"].value = False
        self.camera_view_blit_program["u_source_logical_size"].value = (1, 1)
        self.camera_view_blit_program["u_destination_logical_size"].value = (1, 1)
        self.camera_view_blit_buffer = self.ctx.buffer(reserve=16 * 4)
        self.camera_view_blit_vao = self.ctx.vertex_array(
            self.camera_view_blit_program,
            [(self.camera_view_blit_buffer, "2f 2f", "in_pos", "in_uv")],
        )
        self.sky_program = load_program(self.ctx, "sky")
        self.sky_program["u_palette"].value = 0
        self.sky_mesh = Mesh(self.ctx, self.sky_program, build_sky_hemisphere_vertices())
        self.gradient_mesh = Mesh(self.ctx, self.sky_program, build_gradient_cylinder_vertices())
        self.geometry_program = load_program(self.ctx, "geometry")
        self.geometry_program["u_palette"].value = 0
        self.geometry_program["u_point_size"].value = 1.0
        self.indexed_geometry_program = load_program(self.ctx, "indexed_geometry")
        self.indexed_geometry_program["u_palette"].value = 0
        self.indexed_geometry_program["u_primitive_palette"].value = 2
        self.wireframe_occluder_program = load_program(
            self.ctx,
            "wireframe_occluder",
        )
        self.wireframe_occluder_program["u_palette"].value = 0
        self.wireframe_occluder_program["u_palette_index"].value = 0.0
        self.mode4_program = load_program(
            self.ctx,
            "mode4",
            {
                "MODE4_EMISSIVE_C_IN_THRESHOLD": (
                    MODE4_EMISSIVE_C_IN_THRESHOLD
                )
            },
        )
        self.mode4_program["u_palette"].value = 0
        self.textured_program = load_program(
            self.ctx,
            "textured",
            {"TRANSPARENT_PALETTE_INDEX": TRANSPARENT_PALETTE_INDEX},
        )
        self.textured_program["u_palette"].value = 0
        self.textured_program["u_indexed_texture"].value = 1
        self.textured_program["u_target_lighting_enabled"].value = 0
        self.indexed_texmap_program = load_program(self.ctx, "indexed_texmap")
        self.indexed_texmap_program["u_palette"].value = 0
        self.indexed_texmap_program["u_indexed_texture"].value = 1
        self.indexed_texmap_program["u_primitive_contribution"].value = 2
        self.indexed_texmap_program["u_remap_kind"].value = 0
        self.indexed_texmap_program["u_dark_ratio"].value = (0.0, 0.0, 0.0)
        self.indexed_texmap_program["u_fog_color"].value = (0.0, 0.0, 0.0)
        self.indexed_texmap_program["u_s8_ratio"].value = (0.0, 0.0, 0.0)
        self.indexed_texmap_program["u_uv_scale"].value = (1.0, 1.0)
        self.indexed_texmap_program["u_texture_role"].value = 0
        self.indexed_texmap_program["u_texture_size"].value = (1, 1)
        self.indexed_texmap_program["u_target_lighting_enabled"].value = 0
        self.indexed_texmap_program["u_rotor_enhanced"].value = 0
        self.indexed_texmap_program["u_rotor_texture_size"].value = (1, 1)
        self.blit_program = load_program(self.ctx, "blit")
        self.blit_program["u_scene"].value = 0
        self.blit_program["u_overlay"].value = 1
        self.blit_program["u_monitor_brightness"].value = 4
        self.blit_program["u_fade_progress"].value = 0.0
        self.geometry_resources = {
            name: DynamicGeometryResources(
                self.ctx,
                self.geometry_program,
                self.indexed_geometry_program,
                self.wireframe_occluder_program,
                self.mode4_program,
                self.textured_program,
                self.indexed_texmap_program,
            )
            for name in (
                "static",
                "scene",
                "entity",
                "cockpit",
                "view_excluded",
                "target",
            )
        }
        self.blit_buffer = self.ctx.buffer(build_screen_quad_vertices().tobytes())
        self.blit_vao = self.ctx.vertex_array(
            self.blit_program,
            [(self.blit_buffer, SCREEN_VERTEX_FORMAT, "in_pos")],
        )
        self.geometry_upload_frame = None
        self.static_geometry_signature = None
        self.geometry_wireframe_build = False

    def _release_hud_atlas_resources(self):
        self._release_resources(
            "hud_atlas_vao",
            "hud_atlas_buffer",
            "hud_atlas_texture",
        )

    def _release_resources(self, *names):
        for name in names:
            resource = getattr(self, name, None)
            if resource is not None:
                resource.release()
                setattr(self, name, None)

    def update_hud_atlas(self):
        panel_scale = resolved_panel_scale(
            self.size[1],
            self.hud_panel_scaling,
        )
        target_marker_scale = resolved_target_marker_scale(
            self.size[1],
            self.hud_target_marker_scaling,
        )
        atlas = hud_atlas(panel_scale, target_marker_scale)
        if atlas is self.hud_atlas and self.hud_atlas_texture is not None:
            return False
        self._release_hud_atlas_resources()
        self.hud_atlas = atlas
        self.hud_atlas_texture = self.ctx.texture(
            (atlas.width, atlas.height),
            components=2,
            data=atlas.indexed_alpha,
            alignment=1,
        )
        self.hud_atlas_texture.filter = (
            moderngl.NEAREST,
            moderngl.NEAREST,
        )
        self.hud_atlas_texture.repeat_x = False
        self.hud_atlas_texture.repeat_y = False
        self.hud_atlas_buffer = self.ctx.buffer(atlas.vertex_bytes)
        self.hud_atlas_vao = self.ctx.vertex_array(
            self.overlay_sprite_program,
            [(self.hud_atlas_buffer, "2f 2f", "in_pos", "in_uv")],
        )
        return True

    def release(self):
        for texture in self.loading_sprite_textures.values():
            texture.release()
        self.loading_sprite_textures = {}
        for texture in self.overlay_sprite_textures.values():
            texture.release()
        self.overlay_sprite_textures = {}
        self.hud_texture_preload_generation = None
        self.hud_atlas = None
        self.hud_panel_text_slots.clear()
        self.objective_row_text_slots.clear()
        self.menu_page_text_slots.clear()
        self.objectives_title_text_slot = None
        self.objectives_footer_text_slot = None
        self.menu_label_indent_text_slot = None
        self.radar_range_text_slot = None
        self.radar_bearing_text_slot = None
        self.target_nav_text_slot = None
        self.short_message_text_slots.clear()
        for geometry_resources in self.geometry_resources.values():
            geometry_resources.release()
        self.geometry_resources.clear()
        for cached in self.indexed_texture_cache.values():
            texture = cached.get("texture")
            if texture is not None:
                texture.release()
        self.indexed_textures = {}
        self.indexed_texture_cache = {}
        self.indexed_texture_preload_generation = None
        self.indexed_texture_preload_keys = set()
        self._release_resources(*_RENDER_RESOURCE_NAMES)
        self.overlay_rect_writer = None
        self.ctx = None

    def __del__(self):
        self.release()
