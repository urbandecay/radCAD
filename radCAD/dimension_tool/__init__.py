"""Modular SketchUp-style linear dimension tool for radCAD."""

import bpy

from . import properties, updater
from .operator import CLASSES


_ADDON_KEYMAPS = []


def _draw_dimension_delete_entry(menu, context):
    """Add dimension deletion to Blender's Edit Mesh Delete menu."""
    from .model import selected_dimension

    if selected_dimension(context) is None:
        return
    menu.layout.separator()
    menu.layout.operator(
        "view3d.radcad_dimension_delete",
        text="Linear Dimension",
        icon="TRASH",
    )


def _register_delete_menu():
    bpy.types.VIEW3D_MT_edit_mesh_delete.append(_draw_dimension_delete_entry)


def _unregister_delete_menu():
    bpy.types.VIEW3D_MT_edit_mesh_delete.remove(_draw_dimension_delete_entry)


def _register_keymaps():
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is None:
        return
    keymap = keyconfig.keymaps.new(name="3D View", space_type="VIEW_3D")
    keymap_item = keymap.keymap_items.new(
        "view3d.radcad_dimension_pick",
        type="LEFTMOUSE",
        value="PRESS",
    )
    _ADDON_KEYMAPS.append((keymap, keymap_item))
    delete_item = keymap.keymap_items.new(
        "view3d.radcad_dimension_delete",
        type="DEL",
        value="PRESS",
    )
    _ADDON_KEYMAPS.append((keymap, delete_item))


def _unregister_keymaps():
    for keymap, keymap_item in _ADDON_KEYMAPS:
        keymap.keymap_items.remove(keymap_item)
    _ADDON_KEYMAPS.clear()


def register():
    properties.register()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    updater.register()
    _register_keymaps()
    _register_delete_menu()


def unregister():
    _unregister_delete_menu()
    _unregister_keymaps()
    updater.unregister()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    properties.unregister()
