import bpy
from ..modal_state import state


class VIEW3D_OT_line_perp_from_edge(bpy.types.Operator):
    bl_idname = "view3d.line_perp_from_edge"
    bl_label = "Line Perpendicular from Edge"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        state["tool_mode"] = "LINE_PERP_FROM_EDGE"
        return bpy.ops.view3d.radcad_modal('INVOKE_DEFAULT')
