# snapping_utils.py

from dataclasses import dataclass
import math

import bmesh
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector

from .snap_context_l import SnapContext


ELEMENT_SNAP_RADIUS_PX = 15.0


def component_face_normal(result, view_direction):
    """Resolve a real incident face, never an averaged vertex normal."""
    if result.normal is not None:
        return result.normal.copy()
    obj = result.target_object
    indices = set(result.element_indices)
    if obj is None or obj.type != 'MESH' or not indices:
        return None
    matrix = result.target_matrix if result.target_matrix is not None else obj.matrix_world
    normal_matrix = matrix.to_3x3().inverted_safe().transposed()
    candidates = []
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        if max(indices) >= len(bm.verts):
            return None
        faces = bm.verts[min(indices)].link_faces
        for face in faces:
            if not face.hide and indices.issubset({v.index for v in face.verts}):
                candidates.append(normal_matrix @ face.normal)
    else:
        for face in obj.data.polygons:
            if not face.hide and indices.issubset(face.vertices):
                candidates.append(normal_matrix @ face.normal)
    candidates = [n.normalized() for n in candidates if n.length_squared > 1e-12]
    # At a shared corner choose the incident face most directly facing the view.
    return max(candidates, key=lambda n: -n.dot(view_direction), default=None)


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
            # Construction guides have a mesh proxy for Blender's native snap
            # operators. radCAD uses its exact infinite-guide snapper instead.
            if obj.get("radcad_construction_snap_proxy", False):
                continue
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
        snap_intersections=False,
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
        search_edges = (
            snap_verts
            or snap_edges
            or snap_edge_center
            or snap_intersections
        )
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

    def query_edge_candidates(
        self,
        x,
        y,
        main_obj,
        max_px,
        center_hit=None,
    ):
        """Collect distinct visible edges around the cursor.

        The GPU snap buffer returns the best primitive at one cursor location.
        Intersection snapping needs the two primitives that meet near that
        location, so sample a small ring around the cursor and deduplicate the
        edge hits.  This avoids scanning every mesh edge on every mouse move.
        """
        radius = max(2.0, min(float(max_px), 18.0))
        candidates = []
        seen = set()

        def add_hit(hit):
            if hit is None:
                return
            snap_obj, _location, element, element_co = hit
            if element_co is None or len(element) != 2 or len(element_co) != 2:
                return

            target = snap_obj.data[0]
            key = (id(target), tuple(sorted(int(index) for index in element)))
            if key in seen:
                return
            seen.add(key)
            candidates.append((snap_obj, _location, element, element_co))

        add_hit(center_hit if center_hit is not None else self.query(x, y, main_obj))

        # The outer ring normally finds both sides of an X with only eight
        # cached-buffer lookups.  A smaller fallback ring helps with very
        # shallow crossings without imposing that cost on every mouse move.
        ring_radius = radius
        count = 8
        for index in range(count):
            angle = (2.0 * math.pi * index) / count
            add_hit(
                self.query(
                    x + ring_radius * math.cos(angle),
                    y + ring_radius * math.sin(angle),
                    main_obj,
                )
            )

        if len(candidates) < 2:
            ring_radius = radius * 0.5
            for index in range(4):
                angle = (2.0 * math.pi * index) / 4.0
                add_hit(
                    self.query(
                        x + ring_radius * math.cos(angle),
                        y + ring_radius * math.sin(angle),
                        main_obj,
                    )
                )

        return candidates


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


def _closest_points_on_segments(p1, q1, p2, q2):
    """Return closest points and parameters for two 3D line segments."""
    d1 = q1 - p1
    d2 = q2 - p2
    r = p1 - p2
    a = d1.dot(d1)
    e = d2.dot(d2)
    f = d2.dot(r)

    if a <= 1.0e-12 and e <= 1.0e-12:
        return p1.copy(), p2.copy(), 0.0, 0.0
    if a <= 1.0e-12:
        t = max(0.0, min(1.0, f / e))
        return p1.copy(), p2 + d2 * t, 0.0, t

    c = d1.dot(r)
    if e <= 1.0e-12:
        s = max(0.0, min(1.0, -c / a))
        return p1 + d1 * s, p2.copy(), s, 0.0

    b = d1.dot(d2)
    denominator = a * e - b * b
    if denominator <= 1.0e-12 * a * e:
        # Parallel/collinear segments have no unique intersection point.
        return None

    s = (b * f - c * e) / denominator
    s = max(0.0, min(1.0, s))

    t = (b * s + f) / e
    if t < 0.0:
        t = 0.0
        s = max(0.0, min(1.0, -c / a))
    elif t > 1.0:
        t = 1.0
        s = max(0.0, min(1.0, (b - c) / a))

    return p1 + d1 * s, p2 + d2 * t, s, t


def _axis_segment_intersection(origin, direction, start, end):
    """Intersect an infinite drawing axis with a finite edge in world space."""
    edge = end - start
    a = direction.length_squared
    e = edge.length_squared
    if a <= 1.0e-12 or e <= 1.0e-12:
        return None
    offset = origin - start
    b = direction.dot(edge)
    denominator = a * e - b * b
    if denominator <= 1.0e-12 * a * e:
        return None
    c = direction.dot(offset)
    f = edge.dot(offset)
    edge_t = (a * f - b * c) / denominator
    if not 0.0 <= edge_t <= 1.0:
        return None
    point = origin + direction * ((b * f - e * c) / denominator)
    tolerance = 1.0e-6 * max(1.0, edge.length)
    if (point - (start + edge * edge_t)).length > tolerance:
        return None
    return point


def snap_axis_intersection(ctx, obj, x, y, origin, direction, max_px):
    """Intersect the hovered edge with the active drawing axis.

    Called after the regular mesh query has prepared the viewport snap buffer.
    The cursor selects the edge, not the intersection: the crossing can be
    arbitrarily far along that edge from the cursor.
    Screen-only crossings at different depths are deliberately rejected.
    """
    hit = _snap_engine.query(x, y, obj)
    if hit is None:
        return None
    _snap_obj, _location, element, coordinates = hit
    if coordinates is None or len(element) != 2 or len(coordinates) != 2:
        return None
    return _axis_segment_intersection(
        origin, direction, Vector(coordinates[0]), Vector(coordinates[1])
    )


def _intersection_result(ctx, obj, x, y, max_px, center_hit=None):
    """Return a true 3D segment intersection near the cursor, if present."""
    candidates = _snap_engine.query_edge_candidates(
        x,
        y,
        obj,
        max_px,
        center_hit=center_hit,
    )
    if len(candidates) < 2:
        return None

    best = None
    best_distance_sq = max_px * max_px
    parameter_epsilon = 1.0e-6

    for index, first in enumerate(candidates):
        first_snap_obj, _location, first_element, first_co = first
        first_target = first_snap_obj.data[0]
        first_indices = set(int(value) for value in first_element)
        p1, q1 = (Vector(first_co[0]), Vector(first_co[1]))
        first_length = (q1 - p1).length

        for second in candidates[index + 1:]:
            second_snap_obj, _location, second_element, second_co = second
            second_target = second_snap_obj.data[0]
            second_indices = set(int(value) for value in second_element)

            # A shared mesh vertex is already handled by vertex snapping and
            # is not an unwelded X intersection.
            if first_target is second_target and first_indices.intersection(second_indices):
                continue

            p2, q2 = (Vector(second_co[0]), Vector(second_co[1]))
            second_length = (q2 - p2).length
            closest = _closest_points_on_segments(
                p1, q1, p2, q2
            )
            if closest is None:
                continue
            c1, c2, first_t, second_t = closest

            if (
                first_t < -parameter_epsilon
                or first_t > 1.0 + parameter_epsilon
                or second_t < -parameter_epsilon
                or second_t > 1.0 + parameter_epsilon
            ):
                continue

            # Reject collinear/near-parallel overlap and screen-only crossings
            # at different depths.  A real intersection has coincident closest
            # points in world space.
            distance = (c1 - c2).length
            tolerance = 1.0e-6 * max(1.0, min(first_length, second_length))
            if distance > tolerance:
                continue

            point = (c1 + c2) * 0.5
            point_2d = location_3d_to_region_2d(
                ctx.region,
                ctx.region_data,
                point,
                default=None,
            )
            if point_2d is None:
                continue
            distance_sq = (Vector(point_2d) - Vector((x, y))).length_squared
            if distance_sq > best_distance_sq:
                continue

            best_distance_sq = distance_sq
            best = SnapResult(
                point,
                "INTERSECTION",
                None,
                first_target,
                tuple(first_element) + tuple(second_element),
                (),
                tuple(Vector(co) for co in first_co + second_co),
                first_snap_obj.mat.copy(),
            )

    return best


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
        return SnapResult(location.copy(), "VERT", None, target, tuple(element), (1.0,),
                          target_matrix=snap_obj.mat.copy())

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
    snap_intersections=False,
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
        snap_intersections=snap_intersections,
    )
    hit = _snap_engine.query(x, y, obj)

    intersection = None
    if snap_intersections:
        intersection = _intersection_result(
            ctx,
            obj,
            x,
            y,
            max_px,
            center_hit=hit,
        )

    if hit is not None:
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
        if intersection is not None and (
            component is None or component.kind != "VERT"
        ):
            return intersection
        if component is not None:
            return component

        face_result = _face_result(
            ctx,
            hit,
            x,
            y,
            max_px,
            snap_face_center,
            snap_faces,
            include_surface,
        )
        if intersection is not None:
            return intersection
        return face_result

    return intersection


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
    snap_intersections=False,
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
            snap_intersections=snap_intersections,
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
        snap_intersections=kwargs.get("snap_intersections", False),
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
