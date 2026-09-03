"""Pure layout math for linear dimensions."""

from dataclasses import dataclass

from mathutils import Matrix, Vector

from ..constants import EPSILON


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


def text_rotation(layout):
    """Make a font object's local XY plane match the dimension plane."""
    rotation = Matrix((layout.line_direction, layout.offset_direction, layout.plane_normal)).transposed()
    return rotation.to_quaternion()
