"""Shared dimension label dispatch."""

from .angular_formatting import format_dimension_angle
from .linear_formatting import format_dimension_length


def dimension_label(data, measured_value, scene=None):
    override = data.text_override.strip()
    if override:
        return override
    if getattr(data, "dimension_type", "LINEAR") == "ANGLE":
        return format_dimension_angle(measured_value, scene)
    return format_dimension_length(measured_value, scene)
