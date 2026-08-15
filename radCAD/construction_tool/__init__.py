"""Persistent SketchUp-style construction lines for radCAD."""

import bpy

from . import native_snap, overlay, properties
from .operator import CLASSES, register_translate_keymap, unregister_translate_keymap


def register():
    properties.register()
    native_snap.register()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    register_translate_keymap()
    overlay.register()


def unregister():
    overlay.unregister()
    unregister_translate_keymap()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    native_snap.unregister()
    properties.unregister()
