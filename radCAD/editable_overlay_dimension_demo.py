import json
import math

import blf
import bpy
import gpu
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector


DATA_KEY = "editable_overlay_dimension_demo"
OPERATOR_ID = "view3d.editable_overlay_dimension_demo"


def default_data():
    return {
        "points": [[-2.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
        "offset": [0.0, 1.0, 0.0],
    }


def load_data(scene):
    raw = scene.get(DATA_KEY)
    if not raw:
        data = default_data()
        save_data(scene, data)
        return data
    try:
        return json.loads(raw)
    except Exception:
        data = default_data()
        save_data(scene, data)
        return data


def save_data(scene, data):
    scene[DATA_KEY] = json.dumps(data)


def points_from_data(data):
    return [Vector(p) for p in data["points"]]


def set_points(data, points):
    data["points"] = [[p.x, p.y, p.z] for p in points]


def offset_from_data(data):
    return Vector(data["offset"])


def set_offset(data, offset):
    data["offset"] = [offset.x, offset.y, offset.z]


def world_to_screen(region, rv3d, point):
    return view3d_utils.location_3d_to_region_2d(region, rv3d, point)


def mouse_to_plane(context, event, fallback):
    region = context.region
    rv3d = context.space_data.region_3d
    coord = (event.mouse_region_x, event.mouse_region_y)
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    ray_dir = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)

    if abs(ray_dir.z) <= 0.000001:
        return fallback.copy()

    t = -ray_origin.z / ray_dir.z
    return ray_origin + ray_dir * t


def dist_to_segment_2d(point, a, b):
    ab = b - a
    if ab.length_squared <= 0.000001:
        return (point - a).length, 0.0
    t = max(0.0, min(1.0, (point - a).dot(ab) / ab.length_squared))
    closest = a + ab * t
    return (point - closest).length, t


def line_length(points):
    total = 0.0
    for i in range(len(points) - 1):
        total += (points[i + 1] - points[i]).length
    return total


def format_len(length):
    units = bpy.context.scene.unit_settings
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


class VIEW3D_OT_editable_overlay_dimension_demo(bpy.types.Operator):
    bl_idname = OPERATOR_ID
    bl_label = "Editable Overlay Dimension Demo"
    bl_options = {"REGISTER"}

    _handle = None
    _running = False

    drag_kind = None
    drag_index = None
    hover_segment = None

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def draw_line(self, coords, color, width=1.0):
        if len(coords) < 2:
            return
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(width)
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        batch = batch_for_shader(shader, "LINES", {"pos": coords})
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)
        gpu.state.line_width_set(1.0)

    def draw_handle(self, center, color):
        size = 7
        x = center.x
        y = center.y
        coords = [
            (x - size, y),
            (x + size, y),
            (x, y - size),
            (x, y + size),
            (x - size * 0.7, y - size * 0.7),
            (x + size * 0.7, y + size * 0.7),
            (x - size * 0.7, y + size * 0.7),
            (x + size * 0.7, y - size * 0.7),
        ]
        self.draw_line(coords, color, 2.0)

    def draw_callback(self, context):
        region = context.region
        rv3d = context.space_data.region_3d
        data = load_data(context.scene)
        points = points_from_data(data)
        offset = offset_from_data(data)
        dim_points = [p + offset for p in points]

        base_2d = [world_to_screen(region, rv3d, p) for p in points]
        dim_2d = [world_to_screen(region, rv3d, p) for p in dim_points]
        if any(p is None for p in base_2d + dim_2d):
            return

        base_segments = []
        dim_segments = []
        witness_segments = []
        for i in range(len(base_2d) - 1):
            base_segments.extend([base_2d[i], base_2d[i + 1]])
            dim_segments.extend([dim_2d[i], dim_2d[i + 1]])
        for i in range(len(base_2d)):
            witness_segments.extend([base_2d[i], dim_2d[i]])

        self.draw_line(base_segments, (0.05, 0.05, 0.05, 0.65), 2.0)
        self.draw_line(witness_segments, (0.0, 0.55, 1.0, 0.85), 1.5)
        self.draw_line(dim_segments, (0.0, 0.55, 1.0, 1.0), 2.5)

        for p in base_2d:
            self.draw_handle(p, (1.0, 0.15, 0.05, 1.0))

        mid = (dim_2d[0] + dim_2d[-1]) * 0.5
        self.draw_handle(mid, (0.05, 0.35, 1.0, 1.0))

        blf.size(0, 16)
        blf.color(0, 0.0, 0.0, 0.0, 1.0)
        label = format_len(line_length(points))
        width, height = blf.dimensions(0, label)
        blf.position(0, mid.x - width / 2.0, mid.y + 14.0, 0)
        blf.draw(0, label)

        if self.hover_segment is not None:
            hint = "A: add point    Del: remove selected point    Esc: exit"
            blf.size(0, 12)
            blf.color(0, 0.0, 0.0, 0.0, 0.65)
            blf.position(0, 18, 18, 0)
            blf.draw(0, hint)

    def hit_test(self, context, event):
        region = context.region
        rv3d = context.space_data.region_3d
        data = load_data(context.scene)
        points = points_from_data(data)
        offset = offset_from_data(data)
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))

        point_2d = [world_to_screen(region, rv3d, p) for p in points]
        dim_2d = [world_to_screen(region, rv3d, p + offset) for p in points]
        if any(p is None for p in point_2d + dim_2d):
            return None, None

        for i, p in enumerate(point_2d):
            if (mouse - p).length <= 14:
                return "point", i

        dim_mid = (dim_2d[0] + dim_2d[-1]) * 0.5
        if (mouse - dim_mid).length <= 16:
            return "offset", None

        best = None
        best_dist = 12.0
        for i in range(len(dim_2d) - 1):
            dist, _t = dist_to_segment_2d(mouse, dim_2d[i], dim_2d[i + 1])
            if dist < best_dist:
                best_dist = dist
                best = i
        if best is not None:
            return "segment", best

        return None, None

    def modal(self, context, event):
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}

        if event.type == "ESC" and event.value == "PRESS":
            self.finish(context)
            return {"FINISHED"}

        data = load_data(context.scene)
        points = points_from_data(data)
        offset = offset_from_data(data)

        if event.type == "MOUSEMOVE":
            if self.drag_kind == "point" and self.drag_index is not None:
                points[self.drag_index] = mouse_to_plane(context, event, points[self.drag_index])
                set_points(data, points)
                save_data(context.scene, data)
            elif self.drag_kind == "offset":
                p_mid = (points[0] + points[-1]) * 0.5
                new_loc = mouse_to_plane(context, event, p_mid + offset)
                set_offset(data, new_loc - p_mid)
                save_data(context.scene, data)
            else:
                kind, index = self.hit_test(context, event)
                self.hover_segment = index if kind == "segment" else None
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            kind, index = self.hit_test(context, event)
            if kind in {"point", "offset"}:
                self.drag_kind = kind
                self.drag_index = index
            elif kind == "segment":
                self.hover_segment = index
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self.drag_kind = None
            self.drag_index = None
            return {"RUNNING_MODAL"}

        if event.type == "A" and event.value == "PRESS" and self.hover_segment is not None:
            i = self.hover_segment
            points.insert(i + 1, (points[i] + points[i + 1]) * 0.5)
            set_points(data, points)
            save_data(context.scene, data)
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type in {"DEL", "DELETE", "BACK_SPACE", "BACKSPACE"} and event.value == "PRESS":
            kind, index = self.hit_test(context, event)
            if kind == "point" and index is not None and len(points) > 2:
                points.pop(index)
                set_points(data, points)
                save_data(context.scene, data)
                context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    def invoke(self, context, event):
        if self.__class__._running:
            self.finish(context)

        load_data(context.scene)
        self.__class__._handle = bpy.types.SpaceView3D.draw_handler_add(
            self.draw_callback,
            (context,),
            "WINDOW",
            "POST_PIXEL",
        )
        self.__class__._running = True
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def finish(self, context):
        if self.__class__._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self.__class__._handle, "WINDOW")
            self.__class__._handle = None
        self.__class__._running = False
        if context.area:
            context.area.tag_redraw()


def start_in_first_view3d():
    screen = bpy.context.screen
    if not screen:
        return None

    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        space = next((s for s in area.spaces if s.type == "VIEW_3D"), None)
        if region is None or space is None:
            continue
        with bpy.context.temp_override(area=area, region=region, space_data=space):
            bpy.ops.view3d.editable_overlay_dimension_demo("INVOKE_DEFAULT")
        return area
    return None


def register():
    if not hasattr(bpy.types, "VIEW3D_OT_editable_overlay_dimension_demo"):
        bpy.utils.register_class(VIEW3D_OT_editable_overlay_dimension_demo)


def unregister():
    if VIEW3D_OT_editable_overlay_dimension_demo._handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(
            VIEW3D_OT_editable_overlay_dimension_demo._handle,
            "WINDOW",
        )
        VIEW3D_OT_editable_overlay_dimension_demo._handle = None
    VIEW3D_OT_editable_overlay_dimension_demo._running = False
    bpy.utils.unregister_class(VIEW3D_OT_editable_overlay_dimension_demo)


register()
area = start_in_first_view3d()
if area is None:
    print("Editable overlay dimension demo registered. Open a 3D View and run: bpy.ops.view3d.editable_overlay_dimension_demo('INVOKE_DEFAULT')")
else:
    print("Editable overlay dimension demo running.")
    print("Drag red overlay handles to edit points. Drag blue handle to move the dimension offset.")
    print("Hover dimension line and press A to add a point. Press Esc to exit overlay edit mode.")
