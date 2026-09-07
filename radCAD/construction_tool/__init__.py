"""Persistent SketchUp-style construction lines for radCAD."""

import bpy

from . import native_snap, overlay, properties
from .model import selected_construction_line_indices
from .operator import CLASSES, register_translate_keymap, unregister_translate_keymap
from ..registration_utils import safe_unregister_class


def _draw_construction_delete_entry(menu, context):
    """Add construction-line deletion to Blender's Edit Mesh Delete menu."""
    scene = getattr(context, "scene", None)
    if not selected_construction_line_indices(scene):
        return
    menu.layout.separator()
    menu.layout.operator(
        "view3d.radcad_construction_delete",
        text="Construction Lines",
        icon="TRASH",
    )


def _register_delete_menu():
    bpy.types.VIEW3D_MT_edit_mesh_delete.append(_draw_construction_delete_entry)


def _unregister_delete_menu():
    try:
        bpy.types.VIEW3D_MT_edit_mesh_delete.remove(_draw_construction_delete_entry)
    except (ReferenceError, RuntimeError, ValueError):
        pass


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
        safe_unregister_class(cls)
    native_snap.unregister()
    properties.unregister()
