"""Linear-dimension geometry and formatting."""

from .formatting import format_dimension_length
from .geometry import (
    DimensionLayout,
    build_layout,
    dimension_basis,
    dimension_plane_from_face,
    projected_line_direction,
    signed_offset_from_point,
    text_rotation,
)

__all__ = (
    "DimensionLayout",
    "build_layout",
    "dimension_basis",
    "dimension_plane_from_face",
    "projected_line_direction",
    "format_dimension_length",
    "signed_offset_from_point",
    "text_rotation",
)
