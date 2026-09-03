"""Linear-dimension geometry and formatting."""

from .formatting import format_dimension_length
from .geometry import (
    DimensionLayout,
    build_layout,
    dimension_basis,
    signed_offset_from_point,
    text_rotation,
)

__all__ = (
    "DimensionLayout",
    "build_layout",
    "dimension_basis",
    "format_dimension_length",
    "signed_offset_from_point",
    "text_rotation",
)
