"""Creation, association resolution, and updates for persistent dimensions."""

import json

import bmesh
import bpy
from mathutils import Vector

from .constants import COLLECTION_NAME, ROOT_PREFIX
from .formatting import dimension_label
from .geometry import build_layout


_UPDATE_SIGNATURES = {}


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
    anchor.weights = "[]"
    if snap_result is None or snap_result.target_object is None or not snap_result.element_indices:
        return
    anchor.target = snap_result.target_object
    anchor.kind = snap_result.kind
    anchor.indices = json.dumps([int(index) for index in snap_result.element_indices])
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


def resolve_anchor(anchor):
    fallback = Vector(anchor.fallback)
    obj = anchor.target
    indices = [int(index) for index in _read_json_numbers(anchor.indices)]
    weights = [float(weight) for weight in _read_json_numbers(anchor.weights)]
    if obj is None or not indices or len(indices) != len(weights):
        return fallback

    positions = _mesh_vertex_local_positions(obj, indices)
    if positions is None:
        return fallback
    point_local = Vector((0.0, 0.0, 0.0))
    for position, weight in zip(positions, weights):
        point_local += position * weight
    point_world = obj.matrix_world @ point_local
    if (Vector(anchor.fallback) - point_world).length_squared > 1.0e-18:
        anchor.fallback = point_world
    return point_world


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


def create_dimension(context, p1, p2, plane_normal, offset_distance, snap_1=None, snap_2=None):
    collection = _get_collection(context.scene)
    root = bpy.data.objects.new(ROOT_PREFIX, None)
    collection.objects.link(root)

    data = root.radcad_dimension
    data.is_dimension = True
    set_anchor(data.anchor_1, p1, snap_1)
    set_anchor(data.anchor_2, p2, snap_2)
    data.plane_normal = Vector(plane_normal)
    data.offset_distance = offset_distance
    data.text_size = context.scene.radcad_dimension_text_size
    data.arrow_size = context.scene.radcad_dimension_arrow_size
    data.extension_gap = context.scene.radcad_dimension_extension_gap
    data.extension_overshoot = context.scene.radcad_dimension_extension_overshoot
    data.line_width = context.scene.radcad_dimension_line_width
    data.color = context.scene.radcad_dimension_color

    update_dimension(root)
    context.scene.radcad_active_dimension = root
    return root


def dimension_layout(root):
    data = getattr(root, "radcad_dimension", None)
    if data is None or not data.is_dimension:
        return None, ""
    p1 = resolve_anchor(data.anchor_1)
    p2 = resolve_anchor(data.anchor_2)
    layout = build_layout(
        p1,
        p2,
        data.plane_normal,
        data.offset_distance,
        0.001,
        0.001,
        data.extension_gap,
        data.extension_overshoot,
    )
    if layout is None:
        return None, ""
    owning_scene = next((scene for scene in bpy.data.scenes if root.name in scene.objects), bpy.context.scene)
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
    arrow_size = float(data.arrow_size) if data.arrow_size >= 2.0 else 10.0
    line_width = float(data.line_width) if data.line_width >= 0.5 else 1.0
    if data.text_size != text_size:
        data["text_size"] = text_size
    if data.arrow_size != arrow_size:
        data["arrow_size"] = arrow_size
    if data.line_width != line_width:
        data["line_width"] = line_width

    signature = (
        *(round(value, 12) for value in (*layout.p1, *layout.p2, *layout.plane_normal)),
        round(float(data.offset_distance), 12),
        round(text_size, 12),
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

    if abs(data.measured_length - layout.measured_length) > 1.0e-12:
        data.measured_length = layout.measured_length
    if (Vector(data.plane_normal) - layout.plane_normal).length_squared > 1.0e-18:
        data.plane_normal = layout.plane_normal
    return True


def update_all_dimensions(scene=None):
    for obj in iter_dimensions(scene):
        update_dimension(obj)


def delete_dimension(root):
    if root is None:
        return
    _UPDATE_SIGNATURES.pop(root.as_pointer(), None)
    _remove_geometry_children(root)
    for scene in bpy.data.scenes:
        if getattr(scene, "radcad_active_dimension", None) == root:
            scene.radcad_active_dimension = None
    bpy.data.objects.remove(root, do_unlink=True)
