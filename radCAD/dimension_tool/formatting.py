"""Dimension label formatting."""

import math

import bpy

from ..units_utils import format_length


def format_dimension_length(measured_length, scene=None):
    scene = scene or bpy.context.scene
    scale = scene.unit_settings.scale_length or 1.0
    return format_length(measured_length * scale)


def format_dimension_angle(measured_angle, _scene=None):
    """Format an angle dimension in degrees, without length-unit conversion."""
    degrees = math.degrees(abs(float(measured_angle)))
    if abs(degrees - round(degrees)) <= 1.0e-8:
        value = str(int(round(degrees)))
    else:
        value = f"{degrees:.2f}".rstrip("0").rstrip(".")
    return f"{value}\N{DEGREE SIGN}"


def dimension_label(data, measured_value, scene=None):
    override = data.text_override.strip()
    if override:
        return override
    if getattr(data, "dimension_type", "LINEAR") == "ANGLE":
        return format_dimension_angle(measured_value, scene)
    return format_dimension_length(measured_value, scene)
