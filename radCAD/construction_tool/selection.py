"""Viewport selection and repositioning for persistent construction guides."""

import math

import bpy
from bpy_extras.view3d_utils import (
    region_2d_to_origin_3d,
    region_2d_to_vector_3d,
)
from mathutils import Vector
from mathutils.geometry import intersect_line_plane

from ..modal_core import is_event_over_ui, is_number_input
from ..modal_state import state
from ..snapping_utils import snap_scene_geometry
from ..units_utils import parse_length_input
from .model import has_visible_construction_lines, iter_construction_lines
from .native_snap import sync_scene_snap_proxy
from .properties import tag_redraw_all_view3d
from .snapping import CONSTRUCTION_LINE_HIT_RADIUS, pick_construction_line
from .projection import guide_vectors
from .drawing import (
    clear_construction_move_distance_input,
    clear_construction_move_preview,
    set_construction_move_distance_input,
    set_construction_move_preview,
)


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
        self.awaiting_confirmation = False
        self.travel_direction = None
        self.distance_input_active = False
        self.distance_input = ""
        self.distance_input_cursor = 0
        clear_construction_move_distance_input()
        set_construction_move_preview(self.original_anchor, self.original_anchor)
        self._set_active(context.scene, index)
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Construction line: drag, type distance or L, Enter/Click confirm, Esc cancel"
        )
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def _line(self, context):
        lines = iter_construction_lines(context.scene)
        if self.line_index < 0 or self.line_index >= len(lines):
            return None
        return lines[self.line_index]

    def _snap_to_movement_coordinate(self, context, x, y):
        """Snap only the one coordinate available perpendicular to the guide."""
        if self.travel_direction is None:
            return None

        radius = max(15.0, float(state.get("snap_strength", 6.0)) * 2.0)
        try:
            result = snap_scene_geometry(
                context,
                getattr(context, "edit_object", None),
                x,
                y,
                max_px=radius,
                snap_verts=True,
                snap_edges=False,
                snap_edge_center=True,
                snap_face_center=False,
                snap_faces=False,
                include_surface=False,
                enable_mesh=True,
                snap_guides=False,
            )
        except (AttributeError, RuntimeError):
            return None

        if result is None or result.kind not in {"VERT", "EDGE_CENTER"}:
            return None

        signed_distance = (
            Vector(result.location) - self.original_anchor
        ).dot(self.travel_direction)
        return self.original_anchor + self.travel_direction * signed_distance

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
        if delta.length_squared > 1.0e-12:
            self.travel_direction = delta.normalized()
        line = self._line(context)
        if line is None:
            return False
        new_anchor = self.original_anchor + delta
        snapped_anchor = self._snap_to_movement_coordinate(context, x, y)
        if snapped_anchor is not None:
            new_anchor = snapped_anchor
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
        clear_construction_move_distance_input()
        clear_construction_move_preview()
        context.area.tag_redraw()

    def _finish(self, context):
        clear_construction_move_distance_input()
        clear_construction_move_preview()
        context.workspace.status_text_set(None)
        context.area.tag_redraw()

    @staticmethod
    def _event_text(event):
        if event.unicode:
            return event.unicode
        return {
            "ZERO": "0",
            "ONE": "1",
            "TWO": "2",
            "THREE": "3",
            "FOUR": "4",
            "FIVE": "5",
            "SIX": "6",
            "SEVEN": "7",
            "EIGHT": "8",
            "NINE": "9",
            "PERIOD": ".",
            "MINUS": "-",
            "NUMPAD_0": "0",
            "NUMPAD_1": "1",
            "NUMPAD_2": "2",
            "NUMPAD_3": "3",
            "NUMPAD_4": "4",
            "NUMPAD_5": "5",
            "NUMPAD_6": "6",
            "NUMPAD_7": "7",
            "NUMPAD_8": "8",
            "NUMPAD_9": "9",
            "NUMPAD_PERIOD": ".",
            "NUMPAD_MINUS": "-",
            "NUMPAD_SLASH": "/",
        }.get(event.type, "")

    def _insert_distance_text(self, text):
        cursor = self.distance_input_cursor
        self.distance_input = (
            self.distance_input[:cursor] + text + self.distance_input[cursor:]
        )
        self.distance_input_cursor = cursor + len(text)
        set_construction_move_distance_input(
            self.distance_input,
            self.distance_input_cursor,
        )

    def _typed_distance(self, context):
        if not self.distance_input_active or not self.distance_input.strip():
            return None

        meters = abs(parse_length_input(self.distance_input))
        if not math.isfinite(meters) or meters <= 1.0e-8:
            return None

        scale = context.scene.unit_settings.scale_length or 1.0
        return meters / scale

    def _movement_direction(self):
        if self.travel_direction is not None:
            return Vector(self.travel_direction)

        # If the distance is entered before dragging, use a stable direction
        # in the guide's plane and perpendicular to the guide.
        direction = self.plane_normal.cross(self.direction)
        if direction.length_squared <= 1.0e-12:
            return None
        direction.normalize()
        return direction

    def _apply_typed_distance(self, context):
        distance = self._typed_distance(context)
        direction = self._movement_direction()
        line = self._line(context)
        if distance is None or direction is None or line is None:
            return False

        new_anchor = self.original_anchor + direction * distance
        line.anchor = new_anchor
        sync_scene_snap_proxy(context.scene)
        self.travel_direction = direction
        self.changed = True
        set_construction_move_preview(self.original_anchor, new_anchor)
        return True

    def _begin_distance_input(self, event):
        self.distance_input_active = True
        self.distance_input = ""
        self.distance_input_cursor = 0
        set_construction_move_distance_input("", 0)
        if is_number_input(event):
            self._insert_distance_text(self._event_text(event))

    def _handle_distance_input(self, context, event):
        if event.value != "PRESS":
            return {"RUNNING_MODAL"}

        if event.type in {"RET", "NUMPAD_ENTER"}:
            if not self._apply_typed_distance(context):
                self.report({"WARNING"}, "Enter a distance greater than zero")
                return {"RUNNING_MODAL"}
            self._finish(context)
            return {"FINISHED"}

        if event.type == "LEFTMOUSE":
            if is_event_over_ui(context, event):
                return {"PASS_THROUGH"}
            if not self._apply_typed_distance(context):
                self.report({"WARNING"}, "Enter a distance greater than zero")
                return {"RUNNING_MODAL"}
            self._finish(context)
            return {"FINISHED"}

        if event.type == "RIGHTMOUSE":
            if self.changed:
                self._restore(context)
            self._finish(context)
            return {"CANCELLED"}

        if event.type == "ESC":
            self.distance_input_active = False
            self.distance_input = ""
            self.distance_input_cursor = 0
            clear_construction_move_distance_input()
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "LEFT_ARROW":
            self.distance_input_cursor = max(0, self.distance_input_cursor - 1)
        elif event.type == "RIGHT_ARROW":
            self.distance_input_cursor = min(
                len(self.distance_input), self.distance_input_cursor + 1
            )
        elif event.type in {"BACKSPACE", "BACK_SPACE"}:
            cursor = self.distance_input_cursor
            if cursor > 0:
                self.distance_input = (
                    self.distance_input[: cursor - 1] + self.distance_input[cursor:]
                )
                self.distance_input_cursor = cursor - 1
        elif event.type in {"DEL", "DELETE"}:
            cursor = self.distance_input_cursor
            if cursor < len(self.distance_input):
                self.distance_input = (
                    self.distance_input[:cursor] + self.distance_input[cursor + 1:]
                )
        elif event.type == "SPACE":
            self._insert_distance_text(" ")
        elif event.type in {"SLASH", "NUMPAD_SLASH"}:
            self._insert_distance_text("/")
        elif event.type in {"QUOTE", "APOSTROPHE"}:
            self._insert_distance_text('"' if event.shift else "'")
        else:
            text = self._event_text(event)
            if text:
                self._insert_distance_text(text)

        set_construction_move_distance_input(
            self.distance_input,
            self.distance_input_cursor,
        )
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if context.region is None or context.region.type != "WINDOW":
            self._finish(context)
            return {"CANCELLED"}

        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            if is_event_over_ui(context, event):
                return {"RUNNING_MODAL"}
            if self.awaiting_confirmation:
                return {"RUNNING_MODAL"}
            mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            if not self.dragging and (mouse - self.press_mouse).length >= _DRAG_THRESHOLD_PX:
                self.dragging = True
            if self.dragging:
                self._move_to_cursor(context, event.mouse_region_x, event.mouse_region_y)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            was_dragging = self.dragging
            if was_dragging and not is_event_over_ui(context, event):
                self._move_to_cursor(context, event.mouse_region_x, event.mouse_region_y)
            self.dragging = False
            if was_dragging or self.distance_input_active or self.changed:
                # Keep the same modal move alive after the mouse is released.
                # This is the point where the creation tool lets the user type
                # an exact distance before confirming.
                self.awaiting_confirmation = True
            else:
                self._finish(context)
                return {"FINISHED"}
            return {"RUNNING_MODAL"}

        if self.distance_input_active:
            return self._handle_distance_input(context, event)

        if event.value == "PRESS" and (event.type == "L" or is_number_input(event)):
            self._begin_distance_input(event)
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if self.awaiting_confirmation and event.type in {"RET", "NUMPAD_ENTER"}:
            self._finish(context)
            return {"FINISHED"}

        if self.awaiting_confirmation and event.type == "LEFTMOUSE" and event.value == "PRESS":
            if is_event_over_ui(context, event):
                return {"PASS_THROUGH"}
            self._finish(context)
            return {"FINISHED"}

        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            if self.changed:
                self._restore(context)
            self._finish(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}


CLASSES = (VIEW3D_OT_radcad_construction_pick,)
