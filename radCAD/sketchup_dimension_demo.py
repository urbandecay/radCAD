import math

import bpy
from bpy.app.handlers import persistent
from mathutils import Vector


COLLECTION_NAME = "SketchUp Dimension Demo"
P1_NAME = "DimDemo_P1_move_me"
P2_NAME = "DimDemo_P2_move_me"
OFFSET_NAME = "DimDemo_Offset_move_me"
GEOM_LINE_NAME = "DimDemo_real_line"
DIM_LINE_NAME = "DimDemo_dimension_line"
WITNESS_1_NAME = "DimDemo_witness_1"
WITNESS_2_NAME = "DimDemo_witness_2"
TEXT_NAME = "DimDemo_live_text"
HANDLER_NAME = "sketchup_dim_demo_update"

_UPDATING = False


def remove_old_handler():
    handlers = bpy.app.handlers.depsgraph_update_post
    for handler in list(handlers):
        if getattr(handler, "__name__", "") == HANDLER_NAME:
            handlers.remove(handler)


def remove_old_demo():
    col = bpy.data.collections.get(COLLECTION_NAME)
    if not col:
        return

    for obj in list(col.objects):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data and data.users == 0:
            if data.__class__.__name__ == "Mesh":
                bpy.data.meshes.remove(data)
            elif data.__class__.__name__ == "Curve":
                bpy.data.curves.remove(data)

    bpy.data.collections.remove(col)


def get_or_create_collection():
    col = bpy.data.collections.new(COLLECTION_NAME)
    bpy.context.scene.collection.children.link(col)
    return col


def make_material(name, color):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    return mat


def link_to_collection(obj, col):
    col.objects.link(obj)
    for other_col in list(obj.users_collection):
        if other_col != col:
            other_col.objects.unlink(obj)


def make_handle(name, loc, radius, mat, col):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=radius, location=loc)
    obj = bpy.context.object
    obj.name = name
    obj.data.name = name + "_Mesh"
    obj.data.materials.append(mat)
    obj.show_name = True
    link_to_collection(obj, col)
    return obj


def make_line_mesh(name, mat, col):
    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0)], [(0, 1)], [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.data.materials.append(mat)
    col.objects.link(obj)
    return obj


def set_line_world(obj, a, b):
    obj.location = (0, 0, 0)
    obj.rotation_euler = (0, 0, 0)
    obj.scale = (1, 1, 1)
    obj.data.vertices[0].co = a
    obj.data.vertices[1].co = b
    obj.data.update()


def format_distance(length):
    scene = bpy.context.scene
    units = scene.unit_settings
    scaled = length * (units.scale_length or 1.0)

    if units.system == "IMPERIAL":
        inches = scaled * 39.3700787402
        feet = int(inches // 12)
        rem = inches - feet * 12
        if units.length_unit == "INCHES":
            return f'{inches:.2f}"'
        return f"{feet}' {rem:.2f}\""

    if units.length_unit == "MILLIMETERS":
        return f"{scaled * 1000.0:.1f} mm"
    if units.length_unit == "CENTIMETERS":
        return f"{scaled * 100.0:.2f} cm"
    return f"{scaled:.3f} m"


def get_obj(name):
    return bpy.data.objects.get(name)


def perpendicular_offset(raw_offset, line_dir):
    offset = raw_offset - line_dir * raw_offset.dot(line_dir)
    if offset.length <= 0.0001:
        return Vector((0, 1, 0))
    return offset


@persistent
def sketchup_dim_demo_update(scene, depsgraph):
    global _UPDATING
    if _UPDATING:
        return

    p1_obj = get_obj(P1_NAME)
    p2_obj = get_obj(P2_NAME)
    offset_obj = get_obj(OFFSET_NAME)
    geom_line = get_obj(GEOM_LINE_NAME)
    dim_line = get_obj(DIM_LINE_NAME)
    witness_1 = get_obj(WITNESS_1_NAME)
    witness_2 = get_obj(WITNESS_2_NAME)
    text_obj = get_obj(TEXT_NAME)

    if not all((p1_obj, p2_obj, offset_obj, geom_line, dim_line, witness_1, witness_2, text_obj)):
        return

    try:
        _UPDATING = True

        p1 = p1_obj.location.copy()
        p2 = p2_obj.location.copy()
        base = p2 - p1
        if base.length <= 0.0001:
            return

        line_dir = base.normalized()
        mid = (p1 + p2) * 0.5

        last_offset_loc = Vector(offset_obj.get("_last_loc", list(offset_obj.location)))
        handle_moved = (offset_obj.location - last_offset_loc).length > 0.0001

        if handle_moved:
            offset_vec = perpendicular_offset(offset_obj.location - mid, line_dir)
            offset_obj["offset_vec"] = list(offset_vec)
        else:
            offset_vec = Vector(offset_obj.get("offset_vec", [0, 1, 0]))
            offset_vec = perpendicular_offset(offset_vec, line_dir)
            offset_obj.location = mid + offset_vec

        d1 = p1 + offset_vec
        d2 = p2 + offset_vec

        set_line_world(geom_line, p1, p2)
        set_line_world(dim_line, d1, d2)
        set_line_world(witness_1, p1, d1)
        set_line_world(witness_2, p2, d2)

        text_obj.data.body = format_distance(base.length)
        text_obj.location = (d1 + d2) * 0.5 + offset_vec.normalized() * 0.18
        text_obj.rotation_euler = (math.radians(90), 0, math.atan2(line_dir.y, line_dir.x))
        text_obj["measured_length"] = base.length

        offset_obj["_last_loc"] = list(offset_obj.location)
    finally:
        _UPDATING = False


def create_demo():
    remove_old_handler()
    remove_old_demo()

    col = get_or_create_collection()

    red = make_material("DimDemo_Red_Handle", (1.0, 0.1, 0.08, 1.0))
    green = make_material("DimDemo_Green_Handle", (0.1, 0.9, 0.2, 1.0))
    blue = make_material("DimDemo_Blue_Offset_Handle", (0.1, 0.35, 1.0, 1.0))
    black = make_material("DimDemo_Black_Lines", (0.02, 0.02, 0.02, 1.0))
    dim_mat = make_material("DimDemo_Dim_Lines", (0.0, 0.55, 1.0, 1.0))

    p1 = make_handle(P1_NAME, (-2.0, 0.0, 0.0), 0.12, red, col)
    p2 = make_handle(P2_NAME, (2.0, 0.0, 0.0), 0.12, green, col)
    offset = make_handle(OFFSET_NAME, (0.0, 1.0, 0.0), 0.10, blue, col)
    offset["offset_vec"] = [0.0, 1.0, 0.0]
    offset["_last_loc"] = list(offset.location)

    make_line_mesh(GEOM_LINE_NAME, black, col)
    make_line_mesh(DIM_LINE_NAME, dim_mat, col)
    make_line_mesh(WITNESS_1_NAME, dim_mat, col)
    make_line_mesh(WITNESS_2_NAME, dim_mat, col)

    font = bpy.data.curves.new(TEXT_NAME + "_Curve", "FONT")
    font.align_x = "CENTER"
    font.align_y = "CENTER"
    font.size = 0.22
    text = bpy.data.objects.new(TEXT_NAME, font)
    text.data.materials.append(dim_mat)
    col.objects.link(text)

    bpy.app.handlers.depsgraph_update_post.append(sketchup_dim_demo_update)
    sketchup_dim_demo_update(bpy.context.scene, None)

    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    p2.select_set(True)
    bpy.context.view_layer.objects.active = p2

    print("SketchUp dimension demo created.")
    print("Move the red or green handle to resize the measured line.")
    print("Move the blue handle to change how far the dimension sits from the line.")


create_demo()
