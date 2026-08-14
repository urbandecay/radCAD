"""Geometry extraction and cursor placement for edge-offset guides."""

from dataclasses import dataclass

import bmesh
from bpy_extras.view3d_utils import (
    location_3d_to_region_2d,
    region_2d_to_origin_3d,
    region_2d_to_vector_3d,
)
from mathutils import Vector
from mathutils.geometry import intersect_line_plane, intersect_ray_tri


_EPSILON = 1.0e-12


@dataclass
class ConnectedFace:
    """One drawing face incident to the selected source edge."""

    normal: Vector
    inward: Vector
    triangles: tuple


@dataclass
class EdgeReference:
    """Exact source edge plus the faces on which it can be offset."""

    start: Vector
    end: Vector
    direction: Vector
    faces: tuple


@dataclass
class OffsetPlacement:
    """A parallel guide placement resolved from the current cursor ray."""

    face: ConnectedFace
    point: Vector
    offset: Vector

    @property
    def distance(self):
        return self.offset.length


def _face_candidate(edge_start, edge_direction, polygon_points, triangles):
    if not triangles:
        return None

    normal = None
    for triangle in triangles:
        candidate = (triangle[1] - triangle[0]).cross(triangle[2] - triangle[0])
        if candidate.length_squared > _EPSILON:
            normal = candidate.normalized()
            break
    if normal is None:
        return None

    centroid = sum(polygon_points, Vector()) / float(len(polygon_points))
    inward = centroid - edge_start
    inward -= edge_direction * inward.dot(edge_direction)
    if inward.length_squared <= _EPSILON:
        return None
    inward.normalize()
    return ConnectedFace(normal, inward, tuple(triangles))


def _object_mode_faces(context, obj, matrix, vertex_indices, edge_start, edge_direction):
    mesh = obj.evaluated_get(context.evaluated_depsgraph_get()).data
    if any(index < 0 or index >= len(mesh.vertices) for index in vertex_indices):
        return ()

    wanted = {int(index) for index in vertex_indices}
    polygon_indices = []
    polygon_points = {}
    for polygon in mesh.polygons:
        indices = tuple(int(index) for index in polygon.vertices)
        if not wanted.issubset(indices):
            continue
        # Merely sharing a polygon is insufficient for an n-gon: the selected
        # vertices must be consecutive and therefore form a real boundary edge.
        if not any(
            {indices[index], indices[(index + 1) % len(indices)]} == wanted
            for index in range(len(indices))
        ):
            continue
        polygon_indices.append(polygon.index)
        polygon_points[polygon.index] = tuple(matrix @ mesh.vertices[index].co for index in indices)

    if not polygon_indices:
        return ()

    mesh.calc_loop_triangles()
    triangles_by_polygon = {index: [] for index in polygon_indices}
    for triangle in mesh.loop_triangles:
        if triangle.polygon_index not in triangles_by_polygon:
            continue
        triangles_by_polygon[triangle.polygon_index].append(
            tuple(matrix @ mesh.vertices[index].co for index in triangle.vertices)
        )

    faces = []
    for polygon_index in polygon_indices:
        candidate = _face_candidate(
            edge_start,
            edge_direction,
            polygon_points[polygon_index],
            triangles_by_polygon[polygon_index],
        )
        if candidate is not None:
            faces.append(candidate)
    return tuple(faces)


def _edit_mode_faces(obj, matrix, vertex_indices, edge_start, edge_direction):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    if any(index < 0 or index >= len(bm.verts) for index in vertex_indices):
        return ()

    first, second = (bm.verts[int(index)] for index in vertex_indices)
    edge = next(
        (
            candidate
            for candidate in first.link_edges
            if second in candidate.verts and not candidate.hide
        ),
        None,
    )
    if edge is None:
        return ()

    visible_faces = tuple(face for face in edge.link_faces if not face.hide)
    triangles_by_face = {face: [] for face in visible_faces}
    for loops in bm.calc_loop_triangles():
        face = loops[0].face
        if face in triangles_by_face:
            triangles_by_face[face].append(tuple(matrix @ loop.vert.co for loop in loops))

    faces = []
    for face in visible_faces:
        candidate = _face_candidate(
            edge_start,
            edge_direction,
            tuple(matrix @ vertex.co for vertex in face.verts),
            triangles_by_face[face],
        )
        if candidate is not None:
            faces.append(candidate)
    return tuple(faces)


def edge_reference_from_snap(context, snap_result):
    """Resolve an edge snap into its exact direction and connected faces."""
    if (
        snap_result is None
        or snap_result.kind not in {"EDGE", "EDGE_CENTER"}
        or snap_result.target_object is None
        or snap_result.target_object.type != "MESH"
        or len(snap_result.element_indices) != 2
    ):
        return None

    obj = snap_result.target_object
    matrix = snap_result.target_matrix
    if matrix is None:
        matrix = obj.matrix_world

    coordinates = tuple(Vector(co) for co in snap_result.element_coordinates)
    if len(coordinates) != 2:
        if obj.mode == "EDIT" and obj.data.is_editmode:
            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            if any(index < 0 or index >= len(bm.verts) for index in snap_result.element_indices):
                return None
            coordinates = tuple(matrix @ bm.verts[int(index)].co for index in snap_result.element_indices)
        else:
            mesh = obj.evaluated_get(context.evaluated_depsgraph_get()).data
            if any(index < 0 or index >= len(mesh.vertices) for index in snap_result.element_indices):
                return None
            coordinates = tuple(matrix @ mesh.vertices[int(index)].co for index in snap_result.element_indices)

    edge_start, edge_end = coordinates
    direction = edge_end - edge_start
    if direction.length_squared <= _EPSILON:
        return None
    direction.normalize()

    if obj.mode == "EDIT" and obj.data.is_editmode:
        faces = _edit_mode_faces(
            obj,
            matrix,
            snap_result.element_indices,
            edge_start,
            direction,
        )
    else:
        faces = _object_mode_faces(
            context,
            obj,
            matrix,
            snap_result.element_indices,
            edge_start,
            direction,
        )
    if not faces:
        return None
    return EdgeReference(edge_start, edge_end, direction, faces)


def _face_under_cursor(edge, ray_origin, ray_direction):
    best = None
    best_distance = float("inf")
    for face in edge.faces:
        for triangle in face.triangles:
            hit = intersect_ray_tri(
                triangle[0],
                triangle[1],
                triangle[2],
                ray_direction,
                ray_origin,
                True,
            )
            if hit is None:
                continue
            distance = (hit - ray_origin).dot(ray_direction)
            if distance >= 0.0 and distance < best_distance:
                best = (face, hit)
                best_distance = distance
    return best


def _screen_direction_face(context, edge, source_point, mouse):
    source_screen = location_3d_to_region_2d(
        context.region,
        context.region_data,
        source_point,
        default=None,
    )
    if source_screen is None:
        return edge.faces[0]
    mouse_direction = Vector(mouse) - source_screen
    if mouse_direction.length_squared <= _EPSILON:
        return edge.faces[0]
    mouse_direction.normalize()

    sample_length = max((edge.end - edge.start).length * 0.25, 1.0e-4)
    best_face = edge.faces[0]
    best_score = -float("inf")
    for face in edge.faces:
        sample_screen = location_3d_to_region_2d(
            context.region,
            context.region_data,
            source_point + face.inward * sample_length,
            default=None,
        )
        if sample_screen is None:
            continue
        screen_direction = sample_screen - source_screen
        if screen_direction.length_squared <= _EPSILON:
            continue
        score = screen_direction.normalized().dot(mouse_direction)
        if score > best_score:
            best_face = face
            best_score = score
    return best_face


def offset_placement_from_cursor(context, event, edge, source_point, active_face=None):
    """Project the cursor onto an incident face and offset parallel to the edge."""
    mouse = (event.mouse_region_x, event.mouse_region_y)
    ray_origin = region_2d_to_origin_3d(context.region, context.region_data, mouse)
    ray_direction = region_2d_to_vector_3d(context.region, context.region_data, mouse)
    ray_direction.normalize()

    face_hit = _face_under_cursor(edge, ray_origin, ray_direction)
    if face_hit is not None:
        face, point = face_hit
    else:
        face = active_face or _screen_direction_face(context, edge, source_point, mouse)
        point = intersect_line_plane(
            ray_origin,
            ray_origin + ray_direction * 100000.0,
            source_point,
            face.normal,
        )
        if point is None:
            return None

    offset = point - source_point
    offset -= edge.direction * offset.dot(edge.direction)
    # Remove tiny numerical drift away from the chosen drawing face.
    offset -= face.normal * offset.dot(face.normal)
    return OffsetPlacement(face, source_point + offset, offset)
