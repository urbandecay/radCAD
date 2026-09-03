"""Blender operators for construction-aware mesh translation."""

import bmesh
import bpy
from bpy_extras.view3d_utils import region_2d_to_location_3d
from mathutils import Vector

from ..construction_tool.model import has_visible_construction_lines
from ..modal_state import state


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
