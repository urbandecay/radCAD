"""Operator entry point for the compass-based Rotate tool."""

import bmesh
import bpy

from ..modal_core import begin_modal, finish_modal, modal_arc_common
from ..modal_state import state


def _has_selected_geometry(context):
    objects = getattr(context, "objects_in_mode_unique_data", ())
    if not objects and context.edit_object is not None:
        objects = (context.edit_object,)

    for obj in objects:
        if obj.type != "MESH" or not obj.data.is_editmode:
            continue
        bm = bmesh.from_edit_mesh(obj.data)
        if any(vert.select and not vert.hide for vert in bm.verts):
            return True
        if any(edge.select and not edge.hide for edge in bm.edges):
            return True
        if any(face.select and not face.hide for face in bm.faces):
            return True
    return False


class VIEW3D_OT_radcad_rotate(bpy.types.Operator):
    bl_idname = "view3d.radcad_rotate"
    bl_label = "Rotate"
    bl_description = (
        "Rotate selected mesh geometry using a snapped compass pivot"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        available = (
            context.area is not None
            and context.area.type == "VIEW_3D"
            and context.mode == "EDIT_MESH"
            and context.edit_object is not None
        )
        if not available:
            cls.poll_message_set("Enter Mesh Edit Mode to use Rotate")
        return available

    def invoke(self, context, event):
        if not _has_selected_geometry(context):
            self.report({"WARNING"}, "Select mesh geometry before using Rotate")
            return {"CANCELLED"}
        state["tool_mode"] = "ROTATE"
        return begin_modal(self, context, event)

    def modal(self, context, event):
        return modal_arc_common(self, context, event)

    def cancel(self, context):
        manager = getattr(self, "manager", None)
        tool = getattr(manager, "active_tool", None)
        if tool is not None:
            tool.cancel(context)
        finish_modal(self, context)
