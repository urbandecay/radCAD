"""Interactive creation and editing operators for linear dimensions."""

import time

import bpy
from mathutils import Vector

from ..modal_core import DrawManager, is_event_over_ui
from ..modal_state import state
from ..snapping_utils import free_snap_context, invalidate_snap_cache
from .constants import DRAW_HANDLER_2D, DRAW_HANDLER_3D
from .drawing import dimension_hit_distance, draw_preview_2d, draw_preview_3d
from .formatting import format_dimension_length
from .geometry import dimension_basis, signed_offset_from_point
from .model import (
    create_dimension,
    delete_dimension,
    dimension_layout,
    iter_dimensions,
    resolve_anchor,
    selected_dimension,
    update_dimension,
)
from .snapping import pick_point, project_to_plane


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
        if self.stage == 0:
            pick = pick_point(context, event)
            self.current = pick.point
            self.current_pick = pick
        elif self.stage == 1:
            pick = pick_point(context, event, self.p1, self.plane_normal)
            self.current = pick.point
            self.current_pick = pick
            self.preview_label = format_dimension_length((self.current - self.p1).length, context.scene)
        else:
            midpoint = (self.p1 + self.p2) * 0.5
            placement = project_to_plane(
                context,
                event.mouse_region_x,
                event.mouse_region_y,
                midpoint,
                self.plane_normal,
            )
            if placement is not None:
                self.current = placement
                self.offset_distance = signed_offset_from_point(self.p1, self.p2, self.plane_normal, placement)
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
        self.p1 = resolve_anchor(data.anchor_1)
        self.p2 = resolve_anchor(data.anchor_2)
        self.plane_normal = Vector(data.plane_normal)
        self.context = context
        self.stage = 2
        self.current = (self.p1 + self.p2) * 0.5
        self.offset_distance = data.offset_distance
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
        midpoint = (self.p1 + self.p2) * 0.5
        placement = project_to_plane(context, event.mouse_region_x, event.mouse_region_y, midpoint, self.plane_normal)
        if placement is not None:
            self.current = placement
            self.offset_distance = signed_offset_from_point(self.p1, self.p2, self.plane_normal, placement)
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
            distance = dimension_hit_distance(
                context,
                mouse,
                layout.p1,
                layout.p2,
                layout.plane_normal,
                data.offset_distance,
                label,
                data.text_size if data.text_size >= 4.0 else 14.0,
                data.arrow_size if data.arrow_size >= 2.0 else 10.0,
                data.line_width if data.line_width >= 0.5 else 1.5,
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
    VIEW3D_OT_radcad_dimension_linear,
    VIEW3D_OT_radcad_dimension_reposition,
    VIEW3D_OT_radcad_dimension_refresh,
    VIEW3D_OT_radcad_dimension_pick,
    VIEW3D_OT_radcad_dimension_delete,
)
