# snapping_utils.py

from dataclasses import dataclass

import bmesh
from bpy_extras import view3d_utils
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector

from .snap_context_l import SnapContext


ELEMENT_SNAP_RADIUS_PX = 15.0


@dataclass
class SnapResult:
    location: Vector
    kind: str


class _RadCADSnapEngine:
    """Own a Snap Utilities-style GPU snap context for the active radCAD tool."""

    def __init__(self):
        self.sctx = None
        self.component_sctx = None
        self.context_key = None
        self.dirty = True
        self.component_dirty = True
        self.pixel_dist = ELEMENT_SNAP_RADIUS_PX

    @staticmethod
    def _key(ctx):
        return (
            ctx.region.as_pointer(),
            ctx.space_data.as_pointer(),
        )

    @staticmethod
    def _visible_meshes(ctx):
        for obj in ctx.visible_objects:
            if obj.type == 'MESH':
                yield obj, obj.matrix_world

            if obj.instance_type == 'COLLECTION' and obj.instance_collection:
                instance_matrix = obj.matrix_world.copy()
                for child in obj.instance_collection.objects:
                    if child.type == 'MESH':
                        yield child, instance_matrix @ child.matrix_world

    def free(self):
        if self.sctx is not None:
            self.sctx.free()
        if self.component_sctx is not None:
            self.component_sctx.free()
        self.sctx = None
        self.component_sctx = None
        self.context_key = None
        self.dirty = True
        self.component_dirty = True

    def invalidate(self):
        self.dirty = True
        self.component_dirty = True

    def _rebuild_objects(self, sctx, ctx):
        sctx.clear_snap_objects(True)

        for obj, matrix in self._visible_meshes(ctx):
            sctx.add_obj(obj, matrix)

    @staticmethod
    def _update_context(sctx, ctx):
        sctx.update_viewport_context(
            ctx.evaluated_depsgraph_get(),
            ctx.region,
            ctx.space_data,
            True,
        )

    def ensure(
        self,
        ctx,
        max_px,
        snap_verts,
        snap_edges,
        snap_edge_center,
        snap_face_center,
        snap_faces,
    ):
        context_key = self._key(ctx)
        if self.sctx is None or self.context_key != context_key:
            self.free()
            self.sctx = SnapContext(
                ctx.evaluated_depsgraph_get(),
                ctx.region,
                ctx.space_data,
            )
            self.context_key = context_key
            self.dirty = True
        else:
            self._update_context(self.sctx, ctx)

        ui_scale = ctx.preferences.system.ui_scale
        self.pixel_dist = max(1, round(max_px * ui_scale))
        self.sctx.set_pixel_dist(self.pixel_dist)

        # Connected vertices are resolved from selected edges by SnapContext.
        search_edges = snap_verts or snap_edges or snap_edge_center
        search_faces = snap_faces or snap_face_center
        self.sctx.set_snap_mode(snap_verts, search_edges, search_faces)

        if self.dirty:
            self._rebuild_objects(self.sctx, ctx)
            self.dirty = False

    def query(self, ctx, x, y):
        snap_obj, location, element, element_co = self.sctx.snap_get(
            (x, y),
            None,
        )
        if snap_obj is None or location is None or element is None:
            return None
        return snap_obj, location, element, element_co

    def query_components(self, ctx, x, y, snap_verts, snap_edges):
        if self.component_sctx is None:
            self.component_sctx = SnapContext(
                ctx.evaluated_depsgraph_get(),
                ctx.region,
                ctx.space_data,
            )
            self.component_dirty = True
        else:
            self._update_context(self.component_sctx, ctx)

        self.component_sctx.set_pixel_dist(self.pixel_dist)
        self.component_sctx.set_snap_mode(snap_verts, snap_edges, False)
        if self.component_dirty:
            self._rebuild_objects(self.component_sctx, ctx)
            self.component_dirty = False

        snap_obj, location, element, element_co = self.component_sctx.snap_get((x, y), None)
        if snap_obj is None or location is None or element is None:
            return None
        return snap_obj, location, element, element_co


_snap_engine = _RadCADSnapEngine()


def invalidate_snap_cache(allow_incremental=False):
    del allow_incremental
    _snap_engine.invalidate()


def free_snap_context():
    _snap_engine.free()


def _point_within_radius(ctx, point, x, y, max_px):
    point_2d = location_3d_to_region_2d(ctx.region, ctx.region_data, point)
    if point_2d is None:
        return False
    return (point_2d - Vector((x, y))).length_squared <= max_px * max_px


def _point_visible(ctx, point):
    if ctx.space_data.shading.type == 'WIREFRAME' or ctx.space_data.shading.show_xray:
        return True

    point_2d = location_3d_to_region_2d(ctx.region, ctx.region_data, point)
    if point_2d is None:
        return False

    ray_origin = view3d_utils.region_2d_to_origin_3d(ctx.region, ctx.region_data, point_2d)
    ray_direction = view3d_utils.region_2d_to_vector_3d(ctx.region, ctx.region_data, point_2d)
    hit, location, _, _, _, _ = ctx.scene.ray_cast(
        ctx.evaluated_depsgraph_get(),
        ray_origin,
        ray_direction,
    )
    if not hit:
        return True

    target_depth = (point - ray_origin).length
    hit_depth = (location - ray_origin).length
    return hit_depth >= target_depth - max(1e-4, target_depth * 1e-5)


def _face_center(sctx, snap_obj, element):
    obj = snap_obj.data[0]
    if obj.type != 'MESH':
        return None

    vertex_indices = {int(index) for index in element}
    if obj.data.is_editmode:
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        if not vertex_indices or max(vertex_indices) >= len(bm.verts):
            return None

        faces = set(bm.verts[next(iter(vertex_indices))].link_faces)
        for index in vertex_indices:
            faces.intersection_update(bm.verts[index].link_faces)
        if not faces:
            return None
        return snap_obj.mat @ next(iter(faces)).calc_center_median()

    mesh = obj.evaluated_get(sctx.depsgraph).data
    for polygon in mesh.polygons:
        if vertex_indices.issubset(polygon.vertices):
            return snap_obj.mat @ polygon.center
    return None


def _component_result(ctx, hit, x, y, max_px, snap_verts, snap_edges, snap_edge_center):
    if hit is None:
        return None

    _, location, element, element_co = hit
    element_size = len(element)
    if element_size == 1 and snap_verts:
        return SnapResult(location.copy(), "VERT")

    if element_size == 2:
        if snap_edge_center:
            center = (element_co[0] + element_co[1]) * 0.5
            if _point_within_radius(ctx, center, x, y, max_px):
                return SnapResult(center, "EDGE_CENTER")
        if snap_edges:
            return SnapResult(location.copy(), "EDGE")

    return None


def _face_result(ctx, hit, x, y, max_px, snap_face_center, snap_faces):
    if hit is None:
        return None

    snap_obj, location, element, _ = hit
    if len(element) != 3:
        return None

    if snap_face_center:
        center = _face_center(_snap_engine.sctx, snap_obj, element)
        if center is not None and _point_within_radius(ctx, center, x, y, max_px):
            return SnapResult(center, "FACE_CENTER")
    if snap_faces:
        return SnapResult(location.copy(), "FACE")
    return None


def snap_mesh(
    ctx,
    obj,
    x,
    y,
    max_px=ELEMENT_SNAP_RADIUS_PX,
    snap_verts=True,
    snap_edges=True,
    snap_edge_center=True,
    snap_face_center=True,
    snap_faces=False,
):
    del obj
    if ctx.region is None or ctx.region_data is None or ctx.space_data.type != 'VIEW_3D':
        return None

    _snap_engine.ensure(
        ctx,
        max_px,
        snap_verts,
        snap_edges,
        snap_edge_center,
        snap_face_center,
        snap_faces,
    )
    hit = _snap_engine.query(ctx, x, y)
    if hit is None:
        return None

    component = _component_result(
        ctx,
        hit,
        x,
        y,
        max_px,
        snap_verts,
        snap_edges,
        snap_edge_center,
    )
    if component is not None:
        return component

    # Snap Utilities can return a covering face before an edge. Retry components
    # without face IDs, then reject anything actually hidden by that face.
    search_edges = snap_verts or snap_edges or snap_edge_center
    if len(hit[2]) == 3 and search_edges:
        component_hit = _snap_engine.query_components(ctx, x, y, snap_verts, search_edges)
        component = _component_result(
            ctx,
            component_hit,
            x,
            y,
            max_px,
            snap_verts,
            snap_edges,
            snap_edge_center,
        )
        if component is not None and _point_visible(ctx, component.location):
            return component

    return _face_result(ctx, hit, x, y, max_px, snap_face_center, snap_faces)


def snap_to_mesh_components(
    ctx,
    obj,
    x,
    y,
    max_px=ELEMENT_SNAP_RADIUS_PX,
    do_verts=True,
    do_edges=True,
    do_edge_center=True,
    do_face_center=True,
    **kwargs,
):
    result = snap_mesh(
        ctx,
        obj,
        x,
        y,
        max_px=max_px,
        snap_verts=do_verts,
        snap_edges=do_edges,
        snap_edge_center=do_edge_center,
        snap_face_center=do_face_center,
        snap_faces=kwargs.get("do_faces", False),
    )
    return result.location if result is not None else None


def snap_visible_face_components(
    ctx,
    obj,
    x,
    y,
    max_px,
    snap_verts=False,
    snap_edges=False,
    snap_edge_center=False,
    snap_face_center=False,
    snap_faces=False,
):
    result = snap_mesh(
        ctx,
        obj,
        x,
        y,
        max_px=max_px,
        snap_verts=snap_verts,
        snap_edges=snap_edges,
        snap_edge_center=snap_edge_center,
        snap_face_center=snap_face_center,
        snap_faces=snap_faces,
    )
    if result is None:
        return None, None, None
    return result.location, None, result.kind


def snap_edge_or_face_under_mouse(ctx, obj, x, y, max_px, snap_edges=True):
    return snap_visible_face_components(
        ctx,
        obj,
        x,
        y,
        max_px,
        snap_edges=snap_edges,
        snap_faces=True,
    )
