"""SketchUp-style click-and-drag eraser for editable mesh geometry."""

import math
import time
from dataclasses import dataclass

import bmesh
import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from .hud_overlay import draw_ui_button, get_mixed_text_metrics
from .modal_core import DrawManager, is_event_over_ui
from .modal_state import style
from .preferences import get_prefs
from .snapping_utils import free_snap_context, invalidate_snap_cache, snap_mesh


DRAW_HANDLER_3D = "RADCAD_ERASE_HOVER_3D"
DRAW_HANDLER_2D = "RADCAD_ERASE_CONTROLS_2D"
DEFAULT_PICK_RADIUS_PX = 6.0
STROKE_SAMPLE_PX = 7.0
# Native height of the traced SVG reference.
CURSOR_SIZE_PX = 25.0
CURSOR_VIEW_MARGIN_PX = 4.0

_cursor_texture = None
_cursor_texture_source = None


@dataclass
class _EraseTarget:
    kind: str
    obj: object
    indices: tuple
    element: object = None
    point: tuple = ()
    line: tuple = ()
    triangles: tuple = ()
    outline: tuple = ()


def _uniform_color_shader():
    try:
        return gpu.shader.from_builtin("UNIFORM_COLOR")
    except Exception:
        return gpu.shader.from_builtin("3D_UNIFORM_COLOR")


def _face_from_vertex_indices(bm, indices):
    """Resolve the BMesh face represented by a snap-context triangle."""
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    if len(indices) < 3 or min(indices) < 0 or max(indices) >= len(bm.verts):
        return None

    wanted = {int(index) for index in indices}
    candidates = set(bm.verts[next(iter(wanted))].link_faces)
    for index in wanted:
        candidates.intersection_update(bm.verts[index].link_faces)
    return next(
        (face for face in candidates if wanted.issubset({vert.index for vert in face.verts})),
        None,
    )


def _target_from_snap(result):
    """Turn a SnapResult into stable indices plus viewport highlight geometry."""
    if result is None or result.kind not in {"VERT", "EDGE", "FACE"}:
        return None

    obj = result.target_object
    if (
        obj is None
        or obj.type != "MESH"
        or not obj.data.is_editmode
        or not result.element_indices
    ):
        return None

    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    indices = tuple(int(index) for index in result.element_indices)
    matrix = obj.matrix_world

    if result.kind == "VERT":
        index = indices[0]
        if index < 0 or index >= len(bm.verts) or bm.verts[index].hide:
            return None
        point = tuple(matrix @ bm.verts[index].co)
        return _EraseTarget("VERT", obj, (index,), element=bm.verts[index], point=point)

    if result.kind == "EDGE":
        if len(indices) != 2 or min(indices) < 0 or max(indices) >= len(bm.verts):
            return None
        edge = bm.edges.get((bm.verts[indices[0]], bm.verts[indices[1]]))
        if edge is None or edge.hide:
            return None
        line = tuple(tuple(matrix @ vert.co) for vert in edge.verts)
        return _EraseTarget("EDGE", obj, tuple(sorted(indices)), element=edge, line=line)

    face = _face_from_vertex_indices(bm, indices)
    if face is None or face.hide:
        return None

    triangles = []
    for loops in bm.calc_loop_triangles():
        if loops and loops[0].face is face:
            triangles.extend(tuple(matrix @ loop.vert.co) for loop in loops)
    outline = []
    for loop in face.loops:
        outline.extend(
            (
                tuple(matrix @ loop.vert.co),
                tuple(matrix @ loop.link_loop_next.vert.co),
            )
        )
    return _EraseTarget(
        "FACE",
        obj,
        tuple(sorted(vert.index for vert in face.verts)),
        element=face,
        triangles=tuple(triangles),
        outline=tuple(outline),
    )


def _erase_target(target):
    """Delete one still-valid target and update its edit mesh."""
    if target is None:
        return False
    obj = target.obj
    if obj is None or obj.type != "MESH" or not obj.data.is_editmode:
        return False

    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    if target.kind == "VERT":
        index = target.indices[0]
        element = target.element
        if element is None:
            if index < 0 or index >= len(bm.verts):
                return False
            element = bm.verts[index]
        if not element.is_valid or element.hide:
            return False
        bmesh.ops.delete(bm, geom=[element], context="VERTS")

    elif target.kind == "EDGE":
        indices = target.indices
        if len(indices) != 2:
            return False
        element = target.element
        if element is None or not element.is_valid:
            if min(indices) < 0 or max(indices) >= len(bm.verts):
                return False
            element = bm.edges.get((bm.verts[indices[0]], bm.verts[indices[1]]))
        if element is None or not element.is_valid or element.hide:
            return False
        # EDGES also removes attached faces and orphaned endpoints, matching
        # SketchUp's eraser behavior for a boundary or loose line.
        bmesh.ops.delete(bm, geom=[element], context="EDGES")

    elif target.kind == "FACE":
        element = target.element
        if element is None or not element.is_valid:
            element = _face_from_vertex_indices(bm, target.indices)
        if element is None or not element.is_valid or element.hide:
            return False
        # Keep the boundary wire when only the face itself is erased.
        bmesh.ops.delete(bm, geom=[element], context="FACES_ONLY")

    else:
        return False

    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    invalidate_snap_cache()
    return True


def _screen_point(context, point):
    return location_3d_to_region_2d(
        context.region,
        context.region_data,
        Vector(point),
    )


def _point_segment_distance(point, start, end):
    segment = end - start
    if segment.length_squared <= 1.0e-12:
        return (point - start).length
    factor = max(0.0, min(1.0, (point - start).dot(segment) / segment.length_squared))
    return (point - start.lerp(end, factor)).length


def _point_in_triangle(point, a, b, c):
    def signed_area(p1, p2, p3):
        return (p1.x - p3.x) * (p2.y - p3.y) - (p2.x - p3.x) * (p1.y - p3.y)

    d1 = signed_area(point, a, b)
    d2 = signed_area(point, b, c)
    d3 = signed_area(point, c, a)
    epsilon = 1.0e-5
    has_negative = d1 < -epsilon or d2 < -epsilon or d3 < -epsilon
    has_positive = d1 > epsilon or d2 > epsilon or d3 > epsilon
    return not (has_negative and has_positive)


def _target_within_pick_area(context, target, x, y, radius):
    """Enforce the configured radius exactly instead of trusting the GPU search box."""
    if target is None:
        return False

    mouse = Vector((x, y))
    if target.kind == "VERT":
        point = _screen_point(context, target.point)
        return point is not None and (mouse - point).length <= radius

    if target.kind == "EDGE":
        if len(target.line) != 2:
            return False
        start = _screen_point(context, target.line[0])
        end = _screen_point(context, target.line[1])
        return (
            start is not None
            and end is not None
            and _point_segment_distance(mouse, start, end) <= radius
        )

    if target.kind == "FACE":
        for index in range(0, len(target.triangles), 3):
            triangle = target.triangles[index:index + 3]
            if len(triangle) != 3:
                continue
            projected = tuple(_screen_point(context, point) for point in triangle)
            if all(point is not None for point in projected) and _point_in_triangle(
                mouse,
                projected[0],
                projected[1],
                projected[2],
            ):
                return True
    return False


def _draw_hover(operator):
    targets = list(getattr(operator, "pending_targets", ()))
    if not targets:
        target = getattr(operator, "hover_target", None)
        if target is not None:
            targets = [target]
    if not targets:
        return

    shader = _uniform_color_shader()
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")
    try:
        for target in targets:
            if target.kind == "FACE" and target.triangles:
                shader.bind()
                shader.uniform_float("color", (1.0, 0.08, 0.03, 0.22))
                batch_for_shader(shader, "TRIS", {"pos": target.triangles}).draw(shader)
                if target.outline:
                    gpu.state.line_width_set(2.0)
                    shader.uniform_float("color", (1.0, 0.08, 0.03, 1.0))
                    batch_for_shader(shader, "LINES", {"pos": target.outline}).draw(shader)
            elif target.kind == "EDGE" and target.line:
                gpu.state.line_width_set(2.0)
                shader.bind()
                shader.uniform_float("color", (1.0, 0.08, 0.03, 1.0))
                batch_for_shader(shader, "LINES", {"pos": target.line}).draw(shader)
            elif target.kind == "VERT" and target.point:
                gpu.state.point_size_set(8.0)
                shader.bind()
                shader.uniform_float("color", (1.0, 0.08, 0.03, 1.0))
                batch_for_shader(shader, "POINTS", {"pos": (target.point,)}).draw(shader)
    finally:
        gpu.state.point_size_set(1.0)
        gpu.state.line_width_set(1.0)
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("NONE")


def _draw_controls(operator):
    """Draw the Erase mode bar using the same styling as the drawing tools."""
    context = bpy.context
    if context.region is None:
        return

    buttons = (
        ("F1: Vert", operator.erase_verts, "erase_verts"),
        ("F2: Edge", operator.erase_edges, "erase_edges"),
        ("F5: Face", operator.erase_faces, "erase_faces"),
    )
    font_id = 0
    pad_x = 10
    spacing = 8
    total_width = 0.0
    for label, _active, _hitbox_id in buttons:
        _key, _label, key_width, label_width, _height = get_mixed_text_metrics(font_id, label)
        total_width += key_width + label_width + (pad_x * 2) + spacing
    total_width -= spacing

    margin_bottom = 60
    current_x = (context.region.width - total_width) * 0.5
    operator.ui_hitboxes.clear()

    blf.size(font_id, 16)
    title = "Erase"
    title_width = blf.dimensions(font_id, title)[0]
    blf.color(font_id, *style["font_color"])
    blf.position(
        font_id,
        current_x + (total_width - title_width) * 0.5,
        margin_bottom + 38,
        0,
    )
    blf.draw(font_id, title)

    for label, active, hitbox_id in buttons:
        width = draw_ui_button(
            current_x,
            margin_bottom,
            label,
            active,
            hitbox_id,
            hitbox_registry=operator.ui_hitboxes,
        )
        current_x += width + spacing

    _draw_cursor(operator)


def _get_cursor_texture():
    """Build a GPU texture from the exact SVG preview used by the panel."""
    global _cursor_texture, _cursor_texture_source

    # Import lazily to avoid coupling module registration order to the panel.
    from . import panel

    previews = panel.preview_collection
    if previews is None or "erase" not in previews:
        return None

    preview = previews["erase"]
    # Match Blender's button icons: use the preview's UI-sized icon buffer,
    # rather than the larger source image buffer.
    source = (preview.icon_id, tuple(preview.icon_size))
    if _cursor_texture is not None and source == _cursor_texture_source:
        return _cursor_texture

    width, height = preview.icon_size
    pixels = preview.icon_pixels_float[:]
    if width <= 0 or height <= 0 or len(pixels) != width * height * 4:
        return None

    buffer = gpu.types.Buffer("FLOAT", len(pixels), pixels)
    _cursor_texture = gpu.types.GPUTexture(
        (width, height),
        format="RGBA32F",
        data=buffer,
    )
    _cursor_texture.filter_mode(True)
    _cursor_texture_source = source
    return _cursor_texture


def _draw_cursor(operator):
    """Draw the shared SVG eraser with its lower front point as the hotspot."""
    cursor = getattr(operator, "cursor_xy", None)
    if cursor is None or not getattr(operator, "cursor_in_view", False):
        return

    try:
        texture = _get_cursor_texture()
        if texture is None:
            return
        shader = gpu.shader.from_builtin("IMAGE")
    except Exception:
        return

    # Background/headless contexts report zero here; interactive windows use
    # the real display scale.
    ui_scale = bpy.context.preferences.system.ui_scale or 1.0
    height = CURSOR_SIZE_PX * ui_scale
    width = height * (texture.width / texture.height)
    mouse_x, mouse_y = map(float, cursor)

    # Pin the traced SVG's lower front point. In the 138 x 137 rendered
    # preview it lands at approximately (58, 131), or 6 px above the bottom.
    left = mouse_x - width * (58.0 / 138.0)
    bottom = mouse_y - height * (6.0 / 137.0)
    right = left + width
    top = bottom + height

    # A POST_PIXEL handler is clipped to the 3D window region.  Keep the
    # complete cursor inside that region when the pointer approaches an edge;
    # otherwise Blender shows only the loop/front corner of the eraser.
    region = bpy.context.region
    if region is not None:
        margin = CURSOR_VIEW_MARGIN_PX * ui_scale
        if width <= region.width - (margin * 2.0):
            shift_x = max(margin - left, 0.0)
            shift_x += min((region.width - margin) - (right + shift_x), 0.0)
            left += shift_x
            right += shift_x
        if height <= region.height - (margin * 2.0):
            shift_y = max(margin - bottom, 0.0)
            shift_y += min((region.height - margin) - (top + shift_y), 0.0)
            bottom += shift_y
            top += shift_y

    positions = (
        (left, bottom, 0.0),
        (right, bottom, 0.0),
        (right, top, 0.0),
        (left, top, 0.0),
    )
    tex_coords = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))

    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        shader.uniform_sampler("image", texture)
        batch_for_shader(
            shader,
            "TRI_FAN",
            {"pos": positions, "texCoord": tex_coords},
        ).draw(shader)
    finally:
        gpu.state.blend_set("NONE")


def _event_is_in_view(context, event):
    """True only while the pointer is inside the 3D window region."""
    region = context.region
    if region is None or region.type != "WINDOW" or is_event_over_ui(context, event):
        return False
    return (
        0 <= event.mouse_region_x < region.width
        and 0 <= event.mouse_region_y < region.height
    )


class VIEW3D_OT_radcad_erase(bpy.types.Operator):
    bl_idname = "view3d.radcad_erase"
    bl_label = "Erase"
    bl_description = "In Mesh Edit Mode, click or drag across faces, edges, and vertices to erase them"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    running = False

    @classmethod
    def poll(cls, context):
        available = (
            context.area is not None
            and context.area.type == "VIEW_3D"
            and context.mode == "EDIT_MESH"
            and context.edit_object is not None
            and context.edit_object.type == "MESH"
        )
        if not available:
            cls.poll_message_set("Enter Mesh Edit Mode to use the Erase tool")
        return available

    def invoke(self, context, event):
        if context.region is None or context.region.type != "WINDOW":
            self.report({"WARNING"}, "Run the Erase tool from a 3D View")
            return {"CANCELLED"}

        DrawManager.clear_all()
        free_snap_context()
        self.running = True
        self.dragging = False
        self.changed = False
        self.hover_target = None
        self.pending_targets = []
        self.pending_target_keys = set()
        self.stroke_undo_started = False
        self.last_drag_mouse = None
        self.cursor_xy = (event.mouse_region_x, event.mouse_region_y)
        self.cursor_in_view = _event_is_in_view(context, event)
        self.ui_hitboxes = {}
        self.erase_verts = True
        self.erase_edges = True
        self.erase_faces = True
        self.tool_instance_id = f"ERASE_{time.time()}"
        context.scene.active_cad_tool_id = self.tool_instance_id
        try:
            context.window.cursor_modal_set(
                "NONE" if self.cursor_in_view else "DEFAULT"
            )
        except (TypeError, ValueError):
            context.window.cursor_modal_set("ERASER")
        context.area.header_text_set(
            "Erase: click or drag over geometry  |  F1 Vert  F2 Edge  F5 Face  |  Esc/right-click: exit"
        )
        DrawManager.add_handler(DRAW_HANDLER_3D, _draw_hover, (self,), "WINDOW", "POST_VIEW")
        DrawManager.add_handler(DRAW_HANDLER_2D, _draw_controls, (self,), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self)
        self._update_hover(context, event.mouse_region_x, event.mouse_region_y)
        return {"RUNNING_MODAL"}

    def _pick(self, context, x, y):
        if not (self.erase_verts or self.erase_edges or self.erase_faces):
            return None
        preferences = get_prefs()
        pick_radius = (
            preferences.erase_pick_radius
            if preferences is not None
            else DEFAULT_PICK_RADIUS_PX
        )
        result = snap_mesh(
            context,
            context.edit_object,
            x,
            y,
            max_px=pick_radius,
            snap_verts=self.erase_verts,
            snap_edges=self.erase_edges,
            snap_edge_center=False,
            snap_face_center=False,
            snap_faces=self.erase_faces,
        )
        target = _target_from_snap(result)
        if _target_within_pick_area(context, target, x, y, pick_radius):
            return target

        # A nearby component can win the GPU query even when it falls just
        # outside the strict erase radius. If faces are enabled, retry face-only
        # and accept it only when the cursor is genuinely inside that face.
        if self.erase_faces and target is not None and target.kind != "FACE":
            face_result = snap_mesh(
                context,
                context.edit_object,
                x,
                y,
                max_px=1.0,
                snap_verts=False,
                snap_edges=False,
                snap_edge_center=False,
                snap_face_center=False,
                snap_faces=True,
            )
            face_target = _target_from_snap(face_result)
            if _target_within_pick_area(context, face_target, x, y, pick_radius):
                return face_target
        return None

    def _control_at(self, x, y):
        for control, bounds in self.ui_hitboxes.items():
            x_min, x_max, y_min, y_max = bounds
            if x_min <= x <= x_max and y_min <= y <= y_max:
                return control
        return None

    def _toggle_control(self, context, control, x, y):
        if control == "erase_verts":
            self.erase_verts = not self.erase_verts
        elif control == "erase_edges":
            self.erase_edges = not self.erase_edges
        elif control == "erase_faces":
            self.erase_faces = not self.erase_faces
        else:
            return False
        invalidate_snap_cache()
        self._update_hover(context, x, y)
        return True

    def _update_hover(self, context, x, y):
        self.hover_target = self._pick(context, x, y)
        if self.dragging:
            self._queue_target(self.hover_target)
        context.area.tag_redraw()

    @staticmethod
    def _target_key(target):
        if target is None:
            return None
        obj_key = target.obj.as_pointer() if hasattr(target.obj, "as_pointer") else id(target.obj)
        return (obj_key, target.kind, tuple(target.indices))

    def _queue_target(self, target):
        key = self._target_key(target)
        if key is None or key in self.pending_target_keys:
            return False
        self.pending_target_keys.add(key)
        self.pending_targets.append(target)
        return True

    def _begin_stroke_undo(self):
        if self.stroke_undo_started:
            return
        try:
            bpy.ops.ed.undo_push(message="Erase stroke start")
            self.stroke_undo_started = True
            free_snap_context()
            invalidate_snap_cache()
        except RuntimeError:
            # Keep erasing usable in contexts where Blender's undo system is
            # unavailable; the operator's normal UNDO flag still applies when
            # the modal operation finishes.
            self.stroke_undo_started = False

    def _end_stroke_undo(self):
        if not self.stroke_undo_started:
            return
        try:
            bpy.ops.ed.undo_push(message="Erase stroke")
            free_snap_context()
            invalidate_snap_cache()
        except RuntimeError:
            pass
        self.stroke_undo_started = False

    def _collect_at(self, context, x, y):
        target = self._pick(context, x, y)
        return self._queue_target(target)

    def _commit_pending(self, context):
        changed = False
        for target in self.pending_targets:
            if _erase_target(target):
                changed = True
        self.changed = self.changed or changed
        self._end_stroke_undo()
        self.pending_targets.clear()
        self.pending_target_keys.clear()
        invalidate_snap_cache()
        context.area.tag_redraw()

    def _erase_stroke_segment(self, context, x, y):
        current = (float(x), float(y))
        previous = self.last_drag_mouse
        if previous is None:
            self._collect_at(context, *current)
            self.last_drag_mouse = current
            return

        dx = current[0] - previous[0]
        dy = current[1] - previous[1]
        distance = math.hypot(dx, dy)
        if distance < 0.5:
            self.last_drag_mouse = current
            return
        steps = max(1, int(math.ceil(distance / STROKE_SAMPLE_PX)))
        for step in range(1, steps + 1):
            factor = step / steps
            self._collect_at(
                context,
                previous[0] + dx * factor,
                previous[1] + dy * factor,
            )
        self.last_drag_mouse = current

    def modal(self, context, event):
        if context.scene.active_cad_tool_id != self.tool_instance_id:
            self.finish(context)
            return {"FINISHED"} if self.changed else {"CANCELLED"}

        if context.mode != "EDIT_MESH":
            self.finish(context)
            return {"FINISHED"} if self.changed else {"CANCELLED"}

        if hasattr(event, "mouse_region_x") and hasattr(event, "mouse_region_y"):
            self.cursor_xy = (event.mouse_region_x, event.mouse_region_y)
            self.cursor_in_view = _event_is_in_view(context, event)
            try:
                context.window.cursor_modal_set("NONE" if self.cursor_in_view else "DEFAULT")
            except (TypeError, ValueError):
                pass

        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            if is_event_over_ui(context, event):
                self.hover_target = None
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            if self._control_at(event.mouse_region_x, event.mouse_region_y):
                self.hover_target = None
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            if self.dragging:
                self._erase_stroke_segment(context, event.mouse_region_x, event.mouse_region_y)
            self._update_hover(context, event.mouse_region_x, event.mouse_region_y)
            return {"RUNNING_MODAL"}

        if event.type == "Z" and event.value == "PRESS" and event.ctrl:
            if self.dragging:
                self.dragging = False
                self.last_drag_mouse = None
                self.pending_targets.clear()
                self.pending_target_keys.clear()
                self.stroke_undo_started = False
            try:
                bpy.ops.ed.redo() if event.shift else bpy.ops.ed.undo()
                free_snap_context()
                invalidate_snap_cache()
                self.hover_target = None
                context.area.tag_redraw()
            except RuntimeError:
                pass
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if is_event_over_ui(context, event):
                return {"PASS_THROUGH"}
            control = self._control_at(event.mouse_region_x, event.mouse_region_y)
            if self._toggle_control(
                context,
                control,
                event.mouse_region_x,
                event.mouse_region_y,
            ):
                return {"RUNNING_MODAL"}
            self.dragging = True
            self._begin_stroke_undo()
            self.last_drag_mouse = None
            self._erase_stroke_segment(context, event.mouse_region_x, event.mouse_region_y)
            self._update_hover(context, event.mouse_region_x, event.mouse_region_y)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            if self.dragging:
                if not is_event_over_ui(context, event):
                    self._erase_stroke_segment(context, event.mouse_region_x, event.mouse_region_y)
                    self._update_hover(context, event.mouse_region_x, event.mouse_region_y)
                self._commit_pending(context)
            self.dragging = False
            self.last_drag_mouse = None
            self.hover_target = None
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self.finish(context)
            return {"FINISHED"} if self.changed else {"CANCELLED"}

        if event.value == "PRESS" and event.type in {"F1", "F2", "F5"}:
            control = {
                "F1": "erase_verts",
                "F2": "erase_edges",
                "F5": "erase_faces",
            }[event.type]
            self._toggle_control(
                context,
                control,
                event.mouse_region_x,
                event.mouse_region_y,
            )
            return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    def finish(self, context):
        if not self.running:
            return
        self.running = False
        self.dragging = False
        self.hover_target = None
        self.pending_targets.clear()
        self.pending_target_keys.clear()
        self.stroke_undo_started = False
        DrawManager.remove_handler(DRAW_HANDLER_3D)
        DrawManager.remove_handler(DRAW_HANDLER_2D)
        free_snap_context()
        if context.scene.active_cad_tool_id == self.tool_instance_id:
            context.scene.active_cad_tool_id = ""
        try:
            context.window.cursor_modal_restore()
        except RuntimeError:
            pass
        if context.area is not None:
            context.area.header_text_set(None)
            context.area.tag_redraw()

    def cancel(self, context):
        self.finish(context)


CLASSES = (VIEW3D_OT_radcad_erase,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    global _cursor_texture, _cursor_texture_source

    DrawManager.remove_handler(DRAW_HANDLER_3D)
    DrawManager.remove_handler(DRAW_HANDLER_2D)
    _cursor_texture = None
    _cursor_texture_source = None
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
