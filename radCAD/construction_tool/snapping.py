"""Screen-space snapping against persistent construction guides."""

from dataclasses import dataclass

from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector, geometry

from .model import has_visible_construction_lines, iter_construction_lines
from .projection import closest_point_on_guide, guide_vectors


@dataclass
class ConstructionSnapCandidate:
    result: object
    distance_px: float
    is_point: bool


_INTERSECTION_CACHE = {}


def _guide_records(scene):
    records = []
    for line in iter_construction_lines(scene):
        vectors = guide_vectors(line)
        if vectors is not None:
            records.append(vectors)
    return records


def _intersection_signature(records):
    return tuple(
        tuple(anchor) + tuple(direction) + tuple(normal)
        for anchor, direction, normal in records
    )


def _guide_intersections(scene, records):
    scene_key = scene.as_pointer()
    signature = _intersection_signature(records)
    cached = _INTERSECTION_CACHE.get(scene_key)
    if cached is not None and cached[0] == signature:
        return cached[1]

    intersections = []
    for index, (anchor_a, direction_a, normal_a) in enumerate(records):
        for anchor_b, direction_b, _normal_b in records[index + 1:]:
            pair = geometry.intersect_line_line(
                anchor_a,
                anchor_a + direction_a,
                anchor_b,
                anchor_b + direction_b,
            )
            if pair is None:
                continue
            point_a, point_b = pair
            scale = max(1.0, anchor_a.length, anchor_b.length)
            if (point_a - point_b).length > scale * 1.0e-7:
                continue
            point = (point_a + point_b) * 0.5
            if any((existing[0] - point).length <= scale * 1.0e-7 for existing in intersections):
                continue
            intersections.append((point, normal_a.copy()))

    _INTERSECTION_CACHE[scene_key] = (signature, intersections)
    return intersections


def _screen_distance(context, point, mouse):
    projected = location_3d_to_region_2d(
        context.region,
        context.region_data,
        point,
        default=None,
    )
    if projected is None:
        return None
    return (Vector(projected) - Vector(mouse)).length


def snap_construction_lines(context, x, y, max_px):
    """Return the best anchor/intersection/line snap candidate under the mouse."""
    if (
        context.region is None
        or context.region_data is None
        or not has_visible_construction_lines(context.scene)
    ):
        return None

    from ..snapping_utils import SnapResult

    records = _guide_records(context.scene)
    mouse = Vector((x, y))
    ui_scale = max(1.0, float(context.preferences.system.ui_scale))
    radius = float(max_px) * ui_scale

    point_candidates = []
    for anchor, _direction, normal in records:
        distance = _screen_distance(context, anchor, mouse)
        if distance is not None and distance <= radius:
            point_candidates.append((distance, anchor, normal, "CONSTRUCTION_ANCHOR"))

    for point, normal in _guide_intersections(context.scene, records):
        distance = _screen_distance(context, point, mouse)
        if distance is not None and distance <= radius:
            point_candidates.append((distance, point, normal, "CONSTRUCTION_INTERSECTION"))

    if point_candidates:
        distance, point, normal, kind = min(point_candidates, key=lambda item: item[0])
        return ConstructionSnapCandidate(
            SnapResult(point.copy(), kind, normal.copy()),
            distance,
            True,
        )

    line_candidates = []
    for anchor, direction, normal in records:
        closest = closest_point_on_guide(context, anchor, direction, mouse)
        if closest is None:
            continue
        point, distance = closest
        if distance <= radius:
            line_candidates.append((distance, point, normal))

    if not line_candidates:
        return None
    distance, point, normal = min(line_candidates, key=lambda item: item[0])
    return ConstructionSnapCandidate(
        SnapResult(point.copy(), "CONSTRUCTION_LINE", normal.copy()),
        distance,
        False,
    )
