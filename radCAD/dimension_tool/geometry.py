"""Pure layout math for linear and angular dimensions."""

from dataclasses import dataclass
import math

from mathutils import Matrix, Vector

from .constants import EPSILON


@dataclass
class DimensionLayout:
    p1: Vector
    p2: Vector
    d1: Vector
    d2: Vector
    midpoint: Vector
    text_position: Vector
    line_direction: Vector
    offset_direction: Vector
    plane_normal: Vector
    segments: list
    measured_length: float


@dataclass
class AngleLayout:
    vertex: Vector
    ray_1: Vector
    ray_2: Vector
    plane_normal: Vector
    start_direction: Vector
    end_direction: Vector
    radius: float
    sweep_angle: float
    measured_angle: float
    midpoint: Vector
    arc_points: list
    segments: list


def _fallback_normal(line_direction):
    axes = (Vector((0.0, 0.0, 1.0)), Vector((0.0, 1.0, 0.0)), Vector((1.0, 0.0, 0.0)))
    axis = min(axes, key=lambda candidate: abs(candidate.dot(line_direction)))
    normal = line_direction.cross(axis)
    if normal.length_squared <= EPSILON:
        normal = Vector((0.0, 0.0, 1.0))
    return normal.normalized()


def dimension_basis(p1, p2, preferred_normal, dimension_direction=None):
    """Return a basis for an aligned or direction-selected dimension."""
    line = Vector(p2) - Vector(p1)
    if line.length_squared <= EPSILON:
        return None

    normal = Vector(preferred_normal) if preferred_normal is not None else Vector((0.0, 0.0, 1.0))
    if normal.length_squared <= EPSILON:
        normal = Vector((0.0, 0.0, 1.0))
    else:
        normal.normalize()

    requested_direction = (
        Vector(dimension_direction)
        if dimension_direction is not None
        else Vector((0.0, 0.0, 0.0))
    )
    if requested_direction.length_squared > EPSILON:
        requested_direction -= normal * requested_direction.dot(normal)
        line_direction = (
            requested_direction.normalized()
            if requested_direction.length_squared > EPSILON
            else line.normalized()
        )
        if line_direction.dot(line) < 0.0:
            line_direction.negate()
    else:
        line_direction = line.normalized()

    normal -= line_direction * normal.dot(line_direction)
    if normal.length_squared <= EPSILON:
        normal = _fallback_normal(line_direction)
    else:
        normal.normalize()

    offset_direction = normal.cross(line_direction)
    if offset_direction.length_squared <= EPSILON:
        normal = _fallback_normal(line_direction)
        offset_direction = normal.cross(line_direction)
    offset_direction.normalize()
    normal = line_direction.cross(offset_direction).normalized()
    return line_direction, offset_direction, normal


def signed_offset_from_point(p1, p2, preferred_normal, placement):
    basis = dimension_basis(p1, p2, preferred_normal)
    if basis is None:
        return 0.0
    _line_direction, offset_direction, _normal = basis
    midpoint = (Vector(p1) + Vector(p2)) * 0.5
    return (Vector(placement) - midpoint).dot(offset_direction)


def build_layout(
    p1,
    p2,
    preferred_normal,
    offset_distance,
    text_size,
    arrow_size,
    extension_gap,
    extension_overshoot,
    label="",
    dimension_direction=None,
):
    basis = dimension_basis(
        p1,
        p2,
        preferred_normal,
        dimension_direction,
    )
    if basis is None:
        return None

    p1 = Vector(p1)
    p2 = Vector(p2)
    line_direction, offset_direction, normal = basis
    delta = p2 - p1
    is_direction_selected = (
        dimension_direction is not None
        and Vector(dimension_direction).length_squared > EPSILON
    )
    measured_length = (
        abs(delta.dot(line_direction))
        if is_direction_selected
        else delta.length
    )
    if measured_length <= EPSILON:
        return None
    distance = float(offset_distance)
    side = 1.0 if distance >= 0.0 else -1.0
    source_midpoint = (p1 + p2) * 0.5
    p1_offset = (p1 - source_midpoint).dot(offset_direction)
    p2_offset = (p2 - source_midpoint).dot(offset_direction)
    d1 = p1 + offset_direction * (distance - p1_offset)
    d2 = p2 + offset_direction * (distance - p2_offset)
    midpoint = (d1 + d2) * 0.5

    gap = max(0.0, float(extension_gap))
    overshoot = max(0.0, float(extension_overshoot))
    arrow = max(EPSILON, float(arrow_size))
    text_size = max(EPSILON, float(text_size))

    segments = [
        (p1 + offset_direction * side * gap, d1 + offset_direction * side * overshoot),
        (p2 + offset_direction * side * gap, d2 + offset_direction * side * overshoot),
    ]

    # Leave a SketchUp-like break behind the measurement when it fits.
    text_half_width = max(text_size * 0.8, len(label) * text_size * 0.28)
    if measured_length > (text_half_width * 2.0 + arrow * 2.0):
        segments.extend(
            (
                (d1, midpoint - line_direction * text_half_width),
                (midpoint + line_direction * text_half_width, d2),
            )
        )
    else:
        segments.append((d1, d2))

    wing = arrow * 0.38
    if measured_length >= arrow * 2.5:
        left_back = d1 + line_direction * arrow
        right_back = d2 - line_direction * arrow
    else:
        left_back = d1 - line_direction * arrow
        right_back = d2 + line_direction * arrow
    segments.extend(
        (
            (d1, left_back + offset_direction * wing),
            (d1, left_back - offset_direction * wing),
            (d2, right_back + offset_direction * wing),
            (d2, right_back - offset_direction * wing),
        )
    )

    text_position = midpoint + offset_direction * side * text_size * 0.20
    return DimensionLayout(
        p1=p1,
        p2=p2,
        d1=d1,
        d2=d2,
        midpoint=midpoint,
        text_position=text_position,
        line_direction=line_direction,
        offset_direction=offset_direction,
        plane_normal=normal,
        segments=segments,
        measured_length=measured_length,
    )


def _angle_vectors(vertex, ray_1, ray_2, preferred_normal):
    """Return normalized in-plane rays, the plane normal, and signed sweep."""
    vertex = Vector(vertex)
    first = Vector(ray_1) - vertex
    second = Vector(ray_2) - vertex
    if first.length_squared <= EPSILON or second.length_squared <= EPSILON:
        return None

    normal = Vector(preferred_normal) if preferred_normal is not None else Vector((0.0, 0.0, 1.0))
    if normal.length_squared <= EPSILON:
        normal = first.cross(second)
    if normal.length_squared <= EPSILON:
        return None
    normal.normalize()

    first -= normal * first.dot(normal)
    second -= normal * second.dot(normal)
    if first.length_squared <= EPSILON or second.length_squared <= EPSILON:
        return None
    first.normalize()
    second.normalize()

    dot = max(-1.0, min(1.0, first.dot(second)))
    signed_sweep = math.atan2(first.cross(second).dot(normal), dot)
    if abs(signed_sweep) <= 1.0e-8:
        return None
    return vertex, first, second, normal, signed_sweep


def build_angle_layout(
    vertex,
    ray_1,
    ray_2,
    preferred_normal,
    radius,
    text_size,
    arrow_size,
    extension_gap,
    extension_overshoot,
    label="",
):
    """Build the world-space geometry for a three-point angle dimension."""
    vectors = _angle_vectors(vertex, ray_1, ray_2, preferred_normal)
    if vectors is None:
        return None
    vertex, start_direction, end_direction, normal, sweep_angle = vectors

    radius = abs(float(radius))
    if radius <= EPSILON:
        radius = min((Vector(ray_1) - vertex).length, (Vector(ray_2) - vertex).length)
    if radius <= EPSILON:
        return None

    # Use enough samples for a smooth overlay without making every mouse move
    # expensive.  The dimension remains a GPU line strip, not scene geometry.
    sample_count = max(8, min(96, int(abs(sweep_angle) * 24.0) + 1))
    arc_points = []
    for index in range(sample_count + 1):
        factor = index / sample_count
        direction = Matrix.Rotation(sweep_angle * factor, 4, normal) @ start_direction
        arc_points.append(vertex + direction * radius)

    gap = max(0.0, float(extension_gap))
    overshoot = max(0.0, float(extension_overshoot))
    segments = [
        (vertex + start_direction * gap, vertex + start_direction * (radius + overshoot)),
        (vertex + end_direction * gap, vertex + end_direction * (radius + overshoot)),
    ]
    segments.extend(zip(arc_points[:-1], arc_points[1:]))

    midpoint_direction = Matrix.Rotation(sweep_angle * 0.5, 4, normal) @ start_direction
    midpoint = vertex + midpoint_direction * radius
    return AngleLayout(
        vertex=vertex,
        ray_1=Vector(ray_1),
        ray_2=Vector(ray_2),
        plane_normal=normal,
        start_direction=start_direction,
        end_direction=end_direction,
        radius=radius,
        sweep_angle=sweep_angle,
        measured_angle=abs(sweep_angle),
        midpoint=midpoint,
        arc_points=arc_points,
        segments=segments,
    )


def text_rotation(layout):
    """Make a font object's local XY plane match the dimension plane."""
    rotation = Matrix((layout.line_direction, layout.offset_direction, layout.plane_normal)).transposed()
    return rotation.to_quaternion()
