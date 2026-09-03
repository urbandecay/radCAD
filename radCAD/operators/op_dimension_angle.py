"""Blender operator for creating angular dimensions."""

import math
import time

import bpy
from mathutils import Vector

from ..dimension_tool.angular_formatting import format_dimension_angle
from ..dimension_tool.constants import DRAW_HANDLER_2D, DRAW_HANDLER_3D, DRAW_HANDLER_SNAP_HUD
from ..dimension_tool.drawing import draw_preview_2d, draw_preview_3d
from ..dimension_tool.interaction import (
    _angle_preview_layout,
    _axis_for_key,
    _compass_axis_snap,
    _project_point_to_plane,
)
from ..dimension_tool.model import create_angle_dimension
from ..dimension_tool.snapping import pick_point
from ..modal_core import DrawManager, is_event_over_ui
from ..modal_state import state
from ..orientation_utils import orthonormal_basis_from_normal
from ..snapping_utils import free_snap_context, invalidate_snap_cache


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
        self.axis_snap_name = None
        self.axis_snap_vector = None
        self.plane_locked = False
        self.locked_plane_point = None
        self.offset_distance = 0.0
        self.preview_label = ""
        self._creation_committed = False
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
        self.axis_snap_name = None
        self.axis_snap_vector = None
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
            if state.get("angle_axis_snap", False) and not state.get("geometry_snap", False):
                snapped, axis_vector, axis_name = _compass_axis_snap(
                    self.vertex,
                    self.current,
                    self.plane_normal,
                    state.get("snap_strength", 6.0),
                )
                if snapped is not None:
                    self.current = snapped
                    self.axis_snap_name = axis_name
                    self.axis_snap_vector = axis_vector
                    state["current_axis_vector"] = axis_vector.copy()
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
            if state.get("angle_axis_snap", False) and not state.get("geometry_snap", False):
                snapped, axis_vector, axis_name = _compass_axis_snap(
                    self.vertex,
                    self.current,
                    self.plane_normal,
                    state.get("snap_strength", 6.0),
                )
                if snapped is not None:
                    self.current = snapped
                    self.axis_snap_name = axis_name
                    self.axis_snap_vector = axis_vector
                    state["current_axis_vector"] = axis_vector.copy()
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
            if hit_id not in snap_keys and hit_id != "angle_axis_snap":
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

        # A modal operator should commit one annotation at most.  This also
        # protects against a duplicate terminal mouse event during teardown.
        if self._creation_committed:
            return {"FINISHED"}
        self._creation_committed = True
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
        if event.value == "PRESS" and event.type == "A":
            state["angle_axis_snap"] = not state.get("angle_axis_snap", False)
            invalidate_snap_cache()
            self._update(context, event)
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
