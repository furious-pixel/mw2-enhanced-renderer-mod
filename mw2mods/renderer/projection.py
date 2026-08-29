from array import array
import math
from typing import NamedTuple


PROJECTION_FOCAL_LENGTH_PIXELS = 512.0
PROJECTION_BASE_VIEWPORT_HEIGHT_PIXELS = 768.0
PROJECTION_NEAR = 0.001
PROJECTION_FAR = 1000.0


class PerspectiveProjectionInfo(NamedTuple):
    width: int
    height: int
    native_focal_length_pixels: float
    output_focal_length_pixels: float
    game_horizontal_fov_degrees: float
    resolution_horizontal_fov_degrees: float
    effective_horizontal_fov_degrees: float
    max_horizontal_fov_degrees: float | None
    projection_y_scale: float


def perspective_projection_info(
    width,
    height,
    focal_length_pixels=PROJECTION_FOCAL_LENGTH_PIXELS,
    max_horizontal_fov_degrees=None,
):
    """Resolve the game, resolution-scaled, and effective horizontal FOV."""
    width = max(1, int(width))
    height = max(1, int(height))
    focal_length_pixels = max(1.0, float(focal_length_pixels))
    game_horizontal_fov = math.degrees(
        2.0 * math.atan(1024.0 / (2.0 * focal_length_pixels))
    )
    output_focal_length = (
        focal_length_pixels * height / PROJECTION_BASE_VIEWPORT_HEIGHT_PIXELS
    )
    resolution_horizontal_fov = math.degrees(
        2.0 * math.atan(width / (2.0 * output_focal_length))
    )
    maximum = None
    effective_horizontal_fov = resolution_horizontal_fov
    if max_horizontal_fov_degrees is not None:
        maximum = max(30.0, min(170.0, float(max_horizontal_fov_degrees)))
        effective_horizontal_fov = min(resolution_horizontal_fov, maximum)
        output_focal_length = max(
            output_focal_length,
            width / (2.0 * math.tan(math.radians(maximum) * 0.5)),
        )
    return PerspectiveProjectionInfo(
        width=width,
        height=height,
        native_focal_length_pixels=focal_length_pixels,
        output_focal_length_pixels=output_focal_length,
        game_horizontal_fov_degrees=game_horizontal_fov,
        resolution_horizontal_fov_degrees=resolution_horizontal_fov,
        effective_horizontal_fov_degrees=effective_horizontal_fov,
        max_horizontal_fov_degrees=maximum,
        projection_y_scale=2.0 * output_focal_length / height,
    )


def positive_z_projection(
    width,
    height,
    focal_length_pixels=PROJECTION_FOCAL_LENGTH_PIXELS,
    flip_x=True,
    max_horizontal_fov_degrees=None,
    near_plane=PROJECTION_NEAR,
    far_plane=PROJECTION_FAR,
    projection_info=None,
):
    width = max(1, int(width))
    height = max(1, int(height))
    aspect = width / height
    if projection_info is None:
        projection_info = perspective_projection_info(
            width,
            height,
            focal_length_pixels=focal_length_pixels,
            max_horizontal_fov_degrees=max_horizontal_fov_degrees,
        )
    f = projection_info.projection_y_scale
    sx = (-f if flip_x else f) / aspect
    sy = f
    near_plane = max(1e-7, float(near_plane))
    far_plane = max(near_plane * 1.001, float(far_plane))
    z_scale = far_plane / (far_plane - near_plane)
    z_offset = -(near_plane * far_plane) / (far_plane - near_plane)

    return array(
        "f",
        [
            sx, 0.0, 0.0, 0.0,
            0.0, sy, 0.0, 0.0,
            0.0, 0.0, z_scale, 1.0,
            0.0, 0.0, z_offset, 0.0,
        ],
    )


def positive_z_pane_projection(
    width,
    height,
    focal_length_pixels=PROJECTION_FOCAL_LENGTH_PIXELS,
    focal_length_pixels_y=None,
    flip_x=False,
    center_pixels=None,
    near_plane=PROJECTION_NEAR,
    far_plane=PROJECTION_FAR,
):
    width = max(1, int(width))
    height = max(1, int(height))
    focal_length_pixels = max(1.0, float(focal_length_pixels))
    if focal_length_pixels_y is None:
        focal_length_pixels_y = focal_length_pixels
    focal_length_pixels_y = max(1.0, float(focal_length_pixels_y))
    sx = (2.0 * focal_length_pixels) / width
    if flip_x:
        sx = -sx
    sy = (2.0 * focal_length_pixels_y) / height
    if center_pixels is None:
        center_x = width * 0.5
        center_y = height * 0.5
    else:
        center_x = float(center_pixels[0])
        center_y = float(center_pixels[1])
    center_offset_x = (2.0 * center_x / width) - 1.0
    center_offset_y = 1.0 - (2.0 * center_y / height)
    near_plane = max(1e-7, float(near_plane))
    far_plane = max(near_plane * 1.001, float(far_plane))
    z_scale = far_plane / (far_plane - near_plane)
    z_offset = -(near_plane * far_plane) / (far_plane - near_plane)
    return array(
        "f",
        [
            sx, 0.0, 0.0, 0.0,
            0.0, sy, 0.0, 0.0,
            center_offset_x, center_offset_y, z_scale, 1.0,
            0.0, 0.0, z_offset, 0.0,
        ],
    )


def positive_z_orthographic_projection(
    half_width,
    half_height,
    near_plane=PROJECTION_NEAR,
    far_plane=PROJECTION_FAR,
):
    half_width = max(1e-7, float(half_width))
    half_height = max(1e-7, float(half_height))
    near_plane = max(0.0, float(near_plane))
    far_plane = max(near_plane + 1e-6, float(far_plane))
    depth_scale = 2.0 / (far_plane - near_plane)
    depth_offset = -(far_plane + near_plane) / (far_plane - near_plane)
    return array(
        "f",
        [
            1.0 / half_width, 0.0, 0.0, 0.0,
            0.0, 1.0 / half_height, 0.0, 0.0,
            0.0, 0.0, depth_scale, 0.0,
            0.0, 0.0, depth_offset, 1.0,
        ],
    )
