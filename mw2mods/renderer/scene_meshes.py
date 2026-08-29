import math
from array import array


SKY_RADIUS = 200.0
SKY_SEGMENTS = 64
SKY_STACKS = 16
GRADIENT_VERTICAL_SEGMENTS = 14


def _append_vertex(vertices, position, palette_mix):
    vertices.extend((float(position[0]), float(position[1]), float(position[2]), float(palette_mix)))


def build_sky_hemisphere_vertices():
    vertices = array("f")

    for stack in range(SKY_STACKS):
        elev0 = (stack / SKY_STACKS) * (math.pi * 0.5)
        elev1 = ((stack + 1) / SKY_STACKS) * (math.pi * 0.5)
        y0 = math.sin(elev0) * SKY_RADIUS
        y1 = math.sin(elev1) * SKY_RADIUS
        r0 = math.cos(elev0) * SKY_RADIUS
        r1 = math.cos(elev1) * SKY_RADIUS

        for segment in range(SKY_SEGMENTS):
            angle0 = (segment / SKY_SEGMENTS) * math.tau
            angle1 = ((segment + 1) / SKY_SEGMENTS) * math.tau
            p00 = (math.sin(angle0) * r0, y0, math.cos(angle0) * r0)
            p01 = (math.sin(angle1) * r0, y0, math.cos(angle1) * r0)
            p10 = (math.sin(angle0) * r1, y1, math.cos(angle0) * r1)
            p11 = (math.sin(angle1) * r1, y1, math.cos(angle1) * r1)

            _append_vertex(vertices, p00, 0.0)
            _append_vertex(vertices, p10, 0.0)
            _append_vertex(vertices, p11, 0.0)
            _append_vertex(vertices, p00, 0.0)
            _append_vertex(vertices, p11, 0.0)
            _append_vertex(vertices, p01, 0.0)

    return vertices


def build_gradient_cylinder_vertices():
    vertices = array("f")

    for ring in range(GRADIENT_VERTICAL_SEGMENTS):
        t0 = ring / GRADIENT_VERTICAL_SEGMENTS
        t1 = (ring + 1) / GRADIENT_VERTICAL_SEGMENTS
        y0 = 1.0 - t0
        y1 = 1.0 - t1

        for segment in range(SKY_SEGMENTS):
            angle0 = (segment / SKY_SEGMENTS) * math.tau
            angle1 = ((segment + 1) / SKY_SEGMENTS) * math.tau
            p00 = (math.sin(angle0) * SKY_RADIUS, y0, math.cos(angle0) * SKY_RADIUS)
            p01 = (math.sin(angle1) * SKY_RADIUS, y0, math.cos(angle1) * SKY_RADIUS)
            p10 = (math.sin(angle0) * SKY_RADIUS, y1, math.cos(angle0) * SKY_RADIUS)
            p11 = (math.sin(angle1) * SKY_RADIUS, y1, math.cos(angle1) * SKY_RADIUS)

            _append_vertex(vertices, p00, t0)
            _append_vertex(vertices, p10, t1)
            _append_vertex(vertices, p11, t1)
            _append_vertex(vertices, p00, t0)
            _append_vertex(vertices, p11, t1)
            _append_vertex(vertices, p01, t0)

    return vertices


def build_screen_quad_vertices():
    return array(
        "f",
        (
            -1.0, -1.0,
            -1.0, 1.0,
            1.0, -1.0,
            1.0, 1.0,
        ),
    )
