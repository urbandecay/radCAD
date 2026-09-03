"""Blender operators for construction-line editing."""

import bpy

from ..construction_tool.native_snap import sync_scene_snap_proxy
from ..construction_tool.properties import tag_redraw_all_view3d


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
                min(active, len(lines) - 1) if active >= 0 and lines else -1
            )
        sync_scene_snap_proxy(context.scene)
        tag_redraw_all_view3d()
        return {"FINISHED"}


class VIEW3D_OT_radcad_construction_delete(bpy.types.Operator):
    bl_idname = "view3d.radcad_construction_delete"
    bl_label = "Delete Construction Line"
    bl_description = "Delete the selected construction line"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        lines = getattr(scene, "radcad_construction_lines", ())
        index = getattr(scene, "radcad_active_construction_line", -1)
        return 0 <= index < len(lines)

    def execute(self, context):
        scene = context.scene
        lines = scene.radcad_construction_lines
        index = scene.radcad_active_construction_line
        if not 0 <= index < len(lines):
            return {"CANCELLED"}

        lines.remove(index)
        scene.radcad_active_construction_line = -1
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
            context.scene.radcad_active_construction_line = -1
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
    VIEW3D_OT_radcad_construction_delete_last,
    VIEW3D_OT_radcad_construction_delete,
    VIEW3D_OT_radcad_construction_clear,
    VIEW3D_OT_radcad_construction_parameters,
)
