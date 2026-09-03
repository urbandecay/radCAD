"""Blender operator for creating linear dimensions."""

import math
import time

import bpy
from mathutils import Vector

from ..dimension_tool.constants import DRAW_HANDLER_2D, DRAW_HANDLER_3D
from ..dimension_tool.drawing import draw_preview_2d, draw_preview_3d
from ..dimension_tool.interaction import _cursor_driven_offset, _linear_measure_length
from ..dimension_tool.linear_formatting import format_dimension_length
from ..dimension_tool.linear_geometry import dimension_basis
from ..dimension_tool.model import create_dimension
from ..dimension_tool.snapping import pick_point
from ..inference_utils import get_axis_snapped_location
from ..modal_core import DrawManager, is_event_over_ui
from ..modal_state import state
from ..snapping_utils import free_snap_context, invalidate_snap_cache


class VIEW3D_OT_radcad_dimension_linear(bpy.types.Operator):
    bl_idname = "view3d.radcad_dimension_linear"
    bl_label = "Linear Dimension"
    bl_description = "Create an aligned or projected linear dimension from two points"
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
        self.linear_direction = None
        self.offset_distance = 0.0
        self.preview_label = ""
        self._creation_committed = False
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
                allow_projected=True,
            )
            if resolved is not None:
                (
                    self.current,
                    self.plane_normal,
                    self.offset_distance,
                    axis,
                    self.linear_direction,
                ) = resolved
                state["current_axis_vector"] = axis
            self.preview_label = format_dimension_length(
                _linear_measure_length(
                    self.p1,
                    self.p2,
                    self.linear_direction,
                ),
                context.scene,
            )
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

        # A modal operator should commit one annotation at most.  This also
        # protects against a duplicate terminal mouse event during teardown.
        if self._creation_committed:
            return {"FINISHED"}
        self._creation_committed = True
        create_dimension(
            context,
            self.p1,
            self.p2,
            self.plane_normal,
            self.offset_distance,
            self.pick_1,
            self.pick_2,
            linear_direction=self.linear_direction,
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
