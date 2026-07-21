"""Dimension label formatting."""

import bpy

from ..units_utils import format_length


def format_dimension_length(measured_length, scene=None):
    scene = scene or bpy.context.scene
    scale = scene.unit_settings.scale_length or 1.0
    return format_length(measured_length * scale)


def dimension_label(data, measured_length, scene=None):
    override = data.text_override.strip()
    if override:
        return override
    return format_dimension_length(measured_length, scene)
