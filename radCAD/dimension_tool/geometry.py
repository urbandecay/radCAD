"""Compatibility exports for the split linear and angular dimension packages."""

from .angular.geometry import AngleLayout, build_angle_layout
from .linear.geometry import (
    DimensionLayout,
    build_layout,
    dimension_basis,
    dimension_plane_from_face,
    signed_offset_from_point,
    text_rotation,
)

__all__ = (
    "AngleLayout",
    "DimensionLayout",
    "build_angle_layout",
    "build_layout",
    "dimension_basis",
    "dimension_plane_from_face",
    "signed_offset_from_point",
    "text_rotation",
)
