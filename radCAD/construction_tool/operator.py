"""Interactive construction-line creation and management operators."""

import math
import time

import bpy
from mathutils import Vector

from ..modal_core import DrawManager, is_event_over_ui, is_number_input
from ..modal_state import state
from ..snapping_utils import (
    free_snap_context,
    invalidate_snap_cache,
    snap_scene_geometry,
)
from ..units_utils import format_length, parse_length_input
from .drawing import draw_construction_preview
from .geometry import edge_reference_from_snap, offset_placement_from_cursor
from .model import add_construction_line
from .native_snap import sync_scene_snap_proxy
from .properties import tag_redraw_all_view3d


_PREVIEW_HANDLER = "CONSTRUCTION_LINE_PREVIEW_2D"


class VIEW3D_OT_radcad_construction_line(bpy.types.Operator):
    bl_idname = "view3d.radcad_construction_line"
    bl_label = "Construction Line"
    bl_description = "Offset an edge on a connected face to create a parallel construction guide"
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

        # A newly placed guide must not disappear merely because persistent
        # guide visibility was left off from an earlier session.
        if not context.scene.radcad_construction_lines_visible:
            context.scene.radcad_construction_lines_visible = True

        DrawManager.clear_all()
        invalidate_snap_cache()
        self.context = context
        self.stage = 0
        self.anchor = None
        self.current = None
        self.current_pick = None
        self.hover_edge = None
        self.hover_edge_key = None
        self.source_edge = None
        self.source_point = None
        self.active_face = None
        self.edge_direction = None
        self.plane_normal = None
        self.offset_vector = Vector()
        self.offset_distance = 0.0
        self.preview_label = ""
        self.distance_input_active = False
        self.distance_input = ""
        self.distance_input_cursor = 0
        self.drag_origin = None
        self.drag_moved = False
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
            radius = state.get("snap_strength", 6.0) * 2.0
            result = snap_scene_geometry(
                context,
                context.edit_object,
                event.mouse_region_x,
                event.mouse_region_y,
                max_px=radius,
                snap_verts=False,
                snap_edges=True,
                snap_edge_center=False,
                snap_face_center=False,
                snap_faces=False,
                include_surface=False,
                enable_mesh=True,
                snap_guides=False,
            )
            edge_key = None
            if result is not None and result.kind in {"EDGE", "EDGE_CENTER"}:
                edge_key = (
                    result.target_object.as_pointer() if result.target_object is not None else 0,
                    tuple(result.element_indices),
                    tuple(tuple(co) for co in result.element_coordinates),
                )
            if edge_key != self.hover_edge_key:
                self.hover_edge = edge_reference_from_snap(context, result)
                self.hover_edge_key = edge_key
            self.current_pick = result
            self.current = result.location.copy() if self.hover_edge is not None else None
            state["snap_point"] = self.current.copy() if self.current is not None else None
            state["geometry_snap"] = self.current is not None
        else:
            placement = offset_placement_from_cursor(
                context,
                event,
                self.source_edge,
                self.source_point,
                self.active_face,
            )
            if placement is not None:
                self.active_face = placement.face
                self.plane_normal = placement.face.normal.copy()
                self.offset_vector = placement.offset.copy()
                self.offset_distance = placement.distance
                self.anchor = placement.point.copy()
                self.current = placement.point.copy()

                # Use the same screen-space mesh snap path as the other
                # interactive tools while the guide is being dragged.  Faces
                # are deliberately excluded: construction lines may only
                # land on vertices or edges.
                snap = None
                if not event.shift:
                    snap = snap_scene_geometry(
                        context,
                        context.edit_object,
                        event.mouse_region_x,
                        event.mouse_region_y,
                        max_px=state.get("snap_strength", 6.0) * 2.0,
                        snap_verts=True,
                        snap_edges=True,
                        snap_edge_center=False,
                        snap_face_center=False,
                        snap_faces=False,
                        include_surface=False,
                        enable_mesh=True,
                        snap_guides=False,
                    )
                if snap is not None and snap.kind in {"VERT", "EDGE"}:
                    # Snap coordinates are world-space. Project the snapped
                    # component onto the active drawing face so the guide
                    # remains parallel to the source edge on that face.
                    snapped = snap.location.copy()
                    snapped -= self.active_face.normal * (
                        (snapped - self.source_point).dot(self.active_face.normal)
                    )
                    offset = snapped - self.source_point
                    offset -= self.source_edge.direction * offset.dot(
                        self.source_edge.direction
                    )
                    offset -= self.active_face.normal * offset.dot(
                        self.active_face.normal
                    )
                    self.offset_vector = offset
                    self.offset_distance = offset.length
                    self.anchor = self.source_point + offset
                    self.current = self.anchor.copy()
                if not self.distance_input_active:
                    scale = context.scene.unit_settings.scale_length or 1.0
                    self.preview_label = format_length(self.offset_distance * scale)
            state["snap_point"] = self.current.copy() if self.current is not None else None
            state["geometry_snap"] = False
        context.area.tag_redraw()

    def _start_from_edge(self):
        if self.current is None or self.hover_edge is None:
            self.report({"WARNING"}, "Click an edge that belongs to a face")
            return {"RUNNING_MODAL"}

        self.source_edge = self.hover_edge
        self.source_point = self.current.copy()
        self.anchor = self.source_point.copy()
        self.current = self.source_point.copy()
        self.edge_direction = self.source_edge.direction.copy()
        self.active_face = None
        self.plane_normal = self.source_edge.faces[0].normal.copy()
        self.offset_vector = Vector()
        self.offset_distance = 0.0
        self.preview_label = ""
        self.distance_input_active = False
        self.distance_input = ""
        self.distance_input_cursor = 0
        self.stage = 1
        return {"RUNNING_MODAL"}

    def _typed_distance(self, context):
        if not self.distance_input_active or not self.distance_input.strip():
            return None

        meters = abs(parse_length_input(self.distance_input))
        if not math.isfinite(meters) or meters <= 1.0e-8:
            return None

        scale = context.scene.unit_settings.scale_length or 1.0
        return meters / scale, meters

    def _apply_typed_distance(self, context):
        parsed = self._typed_distance(context)
        if parsed is None or self.source_edge is None or self.source_point is None:
            return False

        distance, meters = parsed
        face = self.active_face or self.source_edge.faces[0]
        direction = self.offset_vector.copy()
        if direction.length_squared <= 1.0e-12:
            direction = face.inward.copy()
        if direction.length_squared <= 1.0e-12:
            return False
        direction.normalize()

        self.active_face = face
        self.plane_normal = face.normal.copy()
        self.offset_vector = direction * distance
        self.offset_distance = distance
        self.anchor = self.source_point + self.offset_vector
        self.current = self.anchor.copy()
        self.preview_label = format_length(meters)
        return True

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

    def _handle_distance_input(self, context, event):
        if event.value != "PRESS":
            return {"RUNNING_MODAL"}

        if event.type in {"RET", "NUMPAD_ENTER"}:
            if not self._apply_typed_distance(context):
                self.report({"WARNING"}, "Enter a distance greater than zero")
                return {"RUNNING_MODAL"}
            return self._place(context)

        if event.type == "LEFTMOUSE":
            if is_event_over_ui(context, event):
                return {"PASS_THROUGH"}
            if not self._apply_typed_distance(context):
                self.report({"WARNING"}, "Enter a distance greater than zero")
                return {"RUNNING_MODAL"}
            return self._place(context)

        if event.type == "RIGHTMOUSE":
            self.finish(context)
            return {"CANCELLED"}

        if event.type == "ESC":
            self.distance_input_active = False
            self.distance_input = ""
            self.distance_input_cursor = 0
            self._update(context, event)
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
                    self.distance_input[:cursor] + self.distance_input[cursor + 1 :]
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

        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def _place(self, context):
        if self.offset_distance <= 1.0e-8:
            self.report({"WARNING"}, "Drag away from the edge to set the guide offset")
            return {"RUNNING_MODAL"}

        line = add_construction_line(
            context.scene,
            self.anchor,
            self.edge_direction,
            self.plane_normal,
        )
        if line is None:
            self.report({"WARNING"}, "Could not create a guide on that face")
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
                if self.drag_origin is not None:
                    dx = event.mouse_region_x - self.drag_origin[0]
                    dy = event.mouse_region_y - self.drag_origin[1]
                    self.drag_moved = self.drag_moved or dx * dx + dy * dy >= 9.0
            return {"RUNNING_MODAL"}
        if self.stage == 1:
            if self.distance_input_active:
                return self._handle_distance_input(context, event)
            if event.value == "PRESS" and (event.type == "L" or is_number_input(event)):
                self.distance_input_active = True
                self.distance_input = ""
                self.distance_input_cursor = 0
                if is_number_input(event):
                    self._insert_distance_text(self._event_text(event))
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if is_event_over_ui(context, event):
                return {"PASS_THROUGH"}
            if self.stage == 0:
                result = self._start_from_edge()
                if self.stage == 1:
                    self.drag_origin = (event.mouse_region_x, event.mouse_region_y)
                    self.drag_moved = False
                return result
            return self._place(context)
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            if self.stage == 1 and self.drag_origin is not None:
                should_place = self.drag_moved
                self.drag_origin = None
                self.drag_moved = False
                if should_place:
                    return self._place(context)
            return {"RUNNING_MODAL"}
        if event.type in {"BACK_SPACE", "BACKSPACE"} and event.value == "PRESS":
            if self.stage == 1:
                self.stage = 0
                self.anchor = None
                self.current = None
                self.source_edge = None
                self.source_point = None
                self.active_face = None
                self.edge_direction = None
                self.distance_input_active = False
                self.distance_input = ""
                self.distance_input_cursor = 0
                self.drag_origin = None
                self.drag_moved = False
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
        sync_scene_snap_proxy(context.scene)
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
        sync_scene_snap_proxy(context.scene)
        tag_redraw_all_view3d()
        return {"FINISHED"}


class VIEW3D_OT_radcad_construction_parameters(bpy.types.Operator):
    bl_idname = "view3d.radcad_construction_parameters"
    bl_label = "Construction Line Parameters"
    bl_description = "Open the movable construction line parameters dialog"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(
            self,
            width=380,
            title="Construction Line Parameters",
            confirm_text="Close",
        )

    def execute(self, _context):
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        scene = context.scene

        display_box = layout.box()
        display_box.label(text="Display", icon="HIDE_OFF")
        display_box.prop(
            scene,
            "radcad_construction_lines_visible",
            text="Show Construction Lines",
        )

        appearance_box = layout.box()
        appearance_box.label(text="Appearance", icon="COLOR")
        appearance_box.prop(scene, "radcad_construction_line_color", text="Color")
        appearance_box.prop(scene, "radcad_construction_line_width", text="Width")
        appearance_box.prop(scene, "radcad_construction_dash_length")
        appearance_box.prop(scene, "radcad_construction_dash_gap")

        guides_box = layout.box()
        guides_box.label(
            text=f"Guides: {len(scene.radcad_construction_lines)}",
            icon="TRACKING",
        )
        guides_box.label(text="Blender snap: enable Vertex or Edge", icon="SNAP_ON")
        row = guides_box.row(align=True)
        row.operator(
            "view3d.radcad_construction_delete_last",
            text="Delete Last",
            icon="LOOP_BACK",
        )
        row.operator(
            "view3d.radcad_construction_clear",
            text="Clear All",
            icon="TRASH",
        )


CLASSES = (
    VIEW3D_OT_radcad_construction_line,
    VIEW3D_OT_radcad_construction_delete_last,
    VIEW3D_OT_radcad_construction_clear,
    VIEW3D_OT_radcad_construction_parameters,
)
