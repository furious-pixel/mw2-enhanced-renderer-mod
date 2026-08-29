from __future__ import annotations

from dataclasses import dataclass


REFERENCE_WIDTH = 1024.0
REFERENCE_HEIGHT = 768.0
BASE_FONT_SIZE = 16


@dataclass(frozen=True, slots=True)
class PanelPolicy:
    horizontal: str
    vertical: str
    frame_role: str = "panel"
    canvas_center_x: bool = False


PANEL_POLICIES = {
    "weapon": PanelPolicy("right", "top"),
    "target": PanelPolicy("left", "bottom", "viewport"),
    "mfd": PanelPolicy("right", "bottom", "viewport"),
    "heat_jump": PanelPolicy("center", "bottom", canvas_center_x=True),
    "throttle": PanelPolicy("right", "bottom"),
    "throttle_alt": PanelPolicy("right", "center"),
    "masc": PanelPolicy("right", "bottom"),
    "autopilot": PanelPolicy("center", "bottom"),
    "radar": PanelPolicy("left", "top"),
    "compass": PanelPolicy("center", "top", "legacy"),
    "compass_scaled": PanelPolicy("center", "top"),
    "altimeter": PanelPolicy("left", "center", "legacy"),
    "altimeter_scaled": PanelPolicy("left", "center"),
}

VERTICAL_GROUP_INDEX = {"top": 0, "center": 1, "bottom": 2}


def _resolved_scale(vertical_scale, control):
    if vertical_scale < 1.0:
        return vertical_scale
    return 1.0 + max(0.0, min(1.0, float(control))) * (
        vertical_scale - 1.0
    )


def resolved_panel_scale(viewport_height, control):
    vertical_scale = max(1.0, float(viewport_height)) / REFERENCE_HEIGHT
    return _resolved_scale(vertical_scale, control)


def resolved_target_marker_scale(viewport_height, control):
    vertical_scale = max(1.0, float(viewport_height)) / REFERENCE_HEIGHT
    return _resolved_scale(vertical_scale, control)


def _axis_point(start, end, attachment):
    if attachment == "left" or attachment == "top":
        return float(start)
    if attachment == "right" or attachment == "bottom":
        return float(end)
    return (float(start) + float(end)) * 0.5


def _damage_sprite_scale(panel, frame, requested):
    requested = max(1, int(requested + 0.5))
    if not panel.sprites:
        return float(requested)
    entry = panel.sprites[0]
    panel_left, panel_top, panel_right, panel_bottom = panel.reference_bounds
    center_x = (panel_left + panel_right) * 0.5
    center_y = (panel_top + panel_bottom) * 0.5
    sprite_left = entry.x + entry.sprite.x_offset
    sprite_top = entry.y + entry.sprite.y_offset
    width = entry.sprite.width if entry.draw_width is None else entry.draw_width
    height = entry.sprite.height if entry.draw_height is None else entry.draw_height
    left = max(panel_left, entry.clip_rect[0], sprite_left)
    top = max(panel_top, entry.clip_rect[1], sprite_top)
    right = min(panel_right, entry.clip_rect[2], sprite_left + width)
    bottom = min(panel_bottom, entry.clip_rect[3], sprite_top + height)
    max_fit = float(requested)
    if left < center_x:
        max_fit = min(max_fit, (center_x - panel_left) * frame.scale_x / (center_x - left))
    if right > center_x:
        max_fit = min(max_fit, (panel_right - center_x) * frame.scale_x / (right - center_x))
    if top < center_y:
        max_fit = min(max_fit, (center_y - panel_top) * frame.scale_y / (center_y - top))
    if bottom > center_y:
        max_fit = min(max_fit, (panel_bottom - center_y) * frame.scale_y / (bottom - center_y))
    return float(max(1, min(requested, int(max_fit))))


@dataclass(frozen=True, slots=True)
class PanelTransform:
    origin_x: float
    origin_y: float
    scale_x: float
    scale_y: float

    def point(self, x, y):
        return (
            self.origin_x + float(x) * self.scale_x,
            self.origin_y + float(y) * self.scale_y,
        )

    def rect(self, rect):
        left, top = self.point(rect[0], rect[1])
        right, bottom = self.point(rect[2], rect[3])
        return left, top, right, bottom


class HudLayoutContext:
    __slots__ = (
        "viewport",
        "position_scale",
        "panel_scale",
        "viewport_scale",
        "font_scale",
        "target_marker_scale",
        "legacy_scale",
        "canvas_origin",
        "middle_panel_y",
        "panel_widescreen",
    )

    def __init__(self, viewport, settings):
        width, height = (max(1.0, float(value)) for value in viewport)
        vertical_scale = height / REFERENCE_HEIGHT
        self.viewport = (width, height)
        self.position_scale = _resolved_scale(
            vertical_scale,
            settings["position_scaling"],
        )
        self.panel_scale = resolved_panel_scale(
            height,
            settings["panel_scaling"],
        )
        self.viewport_scale = _resolved_scale(
            vertical_scale,
            settings["viewport_scaling"],
        )
        self.font_scale = _resolved_scale(
            vertical_scale,
            settings["font_scaling"],
        )
        self.target_marker_scale = resolved_target_marker_scale(
            height,
            settings["target_marker_scaling"],
        )
        self.legacy_scale = min(1.0, vertical_scale)
        self.canvas_origin = (
            (width - REFERENCE_WIDTH * self.position_scale) * 0.5,
            (height - REFERENCE_HEIGHT * self.position_scale) * 0.5,
        )
        self.middle_panel_y = (
            REFERENCE_HEIGHT * settings["middle_panel_vertical_position"]
        )
        self.panel_widescreen = (
            settings["top_panel_widescreen_position"],
            settings["middle_panel_widescreen_position"],
            settings["bottom_panel_widescreen_position"],
        )

    @property
    def font_size(self):
        return max(1, int(round(BASE_FONT_SIZE * self.font_scale)))

    def font_size_for(self, base_size):
        return max(1, int(round(float(base_size) * self.font_scale)))

    def scale_for(self, role):
        if role in ("viewport", "damage_sprite"):
            return self.viewport_scale
        if role == "panel_native_sprites":
            return self.panel_scale
        if role == "legacy":
            return self.legacy_scale
        return self.panel_scale

    def _base_transform(self, panel_id, reference_bounds, role):
        policy = PANEL_POLICIES[panel_id]
        left, top, right, bottom = reference_bounds
        pivot_x = _axis_point(left, right, policy.horizontal)
        pivot_y = _axis_point(top, bottom, policy.vertical)
        target_x = REFERENCE_WIDTH * 0.5 if policy.canvas_center_x else pivot_x
        target_y = (
            self.middle_panel_y if policy.vertical == "center" else pivot_y
        )
        target_x = self.canvas_origin[0] + target_x * self.position_scale
        target_y = self.canvas_origin[1] + target_y * self.position_scale
        widescreen_offset = max(0.0, self.canvas_origin[0]) * (
            self.panel_widescreen[VERTICAL_GROUP_INDEX[policy.vertical]]
        )
        if policy.horizontal == "left":
            target_x -= widescreen_offset
        elif policy.horizontal == "right":
            target_x += widescreen_offset
        scale = self.scale_for(role)
        return PanelTransform(
            target_x - pivot_x * scale,
            target_y - pivot_y * scale,
            scale,
            scale,
        )

    def frame_transform(self, panel):
        policy = PANEL_POLICIES[panel.panel_id]
        return self.resolve_transform(
            panel.panel_id,
            panel.reference_bounds,
            policy.frame_role,
            panel.animation_extent,
        )

    def final_frame_transform(self, panel):
        policy = PANEL_POLICIES[panel.panel_id]
        return self._base_transform(
            panel.panel_id,
            panel.reference_bounds,
            policy.frame_role,
        )

    def panel_transforms(self, panel):
        policy = PANEL_POLICIES[panel.panel_id]
        frame = self.frame_transform(panel)
        if panel.content_role == policy.frame_role:
            return frame, frame
        final_frame = self.final_frame_transform(panel)
        if panel.content_role == "damage_sprite":
            return frame, self.damage_sprite_transform(panel, final_frame)
        left, top, right, bottom = panel.reference_bounds
        center_x = (left + right) * 0.5
        center_y = (top + bottom) * 0.5
        final_center_x, final_center_y = final_frame.point(center_x, center_y)
        scale = self.scale_for(panel.content_role)
        content = PanelTransform(
            final_center_x - center_x * scale,
            final_center_y - center_y * scale,
            scale,
            scale,
        )
        return frame, self._animated_transform(
            panel.reference_bounds, panel.animation_extent, content
        )

    def damage_sprite_transform(
        self,
        panel,
        final_frame=None,
        alignment_transform=None,
    ):
        if final_frame is None:
            final_frame = self.final_frame_transform(panel)
        left, top, right, bottom = panel.reference_bounds
        center_x = (left + right) * 0.5
        center_y = (top + bottom) * 0.5
        final_center_x, final_center_y = final_frame.point(center_x, center_y)
        scale = _damage_sprite_scale(
            panel,
            final_frame,
            self.scale_for("damage_sprite"),
        )
        if (
            alignment_transform is not None
            and panel.damage_sprite_center_x is not None
        ):
            target_center_x = panel.damage_sprite_center_x
            target_bottom_y = panel.damage_sprite_bottom_y
            entry = panel.sprites[0]
            sprite_left = entry.x + entry.sprite.x_offset
            sprite_top = entry.y + entry.sprite.y_offset
            width = (
                entry.sprite.width
                if entry.draw_width is None
                else entry.draw_width
            )
            height = (
                entry.sprite.height
                if entry.draw_height is None
                else entry.draw_height
            )
            target_center_x, target_bottom_y = alignment_transform.point(
                target_center_x,
                target_bottom_y,
            )
            target_bottom_y -= (
                panel.damage_sprite_gap * alignment_transform.scale_y
            )
            return PanelTransform(
                round(target_center_x - (sprite_left + width * 0.5) * scale),
                round(target_bottom_y - (sprite_top + height) * scale),
                scale,
                scale,
            )
        content = PanelTransform(
            round(final_center_x - center_x * scale),
            round(final_center_y - center_y * scale),
            scale,
            scale,
        )
        return self._animated_transform(
            panel.reference_bounds, panel.animation_extent, content
        )

    def resolve_transform(
        self,
        panel_id,
        reference_bounds,
        role="panel",
        animation_extent=(1.0, 1.0),
    ):
        return self._animated_transform(
            reference_bounds,
            animation_extent,
            self._base_transform(panel_id, reference_bounds, role),
        )

    def centered_transform(
        self,
        reference_bounds,
        role="panel",
        animation_extent=(1.0, 1.0),
    ):
        left = float(reference_bounds[0])
        top = float(reference_bounds[1])
        right = float(reference_bounds[2])
        bottom = float(reference_bounds[3])
        span_x = max(1.0, right - left)
        span_y = max(1.0, bottom - top)
        extent_x = max(
            0.0,
            min(1.0, float(animation_extent[0])),
        )
        extent_y = max(
            0.0,
            min(1.0, float(animation_extent[1])),
        )
        reveal_x = (1.0 + (span_x - 1.0) * extent_x) / span_x
        reveal_y = (1.0 + (span_y - 1.0) * extent_y) / span_y
        scale = self.scale_for(role)
        scale_x = scale * reveal_x
        scale_y = scale * reveal_y
        center_x = (left + right) * 0.5
        center_y = (top + bottom) * 0.5
        return PanelTransform(
            self.viewport[0] * 0.5 - center_x * scale_x,
            self.viewport[1] * 0.5 - center_y * scale_y,
            scale_x,
            scale_y,
        )

    def content_transform(self, panel):
        return self.panel_transforms(panel)[1]

    @staticmethod
    def _animated_transform(reference_bounds, animation_extent, transform):
        extent_x, extent_y = (
            max(0.0, min(1.0, float(value)))
            for value in animation_extent
        )
        if extent_x == 1.0 and extent_y == 1.0:
            return transform
        left, top, right, bottom = reference_bounds
        pivot_x = float((int(left) + int(right) - 1) // 2)
        pivot_y = float((int(top) + int(bottom) - 1) // 2)
        span_x = max(1.0, float(right) - float(left))
        span_y = max(1.0, float(bottom) - float(top))
        reveal_x = (1.0 + (span_x - 1.0) * extent_x) / span_x
        reveal_y = (1.0 + (span_y - 1.0) * extent_y) / span_y
        animated_left = pivot_x + (float(left) - pivot_x) * extent_x
        animated_top = pivot_y + (float(top) - pivot_y) * extent_y
        native_origin_x = animated_left - float(left) * reveal_x
        native_origin_y = animated_top - float(top) * reveal_y
        scale_x = transform.scale_x * reveal_x
        scale_y = transform.scale_y * reveal_y
        return PanelTransform(
            transform.origin_x + native_origin_x * transform.scale_x,
            transform.origin_y + native_origin_y * transform.scale_y,
            scale_x,
            scale_y,
        )

    def reference_point(self, x, y):
        return (
            self.canvas_origin[0] + float(x) * self.position_scale,
            self.canvas_origin[1] + float(y) * self.position_scale,
        )

    def reference_rect(self, rect):
        left, top = self.reference_point(rect[0], rect[1])
        right, bottom = self.reference_point(rect[2], rect[3])
        return left, top, right, bottom
