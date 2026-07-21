"""Modular SketchUp-style linear dimension tool for radCAD."""

import bpy

from . import properties, updater
from .operator import CLASSES


_ADDON_KEYMAPS = []


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


def unregister():
    _unregister_keymaps()
    updater.unregister()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    properties.unregister()
