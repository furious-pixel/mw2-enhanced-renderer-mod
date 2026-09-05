"""Snap meter edges to output pixels, then build inclusive spans.

Jump-jet, heat-rate, and HTAL share one prefix fill. Heat is that fill on
the right half, mirrored to the left. Throttle is an outer frame plus a
prefix or suffix fill with a one-pixel minimum.
"""

import math

METER_GROW_LEFT_TO_RIGHT = 0
METER_GROW_SYMMETRIC = 1


def round_output_pixel(value):
    return int(math.floor(float(value) + 0.5))


def clamp_unit(amount):
    if amount <= 0.0:
        return 0.0
    if amount >= 1.0:
        return 1.0
    return float(amount)


def snap_half_open_to_inclusive(start, end):
    return round_output_pixel(start), round_output_pixel(end) - 1


def half_open_from_inclusive(lo, hi):
    return lo, hi + 1


def clip_inclusive(lo, hi, clip_lo, clip_hi):
    lo = max(lo, clip_lo)
    hi = min(hi, clip_hi)
    if hi < lo:
        return None
    return lo, hi


def filled_count(length, amount, minimum=0):
    if length <= 0:
        return 0
    count = round_output_pixel(clamp_unit(amount) * length)
    if count < minimum:
        count = minimum
    if count > length:
        return length
    return count


def outline_stroke_pixels(scale_x, scale_y):
    return max(1, round_output_pixel(min(abs(scale_x), abs(scale_y))))


def inset_inclusive_rect(left, top, right, bottom, stroke):
    max_stroke = min(right - left + 1, bottom - top + 1) // 2
    if max_stroke <= 0:
        return left, top, right, bottom, 0
    if stroke > max_stroke:
        stroke = max_stroke
    return left + stroke, top + stroke, right - stroke, bottom - stroke, stroke


def fill_span(lo, hi, amount, from_end=False, minimum=0):
    count = filled_count(hi - lo + 1, amount, minimum)
    if count <= 0:
        return None
    if from_end:
        return hi - count + 1, hi
    return lo, lo + count - 1


def prefix_spans(lo, hi, amount, fill_color, empty_color, from_end=False):
    """One filled span and its remainder along [lo, hi]."""
    filled = fill_span(lo, hi, amount, from_end)
    if filled is None:
        return ((lo, hi, empty_color),)
    fill_lo, fill_hi = filled
    if fill_lo == lo and fill_hi == hi:
        return ((lo, hi, fill_color),)
    if from_end:
        return ((fill_lo, hi, fill_color), (lo, fill_lo - 1, empty_color))
    return ((lo, fill_hi, fill_color), (fill_hi + 1, hi, empty_color))


def _box(boxes, left, top, right, bottom, color_index):
    if right >= left and bottom >= top:
        boxes.append((left, top, right, bottom, color_index))


def _boxes_along(boxes, spans, left, top, right, bottom, axis):
    for span_lo, span_hi, color in spans:
        if axis == "y":
            _box(boxes, left, span_lo, right, span_hi, color)
        else:
            _box(boxes, span_lo, top, span_hi, bottom, color)


def bar_meter_boxes(
    left,
    top,
    right,
    bottom,
    amount,
    axis,
    grow,
    fill_color_index,
    empty_color_index,
    edge_color_index,
):
    boxes = []
    amount = clamp_unit(amount)
    if grow != METER_GROW_SYMMETRIC:
        if axis == "y":
            lo, hi = top, bottom
        else:
            lo, hi = left, right
        _boxes_along(
            boxes,
            prefix_spans(lo, hi, amount, fill_color_index, empty_color_index),
            left,
            top,
            right,
            bottom,
            axis,
        )
        return boxes

    if amount <= 0.0 or amount >= 1.0:
        color = empty_color_index if amount < 1.0 else fill_color_index
        _box(boxes, left, top, right, bottom, color)
        return boxes

    half = (right - left + 1) // 2
    right_lo = right - half + 1
    if amount <= 0.5:
        spans = prefix_spans(
            right_lo, right, amount * 2.0,
            edge_color_index, empty_color_index, True,
        )
    else:
        spans = prefix_spans(
            right_lo, right, amount * 2.0 - 1.0,
            fill_color_index, edge_color_index,
        )
    _boxes_along(boxes, spans, left, top, right, bottom, "x")
    pivot = (left + half - 1) + right_lo
    for box_left, box_top, box_right, box_bottom, color in list(boxes):
        _box(
            boxes,
            pivot - box_right,
            box_top,
            pivot - box_left,
            box_bottom,
            color,
        )
    if (right - left + 1) % 2:
        for span_lo, span_hi, color in spans:
            if span_lo <= right_lo <= span_hi:
                center = left + half
                _box(boxes, center, top, center, bottom, color)
                break
    return boxes


def throttle_fill_rows(
    inner_top, inner_bottom, rest_y, amount, reverse, minimum=1,
):
    rest = min(inner_bottom, max(inner_top, round_output_pixel(rest_y)))
    if reverse:
        return fill_span(rest, inner_bottom, amount, False, minimum)
    return fill_span(inner_top, rest, amount, True, minimum)
