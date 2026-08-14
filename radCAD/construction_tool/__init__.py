"""Persistent SketchUp-style construction lines for radCAD."""

import bpy

from . import overlay, properties
from .operator import CLASSES


def register():
    properties.register()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    overlay.register()


def unregister():
    overlay.unregister()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    properties.unregister()
