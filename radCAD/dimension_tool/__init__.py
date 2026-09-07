"""Modular SketchUp-style linear dimension tool for radCAD."""

import bpy

from . import debug
from . import properties, updater


_ADDON_KEYMAPS = []


def _draw_dimension_delete_entry(menu, context):
    """Add dimension deletion to Blender's Edit Mesh Delete menu."""
    from .model import selected_dimensions

    roots = selected_dimensions(context)
    if not roots:
        return
    dimension_type = (
        "LINEAR"
        if any(getattr(item.radcad_dimension, "dimension_type", "LINEAR") == "LINEAR" for item in roots)
        else "ANGLE"
    )
    menu.layout.separator()
    menu.layout.operator(
        "view3d.radcad_dimension_delete",
        text=(
            "Delete Dimensions" if len(roots) > 1
            else ("Angle Dimension" if dimension_type == "ANGLE" else "Linear Dimension")
        ),
        icon="TRASH",
    )


def _register_delete_menu():
    bpy.types.VIEW3D_MT_edit_mesh_delete.append(_draw_dimension_delete_entry)


def _unregister_delete_menu():
    try:
        bpy.types.VIEW3D_MT_edit_mesh_delete.remove(_draw_dimension_delete_entry)
    except (ReferenceError, RuntimeError, ValueError):
        pass


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
    shift_item = keymap.keymap_items.new(
        "view3d.radcad_dimension_pick",
        type="LEFTMOUSE",
        value="PRESS",
        shift=True,
    )
    _ADDON_KEYMAPS.append((keymap, shift_item))
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
    debug.log("dimension_tool_register_begin", handlers=debug.handler_snapshot())
    properties.register()
    updater.register()
    _register_keymaps()
    _register_delete_menu()
    debug.log("dimension_tool_register_end", handlers=debug.handler_snapshot())


def unregister():
    debug.invalidate_active_preview("dimension_tool_unregister")
    debug.log("dimension_tool_unregister_begin", handlers=debug.handler_snapshot())
    _unregister_delete_menu()
    _unregister_keymaps()
    # A reload can occur while a modal dimension operator is active. Remove
    # its preview callbacks before the old module objects disappear, otherwise
    # the old aligned preview can remain visible and cannot be picked by the
    # new dimension picker.
    from ..modal_core import DrawManager
    from .constants import DRAW_HANDLER_2D, DRAW_HANDLER_3D, DRAW_HANDLER_SNAP_HUD

    for source_id in (DRAW_HANDLER_3D, DRAW_HANDLER_2D, DRAW_HANDLER_SNAP_HUD):
        DrawManager.remove_handler(source_id)
    updater.unregister()
    properties.unregister()
    debug.log("dimension_tool_unregister_end", handlers=debug.handler_snapshot())
