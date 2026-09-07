"""Shared interaction implementations for dimension operators."""

import math
import time

import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
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
from .angular.formatting import format_dimension_angle
from .angular.geometry import build_angle_layout
from . import debug
from .linear.formatting import format_dimension_length
from .linear.geometry import (
    dimension_basis,
    dimension_plane_from_face,
    projected_line_direction,
)
from .model import (
    create_angle_dimension,
    create_dimension,
    delete_dimension,
    dimension_layout,
    iter_dimensions,
    resolve_anchor,
    resolve_dimension_plane,
    selected_dimension,
    set_anchor,
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


def _axis_aligned_view_normal(context):
    """Return the screen-plane normal for an axis-aligned ortho view.

    A snapped vertex can be shared by faces with several different normals.
    In a true 2D orthographic view those face normals must not decide whether
    the dimension can be horizontal or vertical; the viewport plane does.
    Oblique and perspective views keep the existing picked-plane behavior.
    """
    rv3d = getattr(context, "region_data", None)
    if rv3d is None or getattr(rv3d, "view_perspective", None) != "ORTHO":
        return None
    try:
        view_normal = rv3d.view_matrix.inverted().to_3x3() @ Vector((0.0, 0.0, 1.0))
    except (AttributeError, ValueError):
        return None
    if view_normal.length_squared <= 1.0e-12:
        return None
    view_normal.normalize()
    best_alignment = max(
        abs(view_normal.dot(axis)) for axis in _GLOBAL_AXES.values()
    )
    if best_alignment < _AXIS_ALIGNED_DOT:
        return None
    return view_normal


def _projected_dimension_plane(context, fallback_normal):
    """Choose the plane used to infer projected X/Y/Z dimensions."""
    view_normal = _axis_aligned_view_normal(context)
    if view_normal is not None:
        return view_normal
    normal = (
        Vector(fallback_normal)
        if fallback_normal is not None
        else Vector((0.0, 0.0, 1.0))
    )
    if normal.length_squared <= 1.0e-12:
        return Vector((0.0, 0.0, 1.0))
    return normal.normalized()


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


def _picked_face_normal(pick):
    """Return a real supporting-surface normal, not a free-cursor fallback."""
    if pick is None:
        return None
    if hasattr(pick, "face_normal"):
        # A PickResult explicitly reports ``None`` when a component snap had
        # no face/raycast normal. Do not reinterpret its drawing-plane normal
        # as a supporting face.
        normal = pick.face_normal
    else:
        # PickResult gained face_normal after dimensions already existed. Keep
        # test doubles and reloaded operator instances compatible with the old
        # shape while still requiring an actual snap result.
        if getattr(pick, "snap_result", None) is None:
            return None
        normal = getattr(pick, "normal", None)
    if normal is None:
        return None
    normal = Vector(normal)
    if normal.length_squared <= 1.0e-12:
        return None
    return normal.normalized()


def _snap_face_normals(snap_result):
    """Return world normals of faces attached to a snapped mesh component."""
    if snap_result is None:
        return []
    obj = getattr(snap_result, "target_object", None)
    indices = getattr(snap_result, "element_indices", ())
    if obj is None or getattr(obj, "type", None) != "MESH" or not indices:
        return []

    wanted = {int(index) for index in indices}
    try:
        target_matrix = getattr(snap_result, "target_matrix", None) or obj.matrix_world
        normal_matrix = target_matrix.to_3x3().inverted().transposed()
    except (AttributeError, ValueError):
        normal_matrix = obj.matrix_world.to_3x3()

    if obj.mode == "EDIT":
        import bmesh
        mesh = bmesh.from_edit_mesh(obj.data)
        mesh.verts.ensure_lookup_table()
        mesh.verts.index_update()
        mesh.normal_update()
        polygons = (({v.index for v in face.verts}, face.normal) for face in mesh.faces)
    else:
        polygons = ((set(face.vertices), face.normal) for face in obj.data.polygons)
    normals = []
    for vertices, local_normal in polygons:
        if not wanted.issubset(vertices):
            continue
        normal = normal_matrix @ Vector(local_normal)
        if normal.length_squared <= 1.0e-12:
            continue
        normal.normalize()
        if not any(abs(normal.dot(existing)) > 1.0 - 1.0e-6 for existing in normals):
            normals.append(normal)
    return normals


def _supporting_face_normal(snap_1, snap_2, preferred=None):
    """Choose the face normal attached to the measured component.

    Vertex and edge snaps do not carry a face normal themselves. Looking up
    the faces sharing both measured components prevents viewport orientation
    or a missed raycast from turning a face-backed dimension into a free 3D
    plane.
    """
    normals_1 = _snap_face_normals(snap_1)
    normals_2 = _snap_face_normals(snap_2)
    candidates = []
    if normals_1 and normals_2:
        for normal_1 in normals_1:
            for normal_2 in normals_2:
                if abs(normal_1.dot(normal_2)) > 1.0 - 1.0e-6:
                    candidates.append(normal_1.copy())
                    break
    if not candidates:
        candidates.extend(normals_1)
        candidates.extend(normals_2)
    if not candidates:
        return None

    preferred = Vector(preferred) if preferred is not None else None
    if preferred is not None and preferred.length_squared > 1.0e-12:
        preferred.normalize()
        return min(
            candidates,
            key=lambda normal: 1.0 - abs(normal.dot(preferred)),
        ).normalized()
    return candidates[0].normalized()


def _span_face_normals(snap_1, snap_2, p1, p2, fallback):
    """Collect faces containing both snapped components on the same mesh."""
    candidates = []
    if (snap_1 is not None and snap_2 is not None
            and getattr(snap_1, "target_object", None) is getattr(snap_2, "target_object", None)):
        from types import SimpleNamespace
        indices = tuple(getattr(snap_1, "element_indices", ())) + tuple(getattr(snap_2, "element_indices", ()))
        if indices:
            candidates = _snap_face_normals(SimpleNamespace(
                target_object=snap_1.target_object,
                target_matrix=getattr(snap_1, "target_matrix", None),
                element_indices=indices,
            ))
    span = (Vector(p2) - Vector(p1)).normalized()
    candidates = [normal for normal in candidates if abs(normal.dot(span)) < 1.0e-5]
    return candidates or ([fallback.copy()] if fallback is not None else [])


def _screen_direction(context, origin, direction):
    """Project a world direction into the current viewport."""
    origin = Vector(origin)
    direction = Vector(direction)
    if direction.length_squared <= 1.0e-12:
        return None
    start = location_3d_to_region_2d(context.region, context.region_data, origin)
    end = location_3d_to_region_2d(
        context.region,
        context.region_data,
        origin + direction.normalized(),
    )
    if start is None or end is None:
        return None
    projected = Vector(end) - Vector(start)
    if projected.length_squared <= 1.0e-12:
        return None
    return projected.normalized()


def _cursor_face_plane_mode(
    context,
    event,
    p1,
    p2,
    face_normal,
    picked_face_normal=None,
):
    """Choose FACE or NORMAL from the placement cursor, never an arbitrary plane.

    A free third point has no depth information by itself. Its screen-space
    movement does, however, tell us which of the two allowed offset axes the
    user is indicating whenever both axes are visible. A cursor on the source
    face stays FACE; a cursor on a perpendicular face becomes NORMAL. If the
    normal axis is edge-on, the explicit N/Alt controls remain available.
    """
    if picked_face_normal is not None:
        return (
            "NORMAL"
            if abs(Vector(picked_face_normal).normalized().dot(Vector(face_normal).normalized())) < 0.95
            else "FACE"
        )

    midpoint = (Vector(p1) + Vector(p2)) * 0.5
    face_plane = dimension_plane_from_face(p1, p2, face_normal, "FACE")
    normal_plane = dimension_plane_from_face(p1, p2, face_normal, "NORMAL")
    if face_plane is None or normal_plane is None:
        return "FACE"
    face_basis = dimension_basis(p1, p2, face_plane)
    normal_direction = projected_line_direction(p1, p2, normal_plane)
    normal_basis = dimension_basis(p1, p2, normal_plane, normal_direction)
    if face_basis is None or normal_basis is None:
        return "FACE"

    cursor = Vector((event.mouse_region_x, event.mouse_region_y))
    screen_midpoint = location_3d_to_region_2d(
        context.region,
        context.region_data,
        midpoint,
    )
    if screen_midpoint is None:
        return "FACE"
    cursor_delta = cursor - Vector(screen_midpoint)
    if cursor_delta.length_squared <= 36.0:
        return "FACE"
    cursor_delta.normalize()

    face_direction = _screen_direction(context, midpoint, face_basis[1])
    normal_direction = _screen_direction(context, midpoint, normal_basis[1])
    if normal_direction is None:
        return "FACE"
    if face_direction is None:
        # When the source face is edge-on, its in-face offset axis is
        # invisible in the viewport. Any visible placement movement is then
        # the normal-to-face option; falling back to FACE here made that
        # option impossible in exactly that view.
        return "NORMAL"
    face_score = abs(cursor_delta.dot(face_direction))
    normal_score = abs(cursor_delta.dot(normal_direction))
    if normal_score > face_score + 0.08:
        return "NORMAL"
    return "FACE"


def _cursor_placement_point(
    context,
    event,
    plane_point,
    plane_normal,
    pick=None,
    fallback_direction=None,
    fallback_distance=0.0,
    max_distance=None,
):
    """Return the third point projected onto the already selected plane.

    The third click positions the annotation. It must never be allowed to
    twist the annotation plane. A geometry snap is projected back onto that
    plane as well; this prevents a nearby side face from pulling a face-flush
    dimension out of the source plane.
    """
    pick = pick if pick is not None else pick_point(context, event)
    plane_point = Vector(plane_point)
    if pick.snap_result is None:
        projected = project_to_plane(
            context,
            event.mouse_region_x,
            event.mouse_region_y,
            plane_point,
            plane_normal,
        )
        if projected is None:
            projected = pick.point.copy()
    else:
        projected = _project_point_to_plane(pick.point, plane_point, plane_normal)

    projected_is_unusable = projected is None or (
        max_distance is not None
        and (projected - plane_point).length > max_distance
    )
    if fallback_direction is not None and projected_is_unusable:
        fallback_direction = Vector(fallback_direction)
        if fallback_direction.length_squared > 1.0e-12:
            fallback_direction.normalize()

            # In perspective, a ray that is almost parallel to the selected
            # annotation plane can technically intersect it hundreds of
            # units away. That intersection is mathematically valid but is
            # not a usable placement for a dimension created beside the
            # measured object. Recover the cursor's scalar offset in screen
            # space along the one permitted offset axis instead.
            screen_midpoint = location_3d_to_region_2d(
                context.region,
                context.region_data,
                plane_point,
            )
            screen_offset_point = location_3d_to_region_2d(
                context.region,
                context.region_data,
                plane_point + fallback_direction,
            )
            screen_offset = (
                Vector(screen_offset_point) - Vector(screen_midpoint)
                if screen_midpoint is not None and screen_offset_point is not None
                else None
            )
            if screen_offset is not None and screen_offset.length_squared > 1.0e-10:
                cursor = Vector((event.mouse_region_x, event.mouse_region_y))
                distance = (
                    (cursor - Vector(screen_midpoint)).dot(screen_offset)
                    / screen_offset.length_squared
                )
                if max_distance is not None:
                    distance = max(
                        -float(max_distance),
                        min(float(max_distance), distance),
                    )
                projected = plane_point + fallback_direction * distance
            else:
                projected = plane_point + fallback_direction * float(fallback_distance)
        else:
            projected = plane_point.copy()
    if projected is None:
        projected = plane_point.copy()
    if (projected - pick.point).length_squared > 1.0e-12:
        pick.snap_result = None
        state["snap_point"] = None
        state["geometry_snap"] = False
    pick.point = projected
    return pick


def _fixed_plane_offset(
    context,
    event,
    p1,
    p2,
    plane_normal,
    fallback_distance,
    dimension_direction=None,
    max_distance=None,
):
    """Resolve a reposition offset while keeping an established plane fixed."""
    basis = dimension_basis(p1, p2, plane_normal, dimension_direction)
    if basis is None:
        return None

    midpoint = (Vector(p1) + Vector(p2)) * 0.5
    if max_distance is None:
        max_distance = max(
            (Vector(p2) - Vector(p1)).length * 10.0,
            1.0,
        )
    placement = project_to_plane(
        context,
        event.mouse_region_x,
        event.mouse_region_y,
        midpoint,
        plane_normal,
    )
    if placement is None or (
        max_distance is not None
        and (placement - midpoint).length > max_distance
    ):
        placement = None

    if placement is None:
        # A perpendicular annotation plane can be edge-on to the current
        # view. In that case a mouse ray has no reliable intersection with
        # the plane, even though the plane's offset axis is still visible on
        # screen. Recover only the scalar offset along the established basis
        # axis; never replace the selected dimension plane with a cursor-
        # inferred direction.
        screen_midpoint = location_3d_to_region_2d(
            context.region,
            context.region_data,
            midpoint,
        )
        screen_offset_point = location_3d_to_region_2d(
            context.region,
            context.region_data,
            midpoint + basis[1],
        )
        if screen_midpoint is not None and screen_offset_point is not None:
            screen_offset = Vector(screen_offset_point) - Vector(screen_midpoint)
            if screen_offset.length_squared > 1.0e-10:
                cursor = Vector((event.mouse_region_x, event.mouse_region_y))
                distance = (
                    (cursor - Vector(screen_midpoint)).dot(screen_offset)
                    / screen_offset.length_squared
                )
                distance = max(-float(max_distance), min(float(max_distance), distance))
                placement = midpoint + basis[1] * distance

    if placement is None:
        placement = midpoint + basis[1] * float(fallback_distance)

    raw_offset = placement - midpoint
    distance = raw_offset.dot(basis[1])
    if abs(distance) <= 1.0e-10:
        distance = float(fallback_distance)
    dimension_point = midpoint + basis[1] * distance
    return placement, dimension_point, Vector(plane_normal).normalized(), distance, basis[0]


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
    """Resolve free-space linear placement and optional direction.

    Face-backed dimensions use the fixed-plane path in the creation operator;
    this path remains for dimensions without a supporting face and for legacy
    records so their existing axis inference continues to work.
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
    # Alt remains a bypass for axis inference, but it is not required for an
    # aligned dimension.  The mouse position itself decides which placement
    # mode is active.
    bypass_projected = bool(getattr(event, "alt", False)) and allow_projected
    requested = (
        Vector(dimension_direction)
        if dimension_direction is not None and not bypass_projected
        else Vector((0.0, 0.0, 0.0))
    )
    has_requested_direction = requested.length_squared > 1.0e-10

    # While creating a dimension, use the cursor's nearby global axis as the
    # offset direction. The perpendicular axis then becomes the dimension
    # direction, which creates projected horizontal/vertical measurements.
    # Re-evaluate this on every mouse move.  A projected direction is not
    # sticky: moving the cursor away from the global axes must return to the
    # measured span's own direction.
    if allow_projected and not bypass_projected:
        projected_normal = _projected_dimension_plane(context, fallback_normal)
        strength = max(0.1, min(89.0, state.get("snap_strength", 6.0)))
        inferred, offset_axis, axis_name = get_direction_snapped_location(
            midpoint,
            (event.mouse_region_x, event.mouse_region_y),
            context,
            _plane_axes(projected_normal),
            snap_threshold=math.cos(math.radians(strength)),
        )
        if inferred is not None and offset_axis is not None:
            offset_direction = offset_axis.normalized()
            line_direction = offset_direction.cross(projected_normal)
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

    # Saved projected dimensions (reposition/drag paths) keep their requested
    # direction. During creation, however, a missed projected inference means
    # the cursor is asking for the normal/aligned dimension again.
    if has_requested_direction and not allow_projected:
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
    saved_direction = (
        line_direction.copy()
        if has_requested_direction and not allow_projected
        else None
    )
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


class VIEW3D_OT_radcad_dimension_angle(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_angle"
    bl_label = "Angle Dimension"
    bl_description = "Create an angle dimension from a vertex and two rays"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    running = False
    dimension_type = "ANGLE"

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == "VIEW_3D"
            and context.mode in {"OBJECT", "EDIT_MESH"}
        )

    def invoke(self, context, event):
        if context.region is None or context.region.type != "WINDOW":
            self.report({"WARNING"}, "Run the Angle Dimension tool from a 3D View")
            return {"CANCELLED"}

        DrawManager.clear_all()
        invalidate_snap_cache()
        # The angle tool uses the same shared snap state as Rotate/Arc so the
        # existing marker and F-key snap bar can be reused without duplicating
        # the snap overlay implementation.
        state["active"] = True
        state["tool_mode"] = "DIMENSION_ANGLE"
        state["snap_point"] = None
        state["geometry_snap"] = False
        state["ui_hitboxes"] = {}
        self.context = context
        self.stage = 0
        self.vertex = None
        self.ray_1 = None
        self.ray_2 = None
        self.current = None
        self.current_pick = None
        self.pick_vertex = None
        self.pick_ray_1 = None
        self.pick_ray_2 = None
        self.plane_normal = None
        self.compass_center = None
        self.compass_plane_normal = None
        self.compass_x = None
        self.compass_y = None
        self.compass_rotation = 0.0
        self.axis_snap_name = None
        self.axis_snap_vector = None
        self.plane_locked = False
        self.locked_plane_point = None
        self.offset_distance = 0.0
        self.preview_label = ""
        self.running = True
        self.tool_instance_id = f"DIMENSION_ANGLE_{time.time()}"
        context.scene.active_cad_tool_id = self.tool_instance_id
        context.scene.radcad_dimension_icon = "dimension_linear"
        debug.start_preview(self, "ANGLE")
        debug.log_dimension_snapshot(
            context.scene,
            "angle_invoke",
            instance=self.tool_instance_id,
            handlers=debug.handler_snapshot(),
        )
        context.window.cursor_modal_set("DEFAULT")
        DrawManager.add_handler(DRAW_HANDLER_3D, draw_preview_3d, (self,), "WINDOW", "POST_VIEW")
        DrawManager.add_handler(DRAW_HANDLER_2D, draw_preview_2d, (self,), "WINDOW", "POST_PIXEL")
        from ..hud_overlay import draw_hud_2d

        DrawManager.add_handler(
            DRAW_HANDLER_SNAP_HUD,
            draw_hud_2d,
            (),
            "WINDOW",
            "POST_PIXEL",
        )
        context.window_manager.modal_handler_add(self)
        self._update(context, event)
        return {"RUNNING_MODAL"}

    def _set_compass_plane(self, normal):
        normal = Vector(normal)
        if normal.length_squared <= 1.0e-12:
            return False
        normal.normalize()
        self.compass_plane_normal = normal
        self.compass_x, self.compass_y, _normal = orthonormal_basis_from_normal(normal)
        return self.compass_x is not None and self.compass_y is not None

    def _update_compass_rotation(self, point):
        if self.vertex is None or self.compass_x is None or self.compass_y is None:
            return
        direction = Vector(point) - self.vertex
        direction -= self.compass_plane_normal * direction.dot(self.compass_plane_normal)
        if direction.length_squared <= 1.0e-12:
            return
        self.compass_rotation = math.atan2(direction.dot(self.compass_y), direction.dot(self.compass_x))

    def _handle_plane_input(self, context, event):
        if self.stage != 0 or event.value != "PRESS":
            return False

        if event.type == "L":
            if self.plane_locked:
                self.plane_locked = False
                self.locked_plane_point = None
                self.report({"INFO"}, "Angle plane unlocked")
            elif self.compass_plane_normal is not None:
                self.plane_locked = True
                self.locked_plane_point = (
                    self.compass_center.copy()
                    if self.compass_center is not None
                    else Vector((0.0, 0.0, 0.0))
                )
                self.report({"INFO"}, "Angle plane locked")
            self._update(context, event)
            return True

        if event.type not in {"X", "Y", "Z"}:
            return False

        axis = _axis_for_key(event.type)
        if self.plane_locked and self.compass_plane_normal is not None and abs(self.compass_plane_normal.dot(axis)) > 0.99:
            self.plane_locked = False
            self.locked_plane_point = None
            self.report({"INFO"}, f"Unlocked {event.type}-Plane")
        else:
            self._set_compass_plane(axis)
            self.plane_locked = True
            self.locked_plane_point = (
                self.compass_center.copy()
                if self.compass_center is not None
                else Vector((0.0, 0.0, 0.0))
            )
            self.report({"INFO"}, f"Locked to {event.type}-Plane")
        self._update(context, event)
        return True

    def _update(self, context, event):
        state["current_axis_vector"] = None
        self.axis_snap_name = None
        self.axis_snap_vector = None
        if self.stage == 0:
            if self.plane_locked and self.locked_plane_point is not None:
                pick = pick_point(
                    context,
                    event,
                    self.locked_plane_point,
                    self.compass_plane_normal,
                )
            else:
                pick = pick_point(context, event)
            self.current = pick.point
            self.current_pick = pick
            if self.plane_locked:
                projected = _project_point_to_plane(
                    self.current,
                    self.locked_plane_point,
                    self.compass_plane_normal,
                )
                if (projected - self.current).length_squared > 1.0e-12:
                    pick.snap_result = None
                    state["snap_point"] = None
                    state["geometry_snap"] = False
                self.current = projected
            else:
                self._set_compass_plane(
                    pick.normal if pick.normal is not None else Vector((0.0, 0.0, 1.0))
                )
            self.compass_center = self.current.copy()
        elif self.stage == 1:
            pick = pick_point(context, event, self.vertex, self.plane_normal)
            projected = _project_point_to_plane(pick.point, self.vertex, self.plane_normal)
            if (projected - pick.point).length_squared > 1.0e-12:
                pick.snap_result = None
                state["snap_point"] = None
                state["geometry_snap"] = False
            self.current = projected
            self.current_pick = pick
            self.compass_center = self.vertex.copy()
            self._update_compass_rotation(self.current)
            if state.get("angle_axis_snap", False) and not state.get("geometry_snap", False):
                snapped, axis_vector, axis_name = _compass_axis_snap(
                    self.vertex,
                    self.current,
                    self.plane_normal,
                    state.get("snap_strength", 6.0),
                )
                if snapped is not None:
                    self.current = snapped
                    self.axis_snap_name = axis_name
                    self.axis_snap_vector = axis_vector
                    state["current_axis_vector"] = axis_vector.copy()
                    self._update_compass_rotation(self.current)
        else:
            pick = pick_point(context, event, self.vertex, self.plane_normal)
            projected = _project_point_to_plane(pick.point, self.vertex, self.plane_normal)
            if (projected - pick.point).length_squared > 1.0e-12:
                pick.snap_result = None
                state["snap_point"] = None
                state["geometry_snap"] = False
            self.current = projected
            self.current_pick = pick
            self.compass_center = self.vertex.copy()
            if state.get("angle_axis_snap", False) and not state.get("geometry_snap", False):
                snapped, axis_vector, axis_name = _compass_axis_snap(
                    self.vertex,
                    self.current,
                    self.plane_normal,
                    state.get("snap_strength", 6.0),
                )
                if snapped is not None:
                    self.current = snapped
                    self.axis_snap_name = axis_name
                    self.axis_snap_vector = axis_vector
                    state["current_axis_vector"] = axis_vector.copy()
            layout = _angle_preview_layout(self)
            self.preview_label = (
                format_dimension_angle(layout.measured_angle, context.scene)
                if layout is not None
                else ""
            )
        state["stage"] = self.stage
        state["current"] = self.current.copy() if self.current is not None else None
        state["pivot"] = self.vertex.copy() if self.vertex is not None else None
        context.area.tag_redraw()

    def _handle_snap_hud_click(self, context, event):
        """Toggle a snap button from the shared Rotate/Arc snap bar."""
        mouse_x = event.mouse_region_x
        mouse_y = event.mouse_region_y
        snap_keys = {
            "snap_verts",
            "snap_edges",
            "snap_edge_center",
            "snap_face_center",
            "snap_faces",
        }
        for hit_id, bounds in state.get("ui_hitboxes", {}).items():
            if hit_id not in snap_keys and hit_id != "angle_axis_snap":
                continue
            xmin, xmax, ymin, ymax = bounds
            if xmin <= mouse_x <= xmax and ymin <= mouse_y <= ymax:
                state[hit_id] = not state.get(hit_id, False)
                invalidate_snap_cache()
                self._update(context, event)
                return True
        return False

    def _click(self, context, event):
        if self.stage == 0:
            self.vertex = self.current.copy()
            self.pick_vertex = self.current_pick.snap_result
            self.plane_normal = self.compass_plane_normal.copy() if self.compass_plane_normal is not None else self.current_pick.normal.copy()
            if self.plane_normal.length_squared <= 1.0e-12:
                self.plane_normal = Vector((0.0, 0.0, 1.0))
            self.plane_normal.normalize()
            self._set_compass_plane(self.plane_normal)
            self.compass_center = self.vertex.copy()
            self.stage = 1
            return {"RUNNING_MODAL"}

        if self.stage == 1:
            if (self.current - self.vertex).length <= 1.0e-8:
                self.report({"WARNING"}, "The first ray point must be different from the vertex")
                return {"RUNNING_MODAL"}
            self.ray_1 = self.current.copy()
            self.pick_ray_1 = self.current_pick.snap_result
            self.offset_distance = (self.ray_1 - self.vertex).length
            self._update_compass_rotation(self.ray_1)
            self.stage = 2
            self._update(context, event)
            return {"RUNNING_MODAL"}

        if self.current is None or (self.current - self.vertex).length <= 1.0e-8:
            self.report({"WARNING"}, "The second ray point must be different from the vertex")
            return {"RUNNING_MODAL"}
        self.ray_2 = self.current.copy()
        self.pick_ray_2 = self.current_pick.snap_result
        layout = _angle_preview_layout(self)
        if layout is None:
            self.report({"WARNING"}, "The three points must define a non-zero angle")
            return {"RUNNING_MODAL"}

        create_angle_dimension(
            context,
            self.vertex,
            self.ray_1,
            self.ray_2,
            layout.plane_normal,
            self.offset_distance,
            self.pick_vertex,
            self.pick_ray_1,
            self.pick_ray_2,
        )
        self.finish(context)
        return {"FINISHED"}

    def modal(self, context, event):
        if context.scene.active_cad_tool_id != self.tool_instance_id:
            self.finish(context)
            return {"CANCELLED"}

        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}
        if event.type == "MOUSEMOVE":
            if is_event_over_ui(context, event):
                return {"RUNNING_MODAL"}
            self._update(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if is_event_over_ui(context, event):
                return {"PASS_THROUGH"}
            if self._handle_snap_hud_click(context, event):
                return {"RUNNING_MODAL"}
            return self._click(context, event)
        if event.type in {"BACK_SPACE", "BACKSPACE"} and event.value == "PRESS":
            if self.stage == 2:
                self.stage = 1
                self.ray_2 = None
            elif self.stage == 1:
                self.stage = 0
                self.vertex = None
                self.ray_1 = None
                self.offset_distance = 0.0
            self._update(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "ESC" and event.value == "PRESS":
            self.finish(context)
            return {"CANCELLED"}
        if event.value == "PRESS" and event.type in {"L", "X", "Y", "Z"}:
            if self._handle_plane_input(context, event):
                return {"RUNNING_MODAL"}
        if event.value == "PRESS" and event.type == "A":
            state["angle_axis_snap"] = not state.get("angle_axis_snap", False)
            invalidate_snap_cache()
            self._update(context, event)
            return {"RUNNING_MODAL"}
        if event.value == "PRESS" and event.type in {"F1", "F2", "F3", "F4", "F5"}:
            key = {
                "F1": "snap_verts",
                "F2": "snap_edges",
                "F3": "snap_edge_center",
                "F4": "snap_face_center",
                "F5": "snap_faces",
            }[event.type]
            state[key] = not state.get(key, False)
            invalidate_snap_cache()
            self._update(context, event)
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def finish(self, context):
        if not self.running:
            return
        debug.log(
            "angle_finish_begin",
            instance=getattr(self, "tool_instance_id", ""),
            stage=getattr(self, "stage", None),
            handlers=debug.handler_snapshot(),
        )
        self.running = False
        debug.stop_preview(self, "angle_finish")
        state["current_axis_vector"] = None
        state["active"] = False
        state["snap_point"] = None
        state["geometry_snap"] = False
        state["ui_hitboxes"] = {}
        DrawManager.remove_handler(DRAW_HANDLER_3D)
        DrawManager.remove_handler(DRAW_HANDLER_2D)
        DrawManager.remove_handler(DRAW_HANDLER_SNAP_HUD)
        free_snap_context()
        if context.scene.active_cad_tool_id == self.tool_instance_id:
            context.scene.active_cad_tool_id = ""
        try:
            context.window.cursor_modal_restore()
        except RuntimeError:
            pass
        debug.log(
            "angle_finish_end",
            instance=getattr(self, "tool_instance_id", ""),
            handlers=debug.handler_snapshot(),
        )
        context.area.tag_redraw()


class VIEW3D_OT_radcad_dimension_linear(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_linear"
    bl_label = "Linear Dimension"
    bl_description = "Create a face-aligned linear dimension from two points and a placement point"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    running = False

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == "VIEW_3D"
            and context.mode in {"OBJECT", "EDIT_MESH"}
        )

    def invoke(self, context, event):
        if context.region is None or context.region.type != "WINDOW":
            self.report({"WARNING"}, "Run the Dimension tool from a 3D View")
            return {"CANCELLED"}

        DrawManager.clear_all()
        invalidate_snap_cache()
        self.context = context
        self.stage = 0
        self.p1 = None
        self.p2 = None
        self.current = None
        self.pick_1 = None
        self.pick_2 = None
        self.placement_point = None
        self.pick_placement = None
        self.plane_normal = None
        self.face_normal = None
        self.face_normals = []
        self.face_plane_mode = "FACE"
        self.face_plane_mode_override = None
        self.linear_direction = None
        self.offset_distance = 0.0
        self.preview_label = ""
        self.running = True
        self.tool_instance_id = f"DIMENSION_LINEAR_{time.time()}"
        context.scene.active_cad_tool_id = self.tool_instance_id
        context.scene.radcad_dimension_icon = "dimension_linear"
        debug.start_preview(self, "LINEAR")
        debug.log_dimension_snapshot(
            context.scene,
            "linear_invoke",
            instance=self.tool_instance_id,
            handlers=debug.handler_snapshot(),
        )
        context.window.cursor_modal_set("DEFAULT")
        DrawManager.add_handler(DRAW_HANDLER_3D, draw_preview_3d, (self,), "WINDOW", "POST_VIEW")
        DrawManager.add_handler(DRAW_HANDLER_2D, draw_preview_2d, (self,), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self)
        self._update(context, event)
        return {"RUNNING_MODAL"}

    def _update(self, context, event):
        state["current_axis_vector"] = None
        if self.stage == 0:
            pick = pick_point(context, event)
            self.current = pick.point
            self.current_pick = pick
        elif self.stage == 1:
            pick = pick_point(context, event, self.p1, self.plane_normal)
            self.current = pick.point
            self.current_pick = pick
            # Match the line tool's mouse-driven global X/Y/Z inference. Exact
            # geometry snaps retain priority; free and surface picks may infer
            # an axis even when it leaves the first point's drawing plane.
            # A supporting face is authoritative for a dimension. Once one is
            # present, keep the second point on that face-based workflow plane
            # instead of letting global-axis inference pull it away.
            if (
                not state.get("geometry_snap", False)
                and self.face_normal is None
                and _picked_face_normal(pick) is None
            ):
                strength = max(0.1, min(89.0, state.get("snap_strength", 6.0)))
                inferred, axis, _axis_name = get_axis_snapped_location(
                    self.p1,
                    (event.mouse_region_x, event.mouse_region_y),
                    context,
                    snap_threshold=math.cos(math.radians(strength)),
                )
                if inferred is not None:
                    self.current = inferred
                    state["current_axis_vector"] = axis
                    # The inferred point is no longer the surface point returned
                    # by pick_point, so it must not retain that associative anchor.
                    self.current_pick.snap_result = None
            self.preview_label = format_dimension_length((self.current - self.p1).length, context.scene)
        else:
            midpoint = (self.p1 + self.p2) * 0.5
            if self.face_normal is None:
                # Preserve the existing free-space behavior when no source
                # face is available. The face-backed path below is the one
                # that must not infer a new plane from the placement cursor.
                resolved = _cursor_driven_offset(
                    context,
                    event,
                    self.p1,
                    self.p2,
                    self.plane_normal,
                    self.offset_distance,
                    dimension_direction=self.linear_direction,
                    allow_projected=True,
                )
                if resolved is not None:
                    (
                        self.current,
                        self.plane_normal,
                        self.offset_distance,
                        axis,
                        self.linear_direction,
                    ) = resolved
                    state["current_axis_vector"] = axis
                    self.face_plane_mode = "PROJECTED"
                self.placement_point = self.current.copy()
                self.pick_placement = None
                self.preview_label = format_dimension_length(
                    _linear_measure_length(
                        self.p1,
                        self.p2,
                        self.linear_direction,
                    ),
                    context.scene,
                )
            else:
                probe = pick_point(context, event)
                picked_face_normal = _picked_face_normal(probe)
                if self.face_plane_mode_override is not None:
                    mode = self.face_plane_mode_override
                elif getattr(event, "alt", False):
                    # Alt is an explicit placement modifier for face-backed
                    # dimensions: hold it while placing to put the
                    # annotation in the plane normal to the source face.
                    mode = "NORMAL"
                else:
                    mode = _cursor_face_plane_mode(
                        context,
                        event,
                        self.p1,
                        self.p2,
                        self.face_normal,
                        picked_face_normal,
                    )
                self.face_plane_mode = mode
                # Each supporting face supplies one legal offset per mode.
                # Choose the one closest to the cursor's screen direction.
                cursor_origin = location_3d_to_region_2d(context.region, context.region_data, midpoint)
                if cursor_origin is not None:
                    cursor_delta = Vector((event.mouse_region_x, event.mouse_region_y)) - cursor_origin
                    if cursor_delta.length_squared > 1.0e-10:
                        cursor_delta.normalize()
                        best_score = -1.0
                        for face in self.face_normals:
                            candidate_plane = dimension_plane_from_face(self.p1, self.p2, face, mode)
                            candidate_basis = dimension_basis(self.p1, self.p2, candidate_plane)
                            direction = _screen_direction(context, midpoint, candidate_basis[1]) if candidate_basis else None
                            score = abs(cursor_delta.dot(direction)) if direction is not None else -1.0
                            if score > best_score + 1.0e-4:
                                best_score = score
                                self.face_normal = face.copy()
                placement_plane = dimension_plane_from_face(
                    self.p1,
                    self.p2,
                    self.face_normal,
                    mode,
                )
                if placement_plane is None:
                    placement_plane = self.plane_normal

                projected_direction = (
                    projected_line_direction(self.p1, self.p2, placement_plane)
                    if mode == "NORMAL"
                    else None
                )
                basis = dimension_basis(
                    self.p1,
                    self.p2,
                    placement_plane,
                    projected_direction,
                )
                pick = _cursor_placement_point(
                    context,
                    event,
                    midpoint,
                    placement_plane,
                    pick=probe,
                    fallback_direction=basis[1] if basis is not None else None,
                    fallback_distance=self.offset_distance,
                    max_distance=max(
                        (self.p2 - self.p1).length * 10.0,
                        1.0,
                    ),
                )
                if basis is not None:
                    self.plane_normal = basis[2]
                    raw_offset = pick.point - midpoint
                    distance = raw_offset.dot(basis[1])
                    if abs(distance) <= 1.0e-10:
                        distance = float(self.offset_distance)
                    self.offset_distance = distance
                    self.placement_point = midpoint + basis[1] * distance
                    self.current = self.placement_point.copy()
                    # The persisted placement anchor is the actual
                    # dimension-line point. Keep associative snapping only
                    # when projection did not move the snapped point onto the
                    # selected plane.
                    self.pick_placement = (
                        pick.snap_result
                        if (self.placement_point - pick.point).length_squared <= 1.0e-12
                        else None
                    )
                else:
                    self.placement_point = pick.point.copy()
                    self.current = self.placement_point.copy()
                    self.pick_placement = None
                self.linear_direction = (
                    projected_direction
                    if mode == "NORMAL"
                    else None
                )
                self.preview_label = format_dimension_length(
                    _linear_measure_length(
                        self.p1,
                        self.p2,
                        self.linear_direction,
                    ),
                    context.scene,
                )
        debug.log_change(
            f"linear_update_{id(self)}",
            "linear_update",
            instance=getattr(self, "tool_instance_id", ""),
            stage=self.stage,
            mode=("projected" if self.linear_direction is not None else "aligned"),
            axis=state.get("current_axis_vector"),
            direction=self.linear_direction,
            plane=self.plane_normal,
            placement=self.placement_point,
            placement_mode=self.face_plane_mode,
            label=self.preview_label,
        )
        context.area.tag_redraw()

    def _click(self, context, event):
        debug.log(
            "linear_click",
            instance=getattr(self, "tool_instance_id", ""),
            stage=self.stage,
            p1=self.p1,
            p2=self.p2,
            current=self.current,
            direction=self.linear_direction,
            plane=self.plane_normal,
            offset=self.offset_distance,
            placement=self.placement_point,
        )
        if self.stage == 0:
            self.p1 = self.current.copy()
            self.pick_1 = self.current_pick.snap_result
            picked_face = _picked_face_normal(self.current_pick)
            self.face_normal = _supporting_face_normal(
                self.pick_1,
                None,
                preferred=picked_face,
            ) or picked_face
            self.plane_normal = (
                self.face_normal.copy()
                if self.face_normal is not None
                else _projected_dimension_plane(context, self.current_pick.normal)
            )
            self.stage = 1
            return {"RUNNING_MODAL"}
        if self.stage == 1:
            if (self.current - self.p1).length <= 1.0e-8:
                self.report({"WARNING"}, "Dimension points must be different")
                return {"RUNNING_MODAL"}
            self.p2 = self.current.copy()
            self.pick_2 = self.current_pick.snap_result
            picked_face = _picked_face_normal(self.current_pick)
            topology_face = _supporting_face_normal(
                self.pick_1,
                self.pick_2,
                preferred=self.face_normal or picked_face,
            )
            self.face_normal = topology_face or self.face_normal or picked_face
            self.face_normals = _span_face_normals(
                self.pick_1, self.pick_2, self.p1, self.p2, self.face_normal,
            )
            if self.face_normals:
                self.face_normal = self.face_normals[0].copy()
            self.linear_direction = None
            preferred_plane = (
                dimension_plane_from_face(
                    self.p1,
                    self.p2,
                    self.face_normal,
                    "FACE",
                )
                if self.face_normal is not None
                else self.plane_normal
            )
            basis = dimension_basis(self.p1, self.p2, preferred_plane)
            if basis is None:
                self.report({"WARNING"}, "Could not establish a dimension plane")
                return {"RUNNING_MODAL"}
            self.plane_normal = basis[2]
            default_offset = max((self.p2 - self.p1).length * 0.25, context.scene.radcad_dimension_text_size * 2.0)
            self.offset_distance = default_offset
            self.current = (self.p1 + self.p2) * 0.5 + basis[1] * default_offset
            self.placement_point = self.current.copy()
            self.pick_placement = None
            # Default to face-normal extrusion; N selects in-face offsets.
            self.face_plane_mode = "NORMAL" if self.face_normal is not None else "FIXED"
            self.face_plane_mode_override = (
                "NORMAL" if self.face_normal is not None else None
            )
            self.stage = 2
            self._update(context, event)
            return {"RUNNING_MODAL"}

        # A click does not necessarily follow a mouse-move event. Refresh once
        # so the placement anchor is exactly where the user clicked.
        self._update(context, event)
        debug.log_dimension_snapshot(
            context.scene,
            "linear_commit_before",
            instance=getattr(self, "tool_instance_id", ""),
            direction=self.linear_direction,
            plane=self.plane_normal,
            offset=self.offset_distance,
            handlers=debug.handler_snapshot(),
        )
        root = create_dimension(
            context,
            self.p1,
            self.p2,
            self.plane_normal,
            self.offset_distance,
            self.pick_1,
            self.pick_2,
            linear_direction=self.linear_direction,
            placement_point=self.placement_point,
            snap_placement=self.pick_placement,
            placement_mode=(
                self.face_plane_mode
                if self.face_normal is not None
                else "PROJECTED"
            ),
        )
        debug.log_dimension_snapshot(
            context.scene,
            "linear_commit_after",
            instance=getattr(self, "tool_instance_id", ""),
            created=getattr(root, "name", "<none>"),
            handlers=debug.handler_snapshot(),
        )
        self.finish(context)
        return {"FINISHED"}

    def modal(self, context, event):
        if context.scene.active_cad_tool_id != self.tool_instance_id:
            self.finish(context)
            return {"CANCELLED"}

        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}
        if event.type == "MOUSEMOVE":
            if is_event_over_ui(context, event):
                return {"RUNNING_MODAL"}
            self._update(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if is_event_over_ui(context, event):
                return {"PASS_THROUGH"}
            return self._click(context, event)
        if event.type in {"BACK_SPACE", "BACKSPACE"} and event.value == "PRESS":
            if self.stage == 2:
                self.stage = 1
                self.linear_direction = None
                self.placement_point = None
                self.pick_placement = None
                self.face_plane_mode_override = None
            elif self.stage == 1:
                self.stage = 0
                self.p1 = None
            self._update(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "N" and event.value in {"PRESS", "REPEAT"} and self.stage == 2:
            if self.face_normal is None:
                self.report({"INFO"}, "No supporting face; dimension plane is fixed to the view")
            else:
                self.face_plane_mode_override = (
                    "NORMAL"
                    if self.face_plane_mode != "NORMAL"
                    else "FACE"
                )
                self._update(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "ESC" and event.value == "PRESS":
            self.finish(context)
            return {"CANCELLED"}
        if event.value == "PRESS" and event.type in {"F1", "F2", "F3", "F4", "F5"}:
            key = {"F1": "snap_verts", "F2": "snap_edges", "F3": "snap_edge_center", "F4": "snap_face_center", "F5": "snap_faces"}[event.type]
            state[key] = not state.get(key, False)
            invalidate_snap_cache()
            self._update(context, event)
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def finish(self, context):
        if not self.running:
            return
        debug.log(
            "linear_finish_begin",
            instance=getattr(self, "tool_instance_id", ""),
            stage=getattr(self, "stage", None),
            handlers=debug.handler_snapshot(),
        )
        self.running = False
        debug.stop_preview(self, "linear_finish")
        state["current_axis_vector"] = None
        DrawManager.remove_handler(DRAW_HANDLER_3D)
        DrawManager.remove_handler(DRAW_HANDLER_2D)
        free_snap_context()
        if context.scene.active_cad_tool_id == self.tool_instance_id:
            context.scene.active_cad_tool_id = ""
        try:
            context.window.cursor_modal_restore()
        except RuntimeError:
            pass
        debug.log(
            "linear_finish_end",
            instance=getattr(self, "tool_instance_id", ""),
            handlers=debug.handler_snapshot(),
        )
        context.area.tag_redraw()


class VIEW3D_OT_radcad_dimension_reposition(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_reposition"
    bl_label = "Reposition Dimension"
    bl_description = "Move the selected dimension line while retaining its measured endpoints"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    running = False

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D" and selected_dimension(context) is not None

    def invoke(self, context, event):
        self.root = selected_dimension(context)
        data = self.root.radcad_dimension
        self.original_offset = data.offset_distance
        self.original_plane_normal = resolve_dimension_plane(data)
        self.original_linear_direction = Vector(data.linear_direction)
        if self.original_linear_direction.length_squared <= 1.0e-18:
            self.original_linear_direction = None
        self.plane_normal = self.original_plane_normal.copy()
        self.context = context
        self.stage = 2
        self.dimension_type = getattr(data, "dimension_type", "LINEAR")
        self.placement_mode = getattr(data, "placement_mode", "FACE")
        self.offset_distance = data.offset_distance
        self.placement_initialized = bool(
            getattr(data, "placement_initialized", False)
            and getattr(data, "placement_mode", "FACE") != "PROJECTED"
        )
        self.placement_point = None
        if self.dimension_type == "ANGLE":
            self.vertex = resolve_anchor(data.anchor_1)
            self.ray_1 = resolve_anchor(data.anchor_2)
            self.ray_2 = resolve_anchor(data.anchor_3)
            self.p1 = self.vertex
            self.p2 = self.ray_1
            angle_layout = _angle_preview_layout(self)
            self.preview_label = (
                format_dimension_angle(angle_layout.measured_angle, context.scene)
                if angle_layout is not None
                else ""
            )
            self.current = self.vertex.copy()
        else:
            self.p1 = resolve_anchor(data.anchor_1)
            self.p2 = resolve_anchor(data.anchor_2)
            self.linear_direction = Vector(data.linear_direction)
            if self.linear_direction.length_squared <= 1.0e-18:
                self.linear_direction = None
            if self.placement_mode == "NORMAL":
                self.linear_direction = projected_line_direction(
                    self.p1,
                    self.p2,
                    self.plane_normal,
                )
            if self.placement_initialized:
                placement_anchor = getattr(data, "placement_anchor", None)
                if placement_anchor is not None:
                    self.placement_point = resolve_anchor(placement_anchor)
            basis = dimension_basis(
                self.p1,
                self.p2,
                self.plane_normal,
                self.linear_direction,
            )
            self.current = (self.p1 + self.p2) * 0.5 + basis[1] * data.offset_distance
            self.preview_label = format_dimension_length(
                _linear_measure_length(
                    self.p1,
                    self.p2,
                    self.linear_direction,
                ),
                context.scene,
            )
        self.running = True
        DrawManager.clear_all()
        self.tool_instance_id = f"DIMENSION_REPOSITION_{time.time()}"
        context.scene.active_cad_tool_id = self.tool_instance_id
        debug.start_preview(self, "REPOSITION")
        debug.log_dimension_snapshot(
            context.scene,
            "reposition_invoke",
            instance=self.tool_instance_id,
            root=getattr(self.root, "name", "<none>"),
            handlers=debug.handler_snapshot(),
        )
        context.window.cursor_modal_set("DEFAULT")
        DrawManager.add_handler(DRAW_HANDLER_3D, draw_preview_3d, (self,), "WINDOW", "POST_VIEW")
        DrawManager.add_handler(DRAW_HANDLER_2D, draw_preview_2d, (self,), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self)
        self._update(context, event)
        return {"RUNNING_MODAL"}

    def _update(self, context, event):
        state["current_axis_vector"] = None
        if self.dimension_type == "ANGLE":
            resolved = _cursor_driven_angle_radius(
                context,
                event,
                self.vertex,
                self.plane_normal,
                self.offset_distance,
            )
            if resolved is not None:
                self.current, self.offset_distance = resolved
            context.area.tag_redraw()
            return
        if self.placement_initialized:
            resolved = _fixed_plane_offset(
                context,
                event,
                self.p1,
                self.p2,
                self.plane_normal,
                self.offset_distance,
                dimension_direction=self.linear_direction,
            )
        else:
            resolved = _cursor_driven_offset(
                context,
                event,
                self.p1,
                self.p2,
                self.plane_normal,
                self.offset_distance,
                dimension_direction=self.linear_direction,
            )
        if resolved is not None:
            if self.placement_initialized:
                (
                    self.placement_point,
                    self.current,
                    _plane_normal,
                    self.offset_distance,
                    _line_direction,
                ) = resolved
                state["current_axis_vector"] = None
            else:
                (
                    self.current,
                    self.plane_normal,
                    self.offset_distance,
                    axis,
                    self.linear_direction,
                ) = resolved
                state["current_axis_vector"] = axis
        context.area.tag_redraw()

    def modal(self, context, event):
        if context.scene.active_cad_tool_id != self.tool_instance_id:
            self.finish(context)
            return {"CANCELLED"}
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}
        if event.type == "MOUSEMOVE":
            self._update(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if self.dimension_type == "LINEAR":
                self._update(context, event)
            data = self.root.radcad_dimension
            data.offset_distance = self.offset_distance
            data.linear_direction = (
                self.linear_direction.normalized()
                if self.dimension_type == "LINEAR"
                and self.placement_mode != "NORMAL"
                and self.linear_direction is not None
                and self.linear_direction.length_squared > 1.0e-18
                else (0.0, 0.0, 0.0)
            )
            if self.placement_initialized and self.placement_point is not None:
                set_anchor(data.placement_anchor, self.placement_point)
                data.placement_initialized = True
            set_dimension_plane(data, self.plane_normal)
            update_dimension(self.root)
            self.finish(context)
            return {"FINISHED"}
        if event.type == "ESC" and event.value == "PRESS":
            data = self.root.radcad_dimension
            data.offset_distance = self.original_offset
            data.linear_direction = (
                self.original_linear_direction.normalized()
                if self.original_linear_direction is not None
                else (0.0, 0.0, 0.0)
            )
            set_dimension_plane(data, self.original_plane_normal)
            update_dimension(self.root)
            self.finish(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def finish(self, context):
        if not self.running:
            return
        debug.log(
            "reposition_finish_begin",
            instance=getattr(self, "tool_instance_id", ""),
            root=getattr(getattr(self, "root", None), "name", "<none>"),
            handlers=debug.handler_snapshot(),
        )
        self.running = False
        debug.stop_preview(self, "reposition_finish")
        state["current_axis_vector"] = None
        DrawManager.remove_handler(DRAW_HANDLER_3D)
        DrawManager.remove_handler(DRAW_HANDLER_2D)
        if context.scene.active_cad_tool_id == self.tool_instance_id:
            context.scene.active_cad_tool_id = ""
        try:
            context.window.cursor_modal_restore()
        except RuntimeError:
            pass
        debug.log(
            "reposition_finish_end",
            instance=getattr(self, "tool_instance_id", ""),
            handlers=debug.handler_snapshot(),
        )
        context.area.tag_redraw()


class VIEW3D_OT_radcad_dimension_refresh(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_refresh"
    bl_label = "Refresh Dimension"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return selected_dimension(context) is not None

    def execute(self, context):
        update_dimension(selected_dimension(context))
        return {"FINISHED"}


class VIEW3D_OT_radcad_dimension_parameters(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_parameters"
    bl_label = "Dimension Parameters"
    bl_description = "Open the dimension display and editing parameters dialog"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(
            self,
            width=420,
            title="Dimension Parameters",
            confirm_text="Close",
        )

    def execute(self, _context):
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        scene = context.scene

        general_box = layout.box()
        general_box.label(text="Display", icon="HIDE_OFF")
        general_box.prop(scene, "radcad_dimensions_visible", text="Show Dimensions")

        defaults_box = layout.box()
        defaults_box.label(text="New Dimension Defaults", icon="DRIVER_DISTANCE")
        defaults_box.prop(scene, "radcad_dimension_text_size")
        defaults_box.prop(scene, "radcad_dimension_text_thickness")
        defaults_box.prop(scene, "radcad_dimension_arrow_size")
        defaults_box.prop(scene, "radcad_dimension_extension_gap")
        defaults_box.prop(scene, "radcad_dimension_extension_overshoot")
        defaults_box.prop(scene, "radcad_dimension_line_width")
        defaults_box.prop(scene, "radcad_dimension_color")

        selected_box = layout.box()
        selected_box.label(text="Selected Dimension", icon="RESTRICT_SELECT_OFF")
        root = selected_dimension(context)
        if root is None:
            selected_box.label(text="No dimension selected", icon="INFO")
            return

        data = root.radcad_dimension
        selected_box.prop(root, "name", text="Name")
        selected_box.prop(data, "text_override")
        selected_box.prop(data, "offset_distance")
        selected_box.prop(data, "text_size")
        selected_box.prop(data, "text_thickness")
        selected_box.prop(data, "arrow_size")
        selected_box.prop(data, "extension_gap")
        selected_box.prop(data, "extension_overshoot")
        selected_box.prop(data, "line_width")
        selected_box.prop(data, "color")

        row = selected_box.row(align=True)
        row.operator("view3d.radcad_dimension_reposition", text="Reposition")
        row.operator(
            "view3d.radcad_dimension_refresh",
            text="Refresh",
            icon="FILE_REFRESH",
        )
        selected_box.operator(
            "view3d.radcad_dimension_delete",
            text="Delete Dimension",
            icon="TRASH",
        )


class VIEW3D_OT_radcad_dimension_pick(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_pick"
    bl_label = "Select Dimension"
    bl_description = "Select a radCAD dimension by clicking its viewport annotation"
    bl_options = {"INTERNAL", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        if (
            context.region is None
            or context.region.type != "WINDOW"
            or not getattr(context.scene, "radcad_dimensions_visible", True)
            or getattr(context.scene, "active_cad_tool_id", "")
        ):
            return {"PASS_THROUGH"}

        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        picked = None
        best_distance = float("inf")
        for root in reversed(iter_dimensions(context.scene)):
            layout, label = dimension_layout(root)
            if layout is None:
                continue
            data = root.radcad_dimension
            if getattr(data, "dimension_type", "LINEAR") == "ANGLE":
                distance = angle_dimension_hit_distance(
                    context,
                    mouse,
                    layout.vertex,
                    layout.ray_1,
                    layout.ray_2,
                    layout.plane_normal,
                    data.offset_distance,
                    label,
                    data.text_size if data.text_size >= 4.0 else 14.0,
                    max(1.0, float(data.text_thickness)),
                    data.arrow_size if data.arrow_size >= 2.0 else 10.0,
                    data.line_width if data.line_width >= 0.5 else 1.0,
                    data.extension_gap,
                    data.extension_overshoot,
                )
            else:
                hit_direction = (
                    layout.line_direction
                    if getattr(data, "placement_mode", "FACE") == "NORMAL"
                    else data.linear_direction
                )
                distance = dimension_hit_distance(
                    context,
                    mouse,
                    layout.p1,
                    layout.p2,
                    layout.plane_normal,
                    data.offset_distance,
                    label,
                    data.text_size if data.text_size >= 4.0 else 14.0,
                    max(1.0, float(data.text_thickness)),
                    data.arrow_size if data.arrow_size >= 2.0 else 10.0,
                    data.line_width if data.line_width >= 0.5 else 1.0,
                    data.extension_gap,
                    data.extension_overshoot,
                    hit_direction,
                )
            if distance is not None and distance < best_distance:
                picked = root
                best_distance = distance

        if picked is None:
            if context.scene.radcad_active_dimension is not None:
                context.scene.radcad_active_dimension = None
                context.area.tag_redraw()
            return {"PASS_THROUGH"}

        context.scene.radcad_active_dimension = picked
        context.area.tag_redraw()

        # Both dimension types can be resized directly from the viewport.
        # Keep the picker modal after the press so a simple click still
        # selects, while a drag changes the offset until release.
        data = picked.radcad_dimension
        self._drag_root = picked
        self._drag_dimension_type = getattr(data, "dimension_type", "LINEAR")
        self._drag_original_offset = float(data.offset_distance)
        self._drag_original_plane_normal = resolve_dimension_plane(data)
        self._drag_original_linear_direction = Vector(data.linear_direction)
        if self._drag_original_linear_direction.length_squared <= 1.0e-18:
            self._drag_original_linear_direction = None
        self._drag_has_placement = bool(
            getattr(data, "placement_initialized", False)
            and getattr(data, "placement_mode", "FACE") != "PROJECTED"
        )
        self._drag_placement_mode = getattr(data, "placement_mode", "FACE")
        self._drag_current_placement = None
        self._drag_plane_normal = self._drag_original_plane_normal.copy()
        self._drag_start_mouse = Vector(
            (event.mouse_region_x, event.mouse_region_y)
        )
        self._dragging = False
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _update_dimension_drag(self, context, event):
        data = self._drag_root.radcad_dimension
        if self._drag_dimension_type == "ANGLE":
            vertex = resolve_anchor(data.anchor_1)
            if vertex is None:
                return
            resolved = _cursor_driven_angle_radius(
                context,
                event,
                vertex,
                self._drag_plane_normal,
                self._drag_original_offset,
            )
        else:
            p1 = resolve_anchor(data.anchor_1)
            p2 = resolve_anchor(data.anchor_2)
            if p1 is None or p2 is None:
                return
            if self._drag_placement_mode == "NORMAL":
                dimension_direction = projected_line_direction(
                    p1,
                    p2,
                    self._drag_plane_normal,
                )
            else:
                dimension_direction = (
                    Vector(data.linear_direction)
                    if Vector(data.linear_direction).length_squared > 1.0e-18
                    else None
                )
            if self._drag_has_placement:
                resolved = _fixed_plane_offset(
                    context,
                    event,
                    p1,
                    p2,
                    self._drag_plane_normal,
                    self._drag_original_offset,
                    dimension_direction=dimension_direction,
                )
            else:
                resolved = _cursor_driven_offset(
                    context,
                    event,
                    p1,
                    p2,
                    self._drag_plane_normal,
                    self._drag_original_offset,
                    dimension_direction=dimension_direction,
                )
        if resolved is None:
            return
        if self._drag_dimension_type == "ANGLE":
            _point, offset_distance = resolved
        else:
            if self._drag_has_placement:
                (
                    self._drag_current_placement,
                    _point,
                    _plane_normal,
                    offset_distance,
                    _line_direction,
                ) = resolved
            else:
                _point, plane_normal, offset_distance, _axis, _direction = resolved
                self._drag_plane_normal = plane_normal
                set_dimension_plane(data, plane_normal)
        data.offset_distance = offset_distance
        update_dimension(self._drag_root)
        context.area.tag_redraw()

    def modal(self, context, event):
        if context.scene.active_cad_tool_id:
            return {"FINISHED"}

        if event.type == "MOUSEMOVE":
            mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            if (
                not self._dragging
                and (mouse - self._drag_start_mouse).length >= 3.0
            ):
                self._dragging = True
            if self._dragging:
                self._update_dimension_drag(context, event)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            if self._dragging:
                self._update_dimension_drag(context, event)
                if (
                    self._drag_dimension_type == "LINEAR"
                    and self._drag_has_placement
                    and self._drag_current_placement is not None
                ):
                    set_anchor(
                        self._drag_root.radcad_dimension.placement_anchor,
                        self._drag_current_placement,
                    )
                    self._drag_root.radcad_dimension.placement_initialized = True
                    update_dimension(self._drag_root)
            return {"FINISHED"}

        if event.type == "ESC" and event.value == "PRESS":
            data = self._drag_root.radcad_dimension
            set_dimension_plane(data, self._drag_original_plane_normal)
            data.linear_direction = (
                self._drag_original_linear_direction.normalized()
                if self._drag_original_linear_direction is not None
                else (0.0, 0.0, 0.0)
            )
            data.offset_distance = self._drag_original_offset
            update_dimension(self._drag_root)
            context.area.tag_redraw()
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}


class VIEW3D_OT_radcad_dimension_delete(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_delete"
    bl_label = "Delete Dimension"
    bl_description = "Delete the selected dimension and its annotation objects"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return selected_dimension(context) is not None

    def execute(self, context):
        delete_dimension(selected_dimension(context))
        return {"FINISHED"}
