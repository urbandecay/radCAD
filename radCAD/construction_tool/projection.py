"""Viewport projection math shared by guide drawing and snapping."""

from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector

from .model import (
    constrain_direction_to_plane,
    recover_legacy_plane_normal,
)


_EPSILON = 1.0e-10
_PERSPECTIVE_PARALLEL_EPSILON = 1.0e-8


def _screen_from_clip(clip, width, height):
    """Convert a homogeneous clip-space point to region coordinates."""
    if abs(clip.w) <= _EPSILON:
        return None
    return Vector((
        (clip.x / clip.w + 1.0) * float(width) * 0.5,
        (clip.y / clip.w + 1.0) * float(height) * 0.5,
    ))


def guide_vectors(line):
    anchor = Vector(line.anchor)
    raw_direction = Vector(line.direction)
    normal = Vector(line.plane_normal)
    if normal.length_squared <= _EPSILON:
        normal = Vector((0.0, 0.0, 1.0))
    else:
        normal.normalize()

    if getattr(line, "schema_version", 0) < 2:
        normal = recover_legacy_plane_normal(raw_direction, normal)

    direction = constrain_direction_to_plane(raw_direction, normal)
    if direction is None:
        # A legacy guide may have been created parallel to its plane normal,
        # which has no unique in-plane direction. Keep it visible using a
        # stable plane tangent instead of projecting it into the sky.
        reference = min(
            (Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0))),
            key=lambda axis: abs(axis.dot(normal)),
        )
        direction = normal.cross(reference)
        if direction.length_squared <= _EPSILON:
            return None
        direction.normalize()
    return anchor, direction, normal


def projected_guide_axis(context, anchor, direction):
    """Return two screen points defining the projected infinite guide."""
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return None

    anchor = Vector(anchor)
    direction = Vector(direction)
    if direction.length_squared <= _EPSILON:
        return None
    direction.normalize()

    view_distance = max(1.0, float(getattr(rv3d, "view_distance", 1.0)))
    coordinate_scale = max((abs(value) for value in anchor), default=1.0)
    span = max(1.0, view_distance, coordinate_scale * 0.01)
    world_points = (
        anchor,
        anchor + direction * span,
        anchor - direction * span,
        anchor + direction * span * 100.0,
        anchor - direction * span * 100.0,
    )
    projected = []
    for point in world_points:
        screen = location_3d_to_region_2d(region, rv3d, point, default=None)
        if screen is not None:
            projected.append(Vector(screen))

    best_pair = None
    best_distance = _EPSILON
    for index, first in enumerate(projected):
        for second in projected[index + 1:]:
            distance = (second - first).length_squared
            if distance > best_distance:
                best_distance = distance
                best_pair = (first, second)
    return best_pair


def projected_visible_guide_segment(context, anchor, direction):
    """Return the visible part of a 3D infinite guide in region coordinates.

    An infinite world-space line does *not* remain an infinite screen-space
    line in a perspective view. Only one half is in front of the eye; that
    half approaches its vanishing point at the horizon. Extending the 2D
    projection through that point draws the behind-camera half into the sky.
    """
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None or region.width <= 0 or region.height <= 0:
        return None

    # Orthographic guides really do project to infinite screen-space lines.
    if not getattr(rv3d, "is_perspective", False):
        axis = projected_guide_axis(context, anchor, direction)
        if axis is None:
            return None
        return clip_infinite_screen_line(*axis, region.width, region.height)

    anchor = Vector(anchor)
    direction = Vector(direction)
    if direction.length_squared <= _EPSILON:
        return None
    direction.normalize()

    matrix = rv3d.perspective_matrix
    anchor_clip = matrix @ Vector((anchor.x, anchor.y, anchor.z, 1.0))
    direction_clip = matrix @ Vector((direction.x, direction.y, direction.z, 0.0))
    if abs(direction_clip.w) <= _PERSPECTIVE_PARALLEL_EPSILON:
        # This guide's vanishing point is effectively at infinity, so it is a
        # full screen-space line when it is in front of the viewer.
        if anchor_clip.w <= _EPSILON:
            return None
        axis = projected_guide_axis(context, anchor, direction)
        if axis is None:
            return None
        return clip_infinite_screen_line(*axis, region.width, region.height)

    vanishing_screen = _screen_from_clip(direction_clip, region.width, region.height)
    if vanishing_screen is None:
        return None

    # Pick a point on the half of the guide in front of the eye. In Blender's
    # perspective matrix that half has positive clip W. Working in homogeneous
    # coordinates avoids guessing a world-space span or accidentally sampling
    # the branch behind the viewer.
    front_w = max(1.0, abs(anchor_clip.w))
    factor = (front_w - anchor_clip.w) / direction_clip.w
    front_clip = anchor_clip + direction_clip * factor
    front_screen = _screen_from_clip(front_clip, region.width, region.height)
    if front_screen is None:
        return None

    return clip_screen_ray(
        vanishing_screen,
        front_screen,
        region.width,
        region.height,
    )


def closest_screen_coordinate(context, anchor, direction, mouse):
    segment = projected_visible_guide_segment(context, anchor, direction)
    if segment is None:
        return None
    first, second = segment
    screen_direction = second - first
    if screen_direction.length_squared <= _EPSILON:
        return None
    mouse = Vector(mouse)
    factor = (mouse - first).dot(screen_direction) / screen_direction.length_squared
    factor = max(0.0, min(1.0, factor))
    return first + screen_direction * factor


def world_point_for_screen_coordinate(context, anchor, direction, screen_coordinate):
    """Recover the exact point on a 3D guide represented by a projected point."""
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None or region.width <= 0 or region.height <= 0:
        return None

    anchor = Vector(anchor)
    direction = Vector(direction)
    if direction.length_squared <= _EPSILON:
        return None
    direction.normalize()

    matrix = rv3d.perspective_matrix
    anchor_clip = matrix @ Vector((anchor.x, anchor.y, anchor.z, 1.0))
    direction_clip = matrix @ Vector((direction.x, direction.y, direction.z, 0.0))
    screen_coordinate = Vector(screen_coordinate)
    target_x = (2.0 * screen_coordinate.x / float(region.width)) - 1.0
    target_y = (2.0 * screen_coordinate.y / float(region.height)) - 1.0

    equations = (
        (
            target_x * direction_clip.w - direction_clip.x,
            anchor_clip.x - target_x * anchor_clip.w,
        ),
        (
            target_y * direction_clip.w - direction_clip.y,
            anchor_clip.y - target_y * anchor_clip.w,
        ),
    )
    denominator, numerator = max(equations, key=lambda equation: abs(equation[0]))
    if abs(denominator) <= _EPSILON:
        return None
    return anchor + direction * (numerator / denominator)


def closest_point_on_guide(context, anchor, direction, mouse):
    closest_screen = closest_screen_coordinate(context, anchor, direction, mouse)
    if closest_screen is None:
        return None
    point = world_point_for_screen_coordinate(context, anchor, direction, closest_screen)
    if point is None:
        return None
    actual_screen = location_3d_to_region_2d(
        context.region,
        context.region_data,
        point,
        default=None,
    )
    if actual_screen is None:
        return None
    distance = (Vector(actual_screen) - Vector(mouse)).length
    return point, distance


def clip_infinite_screen_line(first, second, width, height):
    """Clip an infinite 2D line to the viewport rectangle."""
    first = Vector(first)
    direction = Vector(second) - first
    if direction.length_squared <= _EPSILON:
        return None

    max_x = max(0.0, float(width) - 1.0)
    max_y = max(0.0, float(height) - 1.0)
    intersections = []

    def add_intersection(factor):
        point = first + direction * factor
        if -1.0e-5 <= point.x <= max_x + 1.0e-5 and -1.0e-5 <= point.y <= max_y + 1.0e-5:
            for existing in intersections:
                if (existing - point).length_squared <= 1.0e-8:
                    return
            intersections.append(point)

    if abs(direction.x) > _EPSILON:
        add_intersection((0.0 - first.x) / direction.x)
        add_intersection((max_x - first.x) / direction.x)
    if abs(direction.y) > _EPSILON:
        add_intersection((0.0 - first.y) / direction.y)
        add_intersection((max_y - first.y) / direction.y)

    if len(intersections) < 2:
        return None
    return max(
        (
            (start, end)
            for index, start in enumerate(intersections)
            for end in intersections[index + 1:]
        ),
        key=lambda pair: (pair[1] - pair[0]).length_squared,
    )


def clip_screen_ray(origin, through, width, height):
    """Clip a 2D ray from ``origin`` through ``through`` to the viewport."""
    origin = Vector(origin)
    direction = Vector(through) - origin
    if direction.length_squared <= _EPSILON:
        return None

    bounds = (
        (origin.x, direction.x, 0.0, max(0.0, float(width) - 1.0)),
        (origin.y, direction.y, 0.0, max(0.0, float(height) - 1.0)),
    )
    start_factor = 0.0
    end_factor = float("inf")
    for coordinate, delta, lower, upper in bounds:
        if abs(delta) <= _EPSILON:
            if coordinate < lower or coordinate > upper:
                return None
            continue
        first = (lower - coordinate) / delta
        second = (upper - coordinate) / delta
        entry, exit_ = min(first, second), max(first, second)
        start_factor = max(start_factor, entry)
        end_factor = min(end_factor, exit_)
        if end_factor < start_factor:
            return None

    if end_factor == float("inf"):
        return None
    start = origin + direction * start_factor
    end = origin + direction * end_factor
    if (end - start).length_squared <= _EPSILON:
        return None
    return start, end
