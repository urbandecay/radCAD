"""Viewport selection and repositioning for persistent construction guides."""

import bpy
from bpy_extras.view3d_utils import (
    region_2d_to_origin_3d,
    region_2d_to_vector_3d,
)
from mathutils import Vector
from mathutils.geometry import intersect_line_plane

from ..modal_core import is_event_over_ui
from .model import has_visible_construction_lines, iter_construction_lines
from .native_snap import sync_scene_snap_proxy
from .properties import tag_redraw_all_view3d
from .snapping import CONSTRUCTION_LINE_HIT_RADIUS, pick_construction_line
from .projection import guide_vectors
from .drawing import clear_construction_move_preview, set_construction_move_preview


_DRAG_THRESHOLD_PX = 3.0


def _project_cursor_to_plane(context, x, y, plane_point, plane_normal):
    origin = region_2d_to_origin_3d(context.region, context.region_data, (x, y))
    direction = region_2d_to_vector_3d(context.region, context.region_data, (x, y))
    return intersect_line_plane(
        origin,
        origin + direction * 100000.0,
        plane_point,
        plane_normal,
    )


class VIEW3D_OT_radcad_construction_pick(bpy.types.Operator):
    """Select and drag a persistent construction-line overlay."""

    bl_idname = "view3d.radcad_construction_pick"
    bl_label = "Select Construction Line"
    bl_description = "Select or drag a construction line"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == "VIEW_3D"
            and context.region is not None
            and context.region.type == "WINDOW"
            and context.region_data is not None
        )

    @staticmethod
    def _set_active(scene, index):
        if hasattr(scene, "radcad_active_construction_line"):
            scene.radcad_active_construction_line = index

    def invoke(self, context, event):
        if (
            is_event_over_ui(context, event)
            or getattr(context.scene, "active_cad_tool_id", "")
            or not has_visible_construction_lines(context.scene)
        ):
            return {"PASS_THROUGH"}

        width = getattr(context.scene, "radcad_construction_line_width", 1.0)
        hit_radius = max(CONSTRUCTION_LINE_HIT_RADIUS, float(width) * 0.5 + 5.0)
        picked = pick_construction_line(
            context,
            event.mouse_region_x,
            event.mouse_region_y,
            max_px=hit_radius,
        )
        if picked is None:
            self._set_active(context.scene, -1)
            tag_redraw_all_view3d()
            return {"PASS_THROUGH"}

        index = picked[0]
        lines = iter_construction_lines(context.scene)
        if index < 0 or index >= len(lines):
            return {"PASS_THROUGH"}
        line = lines[index]
        vectors = guide_vectors(line)
        if vectors is None:
            return {"PASS_THROUGH"}

        anchor, direction, normal = vectors
        self.line_index = index
        self.original_anchor = Vector(anchor)
        self.direction = Vector(direction)
        self.plane_normal = Vector(normal)
        self.press_mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        self.press_plane_point = _project_cursor_to_plane(
            context,
            event.mouse_region_x,
            event.mouse_region_y,
            self.original_anchor,
            self.plane_normal,
        )
        self.dragging = False
        self.changed = False
        set_construction_move_preview(self.original_anchor, self.original_anchor)
        self._set_active(context.scene, index)
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def _line(self, context):
        lines = iter_construction_lines(context.scene)
        if self.line_index < 0 or self.line_index >= len(lines):
            return None
        return lines[self.line_index]

    def _move_to_cursor(self, context, x, y):
        if self.press_plane_point is None:
            return False
        current_plane_point = _project_cursor_to_plane(
            context,
            x,
            y,
            self.original_anchor,
            self.plane_normal,
        )
        if current_plane_point is None:
            return False

        delta = current_plane_point - self.press_plane_point
        # A construction guide moves perpendicular to itself. Removing the
        # along-line component keeps the guide parallel while dragging.
        delta -= self.direction * delta.dot(self.direction)
        line = self._line(context)
        if line is None:
            return False
        new_anchor = self.original_anchor + delta
        if (Vector(line.anchor) - new_anchor).length_squared <= 1.0e-16:
            return False
        line.anchor = new_anchor
        sync_scene_snap_proxy(context.scene)
        set_construction_move_preview(self.original_anchor, new_anchor)
        self.changed = True
        context.area.tag_redraw()
        return True

    def _restore(self, context):
        line = self._line(context)
        if line is None:
            return
        line.anchor = self.original_anchor
        sync_scene_snap_proxy(context.scene)
        clear_construction_move_preview()
        context.area.tag_redraw()

    def _finish(self, context):
        clear_construction_move_preview()
        context.area.tag_redraw()

    def modal(self, context, event):
        if context.region is None or context.region.type != "WINDOW":
            self._finish(context)
            return {"CANCELLED"}

        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            if is_event_over_ui(context, event):
                return {"RUNNING_MODAL"}
            mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            if not self.dragging and (mouse - self.press_mouse).length >= _DRAG_THRESHOLD_PX:
                self.dragging = True
            if self.dragging:
                self._move_to_cursor(context, event.mouse_region_x, event.mouse_region_y)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            if self.dragging and not is_event_over_ui(context, event):
                self._move_to_cursor(context, event.mouse_region_x, event.mouse_region_y)
            self._finish(context)
            return {"FINISHED"}

        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            if self.changed:
                self._restore(context)
            self._finish(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}


CLASSES = (VIEW3D_OT_radcad_construction_pick,)
