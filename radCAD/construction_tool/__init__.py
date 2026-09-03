"""Persistent SketchUp-style construction lines for radCAD."""

import bpy

from . import native_snap, overlay, properties
from .keymap import register_translate_keymap, unregister_translate_keymap


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
    register_translate_keymap()
    _register_delete_menu()
    overlay.register()


def unregister():
    overlay.unregister()
    _unregister_delete_menu()
    unregister_translate_keymap()
    native_snap.unregister()
    properties.unregister()
