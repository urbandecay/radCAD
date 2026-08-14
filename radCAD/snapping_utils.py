# snapping_utils.py

from dataclasses import dataclass

import bmesh
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector

from .snap_context_l import SnapContext


ELEMENT_SNAP_RADIUS_PX = 15.0


@dataclass
class SnapResult:
    location: Vector
    kind: str
    normal: Vector = None
    target_object: object = None
    element_indices: tuple = ()
    element_weights: tuple = ()
    # Edge results can carry their exact world-space endpoints and the matrix
    # used by the snap context.  Tools that derive geometry from an edge need
    # the complete component, especially for evaluated meshes and instances.
    element_coordinates: tuple = ()
    target_matrix: object = None


class _RadCADSnapEngine:
    """Own a Snap Utilities-style GPU snap context for the active radCAD tool."""

    def __init__(self):
        self.sctx = None
        self.context_key = None
        self.main_obj = None
        self.main_snap_obj = None
        self.dirty = True
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
        self.sctx = None
        self.context_key = None
        self.main_obj = None
        self.main_snap_obj = None
        self.dirty = True

    def invalidate(self):
        self.dirty = True

    def _rebuild_objects(self, sctx, ctx):
        sctx.clear_snap_objects(True)
        self.main_obj = None
        self.main_snap_obj = None

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
        shading = ctx.space_data.shading
        occlude_components = search_edges and not (
            shading.show_xray or shading.type == 'WIREFRAME'
        )
        # Snap Utilities draws faces in solid view even when face snapping is
        # disabled. They block hidden components without becoming valid results.
        search_faces = snap_faces or snap_face_center or occlude_components
        self.sctx.set_snap_mode(snap_verts, search_edges, search_faces)

        if self.dirty:
            self._rebuild_objects(self.sctx, ctx)
            self.dirty = False

    def query(self, x, y, main_obj):
        if main_obj is not self.main_obj:
            self.main_obj = main_obj
            self.main_snap_obj = (
                self.sctx._get_snap_obj_by_obj(main_obj)
                if main_obj is not None
                else None
            )
        snap_obj, location, element, element_co = self.sctx.snap_get(
            (x, y),
            self.main_snap_obj,
        )
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
        face = next(iter(faces))
        indices = tuple(vertex.index for vertex in face.verts)
        weights = tuple(1.0 / len(indices) for _index in indices)
        return snap_obj.mat @ face.calc_center_median(), indices, weights

    mesh = obj.evaluated_get(sctx.depsgraph).data
    for polygon in mesh.polygons:
        if vertex_indices.issubset(polygon.vertices):
            indices = tuple(polygon.vertices)
            weights = tuple(1.0 / len(indices) for _index in indices)
            return snap_obj.mat @ polygon.center, indices, weights
    return None


def _component_result(ctx, hit, x, y, max_px, snap_verts, snap_edges, snap_edge_center):
    if hit is None:
        return None

    snap_obj, location, element, element_co = hit
    target = snap_obj.data[0]
    element_size = len(element)
    if element_size == 1 and snap_verts:
        return SnapResult(location.copy(), "VERT", None, target, tuple(element), (1.0,))

    if element_size == 2:
        if snap_edge_center:
            center = (element_co[0] + element_co[1]) * 0.5
            if _point_within_radius(ctx, center, x, y, max_px):
                return SnapResult(
                    center,
                    "EDGE_CENTER",
                    None,
                    target,
                    tuple(element),
                    (0.5, 0.5),
                    tuple(co.copy() for co in element_co),
                    snap_obj.mat.copy(),
                )
        if snap_edges:
            edge = element_co[1] - element_co[0]
            t = 0.0 if edge.length_squared <= 1e-12 else (location - element_co[0]).dot(edge) / edge.length_squared
            t = max(0.0, min(1.0, t))
            return SnapResult(
                location.copy(),
                "EDGE",
                None,
                target,
                tuple(element),
                (1.0 - t, t),
                tuple(co.copy() for co in element_co),
                snap_obj.mat.copy(),
            )

    return None


def _face_result(
    ctx,
    hit,
    x,
    y,
    max_px,
    snap_face_center,
    snap_faces,
    include_surface,
):
    if hit is None:
        return None

    snap_obj, location, element, element_co = hit
    if len(element) != 3:
        return None

    normal = (element_co[1] - element_co[0]).cross(element_co[2] - element_co[0])
    if normal.length_squared > 1e-12:
        normal.normalize()
    else:
        normal = None

    if snap_face_center:
        center_data = _face_center(_snap_engine.sctx, snap_obj, element)
        if center_data is not None:
            center, center_indices, center_weights = center_data
            if _point_within_radius(ctx, center, x, y, max_px):
                return SnapResult(center, "FACE_CENTER", normal, snap_obj.data[0], center_indices, center_weights)
    v0 = element_co[1] - element_co[0]
    v1 = element_co[2] - element_co[0]
    v2 = location - element_co[0]
    d00 = v0.dot(v0)
    d01 = v0.dot(v1)
    d11 = v1.dot(v1)
    d20 = v2.dot(v0)
    d21 = v2.dot(v1)
    denom = d00 * d11 - d01 * d01
    if abs(denom) <= 1e-12:
        weights = ()
    else:
        w1 = (d11 * d20 - d01 * d21) / denom
        w2 = (d00 * d21 - d01 * d20) / denom
        weights = (1.0 - w1 - w2, w1, w2)
    target = snap_obj.data[0]
    if snap_faces:
        return SnapResult(location.copy(), "FACE", normal, target, tuple(element), weights)
    if include_surface:
        return SnapResult(location.copy(), "SURFACE", normal, target, tuple(element), weights)
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
    include_surface=False,
):
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
    hit = _snap_engine.query(x, y, obj)
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

    return _face_result(
        ctx,
        hit,
        x,
        y,
        max_px,
        snap_face_center,
        snap_faces,
        include_surface,
    )


def _result_screen_distance(ctx, result, x, y):
    if result is None:
        return float("inf")
    point_2d = location_3d_to_region_2d(
        ctx.region,
        ctx.region_data,
        result.location,
        default=None,
    )
    if point_2d is None:
        return float("inf")
    return (Vector(point_2d) - Vector((x, y))).length


def snap_scene_geometry(
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
    include_surface=False,
    enable_mesh=True,
    snap_guides=True,
):
    """Combine mesh and construction-guide candidates into one snap result."""
    mesh_result = None
    if enable_mesh:
        mesh_result = snap_mesh(
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
            include_surface=include_surface,
        )

    guide_candidate = None
    if snap_guides:
        try:
            from .construction_tool.snapping import snap_construction_lines

            guide_candidate = snap_construction_lines(ctx, x, y, max_px)
        except (AttributeError, ImportError):
            guide_candidate = None

    if guide_candidate is None:
        return mesh_result
    if mesh_result is None or mesh_result.kind == "SURFACE":
        return guide_candidate.result

    mesh_distance = _result_screen_distance(ctx, mesh_result, x, y)
    if guide_candidate.distance_px < mesh_distance:
        return guide_candidate.result
    return mesh_result


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
