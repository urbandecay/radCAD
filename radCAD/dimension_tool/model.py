"""Creation, association resolution, and updates for persistent dimensions."""

import json

import bmesh
import bpy
from mathutils import Vector

from .constants import COLLECTION_NAME, ROOT_PREFIX
from . import debug
from .formatting import dimension_label
from .angular.geometry import build_angle_layout
from .linear.geometry import build_layout, dimension_basis, projected_line_direction


_UPDATE_SIGNATURES = {}
_ELEMENT_EXISTENCE_CACHE = {}
_VERTEX_ID_ATTRIBUTE = ".radcad_dimension_vertex_id"
_NEXT_VERTEX_ID_KEY = "radcad_dimension_next_vertex_id"
_ORIENTATION_TARGET_UNSET = object()
_LOCAL_AXIS_ALIGNMENT = 0.9998476951563913  # cos(1 degree)
_LOCAL_AXES = (
    Vector((1.0, 0.0, 0.0)),
    Vector((0.0, 1.0, 0.0)),
    Vector((0.0, 0.0, 1.0)),
)


def dimension_root(obj):
    while obj is not None:
        data = getattr(obj, "radcad_dimension", None)
        if data is not None and data.is_dimension:
            return obj
        obj = obj.parent
    return None


def selected_dimension(context):
    active = getattr(context.scene, "radcad_active_dimension", None)
    active_data = getattr(active, "radcad_dimension", None) if active is not None else None
    if active_data is not None and active_data.is_dimension:
        return active
    return dimension_root(context.active_object)


def selected_dimensions(context):
    """Return all selected annotations, keeping one active edit target."""
    scene = getattr(context, "scene", None)
    roots = iter_dimensions(scene)
    selected = [
        root for root in roots
        if bool(getattr(getattr(root, "radcad_dimension", None), "selected", False))
    ]
    active = getattr(scene, "radcad_active_dimension", None) if scene is not None else None
    if not selected and active in roots:
        selected = [active]
    return selected


def clear_dimension_selection(scene):
    for root in iter_dimensions(scene):
        data = getattr(root, "radcad_dimension", None)
        if data is not None:
            data.selected = False
    if scene is not None:
        scene.radcad_active_dimension = None


def iter_dimensions(scene=None):
    # bpy.data is intentionally replaced with _RestrictData while Blender is
    # inside an add-on register() call. Treat that phase as an empty database;
    # updater.py schedules the real migration immediately after registration.
    objects = scene.objects if scene is not None else getattr(bpy.data, "objects", ())
    return [
        obj
        for obj in objects
        if getattr(obj, "radcad_dimension", None) is not None
        and obj.radcad_dimension.is_dimension
    ]


def _read_json_numbers(raw):
    try:
        return list(json.loads(raw))
    except (TypeError, ValueError):
        return []


def set_anchor(anchor, point, snap_result=None):
    anchor.fallback = Vector(point)
    anchor.target = None
    anchor.kind = "FREE"
    anchor.indices = "[]"
    anchor.vertex_ids = "[]"
    anchor.weights = "[]"
    if snap_result is None or snap_result.target_object is None or not snap_result.element_indices:
        return
    anchor.target = snap_result.target_object
    anchor.kind = snap_result.kind
    indices = [int(index) for index in snap_result.element_indices]
    anchor.indices = json.dumps(indices)
    vertex_ids = _ensure_vertex_ids(anchor.target, indices)
    anchor.vertex_ids = json.dumps(vertex_ids or [])
    anchor.weights = json.dumps([float(weight) for weight in snap_result.element_weights])


def _mesh_vertex_local_positions(obj, indices):
    if obj.type != "MESH":
        return None
    if obj.mode == "EDIT" and obj.data.is_editmode:
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        if any(index < 0 or index >= len(bm.verts) for index in indices):
            return None
        return [bm.verts[index].co.copy() for index in indices]
    if any(index < 0 or index >= len(obj.data.vertices) for index in indices):
        return None
    return [obj.data.vertices[index].co.copy() for index in indices]


def _next_vertex_id(mesh):
    vertex_id = max(1, int(mesh.get(_NEXT_VERTEX_ID_KEY, 1)))
    mesh[_NEXT_VERTEX_ID_KEY] = vertex_id + 1
    return vertex_id


def _ensure_vertex_ids(obj, indices):
    """Give referenced mesh vertices persistent IDs that survive reindexing."""
    if obj is None or obj.type != "MESH" or not indices:
        return None
    mesh = obj.data
    if obj.mode == "EDIT" and mesh.is_editmode:
        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()
        if any(index < 0 or index >= len(bm.verts) for index in indices):
            return None
        layer = bm.verts.layers.int.get(_VERTEX_ID_ATTRIBUTE)
        if layer is None:
            layer = bm.verts.layers.int.new(_VERTEX_ID_ATTRIBUTE)
        vertex_ids = []
        changed = False
        for index in indices:
            vertex = bm.verts[index]
            vertex_id = int(vertex[layer])
            if vertex_id <= 0:
                vertex_id = _next_vertex_id(mesh)
                vertex[layer] = vertex_id
                changed = True
            vertex_ids.append(vertex_id)
        if changed:
            bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        return vertex_ids

    if any(index < 0 or index >= len(mesh.vertices) for index in indices):
        return None
    attribute = mesh.attributes.get(_VERTEX_ID_ATTRIBUTE)
    if attribute is None:
        attribute = mesh.attributes.new(_VERTEX_ID_ATTRIBUTE, "INT", "POINT")
    if attribute.domain != "POINT" or attribute.data_type != "INT":
        return None
    vertex_ids = []
    for index in indices:
        vertex_id = int(attribute.data[index].value)
        if vertex_id <= 0:
            vertex_id = _next_vertex_id(mesh)
            attribute.data[index].value = vertex_id
        vertex_ids.append(vertex_id)
    return vertex_ids


def _vertex_ids_at_indices(obj, indices):
    if obj is None or obj.type != "MESH":
        return None
    mesh = obj.data
    if obj.mode == "EDIT" and mesh.is_editmode:
        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()
        layer = bm.verts.layers.int.get(_VERTEX_ID_ATTRIBUTE)
        if layer is None or any(index < 0 or index >= len(bm.verts) for index in indices):
            return None
        return [int(bm.verts[index][layer]) for index in indices]

    attribute = mesh.attributes.get(_VERTEX_ID_ATTRIBUTE)
    if (
        attribute is None
        or attribute.domain != "POINT"
        or attribute.data_type != "INT"
        or any(index < 0 or index >= len(mesh.vertices) for index in indices)
    ):
        return None
    return [int(attribute.data[index].value) for index in indices]


def _find_vertex_indices(obj, vertex_ids):
    """Recover current indices after a topology edit has renumbered vertices."""
    wanted = set(vertex_ids)
    found = {}
    mesh = obj.data
    if obj.mode == "EDIT" and mesh.is_editmode:
        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()
        layer = bm.verts.layers.int.get(_VERTEX_ID_ATTRIBUTE)
        if layer is None:
            return None
        for index, vertex in enumerate(bm.verts):
            vertex_id = int(vertex[layer])
            if vertex_id in wanted and vertex_id not in found:
                found[vertex_id] = index
    else:
        attribute = mesh.attributes.get(_VERTEX_ID_ATTRIBUTE)
        if attribute is None or attribute.domain != "POINT" or attribute.data_type != "INT":
            return None
        for index, value in enumerate(attribute.data):
            vertex_id = int(value.value)
            if vertex_id in wanted and vertex_id not in found:
                found[vertex_id] = index
    if any(vertex_id not in found for vertex_id in vertex_ids):
        return None
    return [found[vertex_id] for vertex_id in vertex_ids]


def _mesh_element_exists(obj, kind, indices):
    if kind == "VERT":
        return len(indices) == 1
    wanted = set(indices)
    mesh = obj.data
    edit_bmesh = None
    if obj.mode == "EDIT" and mesh.is_editmode:
        edit_bmesh = bmesh.from_edit_mesh(mesh)
        edit_bmesh.verts.ensure_lookup_table()
        topology_size = (len(edit_bmesh.verts), len(edit_bmesh.edges), len(edit_bmesh.faces))
    else:
        topology_size = (len(mesh.vertices), len(mesh.edges), len(mesh.polygons))
    cache_key = (mesh.as_pointer(), kind, tuple(sorted(wanted)), topology_size)
    cached = _ELEMENT_EXISTENCE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if edit_bmesh is not None:
        wanted_vertices = {edit_bmesh.verts[index] for index in wanted}
        if kind in {"EDGE", "EDGE_CENTER"}:
            exists = len(wanted_vertices) == 2 and any(set(edge.verts) == wanted_vertices for edge in edit_bmesh.edges)
        elif kind in {"FACE", "FACE_CENTER", "SURFACE"}:
            exists = any(wanted_vertices.issubset(set(face.verts)) for face in edit_bmesh.faces)
        else:
            exists = True
    elif kind in {"EDGE", "EDGE_CENTER"}:
        exists = len(wanted) == 2 and any(set(edge.vertices) == wanted for edge in mesh.edges)
    elif kind in {"FACE", "FACE_CENTER", "SURFACE"}:
        exists = any(wanted.issubset(set(polygon.vertices)) for polygon in mesh.polygons)
    else:
        exists = True
    _ELEMENT_EXISTENCE_CACHE[cache_key] = exists
    return exists


def _resolve_associated_anchor(anchor):
    obj = anchor.target
    indices = [int(index) for index in _read_json_numbers(anchor.indices)]
    weights = [float(weight) for weight in _read_json_numbers(anchor.weights)]
    if obj is None or obj.type != "MESH" or not indices or len(indices) != len(weights):
        return None

    vertex_ids = [int(vertex_id) for vertex_id in _read_json_numbers(anchor.vertex_ids)]
    if not vertex_ids:
        # Migrate dimensions saved before persistent vertex identities existed.
        vertex_ids = _ensure_vertex_ids(obj, indices)
        if vertex_ids is None:
            return None
        anchor.vertex_ids = json.dumps(vertex_ids)
    if len(vertex_ids) != len(indices) or any(vertex_id <= 0 for vertex_id in vertex_ids):
        return None

    current_ids = _vertex_ids_at_indices(obj, indices)
    if current_ids != vertex_ids:
        indices = _find_vertex_indices(obj, vertex_ids)
        if indices is None:
            return None
        anchor.indices = json.dumps(indices)
    if not _mesh_element_exists(obj, anchor.kind, indices):
        return None

    positions = _mesh_vertex_local_positions(obj, indices)
    if positions is None:
        return None
    point_local = Vector((0.0, 0.0, 0.0))
    for position, weight in zip(positions, weights):
        point_local += position * weight
    return obj.matrix_world @ point_local


def resolve_anchor(anchor):
    fallback = Vector(anchor.fallback)
    if anchor.kind == "FREE":
        return fallback
    point_world = _resolve_associated_anchor(anchor)
    if point_world is None:
        return fallback
    if (Vector(anchor.fallback) - point_world).length_squared > 1.0e-18:
        anchor.fallback = point_world
    return point_world


def _normal_to_local(obj, normal_world):
    """Convert a world-space plane normal to object-local coordinates."""
    normal_local = obj.matrix_world.to_3x3().transposed() @ Vector(normal_world)
    if normal_local.length_squared <= 1.0e-18:
        return None
    return normal_local.normalized()


def _normal_to_world(obj, normal_local):
    """Convert an object-local plane normal to world coordinates."""
    try:
        normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
    except ValueError:
        return None
    normal_world = normal_matrix @ Vector(normal_local)
    if normal_world.length_squared <= 1.0e-18:
        return None
    return normal_world.normalized()


def set_dimension_plane(data, plane_normal, orientation_target=_ORIENTATION_TARGET_UNSET):
    """Save a dimension plane in world space and, when attached, object space."""
    normal_world = Vector(plane_normal)
    if normal_world.length_squared <= 1.0e-18:
        return False
    normal_world.normalize()

    if orientation_target is _ORIENTATION_TARGET_UNSET:
        orientation_target = data.orientation_target

    data.orientation_initialized = True
    data.orientation_target = orientation_target
    data.plane_normal = normal_world
    if orientation_target is not None:
        normal_local = _normal_to_local(orientation_target, normal_world)
        if normal_local is None:
            data.orientation_target = None
            return False
        data.plane_normal_local = normal_local
    return True


def resolve_dimension_plane(data):
    """Return the live world-space plane normal for a saved dimension."""
    target = data.orientation_target
    if target is not None:
        normal_world = _normal_to_world(target, data.plane_normal_local)
        if normal_world is not None:
            return normal_world
    normal_world = Vector(data.plane_normal)
    if normal_world.length_squared <= 1.0e-18:
        return Vector((0.0, 0.0, 1.0))
    return normal_world.normalized()


def _common_anchor_target(data):
    anchors = [data.anchor_1, data.anchor_2]
    if getattr(data, "dimension_type", "LINEAR") == "ANGLE":
        anchors.append(data.anchor_3)
    elif getattr(data, "placement_initialized", False):
        placement_anchor = getattr(data, "placement_anchor", None)
        # A free placement point should not prevent an endpoint-attached
        # dimension plane from following its measured object.  If the third
        # point is associated, however, it must belong to that same object.
        if (
            placement_anchor is not None
            and placement_anchor.kind != "FREE"
            and placement_anchor.target is not None
        ):
            anchors.append(placement_anchor)
    if all(anchor.kind != "FREE" and anchor.target is not None for anchor in anchors):
        target = anchors[0].target
        if all(anchor.target == target for anchor in anchors[1:]):
            return target
    return None


def _migrate_dimension_orientation(data, p1, p2):
    """Attach legacy world-fixed planes to their measured object when possible."""
    if data.orientation_initialized:
        return

    target = _common_anchor_target(data)
    current_normal = resolve_dimension_plane(data)
    if target is None:
        set_dimension_plane(data, current_normal, None)
        return

    # Legacy axis-aligned dimensions can be repaired even when the object was
    # already rotated before this migration.  Pick the object's valid local
    # dimension plane that stays closest to the old on-screen offset direction.
    line_world = Vector(p2) - Vector(p1)
    current_basis = dimension_basis(p1, p2, current_normal)
    try:
        line_local = target.matrix_world.to_3x3().inverted() @ line_world
    except ValueError:
        line_local = Vector((0.0, 0.0, 0.0))

    best_normal = None
    if (
        current_basis is not None
        and line_local.length_squared > 1.0e-18
        and max(abs(line_local.normalized().dot(axis)) for axis in _LOCAL_AXES)
        >= _LOCAL_AXIS_ALIGNMENT
    ):
        line_local.normalize()
        current_offset = current_basis[1]
        best_score = -1.0
        for local_normal in _LOCAL_AXES:
            if abs(line_local.dot(local_normal)) >= _LOCAL_AXIS_ALIGNMENT:
                continue
            world_normal = _normal_to_world(target, local_normal)
            if world_normal is None:
                continue
            candidate_basis = dimension_basis(p1, p2, world_normal)
            if candidate_basis is None:
                continue
            candidate_offset = candidate_basis[1]
            score = abs(candidate_offset.dot(current_offset))
            if score > best_score:
                if candidate_offset.dot(current_offset) < 0.0:
                    world_normal.negate()
                best_score = score
                best_normal = world_normal

    set_dimension_plane(
        data,
        best_normal if best_normal is not None else current_normal,
        target,
    )


def _migrate_linear_placement(data, p1, p2, plane_normal):
    """Give legacy dimensions a stable equivalent placement anchor."""
    if getattr(data, "placement_initialized", False):
        return
    placement_anchor = getattr(data, "placement_anchor", None)
    if placement_anchor is None:
        return

    direction = Vector(data.linear_direction)
    basis = dimension_basis(
        p1,
        p2,
        plane_normal,
        direction if direction.length_squared > 1.0e-18 else None,
    )
    if basis is None:
        return
    midpoint = (Vector(p1) + Vector(p2)) * 0.5
    placement = midpoint + basis[1] * float(data.offset_distance)
    set_anchor(placement_anchor, placement)
    data.placement_initialized = True
    data.placement_mode = (
        "PROJECTED" if direction.length_squared > 1.0e-18 else "FIXED"
    )


def dimension_anchors_valid(root):
    data = getattr(root, "radcad_dimension", None)
    if data is None or not data.is_dimension:
        return False
    anchors = (data.anchor_1, data.anchor_2)
    if getattr(data, "dimension_type", "LINEAR") == "ANGLE":
        anchors += (data.anchor_3,)
    elif getattr(data, "placement_initialized", False):
        placement_anchor = getattr(data, "placement_anchor", None)
        if (
            placement_anchor is not None
            and placement_anchor.kind != "FREE"
            and _resolve_associated_anchor(placement_anchor) is None
        ):
            return False
    for anchor in anchors:
        if anchor.kind != "FREE" and _resolve_associated_anchor(anchor) is None:
            return False
    return True


def _collection_in_tree(parent, target):
    if parent == target:
        return True
    return any(_collection_in_tree(child, target) for child in parent.children)


def _get_collection(scene):
    collection = bpy.data.collections.get(COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(COLLECTION_NAME)
        scene.collection.children.link(collection)
    elif not _collection_in_tree(scene.collection, collection):
        scene.collection.children.link(collection)
    return collection


def _remove_geometry_children(root):
    """Migrate dimensions made by the discarded geometry implementation."""
    materials = set()
    for child in list(root.children):
        datablock = child.data
        if datablock is not None and hasattr(datablock, "materials"):
            materials.update(datablock.materials)
        bpy.data.objects.remove(child, do_unlink=True)
        if datablock is not None and datablock.users == 0 and isinstance(datablock, bpy.types.Curve):
            bpy.data.curves.remove(datablock)
    for material in materials:
        if material is not None and material.users == 0:
            bpy.data.materials.remove(material)


def _apply_dimension_style(context, data):
    """Copy the scene's current annotation defaults to a new dimension."""
    data.text_size = context.scene.radcad_dimension_text_size
    data.text_thickness = context.scene.radcad_dimension_text_thickness
    data.arrow_size = context.scene.radcad_dimension_arrow_size
    data.extension_gap = context.scene.radcad_dimension_extension_gap
    data.extension_overshoot = context.scene.radcad_dimension_extension_overshoot
    data.line_width = context.scene.radcad_dimension_line_width
    data.color = context.scene.radcad_dimension_color


def create_dimension(
    context,
    p1,
    p2,
    plane_normal,
    offset_distance,
    snap_1=None,
    snap_2=None,
    linear_direction=None,
    placement_point=None,
    snap_placement=None,
    placement_mode="FACE",
):
    debug.log_dimension_snapshot(
        context.scene,
        "create_linear_begin",
        p1=p1,
        p2=p2,
        plane=plane_normal,
        offset=offset_distance,
        direction=linear_direction,
        placement=placement_point,
        placement_mode=placement_mode,
    )
    debug.log(
        "linear_existing_dimensions",
        policy="preserve_existing",
        candidates=[
            {
                "name": root.name,
                "pointer": root.as_pointer(),
            }
            for root in iter_dimensions(context.scene)
            if getattr(
                getattr(root, "radcad_dimension", None),
                "dimension_type",
                "LINEAR",
            ) == "LINEAR"
        ],
    )
    collection = _get_collection(context.scene)
    root = bpy.data.objects.new(ROOT_PREFIX, None)
    collection.objects.link(root)

    data = root.radcad_dimension
    data.is_dimension = True
    data.selected = False
    data.dimension_type = "LINEAR"
    set_anchor(data.anchor_1, p1, snap_1)
    set_anchor(data.anchor_2, p2, snap_2)
    if placement_point is not None:
        set_anchor(data.placement_anchor, placement_point, snap_placement)
        data.placement_initialized = True
    data.placement_mode = str(placement_mode).upper()
    set_dimension_plane(data, plane_normal, _common_anchor_target(data))
    direction = (
        Vector(linear_direction)
        if linear_direction is not None
        else Vector((0.0, 0.0, 0.0))
    )
    if direction.length_squared > 1.0e-18 and data.placement_mode != "NORMAL":
        data.linear_direction = direction.normalized()
    elif data.placement_mode == "NORMAL":
        data.linear_direction = (0.0, 0.0, 0.0)
    data.offset_distance = offset_distance
    _apply_dimension_style(context, data)

    updated = update_dimension(root)
    debug.log(
        "linear_dimension_created",
        created=root.name,
        updated=updated,
        preserved_count=len(iter_dimensions(context.scene)),
    )
    # Linear dimensions are finished annotations, not an active selection.
    context.scene.radcad_active_dimension = None
    debug.log_dimension_snapshot(
        context.scene,
        "create_linear_end",
        created=root.name,
        pointer=root.as_pointer(),
    )
    return root


def create_angle_dimension(
    context,
    vertex,
    ray_1,
    ray_2,
    plane_normal,
    radius,
    snap_vertex=None,
    snap_ray_1=None,
    snap_ray_2=None,
):
    """Create a persistent angle annotation from a vertex and two ray points."""
    debug.log_dimension_snapshot(
        context.scene,
        "create_angle_begin",
        vertex=vertex,
        ray_1=ray_1,
        ray_2=ray_2,
        plane=plane_normal,
        radius=radius,
    )
    collection = _get_collection(context.scene)
    root = bpy.data.objects.new(ROOT_PREFIX, None)
    collection.objects.link(root)

    data = root.radcad_dimension
    data.is_dimension = True
    data.dimension_type = "ANGLE"
    set_anchor(data.anchor_1, vertex, snap_vertex)
    set_anchor(data.anchor_2, ray_1, snap_ray_1)
    set_anchor(data.anchor_3, ray_2, snap_ray_2)
    set_dimension_plane(data, plane_normal, _common_anchor_target(data))
    data.offset_distance = abs(float(radius))
    _apply_dimension_style(context, data)

    update_dimension(root)
    # Angle dimensions are finished annotations, not an active selection.
    context.scene.radcad_active_dimension = None
    debug.log_dimension_snapshot(
        context.scene,
        "create_angle_end",
        created=root.name,
        pointer=root.as_pointer(),
    )
    return root


def dimension_layout(root):
    data = getattr(root, "radcad_dimension", None)
    if data is None or not data.is_dimension or not dimension_anchors_valid(root):
        return None, ""

    owning_scene = next((scene for scene in bpy.data.scenes if root.name in scene.objects), bpy.context.scene)
    if getattr(data, "dimension_type", "LINEAR") == "ANGLE":
        vertex = resolve_anchor(data.anchor_1)
        ray_1 = resolve_anchor(data.anchor_2)
        ray_2 = resolve_anchor(data.anchor_3)
        layout = build_angle_layout(
            vertex,
            ray_1,
            ray_2,
            resolve_dimension_plane(data),
            data.offset_distance,
            0.001,
            0.001,
            data.extension_gap,
            data.extension_overshoot,
        )
        if layout is None:
            return None, ""
        return layout, dimension_label(data, layout.measured_angle, owning_scene)

    p1 = resolve_anchor(data.anchor_1)
    p2 = resolve_anchor(data.anchor_2)
    _migrate_dimension_orientation(data, p1, p2)
    plane_normal = resolve_dimension_plane(data)
    _migrate_linear_placement(data, p1, p2, plane_normal)
    if getattr(data, "placement_mode", "FACE") == "NORMAL":
        linear_direction = projected_line_direction(p1, p2, plane_normal)
    else:
        linear_direction = Vector(data.linear_direction)
        if linear_direction.length_squared <= 1.0e-18:
            linear_direction = None
    layout = build_layout(
        p1,
        p2,
        plane_normal,
        data.offset_distance,
        0.001,
        0.001,
        data.extension_gap,
        data.extension_overshoot,
        dimension_direction=linear_direction,
    )
    if layout is None:
        return None, ""
    return layout, dimension_label(data, layout.measured_length, owning_scene)


def update_dimension(root):
    data = getattr(root, "radcad_dimension", None)
    if data is None or not data.is_dimension:
        return False

    # Data lives in a hidden Empty because Blender properties need an ID owner.
    # Nothing from that object is displayed; the annotation is GPU/HUD only.
    _remove_geometry_children(root)
    if not root.hide_viewport:
        root.hide_viewport = True
    if not root.hide_render:
        root.hide_render = True
    if not root.hide_select:
        root.hide_select = True
    if root.empty_display_size != 0.0001:
        root.empty_display_size = 0.0001

    layout, label = dimension_layout(root)
    if layout is None:
        return False

    # Migrate pixel-style values from the abandoned world-geometry prototype.
    text_size = float(data.text_size) if data.text_size >= 4.0 else 14.0
    text_thickness = max(1.0, float(data.text_thickness))
    arrow_size = float(data.arrow_size) if data.arrow_size >= 2.0 else 10.0
    line_width = float(data.line_width) if data.line_width >= 0.5 else 1.0
    if data.text_size != text_size:
        data["text_size"] = text_size
    if data.arrow_size != arrow_size:
        data["arrow_size"] = arrow_size
    if data.line_width != line_width:
        data["line_width"] = line_width

    dimension_type = getattr(data, "dimension_type", "LINEAR")
    if dimension_type == "ANGLE":
        layout_values = (*layout.vertex, *layout.ray_1, *layout.ray_2, *layout.plane_normal)
        measured_value = layout.measured_angle
    else:
        layout_values = (*layout.p1, *layout.p2, *layout.plane_normal)
        measured_value = layout.measured_length

    signature = (
        dimension_type,
        *(round(value, 12) for value in layout_values),
        *(
            round(float(value), 12)
            for value in getattr(data, "linear_direction", (0.0, 0.0, 0.0))
        ),
        round(float(data.offset_distance), 12),
        round(text_size, 12),
        round(text_thickness, 12),
        round(arrow_size, 12),
        round(float(data.extension_gap), 12),
        round(float(data.extension_overshoot), 12),
        round(line_width, 12),
        *(round(float(value), 8) for value in data.color),
        label,
    )
    root_pointer = root.as_pointer()
    if _UPDATE_SIGNATURES.get(root_pointer) == signature:
        return True
    _UPDATE_SIGNATURES[root_pointer] = signature

    if dimension_type == "ANGLE":
        if abs(data.measured_angle - measured_value) > 1.0e-12:
            data.measured_angle = measured_value
    elif abs(data.measured_length - measured_value) > 1.0e-12:
        data.measured_length = measured_value
    if (Vector(data.plane_normal) - layout.plane_normal).length_squared > 1.0e-18:
        set_dimension_plane(data, layout.plane_normal)
    return True


def update_all_dimensions(scene=None):
    roots = list(iter_dimensions(scene))
    for obj in roots:
        if dimension_anchors_valid(obj):
            update_dimension(obj)
        else:
            delete_dimension(obj)


def delete_dimension(root):
    if root is None:
        return
    _UPDATE_SIGNATURES.pop(root.as_pointer(), None)
    _remove_geometry_children(root)
    for scene in bpy.data.scenes:
        if getattr(scene, "radcad_active_dimension", None) == root:
            scene.radcad_active_dimension = None
    bpy.data.objects.remove(root, do_unlink=True)

    # The collection is created by the dimension tool solely as a container
    # for dimension data objects. Remove it once the final dimension is gone.
    collection = bpy.data.collections.get(COLLECTION_NAME)
    if collection is not None and not collection.objects and not collection.children:
        bpy.data.collections.remove(collection, do_unlink=True)


def delete_dimensions(roots):
    """Delete a stable snapshot of dimensions as one undoable operation."""
    for root in list(roots):
        if root is not None and root.name in bpy.data.objects:
            delete_dimension(root)
