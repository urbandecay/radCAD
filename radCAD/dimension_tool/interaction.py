"""Shared interaction helpers for dimension operators."""

import math
import time

import bpy
from mathutils import Vector

from ..inference_utils import get_axis_snapped_location, get_direction_snapped_location
from ..modal_core import DrawManager, is_event_over_ui
from ..modal_state import state
from ..orientation_utils import orthonormal_basis_from_normal
from ..snapping_utils import free_snap_context, invalidate_snap_cache
from .constants import DRAW_HANDLER_2D, DRAW_HANDLER_3D, DRAW_HANDLER_SNAP_HUD
from .drawing import (
    angle_dimension_hit_distance,
    dimension_hit_distance,
    draw_preview_2d,
    draw_preview_3d,
)
from .angular_formatting import format_dimension_angle
from .angular_geometry import build_angle_layout
from .linear_formatting import format_dimension_length
from .linear_geometry import dimension_basis
from .model import (
    create_angle_dimension,
    create_dimension,
    delete_dimension,
    dimension_layout,
    iter_dimensions,
    resolve_anchor,
    resolve_dimension_plane,
    selected_dimension,
    set_dimension_plane,
    update_dimension,
)
from .snapping import pick_point, project_to_plane


_GLOBAL_AXES = {
    "X": Vector((1.0, 0.0, 0.0)),
    "Y": Vector((0.0, 1.0, 0.0)),
    "Z": Vector((0.0, 0.0, 1.0)),
}
_AXIS_ALIGNED_DOT = math.cos(math.radians(1.0))


def _dimension_offset_axes(line_direction):
    """Return global axes projected into a dimension's cross-plane."""
    candidates = {}
    line_direction = Vector(line_direction)
    for axis_name, axis in _GLOBAL_AXES.items():
        projected = axis - line_direction * axis.dot(line_direction)
        if projected.length_squared > 1.0e-10:
            candidates[axis_name] = projected.normalized()
    return candidates


def _plane_axes(plane_normal):
    """Return global axes projected into the supplied drawing plane."""
    normal = Vector(plane_normal)
    if normal.length_squared <= 1.0e-10:
        return {}
    normal.normalize()
    candidates = {}
    for axis_name, axis in _GLOBAL_AXES.items():
        projected = axis - normal * axis.dot(normal)
        if projected.length_squared > 1.0e-10:
            candidates[axis_name] = projected.normalized()
    return candidates


def _cursor_driven_offset(
    context,
    event,
    p1,
    p2,
    fallback_normal,
    fallback_distance,
    dimension_direction=None,
    allow_projected=False,
):
    """Resolve a linear dimension's placement and optional direction.

    During creation, a cursor direction that is close to a global axis can
    choose the dimension direction itself.  This allows a diagonal pair of
    points to produce a horizontal or vertical projected dimension.  Existing
    dimensions pass their saved direction so repositioning only changes the
    offset and does not change what they measure.
    """
    aligned_basis = dimension_basis(p1, p2, fallback_normal)
    if aligned_basis is None:
        return None

    aligned_line_direction, aligned_fallback_direction, aligned_normal = aligned_basis
    midpoint = (Vector(p1) + Vector(p2)) * 0.5

    # Read the mouse on a view-facing plane, then use its position relative to
    # the measured midpoint to determine the dimension offset.
    view_normal = (
        context.region_data.view_matrix.inverted().to_3x3()
        @ Vector((0.0, 0.0, 1.0))
    ).normalized()
    placement = project_to_plane(
        context,
        event.mouse_region_x,
        event.mouse_region_y,
        midpoint,
        view_normal,
    )
    if placement is None:
        return None

    raw_offset = placement - midpoint
    requested = (
        Vector(dimension_direction)
        if dimension_direction is not None
        else Vector((0.0, 0.0, 0.0))
    )
    has_requested_direction = requested.length_squared > 1.0e-10

    # While creating a dimension, use the cursor's nearby global axis as the
    # offset direction. The perpendicular axis then becomes the dimension
    # direction, which creates projected horizontal/vertical measurements.
    if allow_projected and not has_requested_direction:
        strength = max(0.1, min(89.0, state.get("snap_strength", 6.0)))
        inferred, offset_axis, axis_name = get_direction_snapped_location(
            midpoint,
            (event.mouse_region_x, event.mouse_region_y),
            context,
            _plane_axes(fallback_normal),
            snap_threshold=math.cos(math.radians(strength)),
        )
        if inferred is not None and offset_axis is not None:
            offset_direction = offset_axis.normalized()
            line_direction = offset_direction.cross(Vector(fallback_normal))
            if line_direction.length_squared > 1.0e-10:
                line_direction.normalize()
                if line_direction.dot(Vector(p2) - Vector(p1)) < 0.0:
                    line_direction.negate()
                distance = abs((inferred - midpoint).dot(offset_direction))
                measured_length = abs(
                    (Vector(p2) - Vector(p1)).dot(line_direction)
                )
                if distance > 1.0e-8 and measured_length > 1.0e-8:
                    plane_normal = line_direction.cross(offset_direction)
                    plane_normal.normalize()
                    return (
                        midpoint + offset_direction * distance,
                        plane_normal,
                        distance,
                        _GLOBAL_AXES[axis_name].copy(),
                        line_direction,
                    )

    if has_requested_direction:
        basis = dimension_basis(
            p1,
            p2,
            fallback_normal,
            requested,
        )
        if basis is None:
            return None
        line_direction, fallback_direction, normal = basis
    else:
        line_direction = aligned_line_direction
        fallback_direction = aligned_fallback_direction
        normal = aligned_normal

    offset_direction = raw_offset - line_direction * raw_offset.dot(line_direction)
    if offset_direction.length_squared <= 1.0e-10:
        distance = float(fallback_distance)
        offset_direction = fallback_direction
    else:
        offset_direction.normalize()
        distance = raw_offset.length

    inferred_axis = None
    strength = max(0.1, min(89.0, state.get("snap_strength", 6.0)))
    axis_aligned = (
        max(abs(line_direction.dot(axis)) for axis in _GLOBAL_AXES.values())
        >= _AXIS_ALIGNED_DOT
    )
    snap_threshold = 0.0 if axis_aligned else math.cos(math.radians(strength))
    inferred, offset_axis, axis_name = get_direction_snapped_location(
        midpoint,
        (event.mouse_region_x, event.mouse_region_y),
        context,
        _dimension_offset_axes(line_direction),
        snap_threshold=snap_threshold,
    )
    if inferred is not None and offset_axis is not None:
        inferred_distance = (inferred - midpoint).dot(offset_axis)
        if inferred_distance < 0.0:
            offset_axis.negate()
            inferred_distance = -inferred_distance
        if inferred_distance > 1.0e-8:
            offset_direction = offset_axis
            distance = inferred_distance
            inferred_axis = _GLOBAL_AXES[axis_name].copy()

    plane_normal = line_direction.cross(offset_direction)
    if plane_normal.length_squared <= 1.0e-10:
        return None
    plane_normal.normalize()
    current = midpoint + offset_direction * distance
    saved_direction = line_direction.copy() if has_requested_direction else None
    return current, plane_normal, distance, inferred_axis, saved_direction


def _linear_measure_length(p1, p2, dimension_direction=None):
    """Return the aligned or projected length represented by two points."""
    delta = Vector(p2) - Vector(p1)
    if (
        dimension_direction is not None
        and Vector(dimension_direction).length_squared > 1.0e-10
    ):
        direction = Vector(dimension_direction).normalized()
        return abs(delta.dot(direction))
    return delta.length


def _project_point_to_plane(point, plane_point, plane_normal):
    point = Vector(point)
    plane_point = Vector(plane_point)
    normal = Vector(plane_normal)
    if normal.length_squared <= 1.0e-12:
        return point
    normal.normalize()
    delta = point - plane_point
    return point - normal * delta.dot(normal)


def _cursor_driven_angle_radius(context, event, vertex, plane_normal, fallback_radius):
    """Resolve an angle annotation radius from the cursor on its dimension plane."""
    placement = project_to_plane(
        context,
        event.mouse_region_x,
        event.mouse_region_y,
        vertex,
        plane_normal,
    )
    if placement is None:
        return None
    placement = _project_point_to_plane(placement, vertex, plane_normal)
    radius = (placement - Vector(vertex)).length
    if radius <= 1.0e-8:
        return Vector(vertex), abs(float(fallback_radius))
    return placement, radius


def _angle_preview_layout(operator):
    ray_2 = getattr(operator, "ray_2", None)
    if ray_2 is None:
        ray_2 = getattr(operator, "current", None)
    if (
        getattr(operator, "vertex", None) is None
        or getattr(operator, "ray_1", None) is None
        or ray_2 is None
    ):
        return None
    return build_angle_layout(
        operator.vertex,
        operator.ray_1,
        ray_2,
        operator.plane_normal,
        operator.offset_distance,
        0.001,
        0.001,
        0.0,
        0.0,
    )


def _axis_for_key(key):
    return {
        "X": Vector((1.0, 0.0, 0.0)),
        "Y": Vector((0.0, 1.0, 0.0)),
        "Z": Vector((0.0, 0.0, 1.0)),
    }[key]


def _compass_axis_snap(vertex, point, plane_normal, alignment_degrees):
    """Snap a compass ray to an aligned global axis in its active plane."""
    vertex = Vector(vertex)
    point = Vector(point)
    normal = Vector(plane_normal)
    if normal.length_squared <= 1.0e-12:
        return None, None, None
    normal.normalize()

    ray = point - vertex
    ray -= normal * ray.dot(normal)
    ray_length = ray.length
    if ray_length <= 1.0e-8:
        return None, None, None
    ray.normalize()

    alignment_limit = math.cos(
        math.radians(max(0.1, min(89.0, alignment_degrees)))
    )
    best = None
    best_alignment = alignment_limit
    for axis_name, axis in _GLOBAL_AXES.items():
        # A true global axis can only remain on the selected compass plane
        # when the plane normal is perpendicular to it.  Do not move a ray
        # out of the measurement plane just to force an axis snap.
        if abs(normal.dot(axis)) > 1.0e-4:
            continue
        alignment = abs(ray.dot(axis))
        if alignment < best_alignment:
            continue
        signed_axis = axis.copy()
        if ray.dot(signed_axis) < 0.0:
            signed_axis.negate()
        best = (vertex + signed_axis * ray_length, signed_axis, axis_name)
        best_alignment = alignment
    return best if best is not None else (None, None, None)
