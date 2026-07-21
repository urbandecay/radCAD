"""Modular SketchUp-style linear dimension tool for radCAD."""

import bpy

from . import properties, updater
from .operator import CLASSES


def register():
    properties.register()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    updater.register()


def unregister():
    updater.unregister()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    properties.unregister()
