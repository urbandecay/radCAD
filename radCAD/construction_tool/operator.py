"""Interactive construction-line creation and management operators."""

import math
import time

import bpy
from mathutils import Vector

from ..dimension_tool.snapping import pick_point
from ..inference_utils import get_axis_snapped_location
from ..modal_core import DrawManager, is_event_over_ui
from ..modal_state import state
from ..snapping_utils import free_snap_context, invalidate_snap_cache
from .drawing import draw_construction_preview
from .model import add_construction_line, constrain_direction_to_plane
from .properties import tag_redraw_all_view3d


_PREVIEW_HANDLER = "CONSTRUCTION_LINE_PREVIEW_2D"


class VIEW3D_OT_radcad_construction_line(bpy.types.Operator):
    bl_idname = "view3d.radcad_construction_line"
    bl_label = "Construction Line"
    bl_description = "Create an infinite, persistent construction guide from two points"
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
            self.report({"WARNING"}, "Run the Construction Line tool from a 3D View")
            return {"CANCELLED"}

        DrawManager.clear_all()
        invalidate_snap_cache()
        self.context = context
        self.stage = 0
        self.anchor = None
        self.current = None
        self.current_pick = None
        self.plane_normal = None
        self.running = True
        self.tool_instance_id = f"CONSTRUCTION_LINE_{time.time()}"
        context.scene.active_cad_tool_id = self.tool_instance_id
        context.window.cursor_modal_set("DEFAULT")
        DrawManager.add_handler(
            _PREVIEW_HANDLER,
            draw_construction_preview,
            (self,),
            "WINDOW",
            "POST_PIXEL",
        )
        context.window_manager.modal_handler_add(self)
        self._update(context, event)
        return {"RUNNING_MODAL"}

    def _update(self, context, event):
        state["current_axis_vector"] = None
        if self.stage == 0:
            pick = pick_point(context, event)
        else:
            pick = pick_point(context, event, self.anchor, self.plane_normal)
        self.current = pick.point
        self.current_pick = pick

        if self.stage == 1 and not state.get("geometry_snap", False):
            strength = max(0.1, min(89.0, state.get("snap_strength", 6.0)))
            inferred, axis, _axis_name = get_axis_snapped_location(
                self.anchor,
                (event.mouse_region_x, event.mouse_region_y),
                context,
                snap_threshold=math.cos(math.radians(strength)),
            )
            if inferred is not None:
                self.current = inferred
                state["current_axis_vector"] = axis
                self.current_pick.snap_result = None

        if self.stage == 1 and self.plane_normal is not None:
            constrained = constrain_direction_to_plane(
                self.current - self.anchor,
                self.plane_normal,
            )
            if constrained is not None:
                self.current = self.anchor + constrained
                self.current_pick.point = self.current.copy()
            else:
                self.current = self.anchor.copy()
                self.current_pick.point = self.current.copy()
        context.area.tag_redraw()

    def _click(self, context):
        if self.stage == 0:
            self.anchor = self.current.copy()
            self.plane_normal = (
                self.current_pick.normal.copy()
                if self.current_pick.normal is not None
                else Vector((0.0, 0.0, 1.0))
            )
            self.stage = 1
            return {"RUNNING_MODAL"}

        direction = self.current - self.anchor
        if direction.length_squared <= 1.0e-12:
            self.report({"WARNING"}, "Move away from the anchor to set a line direction")
            return {"RUNNING_MODAL"}
        line = add_construction_line(
            context.scene,
            self.anchor,
            direction,
            self.plane_normal,
        )
        if line is None:
            self.report({"WARNING"}, "Construction line direction must lie in the drawing plane")
            return {"RUNNING_MODAL"}
        self.finish(context)
        tag_redraw_all_view3d()
        return {"FINISHED"}

    def modal(self, context, event):
        if context.scene.active_cad_tool_id != self.tool_instance_id:
            self.finish(context)
            return {"CANCELLED"}

        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}
        if event.type == "MOUSEMOVE":
            if not is_event_over_ui(context, event):
                self._update(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if is_event_over_ui(context, event):
                return {"PASS_THROUGH"}
            return self._click(context)
        if event.type in {"BACK_SPACE", "BACKSPACE"} and event.value == "PRESS":
            if self.stage == 1:
                self.stage = 0
                self.anchor = None
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
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self.finish(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def finish(self, context):
        if not self.running:
            return
        self.running = False
        DrawManager.remove_handler(_PREVIEW_HANDLER)
        state["snap_point"] = None
        state["geometry_snap"] = False
        state["current_axis_vector"] = None
        free_snap_context()
        if context.scene.active_cad_tool_id == self.tool_instance_id:
            context.scene.active_cad_tool_id = ""
        try:
            context.window.cursor_modal_restore()
        except RuntimeError:
            pass
        context.area.tag_redraw()

    def cancel(self, context):
        self.finish(context)


class VIEW3D_OT_radcad_construction_delete_last(bpy.types.Operator):
    bl_idname = "view3d.radcad_construction_delete_last"
    bl_label = "Delete Last Construction Line"
    bl_description = "Remove the most recently created construction line"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        lines = getattr(context.scene, "radcad_construction_lines", ())
        return len(lines) > 0

    def execute(self, context):
        lines = context.scene.radcad_construction_lines
        lines.remove(len(lines) - 1)
        tag_redraw_all_view3d()
        return {"FINISHED"}


class VIEW3D_OT_radcad_construction_clear(bpy.types.Operator):
    bl_idname = "view3d.radcad_construction_clear"
    bl_label = "Clear Construction Lines"
    bl_description = "Remove every construction line in the current scene"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        lines = getattr(context.scene, "radcad_construction_lines", ())
        return len(lines) > 0

    def execute(self, context):
        context.scene.radcad_construction_lines.clear()
        tag_redraw_all_view3d()
        return {"FINISHED"}


CLASSES = (
    VIEW3D_OT_radcad_construction_line,
    VIEW3D_OT_radcad_construction_delete_last,
    VIEW3D_OT_radcad_construction_clear,
)
