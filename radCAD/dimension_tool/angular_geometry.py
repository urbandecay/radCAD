"""Pure layout math for angular dimensions."""

from dataclasses import dataclass
import math

from mathutils import Matrix, Vector

from .constants import EPSILON


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
