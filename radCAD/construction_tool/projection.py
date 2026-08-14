"""Viewport projection math shared by guide drawing and snapping."""

from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector


_EPSILON = 1.0e-10


def guide_vectors(line):
    anchor = Vector(line.anchor)
    direction = Vector(line.direction)
    if direction.length_squared <= _EPSILON:
        return None
    direction.normalize()
    normal = Vector(line.plane_normal)
    if normal.length_squared <= _EPSILON:
        normal = Vector((0.0, 0.0, 1.0))
    else:
        normal.normalize()
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


def closest_screen_coordinate(context, anchor, direction, mouse):
    axis = projected_guide_axis(context, anchor, direction)
    if axis is None:
        return None
    first, second = axis
    screen_direction = second - first
    if screen_direction.length_squared <= _EPSILON:
        return None
    mouse = Vector(mouse)
    factor = (mouse - first).dot(screen_direction) / screen_direction.length_squared
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
