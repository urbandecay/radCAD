"""Interactive construction-line creation and management operators."""

import math
import time

import bmesh
import bpy
from bpy_extras.view3d_utils import region_2d_to_location_3d
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
from .model import (
    add_construction_line, has_visible_construction_lines,
    clear_construction_selection, selected_construction_line_indices,
)
from .native_snap import sync_scene_snap_proxy
from .properties import tag_redraw_all_view3d
from .selection import VIEW3D_OT_radcad_construction_pick


_PREVIEW_HANDLER = "CONSTRUCTION_LINE_PREVIEW_2D"
_TRANSLATE_KEYMAP_ITEMS = []


class VIEW3D_OT_radcad_construction_translate(bpy.types.Operator):
    """Move edit vertices with exact construction-guide snapping."""

    bl_idname = "view3d.radcad_construction_translate"
    bl_label = "Move with Construction Snapping"
    bl_description = "Move selected mesh elements and snap them exactly onto construction lines"
    bl_options = {"REGISTER", "UNDO", "BLOCKING", "GRAB_CURSOR_X", "GRAB_CURSOR_Y"}

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == "VIEW_3D"
            and context.region is not None
            and context.region.type == "WINDOW"
            and context.region_data is not None
            and context.mode == "EDIT_MESH"
            and context.edit_object is not None
        )

    def invoke(self, context, event):
        # Blender's magnet is the single authority for snapping. Never turn
        # it on here and never construction-snap while the user has it off.
        tool_settings = getattr(context.scene, "tool_settings", None)
        if (
            not has_visible_construction_lines(context.scene)
            or tool_settings is None
            or not tool_settings.use_snap
        ):
            return bpy.ops.transform.translate("INVOKE_DEFAULT")

        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        selected = [vert for vert in bm.verts if vert.select and not vert.hide]
        if not selected:
            return bpy.ops.transform.translate("INVOKE_DEFAULT")

        active = bm.select_history.active
        if not isinstance(active, bmesh.types.BMVert) or not active.select:
            active = selected[0]

        self._object = obj
        self._mesh = obj.data
        # Never keep BMVert objects across modal events. Blender can replace
        # the edit BMesh when it flushes an update, which makes those Python
        # references raise "BMesh data ... has been removed". Stable indices
        # let every event reacquire the current live BMesh instead.
        self._selected = [(vert.index, vert.co.copy()) for vert in selected]
        self._reference_local = active.co.copy()
        self._matrix_world = obj.matrix_world.copy()
        self._matrix_world_inverse = self._matrix_world.inverted_safe()
        self._reference_world = self._matrix_world @ self._reference_local
        self._mouse_start = Vector((event.mouse_region_x, event.mouse_region_y))
        self._mouse_world_start = region_2d_to_location_3d(
            context.region,
            context.region_data,
            self._mouse_start,
            self._reference_world,
        )
        self._constraint_axis = None
        self._constraint_plane = False
        self._snapped = False
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Move: mouse   •   Construction guides snap automatically   •   X/Y/Z constrain   •   LMB/Enter confirm   •   Esc cancel"
        )
        self._update_from_mouse(context, event)
        return {"RUNNING_MODAL"}

    def _constrain_delta(self, delta):
        axis = self._constraint_axis
        if axis is None:
            return delta
        constrained = delta.copy()
        if self._constraint_plane:
            constrained[axis] = 0.0
            return constrained
        result = Vector((0.0, 0.0, 0.0))
        result[axis] = constrained[axis]
        return result

    def _apply_delta(self, delta):
        bm = bmesh.from_edit_mesh(self._mesh)
        bm.verts.ensure_lookup_table()
        for vertex_index, original_local in self._selected:
            original_world = self._matrix_world @ original_local
            bm.verts[vertex_index].co = self._matrix_world_inverse @ (original_world + delta)
        bmesh.update_edit_mesh(self._mesh, loop_triangles=False, destructive=False)

    def _update_from_mouse(self, context, event):
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        # This operator exists specifically to make guides dependable during
        # mesh editing.  Use a deliberately strong magnet so the dashed gaps,
        # face occlusion, and Blender's proxy-edge picking cannot make a guide
        # feel intermittent.
        radius = max(30.0, float(state.get("snap_strength", 6.0)) * 4.0)
        candidate = None
        if context.scene.tool_settings.use_snap:
            try:
                from .snapping import snap_construction_lines

                candidate = snap_construction_lines(
                    context,
                    event.mouse_region_x,
                    event.mouse_region_y,
                    radius,
                )
            except (AttributeError, RuntimeError):
                candidate = None

        if candidate is not None:
            delta = candidate.result.location - self._reference_world
            self._snapped = True
        else:
            mouse_world = region_2d_to_location_3d(
                context.region,
                context.region_data,
                mouse,
                self._reference_world,
            )
            delta = self._constrain_delta(mouse_world - self._mouse_world_start)
            self._snapped = False

        self._apply_delta(delta)
        context.area.tag_redraw()

    def _restore(self):
        bm = bmesh.from_edit_mesh(self._mesh)
        bm.verts.ensure_lookup_table()
        for vertex_index, original_local in self._selected:
            bm.verts[vertex_index].co = original_local
        bmesh.update_edit_mesh(self._mesh, loop_triangles=False, destructive=False)

    def _finish(self, context):
        context.workspace.status_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        if event.type == "MOUSEMOVE":
            self._update_from_mouse(context, event)
            return {"RUNNING_MODAL"}

        if event.type in {"X", "Y", "Z"} and event.value == "PRESS":
            axis = {"X": 0, "Y": 1, "Z": 2}[event.type]
            if self._constraint_axis == axis and self._constraint_plane == event.shift:
                self._constraint_axis = None
                self._constraint_plane = False
            else:
                self._constraint_axis = axis
                self._constraint_plane = bool(event.shift)
            self._update_from_mouse(context, event)
            return {"RUNNING_MODAL"}

        if event.type in {"LEFTMOUSE", "RET", "NUMPAD_ENTER", "SPACE"} and event.value == "PRESS":
            self._finish(context)
            return {"FINISHED"}

        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            self._restore()
            self._finish(context)
            return {"CANCELLED"}

        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}
        return {"RUNNING_MODAL"}


class VIEW3D_OT_radcad_construction_duplicate_translate(bpy.types.Operator):
    """Duplicate edit geometry, then move it with exact guide snapping."""

    bl_idname = "view3d.radcad_construction_duplicate_translate"
    bl_label = "Duplicate with Construction Snapping"
    bl_description = "Duplicate selected mesh elements and snap them onto construction lines"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return VIEW3D_OT_radcad_construction_translate.poll(context)

    def invoke(self, context, _event):
        tool_settings = getattr(context.scene, "tool_settings", None)
        if (
            not has_visible_construction_lines(context.scene)
            or tool_settings is None
            or not tool_settings.use_snap
        ):
            return bpy.ops.mesh.duplicate_move("INVOKE_DEFAULT")

        duplicate_result = bpy.ops.mesh.duplicate()
        if "FINISHED" not in duplicate_result:
            return duplicate_result
        return bpy.ops.view3d.radcad_construction_translate("INVOKE_DEFAULT")


def register_translate_keymap():
    unregister_translate_keymap()
    window_manager = getattr(bpy.context, "window_manager", None)
    key_config = getattr(getattr(window_manager, "keyconfigs", None), "addon", None)
    if key_config is None:
        return
    keymap = key_config.keymaps.new(name="Mesh", space_type="EMPTY")
    # A source reload can replace this module before its old Python list is
    # available to unregister. Remove any stale binding by operator id so G
    # always reaches the current implementation exactly once.
    operator_ids = {
        VIEW3D_OT_radcad_construction_translate.bl_idname,
        VIEW3D_OT_radcad_construction_duplicate_translate.bl_idname,
        VIEW3D_OT_radcad_construction_pick.bl_idname,
        VIEW3D_OT_radcad_construction_delete.bl_idname,
    }
    for existing in list(keymap.keymap_items):
        if existing.idname in operator_ids:
            keymap.keymap_items.remove(existing)
    keymap_item = keymap.keymap_items.new(
        VIEW3D_OT_radcad_construction_translate.bl_idname,
        "G",
        "PRESS",
        head=True,
    )
    _TRANSLATE_KEYMAP_ITEMS.append((keymap, keymap_item))
    duplicate_keymap_item = keymap.keymap_items.new(
        VIEW3D_OT_radcad_construction_duplicate_translate.bl_idname,
        "D",
        "PRESS",
        shift=True,
        head=True,
    )
    _TRANSLATE_KEYMAP_ITEMS.append((keymap, duplicate_keymap_item))
    view_keymap = key_config.keymaps.new(name="3D View", space_type="VIEW_3D")
    for existing in list(view_keymap.keymap_items):
        if existing.idname in {
            VIEW3D_OT_radcad_construction_pick.bl_idname,
            VIEW3D_OT_radcad_construction_delete.bl_idname,
        }:
            view_keymap.keymap_items.remove(existing)
    pick_item = view_keymap.keymap_items.new(
        VIEW3D_OT_radcad_construction_pick.bl_idname,
        "LEFTMOUSE",
        "PRESS",
        head=True,
    )
    _TRANSLATE_KEYMAP_ITEMS.append((view_keymap, pick_item))
    shift_pick_item = view_keymap.keymap_items.new(
        VIEW3D_OT_radcad_construction_pick.bl_idname,
        "LEFTMOUSE",
        "PRESS",
        shift=True,
        head=True,
    )
    _TRANSLATE_KEYMAP_ITEMS.append((view_keymap, shift_pick_item))
    delete_item = view_keymap.keymap_items.new(
        VIEW3D_OT_radcad_construction_delete.bl_idname,
        "DEL",
        "PRESS",
        head=True,
    )
    _TRANSLATE_KEYMAP_ITEMS.append((view_keymap, delete_item))


def unregister_translate_keymap():
    for keymap, keymap_item in _TRANSLATE_KEYMAP_ITEMS:
        try:
            keymap.keymap_items.remove(keymap_item)
        except (ReferenceError, RuntimeError):
            pass
    _TRANSLATE_KEYMAP_ITEMS.clear()


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
                snap_edge_center=True,
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
                        snap_edge_center=True,
                        snap_face_center=False,
                        snap_faces=False,
                        include_surface=False,
                        enable_mesh=True,
                        snap_guides=False,
                    )
                if snap is not None and snap.kind in {"VERT", "EDGE", "EDGE_CENTER"}:
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
        if hasattr(context.scene, "radcad_active_construction_line"):
            clear_construction_selection(context.scene)
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
        if hasattr(context.scene, "radcad_active_construction_line"):
            active = context.scene.radcad_active_construction_line
            context.scene.radcad_active_construction_line = (
                active if 0 <= active < len(lines) else -1
            )
        sync_scene_snap_proxy(context.scene)
        tag_redraw_all_view3d()
        return {"FINISHED"}


class VIEW3D_OT_radcad_construction_delete(bpy.types.Operator):
    bl_idname = "view3d.radcad_construction_delete"
    bl_label = "Delete Construction Lines"
    bl_description = "Delete all selected construction lines"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return bool(selected_construction_line_indices(scene))

    def execute(self, context):
        scene = context.scene
        lines = scene.radcad_construction_lines
        indices = selected_construction_line_indices(scene)
        if not indices:
            return {"CANCELLED"}

        for index in reversed(indices):
            lines.remove(index)
        clear_construction_selection(scene)
        sync_scene_snap_proxy(scene)
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
        if hasattr(context.scene, "radcad_active_construction_line"):
            clear_construction_selection(context.scene)
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
        guides_box.label(text="Edit Mode G: construction snap active", icon="SNAP_ON")
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
    VIEW3D_OT_radcad_construction_translate,
    VIEW3D_OT_radcad_construction_duplicate_translate,
    VIEW3D_OT_radcad_construction_pick,
    VIEW3D_OT_radcad_construction_line,
    VIEW3D_OT_radcad_construction_delete_last,
    VIEW3D_OT_radcad_construction_delete,
    VIEW3D_OT_radcad_construction_clear,
    VIEW3D_OT_radcad_construction_parameters,
)
