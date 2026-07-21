"""Mouse projection and radCAD snap adaptation for dimensions."""

from dataclasses import dataclass

from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
from mathutils import Vector
from mathutils.geometry import intersect_line_plane

from ..modal_state import state
from ..plane_utils import raycast_under_mouse
from ..snapping_utils import snap_mesh


@dataclass
class PickResult:
    point: Vector
    normal: Vector
    snap_result: object = None


def project_to_plane(context, x, y, plane_point, plane_normal):
    origin = region_2d_to_origin_3d(context.region, context.region_data, (x, y))
    direction = region_2d_to_vector_3d(context.region, context.region_data, (x, y))
    return intersect_line_plane(origin, origin + direction * 100000.0, plane_point, plane_normal)


def _view_plane_normal(context):
    return (context.region_data.view_matrix.inverted().to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()


def pick_point(context, event, plane_point=None, plane_normal=None):
    radius = state.get("snap_strength", 6.0) * 2.0
    result = snap_mesh(
        context,
        context.edit_object,
        event.mouse_region_x,
        event.mouse_region_y,
        max_px=radius,
        snap_verts=state.get("snap_verts", True),
        snap_edges=state.get("snap_edges", False),
        snap_edge_center=state.get("snap_edge_center", False),
        snap_face_center=state.get("snap_face_center", False),
        snap_faces=state.get("snap_faces", False),
        include_surface=True,
    )
    if result is not None:
        normal = result.normal
        if normal is None:
            _location, normal, _obj = raycast_under_mouse(context, event.mouse_region_x, event.mouse_region_y)
        if normal is None:
            normal = plane_normal or _view_plane_normal(context)
        state["snap_point"] = result.location.copy()
        state["geometry_snap"] = result.kind != "SURFACE"
        return PickResult(result.location.copy(), Vector(normal), result)

    state["snap_point"] = None
    state["geometry_snap"] = False
    normal = Vector(plane_normal) if plane_normal is not None else _view_plane_normal(context)
    point_on_plane = Vector(plane_point) if plane_point is not None else Vector((0.0, 0.0, 0.0))
    point = project_to_plane(context, event.mouse_region_x, event.mouse_region_y, point_on_plane, normal)
    if point is None:
        point = point_on_plane.copy()
    return PickResult(point, normal, None)
