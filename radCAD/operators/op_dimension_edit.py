"""Blender operators for selecting and editing dimensions."""

import time

import bpy
from mathutils import Vector

from ..dimension_tool.angular_formatting import format_dimension_angle
from ..dimension_tool.constants import DRAW_HANDLER_2D, DRAW_HANDLER_3D
from ..dimension_tool.drawing import (
    angle_dimension_hit_distance,
    dimension_hit_distance,
    draw_preview_2d,
    draw_preview_3d,
)
from ..dimension_tool.interaction import (
    _angle_preview_layout,
    _cursor_driven_angle_radius,
    _cursor_driven_offset,
    _linear_measure_length,
)
from ..dimension_tool.linear_formatting import format_dimension_length
from ..dimension_tool.linear_geometry import dimension_basis
from ..dimension_tool.model import (
    delete_dimension,
    dimension_layout,
    iter_dimensions,
    resolve_anchor,
    resolve_dimension_plane,
    selected_dimension,
    set_dimension_plane,
    update_dimension,
)
from ..modal_core import DrawManager, is_event_over_ui
from ..modal_state import state


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
        self.original_linear_direction = Vector(data.linear_direction)
        if self.original_linear_direction.length_squared <= 1.0e-18:
            self.original_linear_direction = None
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
            self.linear_direction = Vector(data.linear_direction)
            if self.linear_direction.length_squared <= 1.0e-18:
                self.linear_direction = None
            basis = dimension_basis(
                self.p1,
                self.p2,
                self.plane_normal,
                self.linear_direction,
            )
            self.current = (self.p1 + self.p2) * 0.5 + basis[1] * data.offset_distance
            self.preview_label = format_dimension_length(
                _linear_measure_length(
                    self.p1,
                    self.p2,
                    self.linear_direction,
                ),
                context.scene,
            )
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
            dimension_direction=self.linear_direction,
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
            data = self.root.radcad_dimension
            data.offset_distance = self.offset_distance
            data.linear_direction = (
                self.linear_direction.normalized()
                if self.dimension_type == "LINEAR"
                and self.linear_direction is not None
                and self.linear_direction.length_squared > 1.0e-18
                else (0.0, 0.0, 0.0)
            )
            set_dimension_plane(data, self.plane_normal)
            update_dimension(self.root)
            self.finish(context)
            return {"FINISHED"}
        if event.type == "ESC" and event.value == "PRESS":
            data = self.root.radcad_dimension
            data.offset_distance = self.original_offset
            data.linear_direction = (
                self.original_linear_direction.normalized()
                if self.original_linear_direction is not None
                else (0.0, 0.0, 0.0)
            )
            set_dimension_plane(data, self.original_plane_normal)
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
    bl_options = {"INTERNAL", "UNDO", "BLOCKING"}

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
                    data.linear_direction,
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

        # Both dimension types can be resized directly from the viewport.
        # Keep the picker modal after the press so a simple click still
        # selects, while a drag changes the offset until release.
        data = picked.radcad_dimension
        self._drag_root = picked
        self._drag_dimension_type = getattr(data, "dimension_type", "LINEAR")
        self._drag_original_offset = float(data.offset_distance)
        self._drag_original_plane_normal = resolve_dimension_plane(data)
        self._drag_original_linear_direction = Vector(data.linear_direction)
        if self._drag_original_linear_direction.length_squared <= 1.0e-18:
            self._drag_original_linear_direction = None
        self._drag_plane_normal = self._drag_original_plane_normal.copy()
        self._drag_start_mouse = Vector(
            (event.mouse_region_x, event.mouse_region_y)
        )
        self._dragging = False
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _update_dimension_drag(self, context, event):
        data = self._drag_root.radcad_dimension
        if self._drag_dimension_type == "ANGLE":
            vertex = resolve_anchor(data.anchor_1)
            if vertex is None:
                return
            resolved = _cursor_driven_angle_radius(
                context,
                event,
                vertex,
                self._drag_plane_normal,
                self._drag_original_offset,
            )
        else:
            p1 = resolve_anchor(data.anchor_1)
            p2 = resolve_anchor(data.anchor_2)
            if p1 is None or p2 is None:
                return
            resolved = _cursor_driven_offset(
                context,
                event,
                p1,
                p2,
                self._drag_plane_normal,
                self._drag_original_offset,
                dimension_direction=(
                    Vector(data.linear_direction)
                    if Vector(data.linear_direction).length_squared > 1.0e-18
                    else None
                ),
            )
        if resolved is None:
            return
        if self._drag_dimension_type == "ANGLE":
            _point, offset_distance = resolved
        else:
            _point, plane_normal, offset_distance, _axis, _direction = resolved
            self._drag_plane_normal = plane_normal
            set_dimension_plane(data, plane_normal)
        data.offset_distance = offset_distance
        update_dimension(self._drag_root)
        context.area.tag_redraw()

    def modal(self, context, event):
        if context.scene.active_cad_tool_id:
            return {"FINISHED"}

        if event.type == "MOUSEMOVE":
            mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            if (
                not self._dragging
                and (mouse - self._drag_start_mouse).length >= 3.0
            ):
                self._dragging = True
            if self._dragging:
                self._update_dimension_drag(context, event)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            if self._dragging:
                self._update_dimension_drag(context, event)
            return {"FINISHED"}

        if event.type == "ESC" and event.value == "PRESS":
            data = self._drag_root.radcad_dimension
            set_dimension_plane(data, self._drag_original_plane_normal)
            data.linear_direction = (
                self._drag_original_linear_direction.normalized()
                if self._drag_original_linear_direction is not None
                else (0.0, 0.0, 0.0)
            )
            data.offset_distance = self._drag_original_offset
            update_dimension(self._drag_root)
            context.area.tag_redraw()
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}


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
    VIEW3D_OT_radcad_dimension_reposition,
    VIEW3D_OT_radcad_dimension_refresh,
    VIEW3D_OT_radcad_dimension_parameters,
    VIEW3D_OT_radcad_dimension_pick,
    VIEW3D_OT_radcad_dimension_delete,
)
