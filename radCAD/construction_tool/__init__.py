"""Persistent SketchUp-style construction lines for radCAD."""

import bpy

from . import native_snap, overlay, properties
from .operator import CLASSES, register_translate_keymap, unregister_translate_keymap


def _draw_construction_delete_entry(menu, context):
    """Add construction-line deletion to Blender's Edit Mesh Delete menu."""
    scene = getattr(context, "scene", None)
    lines = getattr(scene, "radcad_construction_lines", ())
    index = getattr(scene, "radcad_active_construction_line", -1)
    if not 0 <= index < len(lines):
        return
    menu.layout.separator()
    menu.layout.operator(
        "view3d.radcad_construction_delete",
        text="Construction Line",
        icon="TRASH",
    )


def _register_delete_menu():
    bpy.types.VIEW3D_MT_edit_mesh_delete.append(_draw_construction_delete_entry)


def _unregister_delete_menu():
    bpy.types.VIEW3D_MT_edit_mesh_delete.remove(_draw_construction_delete_entry)


def register():
    properties.register()
    native_snap.register()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    register_translate_keymap()
    _register_delete_menu()
    overlay.register()


def unregister():
    overlay.unregister()
    _unregister_delete_menu()
    unregister_translate_keymap()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    native_snap.unregister()
    properties.unregister()
