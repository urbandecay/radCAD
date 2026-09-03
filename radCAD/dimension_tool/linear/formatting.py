"""Formatting for linear dimension labels."""

import bpy

from ...units_utils import format_length


def format_dimension_length(measured_length, scene=None):
    scene = scene or bpy.context.scene
    scale = scene.unit_settings.scale_length or 1.0
    return format_length(measured_length * scale)
