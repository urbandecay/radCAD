"""Interactive creation and editing operators for dimensions."""

import math
import time

import bpy
from mathutils import Vector

from ..inference_utils import get_axis_snapped_location, get_direction_snapped_location
from ..modal_core import DrawManager, is_event_over_ui
from ..modal_state import state
from ..orientation_utils import orthonormal_basis_from_normal
from ..snapping_utils import free_snap_context, invalidate_snap_cache
from .constants import DRAW_HANDLER_2D, DRAW_HANDLER_3D, DRAW_HANDLER_SNAP_HUD
from .drawing import (
    angle_dimension_hit_distance,
    dimension_hit_distance,
    draw_preview_2d,
    draw_preview_3d,
)
from .formatting import format_dimension_angle, format_dimension_length
from .geometry import build_angle_layout, dimension_basis
from .model import (
    create_angle_dimension,
    create_dimension,
    delete_dimension,
    dimension_layout,
    iter_dimensions,
    resolve_anchor,
    resolve_dimension_plane,
    selected_dimension,
    set_dimension_plane,
    update_dimension,
)
from .snapping import pick_point, project_to_plane


_GLOBAL_AXES = {
    "X": Vector((1.0, 0.0, 0.0)),
    "Y": Vector((0.0, 1.0, 0.0)),
    "Z": Vector((0.0, 0.0, 1.0)),
}
_AXIS_ALIGNED_DOT = math.cos(math.radians(1.0))


def _dimension_offset_axes(line_direction):
    """Return global axes projected into the dimension's cross-plane."""
    candidates = {}
    for axis_name, axis in _GLOBAL_AXES.items():
        projected = axis - line_direction * axis.dot(line_direction)
        if projected.length_squared > 1.0e-10:
            candidates[axis_name] = projected.normalized()
    return candidates


def _cursor_driven_offset(context, event, p1, p2, fallback_normal, fallback_distance):
    """Resolve a dimension offset from the cursor, with line-style axis inference."""
    basis = dimension_basis(p1, p2, fallback_normal)
    if basis is None:
        return None

    line_direction, fallback_direction, _normal = basis
    midpoint = (Vector(p1) + Vector(p2)) * 0.5

    # Read the mouse on a view-facing plane, then remove its component along
    # the measured span. Unlike the old fixed dimension plane, this lets the
    # witness direction rotate all the way around the measured line.
    view_normal = (
        context.region_data.view_matrix.inverted().to_3x3()
        @ Vector((0.0, 0.0, 1.0))
    ).normalized()
    placement = project_to_plane(
        context,
        event.mouse_region_x,
        event.mouse_region_y,
        midpoint,
        view_normal,
    )
    if placement is None:
        return None

    raw_offset = placement - midpoint
    raw_offset -= line_direction * raw_offset.dot(line_direction)
    if raw_offset.length_squared <= 1.0e-10:
        distance = float(fallback_distance)
        return midpoint + fallback_direction * distance, _normal, distance, None

    offset_direction = raw_offset.normalized()
    distance = raw_offset.length
    inferred_axis = None

    strength = max(0.1, min(89.0, state.get("snap_strength", 6.0)))
    axis_aligned = (
        max(abs(line_direction.dot(axis)) for axis in _GLOBAL_AXES.values())
        >= _AXIS_ALIGNED_DOT
    )
    # Like SketchUp, a dimension measured along a global axis always pulls out
    # along whichever remaining global axis is closest to the cursor.  There is
    # no useful crooked/free plane between those two choices.
    snap_threshold = 0.0 if axis_aligned else math.cos(math.radians(strength))

    offset_axes = _dimension_offset_axes(line_direction)
    inferred, offset_axis, axis_name = get_direction_snapped_location(
        midpoint,
        (event.mouse_region_x, event.mouse_region_y),
        context,
        offset_axes,
        snap_threshold=snap_threshold,
    )
    if inferred is not None and offset_axis is not None:
        inferred_distance = (inferred - midpoint).dot(offset_axis)
        if inferred_distance > 1.0e-8:
            offset_direction = offset_axis
            distance = inferred_distance
            inferred_axis = _GLOBAL_AXES[axis_name].copy()
            if inferred_axis.dot(offset_direction) < 0.0:
                inferred_axis.negate()

    plane_normal = line_direction.cross(offset_direction)
    if plane_normal.length_squared <= 1.0e-10:
        return None
    plane_normal.normalize()
    current = midpoint + offset_direction * distance
    return current, plane_normal, distance, inferred_axis


def _project_point_to_plane(point, plane_point, plane_normal):
    point = Vector(point)
    plane_point = Vector(plane_point)
    normal = Vector(plane_normal)
    if normal.length_squared <= 1.0e-12:
        return point
    normal.normalize()
    delta = point - plane_point
    return point - normal * delta.dot(normal)


def _cursor_driven_angle_radius(context, event, vertex, plane_normal, fallback_radius):
    """Resolve an angle annotation radius from the cursor on its dimension plane."""
    placement = project_to_plane(
        context,
        event.mouse_region_x,
        event.mouse_region_y,
        vertex,
        plane_normal,
    )
    if placement is None:
        return None
    placement = _project_point_to_plane(placement, vertex, plane_normal)
    radius = (placement - Vector(vertex)).length
    if radius <= 1.0e-8:
        return Vector(vertex), abs(float(fallback_radius))
    return placement, radius


def _angle_preview_layout(operator):
    ray_2 = getattr(operator, "ray_2", None)
    if ray_2 is None:
        ray_2 = getattr(operator, "current", None)
    if (
        getattr(operator, "vertex", None) is None
        or getattr(operator, "ray_1", None) is None
        or ray_2 is None
    ):
        return None
    return build_angle_layout(
        operator.vertex,
        operator.ray_1,
        ray_2,
        operator.plane_normal,
        operator.offset_distance,
        0.001,
        0.001,
        0.0,
        0.0,
    )


def _axis_for_key(key):
    return {
        "X": Vector((1.0, 0.0, 0.0)),
        "Y": Vector((0.0, 1.0, 0.0)),
        "Z": Vector((0.0, 0.0, 1.0)),
    }[key]


class VIEW3D_OT_radcad_dimension_angle(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_angle"
    bl_label = "Angle Dimension"
    bl_description = "Create an angle dimension from a vertex and two rays"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    running = False
    dimension_type = "ANGLE"

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == "VIEW_3D"
            and context.mode in {"OBJECT", "EDIT_MESH"}
        )

    def invoke(self, context, event):
        if context.region is None or context.region.type != "WINDOW":
            self.report({"WARNING"}, "Run the Angle Dimension tool from a 3D View")
            return {"CANCELLED"}

        DrawManager.clear_all()
        invalidate_snap_cache()
        # The angle tool uses the same shared snap state as Rotate/Arc so the
        # existing marker and F-key snap bar can be reused without duplicating
        # the snap overlay implementation.
        state["active"] = True
        state["tool_mode"] = "DIMENSION_ANGLE"
        state["snap_point"] = None
        state["geometry_snap"] = False
        state["ui_hitboxes"] = {}
        self.context = context
        self.stage = 0
        self.vertex = None
        self.ray_1 = None
        self.ray_2 = None
        self.current = None
        self.current_pick = None
        self.pick_vertex = None
        self.pick_ray_1 = None
        self.pick_ray_2 = None
        self.plane_normal = None
        self.compass_center = None
        self.compass_plane_normal = None
        self.compass_x = None
        self.compass_y = None
        self.compass_rotation = 0.0
        self.plane_locked = False
        self.locked_plane_point = None
        self.offset_distance = 0.0
        self.preview_label = ""
        self.running = True
        self.tool_instance_id = f"DIMENSION_ANGLE_{time.time()}"
        context.scene.active_cad_tool_id = self.tool_instance_id
        context.scene.radcad_dimension_icon = "dimension_linear"
        context.window.cursor_modal_set("DEFAULT")
        DrawManager.add_handler(DRAW_HANDLER_3D, draw_preview_3d, (self,), "WINDOW", "POST_VIEW")
        DrawManager.add_handler(DRAW_HANDLER_2D, draw_preview_2d, (self,), "WINDOW", "POST_PIXEL")
        from ..hud_overlay import draw_hud_2d

        DrawManager.add_handler(
            DRAW_HANDLER_SNAP_HUD,
            draw_hud_2d,
            (),
            "WINDOW",
            "POST_PIXEL",
        )
        context.window_manager.modal_handler_add(self)
        self._update(context, event)
        return {"RUNNING_MODAL"}

    def _set_compass_plane(self, normal):
        normal = Vector(normal)
        if normal.length_squared <= 1.0e-12:
            return False
        normal.normalize()
        self.compass_plane_normal = normal
        self.compass_x, self.compass_y, _normal = orthonormal_basis_from_normal(normal)
        return self.compass_x is not None and self.compass_y is not None

    def _update_compass_rotation(self, point):
        if self.vertex is None or self.compass_x is None or self.compass_y is None:
            return
        direction = Vector(point) - self.vertex
        direction -= self.compass_plane_normal * direction.dot(self.compass_plane_normal)
        if direction.length_squared <= 1.0e-12:
            return
        self.compass_rotation = math.atan2(direction.dot(self.compass_y), direction.dot(self.compass_x))

    def _handle_plane_input(self, context, event):
        if self.stage != 0 or event.value != "PRESS":
            return False

        if event.type == "L":
            if self.plane_locked:
                self.plane_locked = False
                self.locked_plane_point = None
                self.report({"INFO"}, "Angle plane unlocked")
            elif self.compass_plane_normal is not None:
                self.plane_locked = True
                self.locked_plane_point = (
                    self.compass_center.copy()
                    if self.compass_center is not None
                    else Vector((0.0, 0.0, 0.0))
                )
                self.report({"INFO"}, "Angle plane locked")
            self._update(context, event)
            return True

        if event.type not in {"X", "Y", "Z"}:
            return False

        axis = _axis_for_key(event.type)
        if self.plane_locked and self.compass_plane_normal is not None and abs(self.compass_plane_normal.dot(axis)) > 0.99:
            self.plane_locked = False
            self.locked_plane_point = None
            self.report({"INFO"}, f"Unlocked {event.type}-Plane")
        else:
            self._set_compass_plane(axis)
            self.plane_locked = True
            self.locked_plane_point = (
                self.compass_center.copy()
                if self.compass_center is not None
                else Vector((0.0, 0.0, 0.0))
            )
            self.report({"INFO"}, f"Locked to {event.type}-Plane")
        self._update(context, event)
        return True

    def _update(self, context, event):
        state["current_axis_vector"] = None
        if self.stage == 0:
            if self.plane_locked and self.locked_plane_point is not None:
                pick = pick_point(
                    context,
                    event,
                    self.locked_plane_point,
                    self.compass_plane_normal,
                )
            else:
                pick = pick_point(context, event)
            self.current = pick.point
            self.current_pick = pick
            if self.plane_locked:
                projected = _project_point_to_plane(
                    self.current,
                    self.locked_plane_point,
                    self.compass_plane_normal,
                )
                if (projected - self.current).length_squared > 1.0e-12:
                    pick.snap_result = None
                    state["snap_point"] = None
                    state["geometry_snap"] = False
                self.current = projected
            else:
                self._set_compass_plane(
                    pick.normal if pick.normal is not None else Vector((0.0, 0.0, 1.0))
                )
            self.compass_center = self.current.copy()
        elif self.stage == 1:
            pick = pick_point(context, event, self.vertex, self.plane_normal)
            projected = _project_point_to_plane(pick.point, self.vertex, self.plane_normal)
            if (projected - pick.point).length_squared > 1.0e-12:
                pick.snap_result = None
                state["snap_point"] = None
                state["geometry_snap"] = False
            self.current = projected
            self.current_pick = pick
            self.compass_center = self.vertex.copy()
            self._update_compass_rotation(self.current)
        else:
            pick = pick_point(context, event, self.vertex, self.plane_normal)
            projected = _project_point_to_plane(pick.point, self.vertex, self.plane_normal)
            if (projected - pick.point).length_squared > 1.0e-12:
                pick.snap_result = None
                state["snap_point"] = None
                state["geometry_snap"] = False
            self.current = projected
            self.current_pick = pick
            self.compass_center = self.vertex.copy()
            layout = _angle_preview_layout(self)
            self.preview_label = (
                format_dimension_angle(layout.measured_angle, context.scene)
                if layout is not None
                else ""
            )
        state["stage"] = self.stage
        state["current"] = self.current.copy() if self.current is not None else None
        state["pivot"] = self.vertex.copy() if self.vertex is not None else None
        context.area.tag_redraw()

    def _handle_snap_hud_click(self, context, event):
        """Toggle a snap button from the shared Rotate/Arc snap bar."""
        mouse_x = event.mouse_region_x
        mouse_y = event.mouse_region_y
        snap_keys = {
            "snap_verts",
            "snap_edges",
            "snap_edge_center",
            "snap_face_center",
            "snap_faces",
        }
        for hit_id, bounds in state.get("ui_hitboxes", {}).items():
            if hit_id not in snap_keys:
                continue
            xmin, xmax, ymin, ymax = bounds
            if xmin <= mouse_x <= xmax and ymin <= mouse_y <= ymax:
                state[hit_id] = not state.get(hit_id, False)
                invalidate_snap_cache()
                self._update(context, event)
                return True
        return False

    def _click(self, context, event):
        if self.stage == 0:
            self.vertex = self.current.copy()
            self.pick_vertex = self.current_pick.snap_result
            self.plane_normal = self.compass_plane_normal.copy() if self.compass_plane_normal is not None else self.current_pick.normal.copy()
            if self.plane_normal.length_squared <= 1.0e-12:
                self.plane_normal = Vector((0.0, 0.0, 1.0))
            self.plane_normal.normalize()
            self._set_compass_plane(self.plane_normal)
            self.compass_center = self.vertex.copy()
            self.stage = 1
            return {"RUNNING_MODAL"}

        if self.stage == 1:
            if (self.current - self.vertex).length <= 1.0e-8:
                self.report({"WARNING"}, "The first ray point must be different from the vertex")
                return {"RUNNING_MODAL"}
            self.ray_1 = self.current.copy()
            self.pick_ray_1 = self.current_pick.snap_result
            self.offset_distance = (self.ray_1 - self.vertex).length
            self._update_compass_rotation(self.ray_1)
            self.stage = 2
            self._update(context, event)
            return {"RUNNING_MODAL"}

        if self.current is None or (self.current - self.vertex).length <= 1.0e-8:
            self.report({"WARNING"}, "The second ray point must be different from the vertex")
            return {"RUNNING_MODAL"}
        self.ray_2 = self.current.copy()
        self.pick_ray_2 = self.current_pick.snap_result
        layout = _angle_preview_layout(self)
        if layout is None:
            self.report({"WARNING"}, "The three points must define a non-zero angle")
            return {"RUNNING_MODAL"}

        create_angle_dimension(
            context,
            self.vertex,
            self.ray_1,
            self.ray_2,
            layout.plane_normal,
            self.offset_distance,
            self.pick_vertex,
            self.pick_ray_1,
            self.pick_ray_2,
        )
        self.finish(context)
        return {"FINISHED"}

    def modal(self, context, event):
        if context.scene.active_cad_tool_id != self.tool_instance_id:
            self.finish(context)
            return {"CANCELLED"}

        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}
        if event.type == "MOUSEMOVE":
            if is_event_over_ui(context, event):
                return {"RUNNING_MODAL"}
            self._update(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if is_event_over_ui(context, event):
                return {"PASS_THROUGH"}
            if self._handle_snap_hud_click(context, event):
                return {"RUNNING_MODAL"}
            return self._click(context, event)
        if event.type in {"BACK_SPACE", "BACKSPACE"} and event.value == "PRESS":
            if self.stage == 2:
                self.stage = 1
                self.ray_2 = None
            elif self.stage == 1:
                self.stage = 0
                self.vertex = None
                self.ray_1 = None
                self.offset_distance = 0.0
            self._update(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "ESC" and event.value == "PRESS":
            self.finish(context)
            return {"CANCELLED"}
        if event.value == "PRESS" and event.type in {"L", "X", "Y", "Z"}:
            if self._handle_plane_input(context, event):
                return {"RUNNING_MODAL"}
        if event.value == "PRESS" and event.type in {"F1", "F2", "F3", "F4", "F5"}:
            key = {
                "F1": "snap_verts",
                "F2": "snap_edges",
                "F3": "snap_edge_center",
                "F4": "snap_face_center",
                "F5": "snap_faces",
            }[event.type]
            state[key] = not state.get(key, False)
            invalidate_snap_cache()
            self._update(context, event)
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def finish(self, context):
        if not self.running:
            return
        self.running = False
        state["current_axis_vector"] = None
        state["active"] = False
        state["snap_point"] = None
        state["geometry_snap"] = False
        state["ui_hitboxes"] = {}
        DrawManager.remove_handler(DRAW_HANDLER_3D)
        DrawManager.remove_handler(DRAW_HANDLER_2D)
        DrawManager.remove_handler(DRAW_HANDLER_SNAP_HUD)
        free_snap_context()
        if context.scene.active_cad_tool_id == self.tool_instance_id:
            context.scene.active_cad_tool_id = ""
        try:
            context.window.cursor_modal_restore()
        except RuntimeError:
            pass
        context.area.tag_redraw()


class VIEW3D_OT_radcad_dimension_linear(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_linear"
    bl_label = "Linear Dimension"
    bl_description = "Create an aligned dimension from two points and an offset"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    running = False

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == "VIEW_3D"
            and context.mode in {"OBJECT", "EDIT_MESH"}
        )

    def invoke(self, context, event):
        if context.region is None or context.region.type != "WINDOW":
            self.report({"WARNING"}, "Run the Dimension tool from a 3D View")
            return {"CANCELLED"}

        DrawManager.clear_all()
        invalidate_snap_cache()
        self.context = context
        self.stage = 0
        self.p1 = None
        self.p2 = None
        self.current = None
        self.pick_1 = None
        self.pick_2 = None
        self.plane_normal = None
        self.offset_distance = 0.0
        self.preview_label = ""
        self.running = True
        self.tool_instance_id = f"DIMENSION_LINEAR_{time.time()}"
        context.scene.active_cad_tool_id = self.tool_instance_id
        context.scene.radcad_dimension_icon = "dimension_linear"
        context.window.cursor_modal_set("DEFAULT")
        DrawManager.add_handler(DRAW_HANDLER_3D, draw_preview_3d, (self,), "WINDOW", "POST_VIEW")
        DrawManager.add_handler(DRAW_HANDLER_2D, draw_preview_2d, (self,), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self)
        self._update(context, event)
        return {"RUNNING_MODAL"}

    def _update(self, context, event):
        state["current_axis_vector"] = None
        if self.stage == 0:
            pick = pick_point(context, event)
            self.current = pick.point
            self.current_pick = pick
        elif self.stage == 1:
            pick = pick_point(context, event, self.p1, self.plane_normal)
            self.current = pick.point
            self.current_pick = pick
            # Match the line tool's mouse-driven global X/Y/Z inference. Exact
            # geometry snaps retain priority; free and surface picks may infer
            # an axis even when it leaves the first point's drawing plane.
            if not state.get("geometry_snap", False):
                strength = max(0.1, min(89.0, state.get("snap_strength", 6.0)))
                inferred, axis, _axis_name = get_axis_snapped_location(
                    self.p1,
                    (event.mouse_region_x, event.mouse_region_y),
                    context,
                    snap_threshold=math.cos(math.radians(strength)),
                )
                if inferred is not None:
                    self.current = inferred
                    state["current_axis_vector"] = axis
                    # The inferred point is no longer the surface point returned
                    # by pick_point, so it must not retain that associative anchor.
                    self.current_pick.snap_result = None
            self.preview_label = format_dimension_length((self.current - self.p1).length, context.scene)
        else:
            resolved = _cursor_driven_offset(
                context,
                event,
                self.p1,
                self.p2,
                self.plane_normal,
                self.offset_distance,
            )
            if resolved is not None:
                self.current, self.plane_normal, self.offset_distance, axis = resolved
                state["current_axis_vector"] = axis
            self.preview_label = format_dimension_length((self.p2 - self.p1).length, context.scene)
        context.area.tag_redraw()

    def _click(self, context, event):
        if self.stage == 0:
            self.p1 = self.current.copy()
            self.pick_1 = self.current_pick.snap_result
            self.plane_normal = self.current_pick.normal.copy()
            self.stage = 1
            return {"RUNNING_MODAL"}
        if self.stage == 1:
            if (self.current - self.p1).length <= 1.0e-8:
                self.report({"WARNING"}, "Dimension points must be different")
                return {"RUNNING_MODAL"}
            self.p2 = self.current.copy()
            self.pick_2 = self.current_pick.snap_result
            basis = dimension_basis(self.p1, self.p2, self.plane_normal)
            self.plane_normal = basis[2]
            default_offset = max((self.p2 - self.p1).length * 0.25, context.scene.radcad_dimension_text_size * 2.0)
            self.offset_distance = default_offset
            self.current = (self.p1 + self.p2) * 0.5 + basis[1] * default_offset
            self.stage = 2
            self._update(context, event)
            return {"RUNNING_MODAL"}

        create_dimension(
            context,
            self.p1,
            self.p2,
            self.plane_normal,
            self.offset_distance,
            self.pick_1,
            self.pick_2,
        )
        self.finish(context)
        return {"FINISHED"}

    def modal(self, context, event):
        if context.scene.active_cad_tool_id != self.tool_instance_id:
            self.finish(context)
            return {"CANCELLED"}

        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}
        if event.type == "MOUSEMOVE":
            if is_event_over_ui(context, event):
                return {"RUNNING_MODAL"}
            self._update(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if is_event_over_ui(context, event):
                return {"PASS_THROUGH"}
            return self._click(context, event)
        if event.type in {"BACK_SPACE", "BACKSPACE"} and event.value == "PRESS":
            if self.stage == 2:
                self.stage = 1
            elif self.stage == 1:
                self.stage = 0
                self.p1 = None
            self._update(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "ESC" and event.value == "PRESS":
            self.finish(context)
            return {"CANCELLED"}
        if event.value == "PRESS" and event.type in {"F1", "F2", "F3", "F4", "F5"}:
            key = {"F1": "snap_verts", "F2": "snap_edges", "F3": "snap_edge_center", "F4": "snap_face_center", "F5": "snap_faces"}[event.type]
            state[key] = not state.get(key, False)
            invalidate_snap_cache()
            self._update(context, event)
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def finish(self, context):
        if not self.running:
            return
        self.running = False
        state["current_axis_vector"] = None
        DrawManager.remove_handler(DRAW_HANDLER_3D)
        DrawManager.remove_handler(DRAW_HANDLER_2D)
        free_snap_context()
        if context.scene.active_cad_tool_id == self.tool_instance_id:
            context.scene.active_cad_tool_id = ""
        try:
            context.window.cursor_modal_restore()
        except RuntimeError:
            pass
        context.area.tag_redraw()


class VIEW3D_OT_radcad_dimension_reposition(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_reposition"
    bl_label = "Reposition Dimension"
    bl_description = "Move the selected dimension line while retaining its measured endpoints"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    running = False

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D" and selected_dimension(context) is not None

    def invoke(self, context, event):
        self.root = selected_dimension(context)
        data = self.root.radcad_dimension
        self.original_offset = data.offset_distance
        self.original_plane_normal = resolve_dimension_plane(data)
        self.plane_normal = self.original_plane_normal.copy()
        self.context = context
        self.stage = 2
        self.dimension_type = getattr(data, "dimension_type", "LINEAR")
        self.offset_distance = data.offset_distance
        if self.dimension_type == "ANGLE":
            self.vertex = resolve_anchor(data.anchor_1)
            self.ray_1 = resolve_anchor(data.anchor_2)
            self.ray_2 = resolve_anchor(data.anchor_3)
            self.p1 = self.vertex
            self.p2 = self.ray_1
            angle_layout = _angle_preview_layout(self)
            self.preview_label = (
                format_dimension_angle(angle_layout.measured_angle, context.scene)
                if angle_layout is not None
                else ""
            )
            self.current = self.vertex.copy()
        else:
            self.p1 = resolve_anchor(data.anchor_1)
            self.p2 = resolve_anchor(data.anchor_2)
            basis = dimension_basis(self.p1, self.p2, self.plane_normal)
            self.current = (self.p1 + self.p2) * 0.5 + basis[1] * data.offset_distance
            self.preview_label = format_dimension_length((self.p2 - self.p1).length, context.scene)
        self.running = True
        DrawManager.clear_all()
        self.tool_instance_id = f"DIMENSION_REPOSITION_{time.time()}"
        context.scene.active_cad_tool_id = self.tool_instance_id
        context.window.cursor_modal_set("DEFAULT")
        DrawManager.add_handler(DRAW_HANDLER_3D, draw_preview_3d, (self,), "WINDOW", "POST_VIEW")
        DrawManager.add_handler(DRAW_HANDLER_2D, draw_preview_2d, (self,), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self)
        self._update(context, event)
        return {"RUNNING_MODAL"}

    def _update(self, context, event):
        state["current_axis_vector"] = None
        if self.dimension_type == "ANGLE":
            resolved = _cursor_driven_angle_radius(
                context,
                event,
                self.vertex,
                self.plane_normal,
                self.offset_distance,
            )
            if resolved is not None:
                self.current, self.offset_distance = resolved
            context.area.tag_redraw()
            return
        resolved = _cursor_driven_offset(
            context,
            event,
            self.p1,
            self.p2,
            self.plane_normal,
            self.offset_distance,
        )
        if resolved is not None:
            self.current, self.plane_normal, self.offset_distance, axis = resolved
            state["current_axis_vector"] = axis
        context.area.tag_redraw()

    def modal(self, context, event):
        if context.scene.active_cad_tool_id != self.tool_instance_id:
            self.finish(context)
            return {"CANCELLED"}
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}
        if event.type == "MOUSEMOVE":
            self._update(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            self.root.radcad_dimension.offset_distance = self.offset_distance
            set_dimension_plane(self.root.radcad_dimension, self.plane_normal)
            update_dimension(self.root)
            self.finish(context)
            return {"FINISHED"}
        if event.type == "ESC" and event.value == "PRESS":
            self.root.radcad_dimension.offset_distance = self.original_offset
            update_dimension(self.root)
            self.finish(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def finish(self, context):
        if not self.running:
            return
        self.running = False
        state["current_axis_vector"] = None
        DrawManager.remove_handler(DRAW_HANDLER_3D)
        DrawManager.remove_handler(DRAW_HANDLER_2D)
        if context.scene.active_cad_tool_id == self.tool_instance_id:
            context.scene.active_cad_tool_id = ""
        try:
            context.window.cursor_modal_restore()
        except RuntimeError:
            pass
        context.area.tag_redraw()


class VIEW3D_OT_radcad_dimension_refresh(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_refresh"
    bl_label = "Refresh Dimension"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return selected_dimension(context) is not None

    def execute(self, context):
        update_dimension(selected_dimension(context))
        return {"FINISHED"}


class VIEW3D_OT_radcad_dimension_parameters(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_parameters"
    bl_label = "Dimension Parameters"
    bl_description = "Open the dimension display and editing parameters dialog"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(
            self,
            width=420,
            title="Dimension Parameters",
            confirm_text="Close",
        )

    def execute(self, _context):
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        scene = context.scene

        general_box = layout.box()
        general_box.label(text="Display", icon="HIDE_OFF")
        general_box.prop(scene, "radcad_dimensions_visible", text="Show Dimensions")

        defaults_box = layout.box()
        defaults_box.label(text="New Dimension Defaults", icon="DRIVER_DISTANCE")
        defaults_box.prop(scene, "radcad_dimension_text_size")
        defaults_box.prop(scene, "radcad_dimension_text_thickness")
        defaults_box.prop(scene, "radcad_dimension_arrow_size")
        defaults_box.prop(scene, "radcad_dimension_extension_gap")
        defaults_box.prop(scene, "radcad_dimension_extension_overshoot")
        defaults_box.prop(scene, "radcad_dimension_line_width")
        defaults_box.prop(scene, "radcad_dimension_color")

        selected_box = layout.box()
        selected_box.label(text="Selected Dimension", icon="RESTRICT_SELECT_OFF")
        root = selected_dimension(context)
        if root is None:
            selected_box.label(text="No dimension selected", icon="INFO")
            return

        data = root.radcad_dimension
        selected_box.prop(root, "name", text="Name")
        selected_box.prop(data, "text_override")
        selected_box.prop(data, "offset_distance")
        selected_box.prop(data, "text_size")
        selected_box.prop(data, "text_thickness")
        selected_box.prop(data, "arrow_size")
        selected_box.prop(data, "extension_gap")
        selected_box.prop(data, "extension_overshoot")
        selected_box.prop(data, "line_width")
        selected_box.prop(data, "color")

        row = selected_box.row(align=True)
        row.operator("view3d.radcad_dimension_reposition", text="Reposition")
        row.operator(
            "view3d.radcad_dimension_refresh",
            text="Refresh",
            icon="FILE_REFRESH",
        )
        selected_box.operator(
            "view3d.radcad_dimension_delete",
            text="Delete Dimension",
            icon="TRASH",
        )


class VIEW3D_OT_radcad_dimension_pick(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_pick"
    bl_label = "Select Dimension"
    bl_description = "Select a radCAD dimension by clicking its viewport annotation"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        if (
            context.region is None
            or context.region.type != "WINDOW"
            or not getattr(context.scene, "radcad_dimensions_visible", True)
            or getattr(context.scene, "active_cad_tool_id", "")
        ):
            return {"PASS_THROUGH"}

        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        picked = None
        best_distance = float("inf")
        for root in reversed(iter_dimensions(context.scene)):
            layout, label = dimension_layout(root)
            if layout is None:
                continue
            data = root.radcad_dimension
            if getattr(data, "dimension_type", "LINEAR") == "ANGLE":
                distance = angle_dimension_hit_distance(
                    context,
                    mouse,
                    layout.vertex,
                    layout.ray_1,
                    layout.ray_2,
                    layout.plane_normal,
                    data.offset_distance,
                    label,
                    data.text_size if data.text_size >= 4.0 else 14.0,
                    max(1.0, float(data.text_thickness)),
                    data.arrow_size if data.arrow_size >= 2.0 else 10.0,
                    data.line_width if data.line_width >= 0.5 else 1.0,
                    data.extension_gap,
                    data.extension_overshoot,
                )
            else:
                distance = dimension_hit_distance(
                    context,
                    mouse,
                    layout.p1,
                    layout.p2,
                    layout.plane_normal,
                    data.offset_distance,
                    label,
                    data.text_size if data.text_size >= 4.0 else 14.0,
                    max(1.0, float(data.text_thickness)),
                    data.arrow_size if data.arrow_size >= 2.0 else 10.0,
                    data.line_width if data.line_width >= 0.5 else 1.0,
                    data.extension_gap,
                    data.extension_overshoot,
                )
            if distance is not None and distance < best_distance:
                picked = root
                best_distance = distance

        if picked is None:
            if context.scene.radcad_active_dimension is not None:
                context.scene.radcad_active_dimension = None
                context.area.tag_redraw()
            return {"PASS_THROUGH"}

        context.scene.radcad_active_dimension = picked
        context.area.tag_redraw()
        return {"FINISHED"}


class VIEW3D_OT_radcad_dimension_delete(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_delete"
    bl_label = "Delete Dimension"
    bl_description = "Delete the selected dimension and its annotation objects"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return selected_dimension(context) is not None

    def execute(self, context):
        delete_dimension(selected_dimension(context))
        return {"FINISHED"}


CLASSES = (
    VIEW3D_OT_radcad_dimension_angle,
    VIEW3D_OT_radcad_dimension_linear,
    VIEW3D_OT_radcad_dimension_reposition,
    VIEW3D_OT_radcad_dimension_refresh,
    VIEW3D_OT_radcad_dimension_parameters,
    VIEW3D_OT_radcad_dimension_pick,
    VIEW3D_OT_radcad_dimension_delete,
)
