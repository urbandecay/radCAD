"""Persistent SketchUp-style construction lines for radCAD."""

import bpy

from . import native_snap, overlay, properties
from .operator import CLASSES


def register():
    properties.register()
    native_snap.register()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    overlay.register()


def unregister():
    overlay.unregister()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    native_snap.unregister()
    properties.unregister()
